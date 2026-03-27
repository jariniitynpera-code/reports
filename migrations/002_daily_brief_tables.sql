-- ============================================================
-- Daily Brief — Supabase migraatio
-- Versio: 002
-- Luotu: 2026-03-27
-- ============================================================

-- ── 1. daily_briefs — generoidut briiffit ────────────────────────────────────

CREATE TABLE IF NOT EXISTS daily_briefs (
    id                  BIGSERIAL PRIMARY KEY,
    brief_date          DATE        NOT NULL UNIQUE,
    -- Päivä jota briiffi koskee (huominen)
    brief_text          TEXT        NOT NULL,
    day_load            TEXT        NOT NULL DEFAULT 'normal',
    -- light / normal / tight / moving
    approval_status     TEXT        NOT NULL DEFAULT 'suggested',
    -- suggested / approved / edited / rejected
    clickup_task_id     TEXT,
    clickup_task_url    TEXT,
    source_summary      JSONB,
    -- {events_count, tasks_fetched, tasks_selected, transitions, shopify_signals}
    generated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    approved_at         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_daily_briefs_date
    ON daily_briefs (brief_date DESC);

-- ── 2. daily_brief_runs — ajot ja audit trail ────────────────────────────────

CREATE TABLE IF NOT EXISTS daily_brief_runs (
    id              BIGSERIAL PRIMARY KEY,
    run_type        TEXT        NOT NULL DEFAULT 'daily_brief',
    brief_date      DATE        NOT NULL,
    status          TEXT        NOT NULL DEFAULT 'running',
    -- running / success / failed / skipped / superseded
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at     TIMESTAMPTZ,
    error_message   TEXT,
    run_metadata    JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uidx_brief_runs_date_success
    ON daily_brief_runs (run_type, brief_date)
    WHERE status = 'success';

CREATE INDEX IF NOT EXISTS idx_brief_runs_date
    ON daily_brief_runs (brief_date DESC);

-- ── Triggeri: updated_at ──────────────────────────────────────────────────────

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger WHERE tgname = 'trg_briefs_updated_at'
    ) THEN
        CREATE TRIGGER trg_briefs_updated_at
        BEFORE UPDATE ON daily_briefs
        FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;
END $$;

-- ── Kommentit ─────────────────────────────────────────────────────────────────

COMMENT ON TABLE daily_briefs IS
    'Generoidut huomisen briiffit. Yksi rivi per päivä. '
    'approval_status: suggested=ehdotus, approved=käyttäjä hyväksynyt, '
    'edited=muokattu, rejected=hylätty.';

COMMENT ON TABLE daily_brief_runs IS
    'Jokaisen briiffiajon audit trail ja idempotenssisuoja.';
