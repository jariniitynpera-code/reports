"""
meeting_notes_tasks.py — ClickUp-tehtävien luonti kokousmuistioista

Logiikka:
  1. Tarkistetaan onko fingerprint jo olemassa Supabasessa
     → kyllä: päivitetään ClickUp-tehtävä tai jätetään rauhaan
     → ei:    luodaan uusi tehtävä
  2. Jokainen toiminto kirjataan audit trailiin

Duplikaattistrategia:
  - Avain: item_fingerprint (SHA256 source_id + source_quote)
  - Tallennettu: meeting_note_tasks -taulussa
  - Toiminta: create / update / skip — ei koskaan duplikaatti
"""

import logging
from dataclasses import dataclass
from datetime import date
from typing import Optional

from clickup_client import ClickUpClient
from meeting_notes_extractor import ExtractedItem, ExtractionResult

import config
import meeting_notes_db

log = logging.getLogger(__name__)

# Tagit item_typen mukaan
_TYPE_TAGS = {
    "action_item": "action-item",
    "follow_up":   "follow-up",
    "decision":    "decision-derived",
}

_CONFIDENCE_TAGS = {
    lambda c: c >= 0.85: "confidence-high",
    lambda c: c >= 0.65: "confidence-medium",
}


# ── Datatyypit ────────────────────────────────────────────────────────────────

@dataclass
class TaskResult:
    """Yhden action item -käsittelyn tulos."""
    item:        ExtractedItem
    action:      str            # "created" | "updated" | "skipped" | "error" | "dry_run"
    task_id:     Optional[str]
    task_url:    Optional[str]
    reason:      str
    fingerprint: str


# ── Pääfunktio ────────────────────────────────────────────────────────────────

def publish_extraction(
    extraction:  ExtractionResult,
    clickup:     ClickUpClient,
    list_id:     str,
    dry_run:     bool = False,
) -> list[TaskResult]:
    """Luo tai päivittää ClickUp-tehtävät extraction-tuloksesta.

    Käsittelee vain kohtia, joilla should_create_task == True.
    Muut tallennetaan audit trailiin 'skipped'-statuksella.

    Returns:
        TaskResult-lista kaikista kohdetuista action itemistä.
    """
    results: list[TaskResult] = []

    for item in extraction.items:
        result = _process_item(item, extraction, clickup, list_id, dry_run)
        results.append(result)

    created = sum(1 for r in results if r.action == "created")
    updated = sum(1 for r in results if r.action == "updated")
    skipped = sum(1 for r in results if r.action in ("skipped", "dry_run"))
    log.info(f"ClickUp: {created} luotu, {updated} päivitetty, {skipped} ohitettu")

    return results


# ── Yhden kohdan käsittely ────────────────────────────────────────────────────

def _process_item(
    item:       ExtractedItem,
    extraction: ExtractionResult,
    clickup:    ClickUpClient,
    list_id:    str,
    dry_run:    bool,
) -> TaskResult:
    """Käsittelee yhden tunnistetun kohdan."""

    # Kohdat joita ei pidä luoda tehtäviksi
    if not item.should_create_task:
        return TaskResult(
            item=item,
            action="skipped",
            task_id=None,
            task_url=None,
            reason=item.reason_if_not_created or "should_create_task=False",
            fingerprint=item.fingerprint,
        )

    if dry_run:
        return TaskResult(
            item=item,
            action="dry_run",
            task_id=None,
            task_url=None,
            reason="Dry-run — ei luotu",
            fingerprint=item.fingerprint,
        )

    # Tarkista duplikaatti
    try:
        existing_task = meeting_notes_db.get_task_by_fingerprint(item.fingerprint)
    except Exception as e:
        log.warning(f"Fingerprint-tarkistus epäonnistui: {e}")
        existing_task = None

    try:
        if existing_task:
            return _update_existing_task(item, existing_task, clickup, extraction)
        else:
            return _create_new_task(item, extraction, clickup, list_id)
    except Exception as e:
        log.error(f"Tehtävän käsittely epäonnistui ({item.title[:40]}): {e}")
        return TaskResult(
            item=item,
            action="error",
            task_id=None,
            task_url=None,
            reason=str(e),
            fingerprint=item.fingerprint,
        )


def _create_new_task(
    item:       ExtractedItem,
    extraction: ExtractionResult,
    clickup:    ClickUpClient,
    list_id:    str,
) -> TaskResult:
    """Luo uuden ClickUp-tehtävän."""
    note     = extraction.meeting_note
    payload  = _build_task_payload(item, note)

    result = clickup._request("POST", f"list/{list_id}/task", json=payload)
    task_id  = result.get("id", "")
    task_url = result.get("url", "")

    log.info(f"Tehtävä luotu: '{item.title[:50]}' → {task_id}")
    return TaskResult(
        item=item,
        action="created",
        task_id=task_id,
        task_url=task_url,
        reason="Uusi tehtävä",
        fingerprint=item.fingerprint,
    )


def _update_existing_task(
    item:          ExtractedItem,
    existing_task: dict,
    clickup:       ClickUpClient,
    extraction:    ExtractionResult,
) -> TaskResult:
    """Päivittää olemassa olevan ClickUp-tehtävän.

    Päivitetään kuvaus jos sisältö on muuttunut.
    Lisätään kommentti uusinta-ajosta.
    """
    task_id  = existing_task["clickup_task_id"]
    task_url = existing_task.get("clickup_task_url", "")

    note = extraction.meeting_note
    new_desc = format_task_description(item, note)

    try:
        clickup._request("PUT", f"task/{task_id}", json={"description": new_desc})
        log.info(f"Tehtävä päivitetty: '{item.title[:50]}' → {task_id}")
        return TaskResult(
            item=item,
            action="updated",
            task_id=task_id,
            task_url=task_url,
            reason="Olemassa oleva tehtävä päivitetty",
            fingerprint=item.fingerprint,
        )
    except Exception as e:
        log.warning(f"Tehtävän päivitys epäonnistui ({task_id}): {e}")
        return TaskResult(
            item=item,
            action="error",
            task_id=task_id,
            task_url=task_url,
            reason=f"Päivitys epäonnistui: {e}",
            fingerprint=item.fingerprint,
        )


# ── Tehtävän rakentaminen ─────────────────────────────────────────────────────

def _build_task_payload(item: ExtractedItem, note) -> dict:
    """Rakentaa ClickUp API -payloadin yhdelle tehtävälle."""
    tags = _compute_task_tags(item)
    payload: dict = {
        "name":        item.title,
        "description": format_task_description(item, note),
        "priority":    _item_to_priority(item),
        "tags":        tags,
    }
    if item.due_date_normalized:
        # ClickUp odottaa millisekunteja
        import calendar as _cal
        from datetime import datetime
        dt = datetime(
            item.due_date_normalized.year,
            item.due_date_normalized.month,
            item.due_date_normalized.day,
            17, 0, 0  # klo 17:00 oletuksena
        )
        payload["due_date"] = int(_cal.timegm(dt.timetuple()) * 1000)
    return payload


def format_task_description(item: ExtractedItem, note) -> str:
    """Muodostaa selkeän tehtäväkuvauksen ClickUpiin.

    Rakenne:
      Lähde ja konteksti
      Alkuperäinen lainaus
      Lisätiedot (vastuuhenkilö, deadline, confidence)
    """
    lines = []

    # Otsikkorivi
    mtg_date = note.meeting_date.strftime("%-d.%-m.%Y") if note.meeting_date else "—"
    title    = note.source_title or "Kokousmuistio"
    lines.append(f"**Lähde:** {title} ({mtg_date})")

    if note.source_url:
        lines.append(f"**Muistio:** {note.source_url}")

    lines.append("")

    # Kuvaus
    if item.description:
        lines.append(item.description)
        lines.append("")

    # Alkuperäinen lainaus
    if item.source_quote:
        lines.append("**Alkuperäinen muistiossa:**")
        lines.append(f"> {item.source_quote}")
        lines.append("")

    # Metatiedot
    meta: list[str] = []
    if item.owner:
        meta.append(f"**Vastuuhenkilö:** {item.owner}")
    if item.due_hint:
        deadline_str = (
            f" ({item.due_date_normalized.strftime('%-d.%-m.%Y')})"
            if item.due_date_normalized else ""
        )
        meta.append(f"**Deadline:** {item.due_hint}{deadline_str}")
    if meta:
        lines.extend(meta)
        lines.append("")

    # Footer
    conf_label = (
        "korkea" if item.confidence >= 0.85
        else "kohtalainen" if item.confidence >= 0.65
        else "matala"
    )
    lines.append(
        f"_Generoitu automaattisesti kokousmuistiosta. "
        f"Tyyppi: {item.item_type}. Varmuus: {conf_label} ({item.confidence:.0%})._"
    )

    return "\n".join(lines)


def _compute_task_tags(item: ExtractedItem) -> list[str]:
    """Laskee ClickUp-tagit item_typen ja confidencen mukaan."""
    tags = ["meeting-notes"]

    type_tag = _TYPE_TAGS.get(item.item_type)
    if type_tag:
        tags.append(type_tag)

    for condition, tag in _CONFIDENCE_TAGS.items():
        if condition(item.confidence):
            tags.append(tag)
            break
    else:
        tags.append("confidence-low")

    return tags


def _item_to_priority(item: ExtractedItem) -> int:
    """Muuntaa confidence + item_type ClickUp-prioriteetiksi."""
    if item.item_type == "action_item" and item.confidence >= 0.85:
        return 2  # high
    if item.item_type == "follow_up":
        return 3  # normal
    return 3      # normal
