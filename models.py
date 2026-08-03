"""
models.py — SQLAlchemy ORM models for the Hopcharge HR BAU system.

Mirrors schema.sql one-to-one. Postgres is the source of truth; the JSON files
in output/ are a mirror kept in step by the dual-write layer (db_sync.py).

Every table carries created_at / updated_at / deleted_at. Soft delete only:
set deleted_at = now() instead of DELETE. The default queries in repo.py
filter `deleted_at IS NULL` so soft-deleted rows disappear from the app but
remain recoverable.

Sensitive employee columns (pan, aadhaar, uan, account_number, salary_ctc) are
stored encrypted as BYTEA via pgcrypto's pgp_sym_encrypt. They are written and
read through the helpers in crypto.py — never as plaintext on the model.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger, CheckConstraint, Column, DateTime, Float, Integer,
    LargeBinary, String, Text, func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase
import uuid


class Base(DeclarativeBase):
    pass


def _uuid_col():
    return Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


# ── 1. form_responses ────────────────────────────────────────────────────────
class FormResponse(Base):
    __tablename__ = "form_responses"
    id = _uuid_col()
    response_id = Column(Text, unique=True, nullable=False)
    form_id = Column(Text)
    form_title = Column(Text)
    name = Column(Text)
    email = Column(Text)
    phone = Column(Text)
    role = Column(Text)
    answers = Column(JSONB, nullable=False, default=list)
    questions = Column(JSONB, nullable=False, default=list)
    objective_score = Column(Integer)
    ai_score = Column(Integer)
    total_score = Column(Integer)
    recommendation = Column(Text)
    submitted_at = Column(DateTime(timezone=True))
    last_synced = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    deleted_at = Column(DateTime(timezone=True))


# ── 2. candidates ────────────────────────────────────────────────────────────
class Candidate(Base):
    __tablename__ = "candidates"
    id = _uuid_col()
    full_name = Column(Text)
    email = Column(Text)
    phone_number = Column(Text)
    location_city = Column(Text)
    linkedin_profile = Column(Text)
    source_file = Column(Text)
    summary_objective_profile = Column(Text)
    work_experience = Column(JSONB, default=list)
    education = Column(JSONB, default=list)
    skills = Column(JSONB, default=list)
    languages = Column(JSONB, default=list)
    certifications_courses = Column(JSONB, default=list)
    internships_projects = Column(JSONB, default=list)
    awards_achievements = Column(JSONB, default=list)
    personal_details = Column(JSONB, default=dict)
    field_confidence = Column(JSONB, default=dict)
    raw_text = Column(Text)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    deleted_at = Column(DateTime(timezone=True))


# ── 3. accepted_candidates ───────────────────────────────────────────────────
class AcceptedCandidate(Base):
    __tablename__ = "accepted_candidates"
    __table_args__ = (
        CheckConstraint("stage IN ('hr','round1','round2')", name="ck_acc_stage"),
    )
    id = _uuid_col()
    response_id = Column(Text, unique=True, nullable=False)
    name = Column(Text)
    email = Column(Text)
    phone = Column(Text)
    role = Column(Text)
    answers = Column(JSONB, default=list)
    objective_score = Column(Integer)
    ai_score = Column(Integer)
    total_score = Column(Integer)
    recommendation = Column(Text)
    stage = Column(Text, nullable=False, default="hr")
    accepted_note = Column(Text, default="")
    accepted_at = Column(DateTime(timezone=True))
    stage_changed_at = Column(DateTime(timezone=True))
    history = Column(JSONB, default=list)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    deleted_at = Column(DateTime(timezone=True))


# ── 4. rejected_candidates ───────────────────────────────────────────────────
class RejectedCandidate(Base):
    __tablename__ = "rejected_candidates"
    id = _uuid_col()
    response_id = Column(Text, unique=True, nullable=False)
    name = Column(Text)
    email = Column(Text)
    phone = Column(Text)
    role = Column(Text)
    answers = Column(JSONB, default=list)
    objective_score = Column(Integer)
    ai_score = Column(Integer)
    total_score = Column(Integer)
    recommendation = Column(Text)
    rejected_round = Column(Text)
    rejected_reason = Column(Text, default="")
    rejected_at = Column(DateTime(timezone=True))
    history = Column(JSONB, default=list)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    deleted_at = Column(DateTime(timezone=True))


# ── 5. selected_candidates ───────────────────────────────────────────────────
class SelectedCandidate(Base):
    __tablename__ = "selected_candidates"
    id = _uuid_col()
    response_id = Column(Text, unique=True, nullable=False)
    email = Column(Text)
    selected_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    deleted_at = Column(DateTime(timezone=True))


# ── 6. employees (sensitive columns are BYTEA, handled in crypto.py) ──────────
class Employee(Base):
    __tablename__ = "employees"
    id = _uuid_col()
    legacy_id = Column(Text)
    employee_status = Column(Text)
    employee_id = Column(Text)
    division = Column(Text)
    department = Column(Text)
    name = Column(Text)
    designation = Column(Text)
    dob = Column(Text)
    age = Column(Text)
    old_joining_date_tpa = Column(Text)
    tpa_employee_code = Column(Text)
    doj = Column(Text)
    service_duration = Column(Text)
    years_of_service_achievement = Column(Text)
    last_date = Column(Text)
    gender = Column(Text)
    fathers_name = Column(Text)
    mothers_name = Column(Text)
    reporting_manager = Column(Text)
    source_of_employment = Column(Text)
    email = Column(Text)
    email_official = Column(Text)
    permanent_address = Column(Text)
    correspondence_address = Column(Text)
    contact_no = Column(Text)
    emergency_contact = Column(Text)
    emergency_relation = Column(Text)
    # encrypted-at-rest:
    pan_enc = Column(LargeBinary)
    aadhaar_enc = Column(LargeBinary)
    uan_enc = Column(LargeBinary)
    account_number_enc = Column(LargeBinary)
    salary_ctc_enc = Column(LargeBinary)
    # non-sensitive remainder:
    esi = Column(Text)
    id_card_given = Column(Text)
    bank_name = Column(Text)
    ifsc = Column(Text)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    deleted_at = Column(DateTime(timezone=True))


# ── 7. colleges ──────────────────────────────────────────────────────────────
class College(Base):
    __tablename__ = "colleges"
    id = _uuid_col()
    legacy_id = Column(Text)
    college_name = Column(Text, nullable=False)
    placement_officer_name = Column(Text)
    designation = Column(Text)
    email = Column(Text)
    phone = Column(Text)
    city = Column(Text)
    state = Column(Text)
    college_type = Column(Text)
    website = Column(Text)
    placement_page_url = Column(Text)
    last_contact_date = Column(Text)
    outreach_status = Column(Text)
    engineering_intake = Column(Integer)
    internship_opportunities = Column(Integer)
    placement_quality_score = Column(Integer)
    historical_engagement = Column(Integer)
    priority_score = Column(Integer)
    priority_level = Column(Text)
    priority_tier = Column(Text)
    source_type = Column(Text)
    source_url = Column(Text)
    confidence_score = Column(Integer)
    last_verified = Column(DateTime(timezone=True))
    notes = Column(Text, default="")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    deleted_at = Column(DateTime(timezone=True))


# ── 8. form_tracking ─────────────────────────────────────────────────────────
class FormTracking(Base):
    __tablename__ = "form_tracking"
    id = _uuid_col()
    token = Column(Text, unique=True, nullable=False)
    email = Column(Text)
    name = Column(Text)
    issued_at = Column(DateTime(timezone=True))
    click_time = Column(DateTime(timezone=True))
    click_count = Column(Integer, default=0)
    last_click_time = Column(DateTime(timezone=True))
    submit_time = Column(DateTime(timezone=True))
    time_taken_seconds = Column(Float)
    status = Column(Text)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    deleted_at = Column(DateTime(timezone=True))


# ── 9. status_tokens ─────────────────────────────────────────────────────────
class StatusToken(Base):
    __tablename__ = "status_tokens"
    id = _uuid_col()
    token = Column(Text, unique=True, nullable=False)
    email = Column(Text)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    deleted_at = Column(DateTime(timezone=True))


# ── 10. scoring_rubrics ──────────────────────────────────────────────────────
class ScoringRubric(Base):
    __tablename__ = "scoring_rubrics"
    id = _uuid_col()
    role_key = Column(Text, unique=True, nullable=False)
    role_name = Column(Text)
    objective_max = Column(Integer)
    ai_max = Column(Integer)
    rubric = Column(JSONB, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    deleted_at = Column(DateTime(timezone=True))


# ── 11. calendly_invites ─────────────────────────────────────────────────────
class CalendlyInvite(Base):
    __tablename__ = "calendly_invites"
    id = _uuid_col()
    sent_at = Column(DateTime(timezone=True))
    sender = Column(Text)
    mode = Column(Text)
    link = Column(Text)
    total = Column(Integer)
    sent = Column(Integer)
    failed = Column(Integer)
    results = Column(JSONB, default=list)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    deleted_at = Column(DateTime(timezone=True))


# ── 12. app_config ───────────────────────────────────────────────────────────
class AppConfig(Base):
    __tablename__ = "app_config"
    id = _uuid_col()
    config_key = Column(Text, unique=True, nullable=False)
    value = Column(JSONB, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    deleted_at = Column(DateTime(timezone=True))


# ── 13. sync_ledger ──────────────────────────────────────────────────────────
class SyncLedger(Base):
    __tablename__ = "sync_ledger"
    id = _uuid_col()
    file_name = Column(Text, unique=True, nullable=False)
    json_checksum = Column(Text)
    last_pushed_to_pg = Column(DateTime(timezone=True))
    last_pulled_to_json = Column(DateTime(timezone=True))
    last_direction = Column(Text)
    note = Column(Text)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
