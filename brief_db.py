"""
brief_db.py — Supabase-operaatiot huomisen briiffiä varten

Käyttää olemassa olevaa db.get_db()-yhteyttä.
"""

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import db

log = logging.getLogger(__name__)


# ── daily_briefs ──────────────────────────────────────────────────────────────

def check_brief_exists(brief_date: date) -> Optional[dict]:
    """Palauttaa olemassa olevan briiffi-rivin tai None."""
    client = db.get_db()
    result = (
        client.table("daily_briefs")
        .select("*")
        .eq("brief_date", brief_date.isoformat())
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def save_brief(
    brief_date:       date,
    brief_text:       str,
    day_load:         str,
    clickup_task_id:  Optional[str],
    clickup_task_url: Optional[str],
    approval_status:  str,
    source_summary:   dict,
) -> None:
    """Tallentaa tai päivittää briiffin Supabaseen."""
    client = db.get_db()
    row = {
        "brief_date":       brief_date.isoformat(),
        "brief_text":       brief_text,
        "day_load":         day_load,
        "clickup_task_id":  clickup_task_id,
        "clickup_task_url": clickup_task_url,
        "approval_status":  approval_status,
        "source_summary":   source_summary,
        "generated_at":     datetime.now(timezone.utc).isoformat(),
    }
    client.table("daily_briefs").upsert(row, on_conflict="brief_date").execute()
    log.debug(f"daily_briefs tallennettu: {brief_date}")


# ── daily_brief_runs ──────────────────────────────────────────────────────────

def check_run_exists(brief_date: date) -> bool:
    """Idempotenssisuoja — onko päivälle jo onnistunut ajo?"""
    client = db.get_db()
    result = (
        client.table("daily_brief_runs")
        .select("id")
        .eq("run_type",   "daily_brief")
        .eq("brief_date", brief_date.isoformat())
        .eq("status",     "success")
        .limit(1)
        .execute()
    )
    return bool(result.data)


def create_run(brief_date: date) -> int:
    """Luo uuden ajokirjauksen. Palauttaa run ID:n."""
    client = db.get_db()
    result = client.table("daily_brief_runs").insert({
        "run_type":   "daily_brief",
        "brief_date": brief_date.isoformat(),
        "status":     "running",
    }).execute()
    return result.data[0]["id"]


def finish_run(
    run_id:       int,
    status:       str,
    error_message: Optional[str] = None,
    run_metadata:  Optional[dict] = None,
) -> None:
    """Merkitsee ajon valmiiksi. Käsittelee force-ajon ylikirjoituksen."""
    client = db.get_db()

    if status == "success":
        run_row = (
            client.table("daily_brief_runs")
            .select("brief_date")
            .eq("id", run_id)
            .single()
            .execute()
        )
        brief_date_val = run_row.data["brief_date"]
        # Merkitään vanhat success-rivit ylikirjoitetuiksi
        client.table("daily_brief_runs").update({"status": "superseded"}).eq(
            "run_type",   "daily_brief"
        ).eq("brief_date", brief_date_val).eq("status", "success").neq("id", run_id).execute()

    update: dict = {
        "status":      status,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    if error_message:
        update["error_message"] = error_message
    if run_metadata:
        update["run_metadata"] = run_metadata

    client.table("daily_brief_runs").update(update).eq("id", run_id).execute()
    log.debug(f"daily_brief_runs päivitetty: id={run_id} status={status}")


# ── Shopify-signaalit ─────────────────────────────────────────────────────────

def get_shopify_signals(brief_date: date):
    """Hakee viimeisten 2 päivän avoimet Shopify-alertit.

    Palauttaa ShopifySignals-olion — vain kriittiset alertit nostetaan briiffiin.
    """
    from brief_logic import ShopifySignals

    client    = db.get_db()
    from_date = (brief_date - timedelta(days=2)).isoformat()

    try:
        result = (
            client.table("shopify_alerts")
            .select("alert_type, description, severity")
            .gte("report_date", from_date)
            .eq("resolved", False)
            .limit(5)
            .execute()
        )
        alerts   = result.data or []
        critical = [a for a in alerts if a.get("severity") == "critical"]

        if critical:
            return ShopifySignals(
                has_open_alerts=True,
                alert_descriptions=[a["description"] for a in critical[:2]],
                report_date=brief_date,
            )
    except Exception as e:
        log.debug(f"Shopify-signaalien haku epäonnistui: {e}")

    return ShopifySignals()
