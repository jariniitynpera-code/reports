"""
brief_logic.py — Huomisen briiffin valinta- ja generointilogiikka

Luokittelee päivän kuorman, valitsee relevantit tehtävät,
tunnistaa aloitustehtävän ja renderöi lopullisen briiffitekstin.

Tärkeä periaate: briiffin pitää rauhoittaa, ei kuormittaa.
Max 3 asiaa. Yksi selkeä aloitustehtävä.
"""

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Optional

from brief_calendar import (
    CalendarEvent,
    TransitionWarning,
    total_meeting_hours,
    has_multiple_locations,
    first_morning_meeting,
)
from brief_tasks import BriefTask

log = logging.getLogger(__name__)

WEEKDAY_FI = [
    "Maanantai", "Tiistai", "Keskiviikko",
    "Torstai", "Perjantai", "Lauantai", "Sunnuntai",
]


# ── Datatyypit ────────────────────────────────────────────────────────────────

class DayLoad(str, Enum):
    LIGHT  = "light"    # Kevyt
    NORMAL = "normal"   # Normaali
    TIGHT  = "tight"    # Tiukka
    MOVING = "moving"   # Liikkuva


@dataclass
class ShopifySignals:
    """Shopify-päiväraportista poimitut signaalit."""
    has_open_alerts:    bool       = False
    alert_descriptions: list[str]  = field(default_factory=list)
    report_date:        Optional[date] = None


@dataclass
class BriefResult:
    """Valmis briiffitulos — sisältää kaiken julkaisua varten."""
    brief_date:          date
    generated_at:        datetime
    day_load:            DayLoad
    day_load_label:      str
    key_items:           list[str]    # Max 3 tärkeintä (vapaa teksti)
    meetings:            list[dict]   # [{time, title, location}]
    transition_warnings: list[str]
    start_task:          Optional[str]
    start_task_url:      Optional[str]
    status_note:         str
    selected_tasks:      list[BriefTask]
    brief_text:          str          # Valmis Markdown ClickUpiin
    source_summary:      dict


# ── Pääfunktio ────────────────────────────────────────────────────────────────

def generate_brief(
    tomorrow:        date,
    events:          list[CalendarEvent],
    tasks:           list[BriefTask],
    shopify_signals: Optional[ShopifySignals] = None,
    max_tasks:       int = 3,
) -> BriefResult:
    """Generoi huomisen briiffin.

    Palauttaa BriefResult-olion joka sisältää valmiin tekstin
    ja kaiken tarvittavan tietokantatallennukseen.
    """
    import config
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo

    tz  = ZoneInfo(config.TIMEZONE)
    now = datetime.now(tz)

    # 1. Luokittele päivä
    day_load = classify_day_load(events)

    # 2. Siirtymävaroitukset (injektoidaan ulkoa, ei kutsuta tässä)
    #    Kutsuja (brief_main.py) välittää transition_warnings
    #    Tässä lasketaan ne suoraan briiffiä varten
    from brief_calendar import detect_transitions
    transitions         = detect_transitions(events)
    transition_warnings = [t.message for t in transitions]

    # 3. Valitse tehtävät
    effective_max = _effective_max_tasks(day_load, max_tasks)
    selected      = select_tasks(tasks, day_load, effective_max)

    # 4. Aloitustehtävä
    start_obj      = select_start_task(selected, events)
    start_task     = start_obj.name if start_obj else None
    start_task_url = start_obj.url  if start_obj else None

    # 5. Key items
    key_items = _build_key_items(selected, shopify_signals)

    # 6. Kokoukset
    meetings = _format_meetings(events)

    # 7. Status
    day_load_label = _day_load_label(day_load)
    status_note    = _build_status_note(day_load, events, transitions, shopify_signals)

    # 8. Renderöi teksti
    brief_text = _render_brief(
        tomorrow=tomorrow,
        day_load_label=day_load_label,
        key_items=key_items,
        meetings=meetings,
        transition_warnings=transition_warnings,
        start_task=start_task,
        start_task_url=start_task_url,
        status_note=status_note,
    )

    source_summary = {
        "events_count":   len(events),
        "tasks_fetched":  len(tasks),
        "tasks_selected": len(selected),
        "transitions":    len(transitions),
        "shopify_signals": bool(shopify_signals and shopify_signals.has_open_alerts),
    }

    log.info(
        f"Briiffi generoitu: {tomorrow} — {day_load.value}, "
        f"{len(selected)} tehtävää, {len(meetings)} tapaamis(ta)"
    )

    return BriefResult(
        brief_date=tomorrow,
        generated_at=now,
        day_load=day_load,
        day_load_label=day_load_label,
        key_items=key_items,
        meetings=meetings,
        transition_warnings=transition_warnings,
        start_task=start_task,
        start_task_url=start_task_url,
        status_note=status_note,
        selected_tasks=selected,
        brief_text=brief_text,
        source_summary=source_summary,
    )


# ── Päivän luokittelu ─────────────────────────────────────────────────────────

def classify_day_load(events: list[CalendarEvent]) -> DayLoad:
    """Luokittelee päivän kuorman kalenteritapahtumien perusteella."""
    import config
    tight_hours = getattr(config, "BRIEF_TIGHT_DAY_MEETING_HOURS", 4.0)

    hours       = total_meeting_hours(events)
    moving      = has_multiple_locations(events)
    timed_count = len([e for e in events if not e.all_day and e.start])

    if moving:
        return DayLoad.MOVING
    if hours >= tight_hours or timed_count >= 4:
        return DayLoad.TIGHT
    if hours <= 1.0 and timed_count <= 1:
        return DayLoad.LIGHT
    return DayLoad.NORMAL


# ── Tehtävien valinta ─────────────────────────────────────────────────────────

def select_tasks(
    tasks:     list[BriefTask],
    day_load:  DayLoad,
    max_tasks: int,
) -> list[BriefTask]:
    """Valitsee briiffiin sopivat tehtävät päivän kuorman mukaan.

    Kiireisiltä päiviltä vain urgent/high tai eräpäivätehtävät.
    """
    if not tasks:
        return []

    candidates = tasks

    if day_load in (DayLoad.TIGHT, DayLoad.MOVING):
        candidates = [
            t for t in tasks
            if t.priority <= 2 or (t.due_date is not None and t.score >= 20.0)
        ]
        if not candidates:
            candidates = tasks[:1]

    return candidates[:max_tasks]


def select_start_task(
    selected_tasks: list[BriefTask],
    events:         list[CalendarEvent],
) -> Optional[BriefTask]:
    """Valitsee yhden selkeän aloitustehtävän.

    Palauttaa aina yksinkertaisesti parhaan tehtävän.
    Jos päivä alkaa kokouksella, tehtävä on silti näkyvissä
    mutta briiffitekstissä kerrotaan konteksti.
    """
    if not selected_tasks:
        return None
    return selected_tasks[0]


# ── Apufunktiot ───────────────────────────────────────────────────────────────

def _effective_max_tasks(day_load: DayLoad, configured_max: int) -> int:
    limits = {
        DayLoad.LIGHT:  min(configured_max, 3),
        DayLoad.NORMAL: min(configured_max, 3),
        DayLoad.TIGHT:  min(configured_max, 2),
        DayLoad.MOVING: min(configured_max, 2),
    }
    return limits.get(day_load, configured_max)


def _build_key_items(
    tasks:           list[BriefTask],
    shopify_signals: Optional[ShopifySignals],
) -> list[str]:
    """Rakentaa max 3 key item -tekstiä briiffiin."""
    items: list[str] = []

    for t in tasks:
        label = t.name
        if t.due_date:
            from datetime import date as _date
            days = (t.due_date - _date.today()).days
            if days < 0:
                label += " ⚠️ myöhässä"
            elif days == 0:
                label += " (erääntyy tänään)"
        items.append(label)

    # Shopify-signaali vain kriittisenä ja vain jos mahtuva
    if shopify_signals and shopify_signals.has_open_alerts and len(items) < 3:
        for desc in shopify_signals.alert_descriptions[:1]:
            items.append(f"Shopify: {desc}")

    return items


def _format_meetings(events: list[CalendarEvent]) -> list[dict]:
    """Muotoilee tapahtumat briiffiin sopivaksi listaksi."""
    meetings: list[dict] = []
    for e in sorted(events, key=lambda x: (x.start or datetime.min.replace(tzinfo=None))):
        if e.all_day:
            meetings.append({"time": "koko päivä", "title": e.title, "location": e.location})
        elif e.start and e.end:
            meetings.append({
                "time":     f"{e.start.strftime('%H:%M')}–{e.end.strftime('%H:%M')}",
                "title":    e.title,
                "location": e.location,
            })
    return meetings


def _day_load_label(day_load: DayLoad) -> str:
    return {
        DayLoad.LIGHT:  "kevyt päivä",
        DayLoad.NORMAL: "normaali päivä",
        DayLoad.TIGHT:  "tiukka päivä",
        DayLoad.MOVING: "liikkuva päivä",
    }.get(day_load, "normaali päivä")


def _build_status_note(
    day_load:        DayLoad,
    events:          list[CalendarEvent],
    transitions:     list[TransitionWarning],
    shopify_signals: Optional[ShopifySignals],
) -> str:
    """Rakentaa lyhyen, rauhoittavan status-huomion."""
    hours       = total_meeting_hours(events)
    timed_count = len([e for e in events if not e.all_day and e.start])

    if day_load == DayLoad.LIGHT:
        if timed_count == 0:
            return "Kalenterissa on väljyyttä — hyvä päivä syventyä yhteen asiaan."
        return "Kevyt päivä, hyvin omaa aikaa."

    if day_load == DayLoad.MOVING:
        return "Liikkuva päivä — jätä aamuun siirtymiin tarvittava väljyys."

    if day_load == DayLoad.TIGHT:
        if transitions:
            return "Päivä on tiivis ja sisältää siirtymiä. Pidä fokus yhdessä pääasiassa."
        return f"Tapaamispainotteinen päivä ({hours:.0f} h kokouksia). Pidä fokus yhdessä pääasiassa."

    if hours > 0:
        return f"Kokouksia {hours:.1f} h, omaa aikaa jää hyvin."
    return "Tasapainoinen päivä."


# ── Briiffin renderöinti ──────────────────────────────────────────────────────

def _render_brief(
    tomorrow:            date,
    day_load_label:      str,
    key_items:           list[str],
    meetings:            list[dict],
    transition_warnings: list[str],
    start_task:          Optional[str],
    start_task_url:      Optional[str],
    status_note:         str,
) -> str:
    """Renderöi lopullisen briiffitekstin Markdownina."""
    weekday = WEEKDAY_FI[tomorrow.weekday()]
    lines: list[str] = []

    lines.append(f"## HUOMINEN — {weekday} {tomorrow.strftime('%-d.%-m.%Y')}")
    lines.append("")

    # Tärkeintä
    if key_items:
        lines.append("**Tärkeintä:**")
        for item in key_items:
            lines.append(f"- {item}")
        lines.append("")

    # Missä pitää olla
    if meetings:
        lines.append("**Missä pitää olla:**")
        for m in meetings:
            loc = f" — {m['location']}" if m.get("location") else ""
            lines.append(f"- {m['time']}: {m['title']}{loc}")
        lines.append("")

    # Huomiot (siirtymät)
    if transition_warnings:
        lines.append("**Huomio:**")
        for w in transition_warnings:
            lines.append(f"- {w}")
        lines.append("")

    # Aloita tästä
    if start_task:
        lines.append("**Aloita tästä:**")
        if start_task_url:
            lines.append(f"→ [{start_task}]({start_task_url})")
        else:
            lines.append(f"→ {start_task}")
        lines.append("")

    # Status
    lines.append(f"**Status:** {day_load_label.capitalize()}")
    lines.append(f"_{status_note}_")
    lines.append("")
    lines.append("---")
    lines.append(
        "_Tämä on automaattinen ehdotus. "
        "Merkitse tehtävä hyväksytyksi lisäämällä tagi **brief-approved** "
        "tai muokkaa vapaasti._"
    )

    return "\n".join(lines)
