"""
db.py — Supabase-tietokantaoperaatiot

Kaikki tietokantakyselyt ja tallennukset ovat tässä tiedostossa.
Käyttää olemassa olevia SUPABASE_URL ja SUPABASE_SERVICE_KEY -tunnuksia.
"""

import logging
from datetime import date, datetime, timezone
from typing import Optional

from supabase import create_client, Client

import config

log = logging.getLogger(__name__)

_client: Optional[Client] = None


def get_db() -> Client:
    """Palauttaa Supabase-asiakkaan. Singleton-pattern."""
    global _client
    if _client is None:
        _client = create_client(config.SUPABASE_URL, config.SUPABASE_SERVICE_KEY)
    return _client


# ── automation_runs ───────────────────────────────────────────────────────────

def create_run(report_date: date) -> int:
    """Luo uuden automation_runs-rivin. Palauttaa run ID:n.

    Nostaa poikkeuksen jos samalle päivälle on jo success-rivi
    (idempotenssisuoja — kutsujalla pitää tarkistaa ensin check_run_exists).
    """
    db = get_db()
    row = {
        "run_type":    "shopify_daily_report",
        "report_date": report_date.isoformat(),
        "status":      "running",
    }
    result = db.table("automation_runs").insert(row).execute()
    run_id = result.data[0]["id"]
    log.debug(f"automation_runs luotu: id={run_id}")
    return run_id


def finish_run(run_id: int, status: str, **kwargs) -> None:
    """Merkitsee ajon valmiiksi (success / failed / skipped).

    Jos status='success' ja samalle päivälle on jo success-rivi (force-ajo),
    merkitään vanhat success-rivit 'superseded' ennen päivitystä.
    """
    db = get_db()

    if status == "success":
        # Haetaan tämän ajon report_date
        run_row = db.table("automation_runs").select("report_date").eq("id", run_id).single().execute()
        report_date_val = run_row.data["report_date"]
        # Merkitään vanhat success-rivit ylikirjoitetuiksi (muut kuin tämä ajo)
        db.table("automation_runs").update({"status": "superseded"}).eq(
            "run_type", "shopify_daily_report"
        ).eq("report_date", report_date_val).eq("status", "success").neq("id", run_id).execute()

    update = {
        "status":      status,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    update.update(kwargs)
    db.table("automation_runs").update(update).eq("id", run_id).execute()
    log.debug(f"automation_runs päivitetty: id={run_id} status={status}")


def check_run_exists(report_date: date) -> bool:
    """Palauttaa True jos päivälle on jo onnistunut ajo (idempotenssisuoja)."""
    db = get_db()
    result = (
        db.table("automation_runs")
        .select("id")
        .eq("run_type",    "shopify_daily_report")
        .eq("report_date", report_date.isoformat())
        .eq("status",      "success")
        .limit(1)
        .execute()
    )
    return bool(result.data)


# ── shopify_daily_orders ──────────────────────────────────────────────────────

def upsert_orders(report_date: date, orders: list[dict]) -> None:
    """Tallentaa/päivittää normalisoidut tilaukset kantaan.

    Käyttää upsert-semantiikkaa (report_date + order_id on UNIQUE).
    """
    if not orders:
        return

    db = get_db()
    rows = [_normalize_order(report_date, o) for o in orders]

    # Supabase upsert 250 kerrallaan
    batch_size = 250
    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        db.table("shopify_daily_orders").upsert(
            batch,
            on_conflict="report_date,order_id",
        ).execute()

    log.info(f"shopify_daily_orders: tallennettu {len(rows)} tilausta ({report_date})")


def _normalize_order(report_date: date, order: dict) -> dict:
    """Normalisoi Shopify-tilausobjektin tietokantariviksi."""
    customer = order.get("customer") or {}
    line_items = order.get("line_items", [])
    refunds    = order.get("refunds", [])

    # Laske items_count
    items_count = sum(int(li.get("quantity", 0)) for li in line_items)

    # Normalisoi line_items — säilytetään vain tarvittavat kentät
    normalized_items = [
        {
            "product_id": str(li.get("product_id", "")),
            "title":      li.get("title", ""),
            "sku":        li.get("sku", ""),
            "quantity":   int(li.get("quantity", 0)),
            "price":      float(li.get("price", 0)),
        }
        for li in line_items
    ]

    return {
        "report_date":           report_date.isoformat(),
        "order_id":              str(order["id"]),
        "order_number":          order.get("name", ""),
        "shopify_created_at":    order.get("created_at"),
        "total_price":           float(order.get("total_price") or 0),
        "subtotal_price":        float(order.get("subtotal_price") or 0),
        "total_tax":             float(order.get("total_tax") or 0),
        "total_discounts":       float(order.get("total_discounts") or 0),
        "financial_status":      order.get("financial_status", ""),
        "fulfillment_status":    order.get("fulfillment_status") or "unfulfilled",
        "is_cancelled":          bool(order.get("cancelled_at")),
        "cancelled_at":          order.get("cancelled_at"),
        "cancel_reason":         order.get("cancel_reason"),
        "customer_id":           str(customer.get("id", "")) if customer.get("id") else None,
        "customer_email":        customer.get("email"),
        "customer_orders_count": int(customer.get("orders_count", 0)),
        "payment_gateway":       order.get("payment_gateway"),
        "items_count":           items_count,
        "line_items":            normalized_items,
        "refunds":               refunds,
        "raw_payload":           order,
    }


# ── shopify_daily_metrics ─────────────────────────────────────────────────────

def upsert_metrics(metrics: dict) -> None:
    """Tallentaa/päivittää päivämetriikan kantaan."""
    db = get_db()
    db.table("shopify_daily_metrics").upsert(
        metrics,
        on_conflict="report_date",
    ).execute()
    log.info(f"shopify_daily_metrics tallennettu: {metrics['report_date']}")


def get_historical_metrics(
    report_date: date,
    days: int = 7,
) -> list[dict]:
    """Hakee viimeisimmät N päivän metriikat historiallista vertailua varten.

    Palauttaa listan diktejä vanhimmasta uusimpaan (ei sisällä report_date:ta).
    """
    db = get_db()
    result = (
        db.table("shopify_daily_metrics")
        .select("*")
        .lt("report_date", report_date.isoformat())
        .order("report_date", desc=True)
        .limit(days)
        .execute()
    )
    return list(reversed(result.data))  # Vanhimmasta uusimpaan


def get_yesterday_metrics(report_date: date) -> Optional[dict]:
    """Hakee edellisen päivän metriikat."""
    from datetime import timedelta
    yesterday = report_date - timedelta(days=1)
    db = get_db()
    result = (
        db.table("shopify_daily_metrics")
        .select("*")
        .eq("report_date", yesterday.isoformat())
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


# ── shopify_daily_reports ─────────────────────────────────────────────────────

def upsert_report(
    report_date: date,
    report_text: str,
    status_level: str,
    clickup_task_id: Optional[str] = None,
    clickup_task_url: Optional[str] = None,
) -> None:
    """Tallentaa/päivittää generoidun raportin kantaan."""
    db = get_db()
    row = {
        "report_date":    report_date.isoformat(),
        "report_text":    report_text,
        "status_level":   status_level,
        "clickup_task_id":  clickup_task_id,
        "clickup_task_url": clickup_task_url,
        "published_at":   datetime.now(timezone.utc).isoformat() if clickup_task_id else None,
    }
    db.table("shopify_daily_reports").upsert(row, on_conflict="report_date").execute()
    log.info(f"shopify_daily_reports tallennettu: {report_date}")


def get_report(report_date: date) -> Optional[dict]:
    """Hakee päivän raportin jos se on jo olemassa."""
    db = get_db()
    result = (
        db.table("shopify_daily_reports")
        .select("*")
        .eq("report_date", report_date.isoformat())
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


# ── shopify_alerts ────────────────────────────────────────────────────────────

def upsert_alert(
    report_date: date,
    alert_type: str,
    severity: str,
    description: str,
    metric_value: Optional[float] = None,
    threshold_value: Optional[float] = None,
    clickup_task_id: Optional[str] = None,
) -> Optional[int]:
    """Tallentaa alertin. Duplikaatit estetään UNIQUE-indeksillä.

    Palauttaa alert ID:n tai None jos duplikaatti.
    """
    db = get_db()
    row = {
        "report_date":     report_date.isoformat(),
        "alert_type":      alert_type,
        "severity":        severity,
        "description":     description,
        "metric_value":    metric_value,
        "threshold_value": threshold_value,
        "clickup_task_id": clickup_task_id,
    }
    try:
        result = db.table("shopify_alerts").insert(row).execute()
        alert_id = result.data[0]["id"] if result.data else None
        log.debug(f"Alert tallennettu: {alert_type} / {severity} (id={alert_id})")
        return alert_id
    except Exception as e:
        # Duplikaatti — sama alert_type + report_date on jo olemassa
        if "duplicate" in str(e).lower() or "unique" in str(e).lower():
            log.debug(f"Alert duplikaatti ohitettu: {alert_type} {report_date}")
            return None
        raise


def get_open_alert(report_date: date, alert_type: str) -> Optional[dict]:
    """Tarkistaa onko kyseiselle päivälle jo avoin alert tietystä tyypistä."""
    db = get_db()
    result = (
        db.table("shopify_alerts")
        .select("*")
        .eq("report_date", report_date.isoformat())
        .eq("alert_type",  alert_type)
        .eq("resolved",    False)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


# ── clickup_sync_log ──────────────────────────────────────────────────────────

def log_clickup_action(
    action: str,
    status: str,
    report_date: Optional[date] = None,
    clickup_task_id: Optional[str] = None,
    clickup_list_id: Optional[str] = None,
    request_body: Optional[dict] = None,
    response_body: Optional[dict] = None,
    error_message: Optional[str] = None,
) -> None:
    """Kirjaa ClickUp API -kutsun audit logiin."""
    db = get_db()
    row = {
        "action":          action,
        "status":          status,
        "report_date":     report_date.isoformat() if report_date else None,
        "clickup_task_id": clickup_task_id,
        "clickup_list_id": clickup_list_id,
        "request_body":    request_body,
        "response_body":   response_body,
        "error_message":   error_message,
    }
    try:
        db.table("clickup_sync_log").insert(row).execute()
    except Exception as e:
        # Loggausvirhe ei saa kaataa pääprosessia
        log.warning(f"clickup_sync_log tallennusvirhe: {e}")
