"""
brief_tasks.py — ClickUp-tehtävien haku ja priorisointi briiffiä varten

Hakee avoimet tehtävät määritetyistä listoista, pistyttää ne
relevanssin mukaan ja palauttaa järjestetyn listan.

Pisteytyslogiikka:
  - Prioriteetti (urgent=40p, high=30p, normal=20p, low=5p)
  - Eräpäivä (tänään=35p, huomenna=20p, +3pv=12p, +7pv=6p, myöhässä=25p)
  - Torstai-efekti: perjantaihin erääntyviä nostetaan torstaita
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Optional

log = logging.getLogger(__name__)


# ── Datatyypit ────────────────────────────────────────────────────────────────

@dataclass
class BriefTask:
    """ClickUp-tehtävä briiffiä varten."""
    id:        str
    name:      str
    priority:  int            # 1=urgent, 2=high, 3=normal, 4=low, 0=ei asetettu
    due_date:  Optional[date]
    list_name: str
    status:    str
    url:       str
    score:     float = 0.0    # Laskennallinen relevanssipiste


# ── Pääfunktio ────────────────────────────────────────────────────────────────

def get_prioritized_tasks(
    clickup,
    list_ids: list[str],
    tomorrow: date,
) -> list[BriefTask]:
    """Hakee ja pistyttää tehtävät annetuista listoista.

    Palauttaa tehtävät pisteiden mukaan järjestettynä (korkein ensin).
    """
    all_tasks: list[BriefTask] = []

    for list_id in list_ids:
        tasks = _fetch_tasks_from_list(clickup, list_id)
        all_tasks.extend(tasks)

    # Deduplikointi id:n perusteella
    seen: set[str] = set()
    unique: list[BriefTask] = []
    for t in all_tasks:
        if t.id not in seen:
            seen.add(t.id)
            unique.append(t)

    # Pistytys ja järjestys
    for task in unique:
        task.score = _score_task(task, tomorrow)

    unique.sort(key=lambda t: t.score, reverse=True)

    log.info(f"ClickUp-tehtäviä haettu: {len(unique)} kpl {len(list_ids)} listalta")
    return unique


# ── API-haku ──────────────────────────────────────────────────────────────────

def _fetch_tasks_from_list(clickup, list_id: str) -> list[BriefTask]:
    """Hakee avoimet tehtävät yhdeltä listalta."""
    try:
        data = clickup._request(
            "GET",
            f"list/{list_id}/task",
            params={
                "page":           0,
                "include_closed": "false",
                "order_by":       "priority",
                "reverse":        "true",
            },
        )
        raw_tasks = data.get("tasks", [])
        return [_normalize_task(t) for t in raw_tasks]
    except Exception as e:
        log.warning(f"Tehtävien haku listalta {list_id} epäonnistui: {e}")
        return []


def _normalize_task(raw: dict) -> BriefTask:
    """Normalisoi ClickUp-tehtävän BriefTask-olioksi."""
    priority_raw = raw.get("priority") or {}
    priority     = int(priority_raw.get("priority", 0)) if priority_raw else 0

    due_ts   = raw.get("due_date")
    due_date = None
    if due_ts:
        try:
            due_date = datetime.fromtimestamp(
                int(due_ts) / 1000, tz=timezone.utc
            ).date()
        except (ValueError, TypeError):
            pass

    list_info   = raw.get("list", {})
    status_info = raw.get("status", {})

    return BriefTask(
        id=raw.get("id", ""),
        name=raw.get("name", ""),
        priority=priority,
        due_date=due_date,
        list_name=list_info.get("name", ""),
        status=status_info.get("status", ""),
        url=raw.get("url", ""),
    )


# ── Pisteytys ─────────────────────────────────────────────────────────────────

def _score_task(task: BriefTask, tomorrow: date) -> float:
    """Laskee tehtävän relevanssin briiffiä varten.

    Korkeampi piste = tärkeämpi briiffiin.
    """
    score = 0.0

    # Prioriteetti
    priority_scores = {1: 40.0, 2: 30.0, 3: 20.0, 4: 5.0, 0: 0.0}
    score += priority_scores.get(task.priority, 0.0)

    # Eräpäivä
    if task.due_date:
        days_until = (task.due_date - tomorrow).days
        if days_until < 0:
            score += 25.0   # Myöhässä
        elif days_until == 0:
            score += 35.0   # Erääntyy huomenna
        elif days_until == 1:
            score += 20.0   # Erääntyy ylihuomenna
        elif days_until <= 3:
            score += 12.0
        elif days_until <= 7:
            score += 6.0

    # Torstai-efekti: perjantaihin erääntyviä nostetaan torstaita
    if tomorrow.weekday() == 3:  # Torstai
        if task.due_date and task.due_date <= tomorrow + timedelta(days=2):
            score += 8.0

    # Perjantailisä: kaikki tehtävät hieman tärkeämpiä
    if tomorrow.weekday() == 4:
        score += 3.0

    return round(score, 1)
