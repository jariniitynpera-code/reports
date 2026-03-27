"""
meeting_notes_db.py — Supabase-operaatiot kokousmuistiomoduulille

Käyttää olemassa olevaa db.get_db()-yhteyttä.
Taulut: meeting_note_sources, meeting_note_runs,
        meeting_note_extractions, meeting_note_tasks
"""

import logging
from datetime import datetime, timezone
from typing import Optional

import db

log = logging.getLogger(__name__)


# ── meeting_note_sources ──────────────────────────────────────────────────────

def get_source_by_id(source_id: str) -> Optional[dict]:
    """Palauttaa olemassa olevan lähteen source_id:n perusteella tai None."""
    client = db.get_db()
    result = (
        client.table("meeting_note_sources")
        .select("*")
        .eq("source_id", source_id)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def create_source(
    source_type:     str,
    source_id:       str,
    source_url:      Optional[str],
    source_title:    Optional[str],
    meeting_date,
    attendees:       list,
    content_preview: str,
    calendar_meta:   Optional[dict] = None,
) -> int:
    """Luo tai päivittää lähteen. Palauttaa DB-rivin id:n."""
    client = db.get_db()
    row = {
        "source_type":     source_type,
        "source_id":       source_id,
        "source_url":      source_url,
        "source_title":    source_title,
        "meeting_date":    meeting_date.isoformat() if meeting_date else None,
        "attendees":       attendees,
        "content_preview": content_preview[:500],
        "calendar_meta":   calendar_meta,
    }
    result = (
        client.table("meeting_note_sources")
        .upsert(row, on_conflict="source_id")
        .execute()
    )
    return result.data[0]["id"]


# ── meeting_note_runs ─────────────────────────────────────────────────────────

def create_run(
    source_db_id:       int,
    dry_run:            bool = False,
    extraction_method:  str  = "",
) -> int:
    """Luo uuden ajokirjauksen. Palauttaa run ID:n."""
    client = db.get_db()
    result = client.table("meeting_note_runs").insert({
        "source_id":          source_db_id,
        "status":             "running",
        "dry_run":            dry_run,
        "extraction_method":  extraction_method,
    }).execute()
    return result.data[0]["id"]


def finish_run(
    run_id:       int,
    status:       str,
    items_found:  int = 0,
    tasks_created: int = 0,
    tasks_updated: int = 0,
    tasks_skipped: int = 0,
    model_used:   Optional[str] = None,
    error_message: Optional[str] = None,
    run_metadata:  Optional[dict] = None,
) -> None:
    """Merkitsee ajon valmiiksi."""
    client = db.get_db()
    update: dict = {
        "status":        status,
        "finished_at":   datetime.now(timezone.utc).isoformat(),
        "items_found":   items_found,
        "tasks_created": tasks_created,
        "tasks_updated": tasks_updated,
        "tasks_skipped": tasks_skipped,
    }
    if model_used:
        update["model_used"] = model_used
    if error_message:
        update["error_message"] = error_message
    if run_metadata:
        update["run_metadata"] = run_metadata

    client.table("meeting_note_runs").update(update).eq("id", run_id).execute()
    log.debug(f"meeting_note_runs päivitetty: id={run_id} status={status}")


# ── meeting_note_extractions ──────────────────────────────────────────────────

def save_extraction(run_id: int, source_db_id: int, item) -> int:
    """Tallentaa yhden tunnistetun kohdan. Palauttaa extraction DB-id:n."""
    client = db.get_db()
    row = {
        "run_id":               run_id,
        "source_id":            source_db_id,
        "item_fingerprint":     item.fingerprint,
        "item_type":            item.item_type,
        "title":                item.title,
        "description":          item.description or None,
        "owner":                item.owner,
        "due_hint":             item.due_hint,
        "due_date":             item.due_date_normalized.isoformat() if item.due_date_normalized else None,
        "source_quote":         item.source_quote or None,
        "confidence":           item.confidence,
        "should_create_task":   item.should_create_task,
        "reason_if_not_created": item.reason_if_not_created,
    }
    result = client.table("meeting_note_extractions").insert(row).execute()
    return result.data[0]["id"]


# ── meeting_note_tasks — duplikaattisuojaus ───────────────────────────────────

def get_task_by_fingerprint(fingerprint: str) -> Optional[dict]:
    """Palauttaa olemassa olevan tehtävä-mappauksen tai None.

    Käytetään duplikaattisuojaukseen ennen uuden tehtävän luomista.
    """
    client = db.get_db()
    result = (
        client.table("meeting_note_tasks")
        .select("*")
        .eq("item_fingerprint", fingerprint)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def save_task_mapping(
    extraction_id:   int,
    fingerprint:     str,
    clickup_task_id: str,
    clickup_task_url: Optional[str],
    action:          str,
) -> None:
    """Tallentaa extraction → ClickUp task -mappauksen.

    Käyttää upsert-logiikkaa: päivittää jos fingerprint on jo olemassa.
    """
    client = db.get_db()
    row = {
        "extraction_id":   extraction_id,
        "item_fingerprint": fingerprint,
        "clickup_task_id": clickup_task_id,
        "clickup_task_url": clickup_task_url,
        "action":          action,
    }
    try:
        client.table("meeting_note_tasks").upsert(
            row, on_conflict="item_fingerprint"
        ).execute()
        log.debug(f"meeting_note_tasks tallennettu: {fingerprint[:8]}… → {clickup_task_id}")
    except Exception as e:
        # Duplikaattivirhe on OK — tarkoittaa tehtävä on jo tallennettu
        log.debug(f"Task-mapping upsert: {e}")
