-- ============================================================
-- Meeting Notes → ClickUp Tasks — Supabase migraatio
-- Versio: 003
-- Luotu: 2026-03-27
-- ============================================================

-- ── 1. meeting_note_sources — prosessoidut muistiolähteet ────────────────────

CREATE TABLE IF NOT EXISTS meeting_note_sources (
    id              BIGSERIAL PRIMARY KEY,
    source_type     TEXT        NOT NULL,
    -- text | file | gdoc
    source_id       TEXT        NOT NULL UNIQUE,
    -- SHA256-hash (text/file) tai Google Drive file ID
    source_url      TEXT,
    source_title    TEXT,
    meeting_date    DATE,
    attendees       JSONB       NOT NULL DEFAULT '[]',
    content_preview TEXT,
    -- Ensimmäiset 500 merkkiä
    calendar_meta   JSONB,
    -- Google Calendar -metatiedot, jos saatavilla
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE meeting_note_sources IS
    'Yksi rivi per prosessoitu kokousmuistio. source_id on deterministinen '
    'tunniste (hash tai Google Drive ID) — sama muistio ei tallennu kahdesti.';

-- ── 2. meeting_note_runs — ajojen audit trail ─────────────────────────────────

CREATE TABLE IF NOT EXISTS meeting_note_runs (
    id              BIGSERIAL PRIMARY KEY,
    source_id       BIGINT      REFERENCES meeting_note_sources(id),
    status          TEXT        NOT NULL DEFAULT 'running',
    -- running | success | failed | superseded
    items_found     INT         NOT NULL DEFAULT 0,
    tasks_created   INT         NOT NULL DEFAULT 0,
    tasks_updated   INT         NOT NULL DEFAULT 0,
    tasks_skipped   INT         NOT NULL DEFAULT 0,
    dry_run         BOOLEAN     NOT NULL DEFAULT FALSE,
    extraction_method TEXT,
    -- claude | rule_based
    model_used      TEXT,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at     TIMESTAMPTZ,
    error_message   TEXT,
    run_metadata    JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_note_runs_source
    ON meeting_note_runs (source_id, created_at DESC);

COMMENT ON TABLE meeting_note_runs IS
    'Jokaisen muistionkäsittelyajon audit trail. Tukee force-uusinta-ajoja '
    '(useita rivejä samalle source_id:lle sallittu).';

-- ── 3. meeting_note_extractions — tunnistetut kohdat ─────────────────────────

CREATE TABLE IF NOT EXISTS meeting_note_extractions (
    id                      BIGSERIAL PRIMARY KEY,
    run_id                  BIGINT      REFERENCES meeting_note_runs(id),
    source_id               BIGINT      REFERENCES meeting_note_sources(id),
    item_fingerprint        TEXT        NOT NULL,
    -- SHA256(source.source_id + "::" + source_quote[:200].lower())
    item_type               TEXT        NOT NULL,
    -- action_item | decision | follow_up
    title                   TEXT        NOT NULL,
    description             TEXT,
    owner                   TEXT,
    due_hint                TEXT,
    due_date                DATE,
    source_quote            TEXT,
    -- Alkuperäinen lainaus muistiosta
    confidence              FLOAT       NOT NULL DEFAULT 0.0,
    should_create_task      BOOLEAN     NOT NULL DEFAULT FALSE,
    reason_if_not_created   TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_extractions_run
    ON meeting_note_extractions (run_id);

CREATE INDEX IF NOT EXISTS idx_extractions_fingerprint
    ON meeting_note_extractions (item_fingerprint);

COMMENT ON TABLE meeting_note_extractions IS
    'Kaikki muistiosta tunnistetut kohdat (action itemit, päätökset, '
    'follow-upit). Tallennetaan myös ne, joista ei luoda tehtävää, '
    'audit trailia varten.';

-- ── 4. meeting_note_tasks — ClickUp-tehtävien mappaus ────────────────────────

CREATE TABLE IF NOT EXISTS meeting_note_tasks (
    id               BIGSERIAL PRIMARY KEY,
    extraction_id    BIGINT      REFERENCES meeting_note_extractions(id),
    item_fingerprint TEXT        NOT NULL UNIQUE,
    -- Pääavain duplikaattisuojaukseen
    clickup_task_id  TEXT        NOT NULL,
    clickup_task_url TEXT,
    action           TEXT        NOT NULL,
    -- created | updated | skipped
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE meeting_note_tasks IS
    'Mapping extraction → ClickUp task. item_fingerprint on UNIQUE, '
    'joten sama action item ei saa koskaan luoda kahta tehtävää. '
    'Uusinta-ajo tunnistaa olemassa olevan tehtävän tämän avulla.';
