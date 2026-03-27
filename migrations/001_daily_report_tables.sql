-- ============================================================
-- Shopify Daily Reports — Supabase migraatio
-- Versio: 001
-- Luotu: 2026-03-27
-- ============================================================

-- ── 1. automation_runs — idempotenssi ja audit trail ────────────────────────

CREATE TABLE IF NOT EXISTS automation_runs (
    id              BIGSERIAL PRIMARY KEY,
    run_type        TEXT        NOT NULL DEFAULT 'shopify_daily_report',
    report_date     DATE        NOT NULL,
    status          TEXT        NOT NULL DEFAULT 'running',
    -- running / success / failed / skipped
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at     TIMESTAMPTZ,
    error_message   TEXT,
    orders_fetched  INT,
    run_metadata    JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Yksi onnistunut ajo per päivä (idempotenssi)
CREATE UNIQUE INDEX IF NOT EXISTS uidx_automation_runs_date_success
    ON automation_runs (run_type, report_date)
    WHERE status = 'success';

CREATE INDEX IF NOT EXISTS idx_automation_runs_date
    ON automation_runs (report_date DESC);

-- ── 2. shopify_daily_orders — raakadata (normalisoitu) ────────────────────

CREATE TABLE IF NOT EXISTS shopify_daily_orders (
    id                  BIGSERIAL PRIMARY KEY,
    report_date         DATE        NOT NULL,
    order_id            TEXT        NOT NULL,
    order_number        TEXT,
    shopify_created_at  TIMESTAMPTZ,
    total_price         NUMERIC(12,2),
    subtotal_price      NUMERIC(12,2),
    total_tax           NUMERIC(12,2),
    total_discounts     NUMERIC(12,2),
    financial_status    TEXT,
    -- paid / pending / refunded / partially_refunded / voided / authorized
    fulfillment_status  TEXT,
    -- null / partial / fulfilled / restocked
    is_cancelled        BOOLEAN     NOT NULL DEFAULT FALSE,
    cancelled_at        TIMESTAMPTZ,
    cancel_reason       TEXT,
    customer_id         TEXT,
    customer_email      TEXT,
    customer_orders_count INT,
    -- orders_count > 1 = palaava asiakas
    payment_gateway     TEXT,
    items_count         INT,
    line_items          JSONB,
    -- [{title, sku, quantity, price, product_id}]
    refunds             JSONB,
    -- raw refund objects
    raw_payload         JSONB,
    -- alkuperäinen Shopify-vastaus
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (report_date, order_id)
);

CREATE INDEX IF NOT EXISTS idx_shopify_orders_date
    ON shopify_daily_orders (report_date DESC);

CREATE INDEX IF NOT EXISTS idx_shopify_orders_status
    ON shopify_daily_orders (report_date, financial_status);

-- ── 3. shopify_daily_metrics — aggregoitu päivädata ──────────────────────

CREATE TABLE IF NOT EXISTS shopify_daily_metrics (
    id                  BIGSERIAL PRIMARY KEY,
    report_date         DATE        NOT NULL UNIQUE,

    -- Tilaukset
    total_orders        INT         NOT NULL DEFAULT 0,
    paid_orders         INT         NOT NULL DEFAULT 0,
    cancelled_orders    INT         NOT NULL DEFAULT 0,
    refunded_orders     INT         NOT NULL DEFAULT 0,
    pending_orders      INT         NOT NULL DEFAULT 0,
    fulfilled_orders    INT         NOT NULL DEFAULT 0,

    -- Liikevaihto
    gross_revenue       NUMERIC(12,2) NOT NULL DEFAULT 0,
    net_revenue         NUMERIC(12,2) NOT NULL DEFAULT 0,
    -- net = gross - refunds
    avg_order_value     NUMERIC(12,2) NOT NULL DEFAULT 0,
    total_discounts     NUMERIC(12,2) NOT NULL DEFAULT 0,

    -- Asiakkaat
    new_customers       INT         NOT NULL DEFAULT 0,
    returning_customers INT         NOT NULL DEFAULT 0,

    -- Palautukset
    total_refunds       INT         NOT NULL DEFAULT 0,
    refund_amount       NUMERIC(12,2) NOT NULL DEFAULT 0,
    refund_rate_pct     NUMERIC(6,2),
    -- % tilauksista

    -- Peruutukset
    cancellation_rate_pct NUMERIC(6,2),

    -- Maksut
    payment_issues      INT         NOT NULL DEFAULT 0,
    -- pending + voided

    -- Tuotteet (top 10)
    top_products        JSONB,
    -- [{title, sku, qty, revenue}]

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_daily_metrics_date
    ON shopify_daily_metrics (report_date DESC);

-- ── 4. shopify_daily_reports — generoidut raportit ───────────────────────

CREATE TABLE IF NOT EXISTS shopify_daily_reports (
    id                  BIGSERIAL PRIMARY KEY,
    report_date         DATE        NOT NULL UNIQUE,
    report_text         TEXT        NOT NULL,
    status_level        TEXT        NOT NULL,
    -- green / yellow / red
    clickup_task_id     TEXT,
    clickup_task_url    TEXT,
    published_at        TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── 5. shopify_alerts — poikkeamat ja toimenpiteet ───────────────────────

CREATE TABLE IF NOT EXISTS shopify_alerts (
    id              BIGSERIAL PRIMARY KEY,
    report_date     DATE        NOT NULL,
    alert_type      TEXT        NOT NULL,
    -- high_refunds / low_sales / sales_spike / payment_issues /
    -- high_cancellations / inventory_risk / fraud_signal
    severity        TEXT        NOT NULL,
    -- warning / critical
    description     TEXT,
    metric_value    NUMERIC(12,4),
    threshold_value NUMERIC(12,4),
    clickup_task_id TEXT,
    -- Follow-up task ClickUpissa
    resolved        BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_alerts_date
    ON shopify_alerts (report_date DESC);

CREATE UNIQUE INDEX IF NOT EXISTS uidx_alerts_type_date
    ON shopify_alerts (alert_type, report_date)
    WHERE resolved = FALSE;
-- Estetään duplikaatti-alertit samalle päivälle

-- ── 6. clickup_sync_log — ClickUp-toimintojen audit trail ────────────────

CREATE TABLE IF NOT EXISTS clickup_sync_log (
    id              BIGSERIAL PRIMARY KEY,
    action          TEXT        NOT NULL,
    -- create_report / update_report / create_task / update_task / add_comment
    report_date     DATE,
    clickup_task_id TEXT,
    clickup_list_id TEXT,
    status          TEXT        NOT NULL,
    -- success / failed
    request_body    JSONB,
    response_body   JSONB,
    error_message   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_clickup_log_date
    ON clickup_sync_log (report_date DESC, created_at DESC);

-- ── Triggerit: updated_at automaattipäivitys ─────────────────────────────

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger WHERE tgname = 'trg_metrics_updated_at'
    ) THEN
        CREATE TRIGGER trg_metrics_updated_at
        BEFORE UPDATE ON shopify_daily_metrics
        FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger WHERE tgname = 'trg_reports_updated_at'
    ) THEN
        CREATE TRIGGER trg_reports_updated_at
        BEFORE UPDATE ON shopify_daily_reports
        FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;
END $$;

-- ── Kommentit ──────────────────────────────────────────────────────────────

COMMENT ON TABLE automation_runs IS
    'Jokaisen automaaatioajon audit trail ja idempotenssisuoja. '
    'Yksi success-rivi per run_type + report_date.';

COMMENT ON TABLE shopify_daily_orders IS
    'Normalisoitu tilausdata Shopifysta. Raakadata säilytetään raw_payload-kentässä.';

COMMENT ON TABLE shopify_daily_metrics IS
    'Aggregoitu päiväkohtainen myyntidata. Käytetään vertailuihin ja trendeihin.';

COMMENT ON TABLE shopify_daily_reports IS
    'Generoidut tekstiraportit ja niiden ClickUp-viittaukset.';

COMMENT ON TABLE shopify_alerts IS
    'Tunnistetut poikkeamat ja niihin liittyvät toimenpidetehtävät.';

COMMENT ON TABLE clickup_sync_log IS
    'Kaikki ClickUp API -kutsut. Käytetään virheiden selvitykseen.';
