"""
colleges/router.py — All HTTP endpoints for the College Outreach module.

Mounted into the main app via:  app.include_router(colleges_router)

Route map
─────────
  CRUD
    POST   /api/colleges                 create
    GET    /api/colleges                 list (filter/sort/paginate)
    GET    /api/colleges/{id}            retrieve
    PATCH  /api/colleges/{id}            partial update
    DELETE /api/colleges/{id}            delete

  Outreach tracking (Feature 4)
    POST   /api/colleges/{id}/status     advance outreach status

  Prioritisation (Feature 7)
    POST   /api/colleges/rescore         recompute all priority scores
    GET    /api/colleges/priorities/top  top-N "contact next" list
    GET    /api/colleges/{id}/score      per-factor score explanation

  Dashboard (Feature 5)
    GET    /api/colleges/stats/dashboard aggregated stats for charts

  Import / Export (Feature 6)
    POST   /api/colleges/import          upload CSV or XLSX
    GET    /api/colleges/export/{fmt}    download csv | xlsx
"""

from __future__ import annotations

import logging
from dataclasses import asdict

from fastapi import APIRouter, HTTPException, UploadFile, File, Query
from fastapi.responses import JSONResponse, StreamingResponse
import io

from colleges import store, services
from colleges.prioritizer import apply_score, explain
from colleges.schemas import (
    CollegeCreate, CollegeUpdate, StatusUpdate, CollegeRecord,
    OutreachStatus, CollegeType,
)

logger = logging.getLogger("volt_cv.colleges")

router = APIRouter(prefix="/api/colleges", tags=["colleges"])


# ──────────────────────────────────────────────
# Static-path routes FIRST (so they aren't shadowed by /{college_id})
# ──────────────────────────────────────────────

@router.get("/stats/dashboard")
async def get_dashboard():
    """Feature 5 — aggregated counts shaped for dashboard cards & charts."""
    return JSONResponse(content={"success": True, **services.dashboard_stats()})


@router.post("/rescore")
async def rescore():
    """Feature 7 — recompute and persist priority scores for all colleges."""
    count = services.rescore_all()
    return {"success": True, "rescored": count}


@router.get("/priorities/top")
async def top_priorities(limit: int = Query(10, ge=1, le=100)):
    """Feature 7 — highest-priority colleges to contact next."""
    return {"success": True, "colleges": services.top_priorities(limit)}


@router.post("/import")
async def import_colleges(
    file: UploadFile = File(...),
    on_duplicate: str = Query("skip", pattern="^(skip|update)$"),
):
    """Feature 6 — import colleges from CSV or Excel."""
    raw = await file.read()
    name = (file.filename or "").lower()
    try:
        if name.endswith(".csv"):
            summary = services.import_csv(raw, on_duplicate=on_duplicate)
        elif name.endswith((".xlsx", ".xlsm")):
            summary = services.import_excel(raw, on_duplicate=on_duplicate)
        else:
            raise HTTPException(status_code=400,
                                detail="Unsupported file type. Upload .csv or .xlsx")
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Import failed: %s", exc)
        raise HTTPException(status_code=400, detail=f"Could not parse file: {exc}")

    services.rescore_all()   # keep scores fresh after a bulk insert
    return {"success": True, **summary}


@router.get("/export/{fmt}")
async def export_colleges(fmt: str):
    """Feature 6 — export the college DB as CSV or Excel."""
    fmt = fmt.lower()
    if fmt == "csv":
        data = services.export_csv()
        return StreamingResponse(
            io.BytesIO(data), media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=colleges.csv"},
        )
    if fmt in ("xlsx", "excel"):
        data = services.export_excel()
        return StreamingResponse(
            io.BytesIO(data),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment; filename=colleges.xlsx"},
        )
    raise HTTPException(status_code=400, detail="Format must be 'csv' or 'xlsx'.")


# ──────────────────────────────────────────────
# CRUD
# ──────────────────────────────────────────────

@router.post("", status_code=201)
async def create_college(body: CollegeCreate):
    rec = CollegeRecord(
        college_name=body.college_name,
        placement_officer_name=body.placement_officer_name,
        designation=body.designation,
        email=body.email,
        phone=body.phone,
        city=body.city,
        state=body.state,
        college_type=body.college_type.value,
        website=body.website,
        placement_page_url=body.placement_page_url,
        outreach_status=body.outreach_status.value,
        engineering_intake=body.engineering_intake,
        internship_opportunities=body.internship_opportunities,
        placement_quality_score=body.placement_quality_score,
        historical_engagement=body.historical_engagement,
        notes=body.notes,
    )
    apply_score(rec)
    saved, action = store.create(rec, on_duplicate="skip")
    if action == "skipped":
        raise HTTPException(status_code=409,
                            detail=f"College already exists: {body.college_name}")
    return {"success": True, "action": action, "college": saved.to_dict()}


@router.get("")
async def list_colleges(
    status: OutreachStatus | None = None,
    college_type: CollegeType | None = None,
    state: str | None = None,
    search: str | None = None,
    sort_by: str = Query("priority_score", pattern="^(priority_score|college_name|last_contact_date|created_at)$"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """List with optional filters, search, sort, and pagination."""
    colleges = store.list_all()

    # Ensure scores exist for sorting
    for c in colleges:
        if c.priority_score is None:
            apply_score(c)

    if status:
        colleges = [c for c in colleges if c.outreach_status == status.value]
    if college_type:
        colleges = [c for c in colleges if c.college_type == college_type.value]
    if state:
        colleges = [c for c in colleges if c.state.lower() == state.lower()]
    if search:
        q = search.lower()
        colleges = [c for c in colleges
                    if q in c.college_name.lower()
                    or q in c.placement_officer_name.lower()
                    or q in c.city.lower()]

    reverse = order == "desc"
    def _key(c: CollegeRecord):
        v = getattr(c, sort_by, None)
        return (v is None, v if v is not None else "")
    colleges.sort(key=_key, reverse=reverse)

    total = len(colleges)
    page = colleges[offset:offset + limit]
    return {
        "success": True,
        "total": total,
        "count": len(page),
        "offset": offset,
        "colleges": [c.to_dict() for c in page],
    }


# ──────────────────────────────────────────────
# Discovery engine (assisted) — MUST be before /{college_id} wildcards
# so /discover, /discover/bulk, /discover/job/*, /discover/accept
# are not swallowed by the /{college_id} catch-all route.
# ──────────────────────────────────────────────
from pydantic import BaseModel as _BM, Field as _Field
from typing import List as _List, Optional as _Optional
from colleges import discovery as _discovery


class DiscoverOneBody(_BM):
    college_name: str = _Field(..., min_length=1)
    website: str = ""


class DiscoverBulkBody(_BM):
    colleges: _List[DiscoverOneBody] = _Field(default_factory=list)


class AcceptContactBody(_BM):
    """Save a reviewed discovery candidate onto a college record."""
    college_id: _Optional[str] = None
    college_name: str = ""
    contact_name: str = ""
    designation: str = ""
    email: str = ""
    phone: str = ""
    website: str = ""
    placement_page_url: str = ""
    source_type: str = "Website"
    source_url: str = ""
    confidence_score: _Optional[int] = None


@router.post("/discover")
async def discover_one(body: DiscoverOneBody):
    """Run assisted discovery for a single college.
    Runs in a thread so the async event loop is never blocked by network I/O."""
    import asyncio
    loop = asyncio.get_event_loop()
    res = await loop.run_in_executor(
        None,
        lambda: _discovery.discover_college(body.college_name, body.website)
    )
    return {
        "success": True,
        "result": res.to_dict(),
        "linkedin_search": _discovery.linkedin_search_url(body.college_name),
        "google_tpo_search": _discovery.google_tpo_search_url(body.college_name),
        "website_guesses": _discovery.website_guess_urls(body.college_name),
    }


@router.post("/discover/bulk")
async def discover_bulk(body: DiscoverBulkBody):
    """Kick off a background bulk-discovery job. Poll /discover/job/{id}."""
    items = [{"college_name": c.college_name, "website": c.website} for c in body.colleges]
    if not items:
        raise HTTPException(status_code=400, detail="No colleges provided.")
    job = _discovery.start_bulk_discovery(items)
    return {"success": True, "job_id": job.id, "total": job.total}


@router.get("/discover/job/{job_id}")
async def discover_job(job_id: str):
    """Progress + (partial) results for a bulk-discovery job."""
    snap = _discovery.tracker.snapshot(job_id)
    if snap is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return {"success": True, **snap}


@router.post("/discover/accept")
async def discover_accept(body: AcceptContactBody):
    """Persist a human-reviewed contact onto a college record. Dedup-safe."""
    patch = {
        "placement_officer_name": body.contact_name,
        "designation": body.designation,
        "email": body.email,
        "phone": body.phone,
        "source_type": body.source_type,
        "source_url": body.source_url,
        "confidence_score": body.confidence_score,
        "last_verified": _discovery._now_iso(),
    }
    if body.website:         patch["website"] = body.website
    if body.placement_page_url: patch["placement_page_url"] = body.placement_page_url
    patch = {k: v for k, v in patch.items() if v not in ("", None)}

    if body.college_id:
        updated = store.update(body.college_id, patch)
        if updated is None:
            raise HTTPException(status_code=404, detail="College not found.")
        return {"success": True, "college": updated.to_dict(), "action": "updated"}

    if not body.college_name:
        raise HTTPException(status_code=400, detail="Provide college_id or college_name.")
    existing = store.find_by_name(body.college_name)
    if existing:
        updated = store.update(existing.id, patch)
        return {"success": True, "college": updated.to_dict(), "action": "updated"}

    rec = CollegeRecord(college_name=body.college_name, **patch)
    created, status = store.create(rec, on_duplicate="skip")
    return {"success": True, "college": created.to_dict(), "action": status}


# ──────────────────────────────────────────────
# CRUD — single college (wildcard routes — MUST stay after /discover*)
# ──────────────────────────────────────────────

@router.get("/{college_id}")
async def get_college(college_id: str):
    c = store.get(college_id)
    if c is None:
        raise HTTPException(status_code=404, detail="College not found.")
    if c.priority_score is None:
        apply_score(c)
    return {"success": True, "college": c.to_dict()}


@router.get("/{college_id}/score")
async def score_breakdown(college_id: str):
    """Feature 7 — explainable per-factor scoring for one college."""
    c = store.get(college_id)
    if c is None:
        raise HTTPException(status_code=404, detail="College not found.")
    return {"success": True, **explain(c)}


@router.patch("/{college_id}")
async def update_college(college_id: str, body: CollegeUpdate):
    patch = {k: (v.value if hasattr(v, "value") else v)
             for k, v in body.model_dump(exclude_unset=True).items()}
    updated = store.update(college_id, patch)
    if updated is None:
        raise HTTPException(status_code=404, detail="College not found.")
    apply_score(updated)
    store.update(college_id, {"priority_score": updated.priority_score,
                              "priority_level": updated.priority_level})
    return {"success": True, "college": updated.to_dict()}


@router.delete("/{college_id}")
async def delete_college(college_id: str):
    ok = store.delete(college_id)
    if not ok:
        raise HTTPException(status_code=404, detail="College not found.")
    return {"success": True, "deleted": college_id}


# ──────────────────────────────────────────────
# Feature 4 — Outreach status tracking
# ──────────────────────────────────────────────

@router.post("/{college_id}/status")
async def set_status(college_id: str, body: StatusUpdate):
    updated = services.update_status(
        college_id, body.outreach_status,
        set_contact_date=body.set_contact_date, note=body.note,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="College not found.")
    return {"success": True, "college": updated.to_dict()}
