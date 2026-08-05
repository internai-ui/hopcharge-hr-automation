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
-- 1. accepted_candidates — candidates moving through the 3-stage pipeline
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
-- 2. rejected_candidates — rejected pipeline + reason + round
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
-- 3. selected_candidates — candidates marked ★ Selected (triggers onboarding)
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
-- 4. employees — 35-field master sheet.  Sensitive columns encrypted at rest.
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
-- 5. colleges — college outreach directory
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
-- 6. sync_ledger — drift tracking between JSON files and Postgres
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
