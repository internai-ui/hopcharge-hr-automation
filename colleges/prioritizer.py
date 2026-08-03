"""
colleges/prioritizer.py — Feature 7: AI-assisted college prioritisation.

"AI-assisted" here means a transparent, deterministic scoring model rather than
an opaque LLM call — for ranking 50+ colleges this is faster, free, explainable,
and reproducible. The weights are tunable in one place. (A future LLM pass could
override `placement_quality_score` from scraped page text; the seam is left open.)

Score is 0-100, composed of weighted, normalised factors:

  factor                     weight   rationale
  ───────────────────────    ──────   ───────────────────────────────────────
  college_type (tier)          30     IIT/NIT/IIIT yield strongest intern pools
  engineering_intake           20     bigger eng. cohorts → more candidates
  internship_opportunities     20     direct signal of internship demand
  placement_quality_score      15     responsiveness / professionalism of TPO
  historical_engagement        15     warm relationships convert faster

priority_level thresholds:  >=75 High,  >=50 Medium,  else Low
"""

from __future__ import annotations

import logging
from typing import Optional

from colleges.schemas import CollegeRecord, CollegeType, PriorityLevel

logger = logging.getLogger("volt_cv.colleges")

# Tier scores (0-1) by college type — the single most predictive factor.
_TYPE_SCORE = {
    CollegeType.IIT.value:        1.00,
    CollegeType.NIT.value:        0.85,
    CollegeType.IIIT.value:       0.80,
    CollegeType.GOVERNMENT.value: 0.60,
    CollegeType.PRIVATE.value:    0.45,
    CollegeType.OTHER.value:      0.30,
}

_WEIGHTS = {
    "type":        30,
    "intake":      20,
    "internships": 20,
    "quality":     15,
    "engagement":  15,
}

# Normalisation ceilings — values at/above these count as "full marks" for that factor.
_INTAKE_CEILING       = 1500   # annual engineering seats
_INTERNSHIP_CEILING   = 200    # known internship slots
_ENGAGEMENT_CEILING   = 10     # prior interactions


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def score_college(c: CollegeRecord) -> tuple[int, str]:
    """Return (priority_score 0-100, priority_level)."""
    type_factor       = _TYPE_SCORE.get(c.college_type, _TYPE_SCORE[CollegeType.OTHER.value])
    intake_factor     = _clamp01((c.engineering_intake or 0) / _INTAKE_CEILING)
    internship_factor = _clamp01((c.internship_opportunities or 0) / _INTERNSHIP_CEILING)
    quality_factor    = _clamp01((c.placement_quality_score or 0) / 100)
    engagement_factor = _clamp01((c.historical_engagement or 0) / _ENGAGEMENT_CEILING)

    raw = (
        type_factor       * _WEIGHTS["type"]
        + intake_factor     * _WEIGHTS["intake"]
        + internship_factor * _WEIGHTS["internships"]
        + quality_factor    * _WEIGHTS["quality"]
        + engagement_factor * _WEIGHTS["engagement"]
    )
    score = round(raw)

    if score >= 75:
        level = PriorityLevel.HIGH.value
    elif score >= 50:
        level = PriorityLevel.MEDIUM.value
    else:
        level = PriorityLevel.LOW.value

    return score, level


def apply_score(c: CollegeRecord) -> CollegeRecord:
    """Mutate-and-return: compute and cache score/level on the record."""
    c.priority_score, c.priority_level = score_college(c)
    return c


def explain(c: CollegeRecord) -> dict:
    """Per-factor breakdown — useful for a dashboard tooltip or audit."""
    type_factor       = _TYPE_SCORE.get(c.college_type, _TYPE_SCORE[CollegeType.OTHER.value])
    intake_factor     = _clamp01((c.engineering_intake or 0) / _INTAKE_CEILING)
    internship_factor = _clamp01((c.internship_opportunities or 0) / _INTERNSHIP_CEILING)
    quality_factor    = _clamp01((c.placement_quality_score or 0) / 100)
    engagement_factor = _clamp01((c.historical_engagement or 0) / _ENGAGEMENT_CEILING)
    score, level = score_college(c)
    return {
        "college_name": c.college_name,
        "priority_score": score,
        "priority_level": level,
        "breakdown": {
            "college_type":             round(type_factor * _WEIGHTS["type"], 1),
            "engineering_intake":       round(intake_factor * _WEIGHTS["intake"], 1),
            "internship_opportunities": round(internship_factor * _WEIGHTS["internships"], 1),
            "placement_quality":        round(quality_factor * _WEIGHTS["quality"], 1),
            "historical_engagement":    round(engagement_factor * _WEIGHTS["engagement"], 1),
        },
    }
