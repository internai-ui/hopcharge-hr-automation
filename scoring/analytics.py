"""
scoring/analytics.py — Feature 8: dashboard analytics over scored candidates.

Reads form_responses.json and produces chart-ready aggregates:
  • average score, counts by recommendation band
  • high-priority / strong-shortlist / awaiting-evaluation tallies
  • score distribution histogram (buckets of 10)
  • role-wise average performance
"""

from __future__ import annotations

import logging

logger = logging.getLogger("volt_cv.scoring")

_BANDS = ["High Priority", "Strong Shortlist", "Shortlist", "Hold", "Reject"]


def _responses() -> list[dict]:
    from forms_retriever import load_store
    return load_store().get("responses", [])


def analytics() -> dict:
    rows = _responses()
    total = len(rows)
    scored = [r for r in rows if r.get("total_score") is not None]
    awaiting = total - len(scored)

    scores = [r["total_score"] for r in scored]
    avg = round(sum(scores) / len(scores), 1) if scores else 0.0

    # Counts by recommendation band
    band_counts = {b: 0 for b in _BANDS}
    for r in scored:
        rec = r.get("recommendation")
        if rec in band_counts:
            band_counts[rec] += 1

    # Score distribution — 10-wide buckets 0-100
    buckets = {f"{i}-{i+9}": 0 for i in range(0, 100, 10)}
    buckets["100"] = 0
    for s in scores:
        if s >= 100:
            buckets["100"] += 1
        else:
            lo = (s // 10) * 10
            buckets[f"{lo}-{lo+9}"] += 1

    # Role-wise average
    role_acc: dict[str, list[int]] = {}
    for r in scored:
        role = r.get("scored_role", "unknown")
        role_acc.setdefault(role, []).append(r["total_score"])
    role_perf = [
        {"role": role, "avg_score": round(sum(v) / len(v), 1), "count": len(v)}
        for role, v in role_acc.items()
    ]

    return {
        "cards": {
            "average_score": avg,
            "high_priority": band_counts["High Priority"],
            "strong_shortlists": band_counts["Strong Shortlist"],
            "awaiting_evaluation": awaiting,
            "total_scored": len(scored),
        },
        "by_recommendation": [{"band": b, "count": band_counts[b]} for b in _BANDS],
        "score_distribution": [{"range": k, "count": v} for k, v in buckets.items()],
        "role_performance": role_perf,
    }
