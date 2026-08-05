"""
ai_resume_parser.py — LLM-based resume extraction.

Routing text through an LLM for structured extraction handles arbitrary formats consistently.
Uses ai_config_store and ai_providers for AI settings.
When disabled or on failure, app.py falls back to parser.parse_resume() (regex + spaCy).
"""

from __future__ import annotations

import logging

from schemas import CandidateRecord, PersonalDetails
import ai_config_store
from ai_providers import get_provider, ProviderError

logger = logging.getLogger("volt_cv.ai_parser")

_SYSTEM_PROMPT = (
    "Extract resume fields EXACTLY as written -- never invent or infer missing "
    'values; use "" or [] if absent. Return ONLY this JSON:\n'
    '{"full_name":"","phone_number":"","email":"","location_city":"",'
    '"summary_objective_profile":"","linkedin_profile":"",'
    '"work_experience":[{"company":"","title":"","duration":"","description":""}],'
    '"education":[{"institution":"","degree":"","year":"","score":""}],'
    '"skills":[""],"languages":[""],"certifications_courses":[""],'
    '"internships_projects":[""],"awards_achievements":[""],'
    '"declaration_reference":"",'
    '"personal_details":{"dob":"","marital_status":"","nationality":"","gender":"","father_name":""},'
    '"field_confidence":{"<field>":0.0}}\n'
    "field_confidence: 0-1 per filled field only. 0.95+ for verbatim facts (email, "
    "degree name); lower for inferred/reconstructed values."
)

_MAX_INPUT_CHARS = 12000


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
    """True when master AI feature toggle is enabled."""
    return ai_config_store.is_feature_enabled()


def parse_resume_ai(raw_text: str, source_file: str = "") -> CandidateRecord:
    """Parse resume text via the configured LLM provider."""
    if not is_available():
        raise ProviderError("AI resume parsing is disabled", status="disabled")

    text = (raw_text or "").strip()
    if not text:
        raise ValueError("No text to parse.")

    provider = get_provider(ai_config_store.get_runtime_config())
    user_prompt = (
        f"Resume Source File: {source_file}\n\n"
        f"Resume Content:\n<resume>\n{text[:_MAX_INPUT_CHARS]}\n</resume>\n\n"
        "Return the JSON described in the system prompt."
    )

    out = provider.complete_json(_SYSTEM_PROMPT, user_prompt)
    if not isinstance(out, dict):
        raise ProviderError("LLM returned non-dict response", status="bad_response")

    pers = out.get("personal_details") if isinstance(out.get("personal_details"), dict) else {}
    pd = PersonalDetails(
        dob=_coerce_str(pers.get("dob")),
        marital_status=_coerce_str(pers.get("marital_status")),
        nationality=_coerce_str(pers.get("nationality")),
        gender=_coerce_str(pers.get("gender")),
        father_name=_coerce_str(pers.get("father_name")),
    )

    rec = CandidateRecord(
        source_file=source_file,
        raw_text=raw_text,
        full_name=_coerce_str(out.get("full_name")),
        phone_number=_coerce_str(out.get("phone_number")),
        email=_coerce_str(out.get("email")),
        location_city=_coerce_str(out.get("location_city")),
        summary_objective_profile=_coerce_str(out.get("summary_objective_profile")),
        linkedin_profile=_coerce_str(out.get("linkedin_profile")),
        work_experience=_coerce_work_experience(out.get("work_experience")),
        education=_coerce_education(out.get("education")),
        skills=_coerce_list_str(out.get("skills")),
        languages=_coerce_list_str(out.get("languages")),
        certifications_courses=_coerce_list_str(out.get("certifications_courses")),
        internships_projects=_coerce_list_str(out.get("internships_projects")),
        awards_achievements=_coerce_list_str(out.get("awards_achievements")),
        declaration_reference=_coerce_str(out.get("declaration_reference")),
        personal_details=pd,
        field_confidence=_coerce_confidence(out.get("field_confidence")),
    )

    if not (rec.email or rec.full_name or rec.phone_number):
        raise ProviderError("AI extraction returned no usable fields.", status="bad_response")

    return rec
