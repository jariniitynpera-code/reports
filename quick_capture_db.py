"""
quick_capture_db.py — Supabase audit log pikasyötöille

Tallentaa jokaisen pikasyötön tulokset (extraction, ClickUp, kalenteri)
quick_captures-tauluun auditointia ja debuggausta varten.
"""

import json
import logging
from datetime import date
from typing import Optional

log = logging.getLogger(__name__)


def save_capture(
    capture_type:           str,
    raw_text:               str,
    extraction=None,        # CaptureExtraction | None
    clickup_task_id:        Optional[str] = None,
    clickup_task_url:       Optional[str] = None,
    calendar_event_id:      Optional[str] = None,
    calendar_event_url:     Optional[str] = None,
    status:                 str = "success",
    error_message:          Optional[str] = None,
    dry_run:                bool = False,
    assignee_id:            Optional[str] = None,
) -> Optional[int]:
    """Tallentaa pikasyötön tuloksen Supabaseen.

    Palauttaa uuden rivin ID:n tai None jos tallennus epäonnistuu.
    """
    try:
        import config
        from supabase import create_client

        client = create_client(config.SUPABASE_URL, config.SUPABASE_SERVICE_KEY)

        row: dict = {
            "capture_type":   capture_type,
            "raw_text":       raw_text,
            "status":         status,
            "dry_run":        dry_run,
        }

        if error_message:
            row["error_message"] = error_message

        if extraction:
            row.update({
                "extracted_title":         extraction.title,
                "extracted_description":   extraction.description,
                "extracted_category":      extraction.category,
                "extracted_list_id":       extraction.list_id,
                "extracted_assignee_name": extraction.assignee_name,
                "extracted_assignee_id":   assignee_id,
                "extracted_priority":      extraction.priority,
                "extracted_due_date":      extraction.due_date.isoformat()
                                           if extraction.due_date else None,
                "extracted_tags":          json.dumps(extraction.tags),
                "needs_calendar":          extraction.needs_calendar,
                "calendar_duration_min":   extraction.calendar_duration_minutes,
                "extraction_method":       extraction.extraction_method,
                "model_used":              extraction.model_used or None,
            })

        if clickup_task_id:
            row["clickup_task_id"]  = clickup_task_id
            row["clickup_task_url"] = clickup_task_url

        if calendar_event_id:
            row["calendar_event_id"]  = calendar_event_id
            row["calendar_event_url"] = calendar_event_url

        result = client.table("quick_captures").insert(row).execute()
        rows = result.data
        if rows:
            record_id = rows[0].get("id")
            log.info(f"quick_capture tallennettu: id={record_id}")
            return record_id

    except Exception as e:
        log.warning(f"Supabase-tallennus epäonnistui (ei kriittinen): {e}")

    return None
