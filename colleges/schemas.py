"""
colleges/schemas.py — Data models for the College Outreach module.

Two layers, deliberately separated:

  • CollegeRecord (dataclass)  — the storage shape persisted to JSON. Mirrors the
    style of the resume CandidateRecord: plain dataclass + asdict() serialisation.
  • Pydantic models           — request/response validation at the API boundary,
    matching the inline BaseModel style already used in app.py.

The outreach status is a constrained Enum so both layers agree on the allowed
states, and the dashboard/prioritiser can reason about progression order.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, EmailStr, field_validator


# ──────────────────────────────────────────────
# Enums
# ──────────────────────────────────────────────

class OutreachStatus(str, Enum):
    """Ordered outreach pipeline states. The order is meaningful — the dashboard
    funnel and the prioritiser both use the index as a progression signal."""
    NOT_CONTACTED        = "Not Contacted"
    EMAIL_SENT           = "Email Sent"
    AWAITING_RESPONSE    = "Awaiting Response"
    INTERESTED           = "Interested"
    NEED_MORE_INFO       = "Need More Information"
    CALL_SCHEDULED       = "Call Scheduled"
    PARTNERSHIP_DISCUSS  = "Partnership Discussion"
    ACTIVE_PARTNER       = "Active Partner"
    NOT_INTERESTED       = "Not Interested"

    @classmethod
    def ordered(cls) -> list["OutreachStatus"]:
        return [
            cls.NOT_CONTACTED, cls.EMAIL_SENT, cls.AWAITING_RESPONSE,
            cls.INTERESTED, cls.NEED_MORE_INFO, cls.CALL_SCHEDULED,
            cls.PARTNERSHIP_DISCUSS, cls.ACTIVE_PARTNER, cls.NOT_INTERESTED,
        ]

    def progression_index(self) -> int:
        """0-based position in the pipeline. NOT_INTERESTED is terminal (last)."""
        return self.ordered().index(self)


class CollegeType(str, Enum):
    IIT        = "IIT"
    NIT        = "NIT"
    IIIT       = "IIIT"
    GOVERNMENT = "Government"
    PRIVATE    = "Private"
    OTHER      = "Other"


class PriorityLevel(str, Enum):
    HIGH   = "High"
    MEDIUM = "Medium"
    LOW    = "Low"


# ──────────────────────────────────────────────
# Storage dataclass
# ──────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id() -> str:
    return "col_" + uuid.uuid4().hex[:12]


@dataclass
class CollegeRecord:
    """The canonical persisted shape for one college. asdict() → JSON row."""
    # Identity
    id: str = field(default_factory=_new_id)

    # Core fields (from spec)
    college_name: str = ""
    placement_officer_name: str = ""
    designation: str = ""
    email: str = ""
    phone: str = ""
    city: str = ""
    state: str = ""
    college_type: str = CollegeType.OTHER.value
    website: str = ""
    placement_page_url: str = ""
    last_contact_date: str = ""          # ISO date or "" if never contacted
    outreach_status: str = OutreachStatus.NOT_CONTACTED.value

    # Extra signals used by the prioritiser (optional, default-safe)
    engineering_intake: Optional[int] = None      # approx annual eng. seats
    internship_opportunities: Optional[int] = None  # known internship count
    placement_quality_score: Optional[int] = None   # 0-100 manual/derived
    historical_engagement: int = 0                   # # of prior interactions

    # Derived (filled by prioritiser; cached for dashboard speed)
    priority_score: Optional[int] = None
    priority_level: Optional[str] = None

    # Discovery metadata (filled when a contact came from the discovery engine)
    priority_tier: Optional[str] = None    # "Tier 1" | "Tier 2" | "Tier 3"
    source_type: str = ""                  # Website | Placement Brochure | LinkedIn | Enriched | Manual
    source_url: str = ""
    confidence_score: Optional[int] = None # 0-100 (per discovery confidence engine)
    last_verified: str = ""

    # Bookkeeping
    notes: str = ""
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict:
        return asdict(self)


# ──────────────────────────────────────────────
# Pydantic API models
# ──────────────────────────────────────────────

class CollegeBase(BaseModel):
    college_name: str = Field(..., min_length=1, max_length=300)
    placement_officer_name: str = ""
    designation: str = ""
    email: str = ""
    phone: str = ""
    city: str = ""
    state: str = ""
    college_type: CollegeType = CollegeType.OTHER
    website: str = ""
    placement_page_url: str = ""
    engineering_intake: Optional[int] = Field(default=None, ge=0)
    internship_opportunities: Optional[int] = Field(default=None, ge=0)
    placement_quality_score: Optional[int] = Field(default=None, ge=0, le=100)
    historical_engagement: int = Field(default=0, ge=0)
    notes: str = ""

    @field_validator("email")
    @classmethod
    def _email_loose(cls, v: str) -> str:
        # Allow empty (many records start without an email); validate only if present.
        v = (v or "").strip()
        if v and "@" not in v:
            raise ValueError("email must contain '@' or be empty")
        return v


class CollegeCreate(CollegeBase):
    """Body for POST /api/colleges."""
    outreach_status: OutreachStatus = OutreachStatus.NOT_CONTACTED


class CollegeUpdate(BaseModel):
    """Body for PATCH /api/colleges/{id}. All fields optional (partial update)."""
    college_name: Optional[str] = Field(default=None, min_length=1, max_length=300)
    placement_officer_name: Optional[str] = None
    designation: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    college_type: Optional[CollegeType] = None
    website: Optional[str] = None
    placement_page_url: Optional[str] = None
    last_contact_date: Optional[str] = None
    outreach_status: Optional[OutreachStatus] = None
    engineering_intake: Optional[int] = Field(default=None, ge=0)
    internship_opportunities: Optional[int] = Field(default=None, ge=0)
    placement_quality_score: Optional[int] = Field(default=None, ge=0, le=100)
    historical_engagement: Optional[int] = Field(default=None, ge=0)
    notes: Optional[str] = None


class StatusUpdate(BaseModel):
    """Body for POST /api/colleges/{id}/status — outreach tracking."""
    outreach_status: OutreachStatus
    set_contact_date: bool = True   # stamp last_contact_date = today when advancing
    note: str = ""


class CollegeOut(CollegeBase):
    """Response shape — the full record including derived fields."""
    id: str
    last_contact_date: str = ""
    outreach_status: OutreachStatus
    priority_score: Optional[int] = None
    priority_level: Optional[str] = None
    priority_tier: Optional[str] = None
    source_type: str = ""
    source_url: str = ""
    confidence_score: Optional[int] = None
    last_verified: str = ""
    created_at: str
    updated_at: str
