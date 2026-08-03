"""
ai_resume_parser.py — LLM-based resume extraction, layered on top of the
regex + spaCy parser in parser.py.

Why this exists
----------------
Regex/spaCy section-heading detection is fast and free, but it breaks on
resumes with unusual layouts, non-standard section names, tables, or creative
formatting — which is most of what "any resume" actually looks like in the
wild. Routing the extracted text through an LLM for structured extraction
handles arbitrary formats far more consistently, at the cost of an API call.

This reuses the SAME AI-provider abstraction and the SAME master feature
toggle already built for candidate scoring (scoring/config_store.py,
scoring/providers.py) — no separate on/off switch, no separate vendor setup.
The default provider is Hugging Face's free-tier Inference Providers router
(see scoring/providers.py:HuggingFaceProvider), so this costs nothing to try.

When the feature is off (default) or the call fails for any reason, the
caller (app.py) falls back to parser.parse_resume(), which always works
fully offline.
"""

from __future__ import annotations

import logging

from schemas import CandidateRecord, PersonalDetails
from scoring import config_store
from scoring.providers import get_provider, ProviderError

logger = logging.getLogger("volt_cv.ai_parser")

_SYSTEM_PROMPT = (
    "You are a precise resume-parsing engine. Extract structured fields from the "
    "candidate's resume text EXACTLY as written -- never invent, guess, or embellish "
    "any value that is not explicitly present in the text. Leave a field empty "
    '("" or [] as appropriate) if it is not present. Return ONLY a single JSON '
    "object, no prose, matching exactly this shape:\n"
    '{"full_name":"","phone_number":"","email":"","location_city":"",'
    '"summary_objective_profile":"","linkedin_profile":"",'
    '"work_experience":[{"company":"","title":"","duration":"","description":""}],'
    '"education":[{"institution":"","degree":"","year":"","score":""}],'
    '"skills":[""],"languages":[""],"certifications_courses":[""],'
    '"internships_projects":[""],"awards_achievements":[""],'
    '"declaration_reference":"",'
    '"personal_details":{"dob":"","marital_status":"","nationality":"","gender":"","father_name":""},'
    '"field_confidence":{"<field name>":0.0}}\n'
    "field_confidence must give a 0.0-1.0 confidence for every top-level field you "
    "filled in (omit fields you left empty). Confidence reflects how explicit and "
    "unambiguous the evidence in the text was: 0.95+ for something copied verbatim "
    "(an email address, a stated degree name), lower for anything inferred or "
    "reconstructed from surrounding context rather than stated outright."
)

_MAX_INPUT_CHARS = 12000   # a couple of resume pages of text; keeps cost/latency bounded


def _coerce_str(v) -> str:
    if v is None:
        return ""
    return str(v).strip()


def _coerce_list_str(v) -> list:
    if not isinstance(v, list):
        return []
    return [_coerce_str(x) for x in v if _coerce_str(x)]


def _coerce_work_experience(v) -> list:
    if not isinstance(v, list):
        return []
    out = []
    for item in v:
        if not isinstance(item, dict):
            continue
        entry = {
            "company": _coerce_str(item.get("company")),
            "title": _coerce_str(item.get("title")),
            "duration": _coerce_str(item.get("duration")),
            "description": _coerce_str(item.get("description")),
        }
        if any(entry.values()):
            out.append(entry)
    return out


def _coerce_education(v) -> list:
    if not isinstance(v, list):
        return []
    out = []
    for item in v:
        if not isinstance(item, dict):
            continue
        entry = {
            "institution": _coerce_str(item.get("institution")),
            "degree": _coerce_str(item.get("degree")),
            "year": _coerce_str(item.get("year")),
            "score": _coerce_str(item.get("score")),
        }
        if any(entry.values()):
            out.append(entry)
    return out


def _coerce_confidence(v) -> dict:
    if not isinstance(v, dict):
        return {}
    out = {}
    for k, val in v.items():
        try:
            f = float(val)
        except (TypeError, ValueError):
            continue
        out[str(k)] = max(0.0, min(1.0, f))
    return out


def is_available() -> bool:
    """
    True only when the master AI-scoring toggle is on AND a real provider
    (not the offline rules-engine) is configured. Mirrors the exact guard
    scoring/providers.get_provider() already applies, so this stays in sync
    with the AI Settings toggle without duplicating its logic.
    """
    if not config_store.is_feature_enabled():
        return False
    provider = get_provider(config_store.get_runtime_config())
    return getattr(provider, "name", "") != "rules-engine"


def parse_resume_ai(raw_text: str, source_file: str = "") -> CandidateRecord:
    """
    Parse resume text via the configured LLM provider.

    Raises ProviderError/ValueError on any failure (disabled, bad key, no
    credits, unusable response, etc.) — callers must catch and fall back to
    parser.parse_resume(), which always works offline.
    """
    provider = get_provider(config_store.get_runtime_config())
    if getattr(provider, "name", "") == "rules-engine":
        raise ProviderError("AI-based resume parsing is not enabled.", status="disabled")

    text = (raw_text or "").strip()
    if not text:
        raise ValueError("No text to parse.")

    user_prompt = (
        f"Resume text:\n<resume>\n{text[:_MAX_INPUT_CHARS]}\n</resume>\n\n"
        "Return the JSON described in the system prompt."
    )
    data = provider.complete_json(_SYSTEM_PROMPT, user_prompt)
    if not isinstance(data, dict):
        raise ProviderError("Model did not return a JSON object.", status="bad_response")

    record = CandidateRecord(source_file=source_file, raw_text=raw_text)
    record.full_name = _coerce_str(data.get("full_name"))
    record.phone_number = _coerce_str(data.get("phone_number"))
    record.email = _coerce_str(data.get("email"))
    record.location_city = _coerce_str(data.get("location_city"))
    record.summary_objective_profile = _coerce_str(data.get("summary_objective_profile"))
    record.linkedin_profile = _coerce_str(data.get("linkedin_profile"))
    record.work_experience = _coerce_work_experience(data.get("work_experience"))
    record.education = _coerce_education(data.get("education"))
    record.skills = _coerce_list_str(data.get("skills"))
    record.languages = _coerce_list_str(data.get("languages"))
    record.certifications_courses = _coerce_list_str(data.get("certifications_courses"))
    record.internships_projects = _coerce_list_str(data.get("internships_projects"))
    record.awards_achievements = _coerce_list_str(data.get("awards_achievements"))
    record.declaration_reference = _coerce_str(data.get("declaration_reference"))

    pd = data.get("personal_details")
    if isinstance(pd, dict):
        record.personal_details = PersonalDetails(
            dob=_coerce_str(pd.get("dob")),
            marital_status=_coerce_str(pd.get("marital_status")),
            nationality=_coerce_str(pd.get("nationality")),
            gender=_coerce_str(pd.get("gender")),
            father_name=_coerce_str(pd.get("father_name")),
        )

    confidence = _coerce_confidence(data.get("field_confidence"))
    # Guarantee every populated field carries SOME confidence value even if the
    # model omitted it from field_confidence, so the UI never shows a filled
    # field with no confidence indicator.
    populated = {
        "full_name": record.full_name, "phone_number": record.phone_number,
        "email": record.email, "location_city": record.location_city,
        "summary_objective_profile": record.summary_objective_profile,
        "linkedin_profile": record.linkedin_profile,
        "work_experience": record.work_experience, "education": record.education,
        "skills": record.skills, "languages": record.languages,
        "certifications_courses": record.certifications_courses,
        "internships_projects": record.internships_projects,
        "awards_achievements": record.awards_achievements,
        "declaration_reference": record.declaration_reference,
    }
    for field_name, value in populated.items():
        if value and field_name not in confidence:
            confidence[field_name] = 0.75   # model filled it in but forgot to score it
    record.field_confidence = confidence

    if not (record.email or record.full_name or record.phone_number):
        # An essentially empty record means the extraction failed, even if the
        # provider didn't raise — don't silently hand back a blank candidate.
        raise ProviderError("AI extraction returned no usable fields.", status="bad_response")

    return record
