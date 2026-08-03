"""
scoring/ — AI Provider Configuration & Candidate Scoring Engine.

Plugs into the main FastAPI app:

    from scoring.router import router as scoring_router
    app.include_router(scoring_router)

Layers:
    config_store.py  Feature 1 + 9 — encrypted provider config (Fernet)
    providers.py     Feature 2 — AIProvider / Claude / OpenAI / Mock + factory
    rubrics.py       Feature 5 — data-driven role rubrics (seeded from spec)
    engine.py        Features 3,4 — hybrid objective + AI scoring
    analytics.py     Feature 8 — dashboard aggregates
    audit.py         Feature 9 — evaluation audit log + rate limiter
    router.py        all HTTP endpoints

Persistence (file-based, matching the rest of the app):
    output/ai_config.json     provider config (key encrypted)
    output/.ai_secret.key      Fernet key (chmod 600)
    output/rubrics.json        role rubrics
    output/ai_audit.log        JSONL audit trail
    Results are written into output/form_responses.json (existing AI slots).
"""

from scoring.router import router  # noqa: F401
