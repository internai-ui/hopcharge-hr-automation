"""
scoring/engine.py — Features 3 & 4: the candidate scoring engine.

Public surface (provider-agnostic — the engine never names a provider):

    score_candidate(answers, role_key) -> dict
    score_response(response_id)         -> dict   (reads/writes form_responses.json)
    score_all_pending()                 -> dict   (Feature 7 bulk)
    detected_role(answers)              -> str|None

Hybrid model (Feature 4):
    final = objective_score + ai_score
    objective : deterministic rules from the rubric applied to form answers
    ai        : the LLM allocates marks per question/category using the rubric guide

Result shape (stored back into each response record):
    objective_score, ai_score, total_score, recommendation,
    ai_score_detail { category_scores, strengths, weaknesses, reasoning },
    objective_detail [ {rule, label, points, max} ],
    scored_role, scored_at
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

from scoring import rubrics, config_store, audit
from scoring.providers import get_provider, ProviderError

logger = logging.getLogger("volt_cv.scoring")


# ──────────────────────────────────────────────
# Recommendation bands — calibrated for 30 obj / 70 AI split
# A genuine fresher with good answers (55+) should not be Reject
# ──────────────────────────────────────────────

def recommendation_for(total: int) -> str:
    if total >= 85: return "High Priority"
    if total >= 70: return "Strong Shortlist"
    if total >= 55: return "Shortlist"
    if total >= 38: return "Hold"
    return "Reject"


# ──────────────────────────────────────────────
# Answer lookup helpers
# ──────────────────────────────────────────────

def _find_answer(answers: list[dict], patterns: list[str]) -> str:
    """
    Return the answer whose question best matches the patterns.

    Rather than taking the first question that contains any pattern (which lets a
    broad term like "experience" grab "Dashboard experience" before "Years of
    Operations Experience"), we score every question by how specifically it
    matches and return the best one. Specificity = longest matching pattern,
    tie-broken by how much of the question the match covers.
    """
    best_answer = ""
    best_score = 0.0
    for a in answers:
        q = (a.get("question") or "").lower()
        if not q:
            continue
        # Best (longest) pattern that appears in this question
        matched = [p for p in patterns if p.lower() in q]
        if not matched:
            continue
        longest = max(matched, key=len)
        # Specificity: pattern length + coverage of the question text
        coverage = len(longest) / max(len(q), 1)
        score = len(longest) + coverage
        if score > best_score:
            best_score = score
            best_answer = (a.get("answer") or "").strip()
    return best_answer


# Role detection — ordered MOST-SPECIFIC first so overlapping labels resolve
# correctly (e.g. "Deputy General Manager" must beat "General Manager", and
# "Operations Supervisor" must not be mistaken for "Operations Specialist").
# The phrases mirror the exact "Role applying for" options on the Google Form.
_ROLE_MATCHERS = [
    ("customer_support_executive", ["customer support", "support executive", "cse",
                                    "customer service", "customer care"]),
    ("operations_supervisor",      ["operations supervisor", "ops supervisor",
                                    "operation supervisor"]),
    ("operations_specialist",      ["operations specialist", "ops specialist",
                                    "operation specialist"]),
    ("management_trainee_founders_office",
                                   ["management trainee", "founder's office",
                                    "founders office", "founder office", "trainee"]),
    ("business_development_manager", ["business development", "bdm"]),
    ("deputy_general_manager",     ["deputy general manager", "deputy general",
                                    "deputy gm", "dgm", "deputy"]),
    ("general_manager_sales",      ["general manager", "gm sales", "gm - sales",
                                    "gm-sales"]),
    ("sales_manager_retail",       ["sales manager", "retail sales", "retail"]),
    ("field_application_engineer", ["field application", "application engineer",
                                    "fae", "field engineer"]),
    ("ai_engineer",                ["ai engineer", "ai/ml engineer", "ml engineer",
                                    "machine learning engineer", "ai engineering",
                                    "artificial intelligence engineer"]),
]


def detected_role(answers: list[dict]) -> str | None:
    """Infer the role from a 'Role applying for' style answer; fall back to None."""
    role_text = _find_answer(answers, ["role applying", "role", "position", "applying for"]).lower()
    if not role_text:
        return None
    for role_key, phrases in _ROLE_MATCHERS:
        if any(p in role_text for p in phrases):
            return role_key
    return None


# ──────────────────────────────────────────────
# Objective scoring (deterministic)
# ──────────────────────────────────────────────

def _rating_value(text: str) -> str | None:
    m = re.search(r"[1-5]", text)
    return m.group(0) if m else None


def score_objective(answers: list[dict], rubric: dict) -> tuple[int, list[dict]]:
    detail = []
    total = 0
    for rule in rubric.get("objective_rules", []):
        ans = _find_answer(answers, rule["match"])
        pts = 0
        if ans:
            strat = rule["strategy"]
            if strat == "rating":
                rv = _rating_value(ans)
                pts = rule["rating_table"].get(rv, 0) if rv else 0
            elif strat == "yesno":
                is_yes = ans.strip().lower().startswith(("yes", "y", "true"))
                if is_yes:
                    pts = rule["yes_points"]
                else:
                    # no_points gives partial credit for fresher/no-experience candidates
                    pts = rule.get("no_points", 0)
            elif strat == "map":
                key = ans.strip().lower()
                pts = rule["map_table"].get(key, 0)
                if pts == 0:  # loose contains fallback
                    for k, v in rule["map_table"].items():
                        if k in key:
                            pts = v; break
            elif strat == "contains":
                low = ans.lower()
                for row in rule["contains_table"]:
                    if any(tok in low for tok in row["any"]):
                        pts = row["points"]; break
            elif strat == "multi":
                # Multi-select checkbox questions: award `per` points for each
                # relevant option the candidate ticked, up to the rule max, plus
                # an optional bonus for selecting a high-value option.
                low = ans.lower()
                hits = sum(1 for opt in rule.get("options", []) if opt.lower() in low)
                pts = hits * rule.get("per", 1)
                bonus = rule.get("bonus")
                if bonus and any(t.lower() in low for t in bonus.get("any", [])):
                    pts += bonus.get("points", 0)
        pts = min(pts, rule["max"])
        total += pts
        detail.append({"rule": rule["id"], "label": rule["label"],
                       "points": pts, "max": rule["max"], "answer_seen": bool(ans)})
    return total, detail


# ──────────────────────────────────────────────
# AI scoring (provider-agnostic)
# ──────────────────────────────────────────────

# Trimmed ~32% vs. the original wording (93->63 tokens, cl100k_base). This is
# the highest-volume prompt in the app -- one call per rubric question per
# candidate scored -- so token cuts here multiply across every AI-scored
# candidate. The "award marks only for explicit evidence" instruction (the
# actual anti-generosity behavior) is kept verbatim; only restated/scene-
# setting phrasing ("senior", "STRICTLY according to the rubric" -- already
# implied by the categories+guides passed in every call) was cut.
_SYSTEM_PROMPT = (
    "You are a strict HR evaluator. Award marks ONLY for explicit evidence in "
    "the answer -- do not be generous. Return ONLY JSON:\n"
    '{"score":int,"category_scores":{"<label>":{"score":int,"max":int}},'
    '"strengths":[...],"weaknesses":[...],"reasoning":"..."}'
)


def _ai_question_prompt(question_block: dict, answer_text: str) -> str:
    cats = [{"id": c["id"], "label": c["label"], "max": c["max"], "guide": c["guide"]}
            for c in question_block["categories"]]
    return (
        f"Question total: {question_block['total']} marks.\n"
        f"Score each category below. The score must not exceed each category's max.\n"
        f"<question_id>{question_block.get('id','')}</question_id>\n"
        f"<categories>{json.dumps(cats)}</categories>\n"
        f"Candidate answer:\n<answer>{answer_text or '(no answer provided)'}</answer>\n"
        f"Return the JSON described in the system prompt. 'score' must equal the "
        f"sum of category scores."
    )


# ── Tunable scoring (admin-set strictness + custom semantic traits) ──
STRICTNESS_SWING = 0.25   # +/-25% on the AI sub-score at the extremes
TRAIT_SWING      = 0.10   # +/-10% based on average custom-trait alignment


def _strictness_factor(strictness: int) -> float:
    """50 -> 1.0 (neutral); 100 -> harsher (x0.75); 0 -> more lenient (x1.25)."""
    s = max(0, min(100, int(strictness)))
    return 1.0 + (50 - s) / 50.0 * STRICTNESS_SWING


def _strictness_clause(strictness: int) -> str:
    # Both branches trimmed ~30% (cl100k_base) vs. the original wording;
    # same directive verb + condition ("score LOW/HIGHER when in doubt") kept.
    s = max(0, min(100, int(strictness)))
    if s >= 70:
        return "\nSTRICT: require explicit, specific evidence. When in doubt, score LOW."
    if s <= 30:
        return ("\nLENIENT: reward genuine intent and partial understanding even if "
                "imperfectly worded. When in doubt, score HIGHER.")
    return ""


def _creativeness_clause(creativeness: int) -> str:
    """Tells the model how much to reward original / unconventional thinking.
    Mirrors the offline engine's creativeness knob so both modes behave alike.
    Both branches trimmed 49-66% (cl100k_base) vs. the original wording."""
    c = max(0, min(100, int(creativeness)))
    if c >= 70:
        return "\nReward original, unconventional reasoning even without textbook phrasing."
    if c <= 30:
        return ("\nReward standard, textbook-correct approaches; no extra credit for "
                "novelty unless clearly correct.")
    return ""


def _trait_factor(avg: float) -> float:
    return 1.0 + (avg - 50.0) / 50.0 * TRAIT_SWING


def score_traits(answers: list[dict], traits: list[dict], provider) -> dict:
    """Rate how strongly the candidate's answers DEMONSTRATE each custom trait,
    judged by gist/meaning (never by keyword presence). Returns
    {"scores": {name: 0-100|None}, "average": float, "mode": str}.

    AI mode -> one extra LLM call (the real semantic judgement). Offline mode ->
    stays neutral (no nudge) rather than fake a keyword match, which the design
    explicitly avoids; true gist scoring needs an AI key.
    """
    names = [t.get("name") for t in (traits or []) if t.get("name")]
    if not names:
        return {"scores": {}, "average": 50.0, "mode": "none"}
    if getattr(provider, "name", "") == "rules-engine":
        return {"scores": {n: None for n in names}, "average": 50.0, "mode": "offline"}
    blob = "\n".join(f"Q: {a.get('question','')}\nA: {a.get('answer','')}"
                     for a in answers if a.get("answer"))
    trait_list = "\n".join(
        f"- {t['name']}: {t.get('description') or '(no description - infer the quality)'}"
        for t in traits if t.get("name"))
    # Trimmed ~37% (cl100k_base) vs. the original wording -- the core "judge by
    # meaning, not keywords" instruction (the actual design philosophy this
    # function exists for) is kept verbatim; only restated emphasis was cut.
    sys = ("You are a behavioural assessor. Judge by MEANING, not keyword presence "
           "-- a quality can be shown without naming it. Rate 0-100 how strongly "
           'the answers demonstrate each trait. Return ONLY JSON: {"traits":{"<name>":<0-100>}}')
    usr = (f"Traits to assess by gist:\n{trait_list}\n\n"
           f"Candidate answers:\n{blob or '(no answers provided)'}\n\nReturn the JSON.")
    try:
        out = provider.complete_json(sys, usr)
        raw = out.get("traits", out) if isinstance(out, dict) else {}
        scores = {}
        for n in names:
            v = raw.get(n) if isinstance(raw, dict) else None
            try:
                scores[n] = max(0, min(100, int(v)))
            except (TypeError, ValueError):
                scores[n] = None
        vals = [v for v in scores.values() if v is not None]
        return {"scores": scores, "average": (sum(vals) / len(vals)) if vals else 50.0,
                "mode": "ai"}
    except ProviderError as e:
        logger.warning("Custom-trait scoring failed: %s", e)
        return {"scores": {n: None for n in names}, "average": 50.0, "mode": "error"}


def score_ai(answers: list[dict], rubric: dict, provider,
             strictness: int = 50, traits: list[dict] | None = None,
             creativeness: int = 50) -> tuple[int, dict]:
    """Run each AI question through the provider; aggregate to an ai_score, then
    apply the role's strictness + creativeness and custom-trait alignment.

    Strictness + creativeness are honoured in BOTH modes:
      • AI mode    — via the system-prompt clauses, plus the bounded strictness
                     multiplier below.
      • Offline    — the rules engine internalises both knobs (set as provider
                     attributes here), so we must NOT also apply the multiplier
                     or we would double-count. Hence sf is neutral offline.

    If the provider itself fails (bad key, quota exhausted, network), that is an
    INFRASTRUCTURE error — not a candidate with a bad answer. We flag it via
    detail['error'] so the caller can avoid presenting a misleading 0/Reject.
    """
    # Publish tuning onto the provider. The offline RulesEngineProvider reads
    # these to shape its scoring; real AI providers simply ignore the attributes.
    try:
        provider.strictness = int(strictness)
        provider.creativeness = int(creativeness)
    except Exception:
        pass
    offline = getattr(provider, "name", "") == "rules-engine"

    sys_prompt = (_SYSTEM_PROMPT + _strictness_clause(strictness)
                  + _creativeness_clause(creativeness))
    all_category_scores = {}
    all_strengths, all_weaknesses, reasonings = [], [], []
    ai_total = 0
    provider_error = None   # first ProviderError encountered, if any

    for q in rubric.get("ai_questions", []):
        answer_text = _find_answer(answers, q["match"])
        prompt = _ai_question_prompt(q, answer_text)
        try:
            result = provider.complete_json(sys_prompt, prompt)
        except ProviderError as e:
            logger.warning("AI scoring failed for %s: %s", q["id"], e)
            if provider_error is None:
                provider_error = {"status": getattr(e, "status", "unknown"), "message": str(e)}
            result = {"score": 0, "category_scores": {}, "strengths": [],
                      "weaknesses": [q["id"]], "reasoning": f"Scoring error: {e}"}

        # Clamp category scores to their max and re-sum defensively
        q_cats = {c["label"]: c["max"] for c in q["categories"]}
        q_total = 0
        for label, mx in q_cats.items():
            got = result.get("category_scores", {}).get(label, {})
            pts = min(int(got.get("score", 0) or 0), mx) if isinstance(got, dict) else 0
            # A label may recur across a role's questions (same competency probed
            # more than once). ACCUMULATE rather than overwrite, so the displayed
            # breakdown keeps every question's marks and its maxes still sum to
            # ai_max instead of silently dropping the earlier question's score.
            if label in all_category_scores:
                all_category_scores[label]["score"] += pts
                all_category_scores[label]["max"]   += mx
            else:
                all_category_scores[label] = {"score": pts, "max": mx}
            q_total += pts
        ai_total += min(q_total, q["total"])
        all_strengths += result.get("strengths", []) or []
        all_weaknesses += result.get("weaknesses", []) or []
        if result.get("reasoning"):
            reasonings.append(f"[{q['id']}] {result['reasoning']}")

    ai_max = rubric.get("ai_max", 60)
    raw_ai = min(ai_total, ai_max)

    # Apply role tuning. In AI mode strictness scales the sub-score; in offline
    # mode the rules engine already baked strictness + creativeness in, so the
    # multiplier stays neutral to avoid double-counting. Custom traits nudge only
    # when an AI provider is available. Skip trait scoring if the provider
    # already errored (don't trigger a second failure).
    sf = 1.0 if offline else _strictness_factor(strictness)
    traits = traits or []
    if traits and provider_error is None:
        tinfo = score_traits(answers, traits, provider)
    else:
        tinfo = {"scores": {t.get("name"): None for t in traits},
                 "average": 50.0, "mode": ("skipped" if traits else "none")}
    tf = _trait_factor(tinfo["average"]) if traits else 1.0
    adjusted = int(round(max(0, min(ai_max, raw_ai * sf * tf))))

    detail = {
        "category_scores": all_category_scores,
        "strengths": list(dict.fromkeys(all_strengths))[:6],
        "weaknesses": list(dict.fromkeys(all_weaknesses))[:6],
        "reasoning": "\n".join(reasonings),
        "tuning": {
            "strictness": int(strictness),
            "creativeness": int(creativeness),
            "mode": "offline" if offline else "ai",
            "strictness_factor": round(sf, 3),
            "trait_scores": tinfo["scores"],
            "trait_average": round(tinfo["average"], 1),
            "trait_factor": round(tf, 3),
            "trait_mode": tinfo["mode"],
            "rubric_ai_score": raw_ai,
            "adjusted_ai_score": adjusted,
        },
    }
    if provider_error is not None:
        detail["error"] = provider_error
    return adjusted, detail


# ──────────────────────────────────────────────
# Top-level: score one candidate's answers
# ──────────────────────────────────────────────

def score_candidate(answers: list[dict], role_key: str | None = None) -> dict:
    """Pure function: answers + role → full hybrid result. No persistence."""
    role_key = role_key or detected_role(answers) or "customer_support_executive"
    rubric = rubrics.get_rubric(role_key)
    if rubric is None:
        raise ValueError(f"No rubric for role '{role_key}'.")

    provider = get_provider(config_store.get_runtime_config())

    # Per-role admin tuning (strictness + custom semantic traits). Best-effort:
    # neutral defaults if the settings module/file isn't available.
    try:
        from admin_settings import get_role_tuning
        tuning = get_role_tuning(role_key)
    except Exception:
        tuning = {"strictness": 50, "creativeness": 50, "traits": []}

    objective_score, objective_detail = score_objective(answers, rubric)
    ai_score, ai_detail = score_ai(answers, rubric, provider,
                                   strictness=tuning.get("strictness", 50),
                                   traits=tuning.get("traits", []),
                                   creativeness=tuning.get("creativeness", 50))
    total = objective_score + ai_score

    # If the AI provider failed (quota/key/network), do NOT present this as a
    # genuine low score. Flag it so the UI shows an error, not a "Reject".
    ai_failed = "error" in ai_detail
    rec = "Scoring Failed" if ai_failed else recommendation_for(total)

    return {
        "objective_score": objective_score,
        "ai_score": ai_score,
        "total_score": total,
        "recommendation": rec,
        "scoring_error": ai_detail.get("error"),   # None unless provider failed
        "scored_role": role_key,
        "role_name": rubric["role_name"],
        "provider_used": provider.name,
        "objective_detail": objective_detail,
        "ai_score_detail": ai_detail,
        "scored_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


# ──────────────────────────────────────────────
# Persistence-aware wrappers (read/write form_responses.json)
# ──────────────────────────────────────────────

def _load_responses():
    from forms_retriever import load_store
    return load_store()


def _save_responses(store):
    from forms_retriever import save_store
    save_store(store)


def score_response(response_id: str) -> dict:
    """Score a single stored response and persist results into its record."""
    store = _load_responses()
    target = next((r for r in store.get("responses", []) if r["response_id"] == response_id), None)
    if target is None:
        raise ValueError(f"Response '{response_id}' not found.")

    result = score_candidate(target.get("answers", []))
    _apply_result(target, result)
    _save_responses(store)

    audit.log_evaluation(response_id, result)
    return result


def score_all_pending() -> dict:
    """Feature 7: score every response that has no total_score yet."""
    store = _load_responses()
    responses = store.get("responses", [])
    pending = [r for r in responses if r.get("total_score") is None]

    scored, errors = 0, 0
    for r in pending:
        try:
            result = score_candidate(r.get("answers", []))
            _apply_result(r, result)
            audit.log_evaluation(r["response_id"], result)
            scored += 1
        except Exception as exc:  # one bad record shouldn't abort the batch
            logger.error("Failed to score %s: %s", r.get("response_id"), exc)
            errors += 1

    _save_responses(store)
    return {"scored": scored, "errors": errors, "remaining_pending": 0,
            "total_responses": len(responses)}


def _apply_result(record: dict, result: dict) -> None:
    """Write the hybrid result into a response record (fills the existing slots).

    If the AI provider failed (quota/key/network) AND this record already holds a
    valid earlier score, we keep the earlier score and just attach the error, so
    an API outage never wipes out good data. If there is no earlier score, we
    store the error state explicitly (recommendation = 'Scoring Failed').
    """
    failed = result.get("scoring_error") is not None
    had_good_score = record.get("total_score") is not None and not record.get("scoring_error")

    if failed and had_good_score:
        # Keep prior numbers; only record that the latest attempt errored.
        record["scoring_error"] = result["scoring_error"]
        record["last_error_at"] = result["scored_at"]
        return

    record["objective_score"] = result["objective_score"]
    record["ai_score"]        = result["ai_score"]
    record["total_score"]     = result["total_score"]
    record["recommendation"]  = result["recommendation"]
    record["scored_role"]     = result["scored_role"]
    record["ai_reasoning"]    = result["ai_score_detail"]["reasoning"]
    record["ai_score_detail"] = result["ai_score_detail"]
    record["objective_detail"] = result["objective_detail"]
    record["scored_at"]       = result["scored_at"]
    record["provider_used"]   = result["provider_used"]
    record["scoring_error"]   = result.get("scoring_error")   # None when OK
    # shortlisted flag: True for "Strong Shortlist" and above (total >= 70;
    # see recommendation_for). Never set on a failed score.
    record["shortlisted"]     = (not failed) and result["total_score"] >= 70
