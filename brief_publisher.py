"""
brief_publisher.py — Briiffin julkaisu ClickUpiin

Idempotenssilogiikka:
  - status=suggested  → päivitetään (data saattoi muuttua)
  - status=approved   → EI ylikirjoiteta
  - status=edited     → EI ylikirjoiteta
  - status=rejected   → päivitetään (uusi ehdotus)
  - ei olemassa       → luodaan uusi

Hyväksyntästatusta hallitaan tageilla:
  brief-suggested / brief-approved / brief-edited / brief-rejected
"""

import logging
from datetime import date
from typing import Tuple

from clickup_client import ClickUpClient
from brief_logic import BriefResult, DayLoad

log = logging.getLogger(__name__)

APPROVAL_TAGS = {
    "suggested": "brief-suggested",
    "approved":  "brief-approved",
    "edited":    "brief-edited",
    "rejected":  "brief-rejected",
}

DAY_LOAD_TAGS = {
    DayLoad.LIGHT:  "brief-light",
    DayLoad.NORMAL: "brief-normal",
    DayLoad.TIGHT:  "brief-tight",
    DayLoad.MOVING: "brief-moving",
}


def get_brief_task_name(brief_date: date) -> str:
    return f"Huomisen briiffi {brief_date.isoformat()}"


def publish_brief(
    brief:    BriefResult,
    clickup:  ClickUpClient,
    list_id:  str,
) -> Tuple[str, str, str]:
    """Julkaisee briiffin ClickUpiin.

    Returns:
        (task_id, task_url, action)
        action: "created" / "updated" / "skipped"
    """
    task_name = get_brief_task_name(brief.brief_date)
    existing  = clickup.find_task_by_name(list_id, task_name)

    if existing:
        task_id  = existing["id"]
        task_url = existing.get("url", "")
        approval = _get_approval_status(existing)

        if approval in ("approved", "edited"):
            log.info(
                f"Briiffi {brief.brief_date} on jo '{approval}' — "
                "ei ylikirjoiteta käyttäjän muutoksia"
            )
            return task_id, task_url, "skipped"

        _update_brief_task(clickup, task_id, brief)
        log.info(f"Briiffi päivitetty: {task_id}")
        return task_id, task_url, "updated"

    task_id, task_url = _create_brief_task(clickup, list_id, task_name, brief)
    log.info(f"Briiffi luotu: {task_id} ({task_name})")
    return task_id, task_url, "created"


def _create_brief_task(
    clickup:   ClickUpClient,
    list_id:   str,
    task_name: str,
    brief:     BriefResult,
) -> Tuple[str, str]:
    tags = [
        "daily-brief",
        APPROVAL_TAGS["suggested"],
        DAY_LOAD_TAGS.get(brief.day_load, "brief-normal"),
    ]
    payload = {
        "name":        task_name,
        "description": brief.brief_text,
        "priority":    3,
        "tags":        tags,
    }
    result = clickup._request("POST", f"list/{list_id}/task", json=payload)
    return result.get("id", ""), result.get("url", "")


def _update_brief_task(
    clickup: ClickUpClient,
    task_id: str,
    brief:   BriefResult,
) -> None:
    clickup._request("PUT", f"task/{task_id}", json={"description": brief.brief_text})

    new_tags = [
        "daily-brief",
        APPROVAL_TAGS["suggested"],
        DAY_LOAD_TAGS.get(brief.day_load, "brief-normal"),
    ]

    # Poista vanhat brief-tagit
    try:
        task_data    = clickup._request("GET", f"task/{task_id}")
        current_tags = [t.get("name", "") for t in task_data.get("tags", [])]
        removable    = set(APPROVAL_TAGS.values()) | set(DAY_LOAD_TAGS.values())
        for old_tag in removable:
            if old_tag in current_tags:
                try:
                    clickup._request("DELETE", f"task/{task_id}/tag/{old_tag}")
                except Exception:
                    pass
    except Exception as e:
        log.debug(f"Vanhojen tagien poisto epäonnistui: {e}")

    for tag in new_tags:
        try:
            clickup._request("POST", f"task/{task_id}/tag/{tag}")
        except Exception as e:
            log.debug(f"Tagin lisäys epäonnistui ({tag}): {e}")


def _get_approval_status(task: dict) -> str:
    """Lukee hyväksyntästatuksen tehtävän tageista."""
    tags = [t.get("name", "") for t in task.get("tags", [])]
    for status, tag in APPROVAL_TAGS.items():
        if tag in tags:
            return status
    return "suggested"
