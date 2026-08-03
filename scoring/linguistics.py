"""
scoring/linguistics.py — comprehensive language analysis engine.

Architecture: CONTENT FIRST, style second.
  • Concept detection carries 80-85% of the score
  • Length gives a gentle bonus (not a penalty for short)
  • Writing style/quality carries only ~10-15%
  • Short precise answers score well; long substantive answers score slightly better

Design goals:
  1. Catch the SAME IDEA expressed in every possible way
  2. Cover formal English, casual Indian English, Hinglish, abbreviations, story-form
  3. Never penalise for informal phrasing, grammar issues, or short but direct answers
  4. Reward breadth of concept coverage
"""

from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import List


# ══════════════════════════════════════════════
# NORMALISATION — aggressive, inclusive
# ══════════════════════════════════════════════

_CONTRACTIONS = {
    # Standard English
    "i'd": "i would", "i'll": "i will", "i'm": "i am", "i've": "i have",
    "we'd": "we would", "we'll": "we will", "we're": "we are", "we've": "we have",
    "they're": "they are", "they'd": "they would", "they'll": "they will",
    "he's": "he is", "she's": "she is", "it's": "it is", "that's": "that is",
    "there's": "there is", "here's": "here is", "let's": "let us",
    "what's": "what is", "who's": "who is", "how's": "how is",
    "don't": "do not", "doesn't": "does not", "didn't": "did not",
    "can't": "can not", "cannot": "can not", "won't": "will not",
    "wouldn't": "would not", "shouldn't": "should not", "couldn't": "could not",
    "isn't": "is not", "aren't": "are not", "wasn't": "was not", "weren't": "were not",
    "haven't": "have not", "hasn't": "has not", "hadn't": "had not",
    "i'd've": "i would have", "could've": "could have", "would've": "would have",
    "should've": "should have", "might've": "might have",
    "customer's": "customer", "team's": "team", "company's": "company",
    # Informal / spoken
    "gonna": "going to", "wanna": "want to", "gotta": "got to",
    "kinda": "kind of", "sorta": "sort of", "lotta": "lot of",
    "tryna": "trying to", "hafta": "have to", "oughta": "ought to",
    "lemme": "let me", "gimme": "give me", "betcha": "bet you",
    "y'all": "you all", "ya": "you", "yep": "yes", "yup": "yes",
    "nope": "no", "nah": "no", "yeah": "yes", "yea": "yes",
    # Indian English / Hinglish normalisation
    "theek": "okay", "theek hai": "okay", "sahi": "correct",
    "zaroor": "definitely", "bilkul": "absolutely",
}


def normalise(text: str) -> str:
    t = (text or "").lower().strip()
    for k, v in _CONTRACTIONS.items():
        t = t.replace(k, v)
    t = re.sub(r"[^\w\s']", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def tokens(text: str) -> List[str]:
    return normalise(text).split()


def sentences(text: str) -> List[str]:
    parts = re.split(
        r"[.!?\n;]+|\b(?:then|after that|next|finally|firstly|secondly|thirdly|"
        r"additionally|furthermore|also|moreover|besides)\b",
        (text or "").lower()
    )
    return [p.strip() for p in parts if p.strip()]


def word_count(text: str) -> int:
    return len(tokens(text))


# ══════════════════════════════════════════════
# CONCEPT ENGINE
# ══════════════════════════════════════════════

@dataclass
class Concept:
    """
    One idea, expressed many ways. detect() returns 0..1 strength.
    Intentionally lenient: 1 hit already scores 0.65.
    """
    name: str
    forms: List[str]
    strong_forms: List[str] = field(default_factory=list)
    negators: List[str] = field(default_factory=lambda: [
        "not", "never", "no ", "without", "fail to", "refuse to",
        "do not", "don t", "avoid", "instead of", "opposite of",
    ])

    def detect(self, norm_text: str) -> float:
        hits = 0
        strong_hit = False
        for form in self.forms:
            idx = norm_text.find(form)
            while idx != -1:
                window = norm_text[max(0, idx - 22):idx]
                if not any(neg in window for neg in self.negators):
                    hits += 1
                    if form in self.strong_forms:
                        strong_hit = True
                idx = norm_text.find(form, idx + len(form))
        if hits == 0:
            return 0.0
        base = min(0.65 + 0.10 * (hits - 1), 0.92)
        if strong_hit:
            base = min(base + 0.12, 1.0)
        return base


def best_concept(norm_text: str, concepts: List[Concept]) -> float:
    """Best single concept score — rewards hitting ANY relevant idea."""
    return max((c.detect(norm_text) for c in concepts), default=0.0)


def coverage_score(norm_text: str, concepts: List[Concept]) -> float:
    """
    Rewards covering multiple concept dimensions.
    Best concept = 65%, each additional concept adds diminishing value.
    Short precise answers that hit one concept still score 65%.
    Long answers covering several concepts can reach 95%+.
    """
    if not concepts:
        return 0.0
    scores = sorted([c.detect(norm_text) for c in concepts], reverse=True)
    # Primary concept dominates; secondary/tertiary add bonus
    result = scores[0] * 0.65
    weights = [0.20, 0.10, 0.05]
    for i, s in enumerate(scores[1:4]):
        result += s * weights[i]
    return min(result, 1.0)


# ══════════════════════════════════════════════
# STRUCTURAL SIGNALS — topic-independent
# ══════════════════════════════════════════════

_OWNERSHIP = Concept("ownership", [
    # First-person commitment
    "i will", "i would", "i can", "i am going to", "let me",
    "i will make sure", "i would ensure", "my responsibility",
    "i take ownership", "i would handle", "i would personally",
    "i am responsible", "it is my job", "i will take care",
    "i will deal", "i will manage", "i will see to it",
    "i promise", "i commit", "committed to", "i guarantee",
    "i will make it right", "on me", "my job to",
    "accountable", "i am accountable", "take responsibility",
    "personally ensure", "personally handle",
    # Informal ownership
    "i got this", "on it", "leave it to me", "i will sort it",
    "i will get it done", "i will handle it", "count on me",
    "i own this", "my problem to fix", "i will fix it",
], strong_forms=["i take ownership", "my responsibility", "accountable", "i guarantee"])

_STEPS = Concept("steps", [
    "first", "then", "after that", "next", "finally", "step",
    "secondly", "thirdly", "begin by", "start by", "once", "subsequently",
    "to begin", "initially", "as a first", "number one", "number two",
    "phase one", "phase two", "in the first step", "step one", "step two",
    "following that", "after which", "and then", "before that",
    "prior to", "once done", "after completing",
], strong_forms=["first", "step", "begin by", "step one"])

_FOLLOWUP = Concept("followup", [
    "follow up", "followup", "follow-up", "check back", "confirm",
    "ensure it is resolved", "make sure the issue", "circle back",
    "keep them updated", "update the customer", "verify the solution",
    "ensure satisfaction", "get back to", "touch base", "reach out again",
    "check in", "revisit", "close the loop", "after the call",
    "once resolved", "post resolution", "after resolution",
    "make sure everything", "verify it worked", "ensure it worked",
    "call back", "callback", "revert back", "revert to",
    "keep in touch", "stay in touch", "update them", "inform them later",
    "let them know once", "once fixed", "after fixing",
    "24 hour", "24hours", "within a day", "next day follow",
    "regular updates", "status update", "progress update",
], strong_forms=["follow up", "close the loop", "circle back", "verify it worked"])

_POLITENESS = Concept("politeness", [
    "please", "thank", "apolog", "sorry", "appreciate", "kindly",
    "assure", "i understand", "i would assure", "empathize", "empathise",
    "sympathize", "sympathise", "feel your", "i get it", "that must be",
    "i am sorry", "forgive", "my apologies", "regret", "unfortunate",
    "inconvenience", "sorry for the inconvenience", "apologize",
    "with respect", "respectfully", "politely", "with care",
    "we value", "we care", "you matter", "your concern matters",
    "valid concern", "understandable", "completely understandable",
], strong_forms=["apolog", "i am sorry", "my apologies", "sorry for the inconvenience"])

_EFFORT = Concept("effort", [
    "try", "trying", "do my best", "best effort", "give it my all",
    "work hard", "put in effort", "make an effort", "work on it",
    "handle it", "deal with it", "take care of it", "sort it out",
    "figure it out", "work through", "best i can", "as best as",
    "put my best", "whole effort", "full effort", "dedicated",
    "dedicated to", "committed to helping", "willing to",
    "ready to", "happy to", "glad to", "eager to",
], strong_forms=["do my best", "give it my all", "whole effort"])

_URGENCY = Concept("urgency", [
    "immediately", "right away", "at once", "asap", "urgent",
    "without delay", "quickly", "fast", "rapid", "swift",
    "straight away", "instantly", "right then", "no time to waste",
    "on priority", "high priority", "top priority", "first priority",
    "as soon as possible", "as quickly as possible",
    "within minutes", "within the hour", "same day", "immediately upon",
    "first thing", "before anything else",
], strong_forms=["immediately", "right away", "asap", "straight away"])


def signal_ownership(norm: str) -> float:  return _OWNERSHIP.detect(norm)
def signal_steps(norm: str) -> float:      return _STEPS.detect(norm)
def signal_followup(norm: str) -> float:   return _FOLLOWUP.detect(norm)
def signal_politeness(norm: str) -> float: return _POLITENESS.detect(norm)
def signal_effort(norm: str) -> float:     return _EFFORT.detect(norm)
def signal_urgency(norm: str) -> float:    return _URGENCY.detect(norm)


def lexical_diversity(text: str) -> float:
    """Type-token ratio of content words (0..1).

    A rough proxy for how varied / original the phrasing is. Used by the offline
    rules engine ONLY when the 'creativeness' knob is raised, to give credit to
    expressive, non-templated answers that don't necessarily hit the exact
    domain keywords. Short answers are inherently diverse, so we damp very short
    ones to avoid rewarding two-word replies."""
    toks = [w for w in tokens(text) if len(w) > 2]
    n = len(toks)
    if n < 4:
        return 0.0
    ttr = len(set(toks)) / n
    # Damp: reward genuine variety in a substantive answer, not terse replies.
    reach = min(n / 25.0, 1.0)
    return max(0.0, min(ttr * reach, 1.0))


def signal_specificity(text: str) -> float:
    """Concrete detail: numbers, tools, named processes, reasoning words."""
    norm = normalise(text)
    score = 0.0
    if re.search(r"\b\d+\b", text):
        score += 0.30
    concrete = [
        "dashboard", "ticket", "crm", "log", "gps", "system", "team",
        "manager", "process", "policy", "sla", "report", "tool", "app",
        "database", "escalation", "checklist", "form", "software", "platform",
        "portal", "email", "call", "chat", "whatsapp", "phone",
        "excel", "sheet", "spreadsheet", "tracking", "monitoring",
        "supervisor", "senior", "department", "operations center",
    ]
    score += min(0.40, 0.09 * sum(1 for t in concrete if t in norm))
    connectives = [
        "because", "so that", "in order to", "which means", "therefore",
        "as a result", "since", "due to", "to ensure", "to make sure",
        "this way", "which would", "so the", "to avoid", "to prevent",
        "reason being", "the purpose", "so they",
    ]
    if any(c in norm for c in connectives):
        score += 0.30
    return min(score, 1.0)


# ══════════════════════════════════════════════
# LENGTH BONUS — gentle, not punishing
# ══════════════════════════════════════════════

def length_bonus(text: str) -> float:
    """
    0..1 bonus for answer length.
    Short answers (5+ words): 0.50 base
    Medium (15+ words): 0.75
    Longer (30+ words): 0.90
    Very long (50+ words): 1.0
    Single/empty: 0.0-0.20
    """
    n = word_count(text)
    if n == 0:    return 0.0
    if n < 3:     return 0.15
    if n < 5:     return 0.35
    if n < 10:    return 0.55
    if n < 20:    return 0.72
    if n < 35:    return 0.85
    if n < 50:    return 0.93
    return 1.0


# ══════════════════════════════════════════════
# ANSWER DEPTH — content-based, style-neutral
# ══════════════════════════════════════════════

# Only truly meaningless noise penalised
_PURE_NOISE = {"um", "uh", "hmm", "blah", "lol", "haha", "idk", "dunno"}


def answer_depth(text: str) -> float:
    """
    Content-focused quality estimate.
    Rewards: substance, some structure, genuine attempt.
    Does NOT penalise: informal phrasing, grammar, short but direct answers.
    """
    toks = tokens(text)
    n = len(toks)
    if n == 0:
        return 0.0
    if n < 3:
        return 0.20

    content = [w for w in toks if w not in _PURE_NOISE and len(w) > 1]
    noise_ratio = 1.0 - (len(content) / max(n, 1))

    # Penalise only heavy noise (>40% noise words)
    noise_penalty = max(0.0, noise_ratio - 0.4) * 0.5

    # Base score: any real attempt over 5 words
    base = 0.60 if n >= 5 else 0.35

    # Sentence structure bonus
    sent_count = max(len(sentences(text)), 1)
    structure_bonus = min(sent_count / 3.0, 1.0) * 0.25

    # Vocabulary variety
    unique_ratio = len(set(content)) / max(len(content), 1)
    vocab_bonus = unique_ratio * 0.15

    raw = base + structure_bonus + vocab_bonus - noise_penalty
    return max(0.15, min(raw, 1.0))


# Legacy alias for compatibility
def answer_quality(text: str) -> float:
    return answer_depth(text)
