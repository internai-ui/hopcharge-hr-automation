-- ============================================================================
-- schema.sql — Hopcharge HR BAU Automation · PostgreSQL schema (Neon)
-- ----------------------------------------------------------------------------
-- Design notes
--   * Postgres is the source of truth. JSON files in output/ are a mirror.
--   * Unstructured / audit data (form answers, history, CV extraction) is kept
--     as JSONB rather than over-normalised — it's read whole and rarely queried
--     by inner field, so JSONB is the pragmatic choice.
--   * Soft deletes everywhere: deleted_at IS NULL means "live". Nothing is ever
--     physically removed, so an accidental delete is always recoverable.
--   * Sensitive employee columns are encrypted at rest with pgcrypto (PGP
--     symmetric). They are stored as bytea; the app passes the key at query
--     time. They are NEVER stored or logged in plaintext in the DB.
-- ----------------------------------------------------------------------------
-- Run once against a fresh Neon database:
--   psql "$DATABASE_URL" -f schema.sql
-- Safe to re-run: every object uses IF NOT EXISTS / CREATE OR REPLACE.
-- ============================================================================

-- ── Extensions ──────────────────────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS "pgcrypto";   -- gen_random_uuid(), pgp_sym_*

-- ── Shared trigger: keep updated_at fresh on every UPDATE ────────────────────
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- 1. form_responses — raw Google Form submissions (Round 0 screening)
-- ============================================================================
CREATE TABLE IF NOT EXISTS form_responses (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    response_id   TEXT UNIQUE NOT NULL,          -- Google's responseId
    form_id       TEXT,
    form_title    TEXT,
    name          TEXT,
    email         TEXT,
    phone         TEXT,
    role          TEXT,
    answers       JSONB NOT NULL DEFAULT '[]',   -- [{question,type,answer}, ...]
    questions     JSONB NOT NULL DEFAULT '[]',   -- form schema snapshot
    objective_score  INTEGER,
    ai_score         INTEGER,
    total_score      INTEGER,
    recommendation   TEXT,                        -- Shortlist / Hold / Reject
    submitted_at  TIMESTAMPTZ,
    last_synced   TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_fr_email        ON form_responses (lower(email)) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_fr_phone        ON form_responses (phone)        WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_fr_total_score  ON form_responses (total_score)  WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_fr_role         ON form_responses (role)         WHERE deleted_at IS NULL;
DROP TRIGGER IF EXISTS trg_fr_updated ON form_responses;
CREATE TRIGGER trg_fr_updated BEFORE UPDATE ON form_responses
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ============================================================================
-- 2. candidates — parsed CV data (from PDFs via pdfplumber / OCR)
-- ============================================================================
CREATE TABLE IF NOT EXISTS candidates (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    full_name     TEXT,
    email         TEXT,
    phone_number  TEXT,
    location_city TEXT,
    linkedin_profile TEXT,
    source_file   TEXT,                           -- original PDF filename
    summary_objective_profile TEXT,
    -- Unstructured extraction kept whole as JSONB:
    work_experience        JSONB DEFAULT '[]',
    education              JSONB DEFAULT '[]',
    skills                 JSONB DEFAULT '[]',
    languages              JSONB DEFAULT '[]',
    certifications_courses JSONB DEFAULT '[]',
    internships_projects   JSONB DEFAULT '[]',
    awards_achievements    JSONB DEFAULT '[]',
    personal_details       JSONB DEFAULT '{}',
    field_confidence       JSONB DEFAULT '{}',
    raw_text      TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_cand_email  ON candidates (lower(email))   WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_cand_phone  ON candidates (phone_number)   WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_cand_source ON candidates (source_file)    WHERE deleted_at IS NULL;
DROP TRIGGER IF EXISTS trg_cand_updated ON candidates;
CREATE TRIGGER trg_cand_updated BEFORE UPDATE ON candidates
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ============================================================================
-- 3. accepted_candidates — candidates moving through the 3-stage pipeline
--    stage ∈ {hr, round1, round2}.  history kept as JSONB audit trail.
-- ============================================================================
CREATE TABLE IF NOT EXISTS accepted_candidates (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    response_id   TEXT UNIQUE NOT NULL,
    name          TEXT,
    email         TEXT,
    phone         TEXT,
    role          TEXT,
    answers       JSONB DEFAULT '[]',
    objective_score INTEGER,
    ai_score        INTEGER,
    total_score     INTEGER,
    recommendation  TEXT,
    stage           TEXT NOT NULL DEFAULT 'hr'
                    CHECK (stage IN ('hr','round1','round2')),
    accepted_note   TEXT DEFAULT '',
    accepted_at     TIMESTAMPTZ,
    stage_changed_at TIMESTAMPTZ,
    history         JSONB DEFAULT '[]',           -- [{stage, at}, ...]
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_acc_email ON accepted_candidates (lower(email)) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_acc_stage ON accepted_candidates (stage)        WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_acc_score ON accepted_candidates (total_score)  WHERE deleted_at IS NULL;
DROP TRIGGER IF EXISTS trg_acc_updated ON accepted_candidates;
CREATE TRIGGER trg_acc_updated BEFORE UPDATE ON accepted_candidates
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ============================================================================
-- 4. rejected_candidates — rejected pipeline + reason + round
-- ============================================================================
CREATE TABLE IF NOT EXISTS rejected_candidates (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    response_id   TEXT UNIQUE NOT NULL,
    name          TEXT,
    email         TEXT,
    phone         TEXT,
    role          TEXT,
    answers       JSONB DEFAULT '[]',
    objective_score INTEGER,
    ai_score        INTEGER,
    total_score     INTEGER,
    recommendation  TEXT,
    rejected_round  TEXT,                          -- e.g. "Round 0 — Form Screening"
    rejected_reason TEXT DEFAULT '',
    rejected_at     TIMESTAMPTZ,
    history         JSONB DEFAULT '[]',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_rej_email ON rejected_candidates (lower(email)) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_rej_round ON rejected_candidates (rejected_round) WHERE deleted_at IS NULL;
DROP TRIGGER IF EXISTS trg_rej_updated ON rejected_candidates;
CREATE TRIGGER trg_rej_updated BEFORE UPDATE ON rejected_candidates
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ============================================================================
-- 5. selected_candidates — candidates marked ★ Selected (triggers onboarding)
-- ============================================================================
CREATE TABLE IF NOT EXISTS selected_candidates (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    response_id   TEXT UNIQUE NOT NULL,
    email         TEXT,
    selected_at   TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_sel_email ON selected_candidates (lower(email)) WHERE deleted_at IS NULL;
DROP TRIGGER IF EXISTS trg_sel_updated ON selected_candidates;
CREATE TRIGGER trg_sel_updated BEFORE UPDATE ON selected_candidates
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ============================================================================
-- 6. employees — 35-field master sheet.  Sensitive columns encrypted at rest.
--    Plaintext columns stay TEXT; the 5 sensitive ones are bytea (pgp_sym).
-- ============================================================================
CREATE TABLE IF NOT EXISTS employees (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    legacy_id     TEXT,                            -- original _id from JSON
    employee_status TEXT,
    employee_id   TEXT,
    division      TEXT,
    department    TEXT,
    name          TEXT,
    designation   TEXT,
    dob           TEXT,
    age           TEXT,                            -- derived, stored as given
    old_joining_date_tpa TEXT,
    tpa_employee_code    TEXT,
    doj           TEXT,
    service_duration             TEXT,            -- derived
    years_of_service_achievement TEXT,            -- derived
    last_date     TEXT,
    gender        TEXT,
    fathers_name  TEXT,
    mothers_name  TEXT,
    reporting_manager    TEXT,
    source_of_employment TEXT,
    email         TEXT,
    email_official TEXT,
    permanent_address     TEXT,
    correspondence_address TEXT,
    contact_no    TEXT,
    emergency_contact  TEXT,
    emergency_relation TEXT,
    -- ── Encrypted-at-rest (bytea, pgp_sym_encrypt). Never plaintext. ──
    pan_enc            BYTEA,
    aadhaar_enc        BYTEA,
    uan_enc            BYTEA,
    account_number_enc BYTEA,
    salary_ctc_enc     BYTEA,
    -- ── Non-sensitive remainder ──
    esi           TEXT,
    id_card_given TEXT,
    bank_name     TEXT,
    ifsc          TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_emp_empid  ON employees (employee_id)  WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_emp_email  ON employees (lower(email_official)) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_emp_status ON employees (employee_status) WHERE deleted_at IS NULL;
DROP TRIGGER IF EXISTS trg_emp_updated ON employees;
CREATE TRIGGER trg_emp_updated BEFORE UPDATE ON employees
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ============================================================================
-- 7. colleges — college outreach directory
-- ============================================================================
CREATE TABLE IF NOT EXISTS colleges (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    legacy_id     TEXT,                            -- original "col_..." id
    college_name  TEXT NOT NULL,
    placement_officer_name TEXT,
    designation   TEXT,
    email         TEXT,
    phone         TEXT,
    city          TEXT,
    state         TEXT,
    college_type  TEXT,
    website       TEXT,
    placement_page_url TEXT,
    last_contact_date  TEXT,
    outreach_status    TEXT,
    engineering_intake INTEGER,
    internship_opportunities INTEGER,
    placement_quality_score  INTEGER,
    historical_engagement    INTEGER,
    priority_score INTEGER,
    priority_level TEXT,
    priority_tier  TEXT,
    source_type    TEXT,
    source_url     TEXT,
    confidence_score INTEGER,
    last_verified  TIMESTAMPTZ,
    notes          TEXT DEFAULT '',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_col_name   ON colleges (college_name)   WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_col_status ON colleges (outreach_status) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_col_email  ON colleges (lower(email))   WHERE deleted_at IS NULL;
DROP TRIGGER IF EXISTS trg_col_updated ON colleges;
CREATE TRIGGER trg_col_updated BEFORE UPDATE ON colleges
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ============================================================================
-- 8. form_tracking — email→token, click + completion timestamps
-- ============================================================================
CREATE TABLE IF NOT EXISTS form_tracking (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    token         TEXT UNIQUE NOT NULL,
    email         TEXT,
    name          TEXT,
    issued_at     TIMESTAMPTZ,
    click_time    TIMESTAMPTZ,
    click_count   INTEGER DEFAULT 0,
    last_click_time TIMESTAMPTZ,
    submit_time   TIMESTAMPTZ,
    time_taken_seconds DOUBLE PRECISION,
    status        TEXT,                            -- issued / submitted
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_ft_email  ON form_tracking (lower(email)) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_ft_status ON form_tracking (status)       WHERE deleted_at IS NULL;
DROP TRIGGER IF EXISTS trg_ft_updated ON form_tracking;
CREATE TRIGGER trg_ft_updated BEFORE UPDATE ON form_tracking
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ============================================================================
-- 9. status_tokens — magic-link tokens for the candidate status portal
-- ============================================================================
CREATE TABLE IF NOT EXISTS status_tokens (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    token         TEXT UNIQUE NOT NULL,
    email         TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_st_email ON status_tokens (lower(email)) WHERE deleted_at IS NULL;
DROP TRIGGER IF EXISTS trg_st_updated ON status_tokens;
CREATE TRIGGER trg_st_updated BEFORE UPDATE ON status_tokens
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ============================================================================
-- 10. scoring_rubrics — role-based scoring config (one row per role)
-- ============================================================================
CREATE TABLE IF NOT EXISTS scoring_rubrics (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    role_key      TEXT UNIQUE NOT NULL,            -- customer_support_executive
    role_name     TEXT,
    objective_max INTEGER,
    ai_max        INTEGER,
    rubric        JSONB NOT NULL,                  -- full rule set kept whole
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at    TIMESTAMPTZ
);
DROP TRIGGER IF EXISTS trg_rub_updated ON scoring_rubrics;
CREATE TRIGGER trg_rub_updated BEFORE UPDATE ON scoring_rubrics
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ============================================================================
-- 11. calendly_invites — interview scheduling log (one row per send batch)
-- ============================================================================
CREATE TABLE IF NOT EXISTS calendly_invites (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sent_at       TIMESTAMPTZ,
    sender        TEXT,
    mode          TEXT,
    link          TEXT,
    total         INTEGER,
    sent          INTEGER,
    failed        INTEGER,
    results       JSONB DEFAULT '[]',              -- [{name,email,status}, ...]
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at    TIMESTAMPTZ
);
DROP TRIGGER IF EXISTS trg_cal_updated ON calendly_invites;
CREATE TRIGGER trg_cal_updated BEFORE UPDATE ON calendly_invites
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ============================================================================
-- 12. app_config — key/value store for settings JSON blobs
--     (ai_config, tracking_config, drive_settings, calendly_settings,
--      employee_sheet_settings, drive_sync_state). One row per config name.
-- ============================================================================
CREATE TABLE IF NOT EXISTS app_config (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    config_key    TEXT UNIQUE NOT NULL,            -- e.g. "tracking_config"
    value         JSONB NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at    TIMESTAMPTZ
);
DROP TRIGGER IF EXISTS trg_cfg_updated ON app_config;
CREATE TRIGGER trg_cfg_updated BEFORE UPDATE ON app_config
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ============================================================================
-- 13. sync_ledger — drift tracking between JSON files and Postgres
--     Records the checksum + last sync of each mirrored JSON file so the
--     background sync job can detect when an "offline" edit happened.
-- ============================================================================
CREATE TABLE IF NOT EXISTS sync_ledger (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    file_name     TEXT UNIQUE NOT NULL,            -- "accepted_candidates.json"
    json_checksum TEXT,                             -- sha256 of last-seen JSON
    last_pushed_to_pg  TIMESTAMPTZ,                 -- PG ← JSON
    last_pulled_to_json TIMESTAMPTZ,                -- JSON ← PG
    last_direction TEXT,                            -- 'pg_wins' / 'json_wins'
    note          TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
DROP TRIGGER IF EXISTS trg_ledger_updated ON sync_ledger;
CREATE TRIGGER trg_ledger_updated BEFORE UPDATE ON sync_ledger
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ============================================================================
-- Done.
-- ============================================================================
