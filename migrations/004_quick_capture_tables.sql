-- Migration 004: Quick Capture -taulut
-- Voice-to-task pipeline: Siri → GitHub Actions → ClickUp + Google Calendar
--
-- Ajettava Supabase SQL Editorissa kerran.

-- ── Pikasyötöt (audit log) ────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS quick_captures (
    id                      BIGSERIAL PRIMARY KEY,

    -- Syöte
    capture_type            TEXT        NOT NULL,  -- 'tehtava' | 'idea'
    raw_text                TEXT        NOT NULL,

    -- Extraction-tulos
    extracted_title         TEXT,
    extracted_description   TEXT,
    extracted_category      TEXT,       -- routing key (ks. CATEGORY_LIST_MAP)
    extracted_list_id       TEXT,       -- ClickUp list ID
    extracted_assignee_name TEXT,
    extracted_assignee_id   TEXT,       -- ClickUp user ID jos löytyi
    extracted_priority      INT,        -- 1=urgent..4=low
    extracted_due_date      DATE,
    extracted_tags          JSONB       DEFAULT '[]',
    needs_calendar          BOOLEAN     DEFAULT FALSE,
    calendar_duration_min   INT,
    extraction_method       TEXT,       -- 'claude' | 'fallback'
    model_used              TEXT,

    -- ClickUp-tulos
    clickup_task_id         TEXT,
    clickup_task_url        TEXT,

    -- Google Calendar -tulos
    calendar_event_id       TEXT,
    calendar_event_url      TEXT,

    -- Status
    status                  TEXT        DEFAULT 'pending',
    -- 'pending' | 'success' | 'error' | 'dry_run'
    error_message           TEXT,
    dry_run                 BOOLEAN     DEFAULT FALSE,

    created_at              TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS quick_captures_created_at_idx
    ON quick_captures (created_at DESC);

CREATE INDEX IF NOT EXISTS quick_captures_status_idx
    ON quick_captures (status);

CREATE INDEX IF NOT EXISTS quick_captures_clickup_task_id_idx
    ON quick_captures (clickup_task_id)
    WHERE clickup_task_id IS NOT NULL;
