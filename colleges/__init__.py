"""
colleges/ — College Outreach & Internship Partnership module.

A self-contained sub-package that plugs into the main FastAPI app:

    from colleges.router import router as colleges_router
    app.include_router(colleges_router)

Layers:
    schemas.py      dataclass (storage) + Pydantic (API) models, enums
    store.py        JSON-file persistence (swap-in seam for Postgres later)
    prioritizer.py  Feature 7 — deterministic scoring engine
    services.py     dashboard, status tracking, CSV/Excel import-export
    router.py       all HTTP endpoints

Deferred:
    crawler.py      Feature 2/3 — Playwright discovery + contact extraction.
                    Not built yet (chosen to ship DB/CRUD/dashboard first).
                    services.bulk_upsert() is the seam the crawler will write to.
"""

from colleges.router import router  # noqa: F401
