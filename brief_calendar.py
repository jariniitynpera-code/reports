"""
brief_calendar.py — Kalenteridatan haku huomisen briiffiä varten

Hakee huomisen tapahtumat yksityiskohtaisina (aika, sijainti, tyyppi)
ja tunnistaa siirtymätarpeet peräkkäisten tapahtumien välillä.

Tukee sekä JSON-tiedostoa (paikallinen ajo) että JSON-merkkijonoa
ympäristömuuttujassa GCAL_CREDENTIALS_JSON (GitHub Actions).

Jos tunnuksia ei ole, palauttaa tyhjän listan — graceful degradation.
"""

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

log = logging.getLogger(__name__)


# ── Datatyypit ────────────────────────────────────────────────────────────────

@dataclass
class CalendarEvent:
    """Yksi kalenteritapahtuma briiffiä varten."""
    title:       str
    start:       Optional[datetime]   # None = koko päivä
    end:         Optional[datetime]   # None = koko päivä
    location:    str
    all_day:     bool
    description: str
    is_physical: bool   # Fyysinen tapaaminen (sijainti tai avainsanat)
    raw:         dict   = field(default_factory=dict, repr=False)


@dataclass
class TransitionWarning:
    """Varoitus liian tiukasta siirtymästä tapahtumien välillä."""
    from_event:          CalendarEvent
    to_event:            CalendarEvent
    gap_minutes:         float
    different_locations: bool
    message:             str


# ── Pääfunktio ────────────────────────────────────────────────────────────────

def get_tomorrow_events(tomorrow: date) -> list[CalendarEvent]:
    """Hakee huomisen kalenteritapahtumat.

    Palauttaa tyhjän listan jos kalenteri ei ole konfiguroitu.
    """
    credentials_file = os.getenv("GCAL_CREDENTIALS_FILE", "")
    credentials_json = os.getenv("GCAL_CREDENTIALS_JSON", "")
    calendar_id      = os.getenv("GCAL_CALENDAR_ID", "primary")

    if not credentials_file and not credentials_json:
        log.debug("Ei kalenteritunnuksia — ohitetaan kalenteriintegraatio")
        return []

    if credentials_file and not os.path.isfile(credentials_file):
        log.warning(f"Kalenteri-tunnustiedostoa ei löydy: {credentials_file}")
        return []

    try:
        return _fetch_detailed_events(tomorrow, credentials_file, credentials_json, calendar_id)
    except ImportError:
        log.warning(
            "google-api-python-client ei asennettu. "
            "Aja: pip install google-api-python-client google-auth"
        )
        return []
    except Exception as e:
        log.warning(f"Kalenterihaku epäonnistui: {e}")
        return []


# ── API-haku ──────────────────────────────────────────────────────────────────

def _fetch_detailed_events(
    target_date:      date,
    credentials_file: str,
    credentials_json: str,
    calendar_id:      str,
) -> list[CalendarEvent]:
    """Varsinainen API-kutsu Google Calendariin."""
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]

    if credentials_json:
        info  = json.loads(credentials_json)
        creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    else:
        creds = service_account.Credentials.from_service_account_file(
            credentials_file, scopes=SCOPES
        )

    service = build("calendar", "v3", credentials=creds, cache_discovery=False)

    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo

    import config
    tz        = ZoneInfo(config.TIMEZONE)
    day_start = datetime(target_date.year, target_date.month, target_date.day, 0, 0, 0, tzinfo=tz)
    day_end   = day_start + timedelta(days=1)

    result = service.events().list(
        calendarId=calendar_id,
        timeMin=day_start.isoformat(),
        timeMax=day_end.isoformat(),
        singleEvents=True,
        orderBy="startTime",
        maxResults=50,
    ).execute()

    raw_events = result.get("items", [])
    log.info(f"Google Calendar: {len(raw_events)} tapahtumaa päivälle {target_date}")

    parsed = [_parse_event(e, tz) for e in raw_events]
    return [e for e in parsed if e is not None]


def _parse_event(raw: dict, tz) -> Optional[CalendarEvent]:
    """Normalisoi yhden Google Calendar -tapahtuman."""
    start_raw = raw.get("start", {})
    end_raw   = raw.get("end", {})
    all_day   = "date" in start_raw and "dateTime" not in start_raw

    start_dt = None
    end_dt   = None

    if not all_day:
        try:
            start_dt = datetime.fromisoformat(start_raw["dateTime"]).astimezone(tz)
            end_dt   = datetime.fromisoformat(end_raw["dateTime"]).astimezone(tz)
        except (KeyError, ValueError):
            pass

    title       = raw.get("summary", "")
    location    = raw.get("location", "") or ""
    description = raw.get("description", "") or ""
    is_physical = bool(location) or _looks_physical(title, description)

    return CalendarEvent(
        title=title,
        start=start_dt,
        end=end_dt,
        location=location,
        all_day=all_day,
        description=description,
        is_physical=is_physical,
        raw=raw,
    )


def _looks_physical(title: str, description: str) -> bool:
    """Arvailee onko tapahtuma fyysinen tapaaminen avainsanojen perusteella."""
    text = (title + " " + description).lower()
    keywords = [
        "meeting", "tapaaminen", "kokous", "palaveri", "lounas", "lunch",
        "toimisto", "office", "vierailu", "visit", "neuvottelu", "haastattelu",
        "interview", "messut", "fair", "seminaari", "seminar",
    ]
    return any(kw in text for kw in keywords)


# ── Siirtymäanalyysi ──────────────────────────────────────────────────────────

def detect_transitions(events: list[CalendarEvent]) -> list[TransitionWarning]:
    """Tunnistaa tiukat siirtymät peräkkäisten tapahtumien välillä.

    Varoittaa jos:
    - Tapahtumilla on eri sijainti ja väli < buffer
    - Peräkkäiset fyysiset tapaamiset ilman siirtymäaikaa (< 5 min)
    """
    import config
    buffer = getattr(config, "BRIEF_TRANSITION_BUFFER_MIN", 20)

    warnings: list[TransitionWarning] = []

    timed = sorted(
        [e for e in events if e.start and e.end and not e.all_day],
        key=lambda e: e.start,
    )

    for i in range(len(timed) - 1):
        curr  = timed[i]
        next_ = timed[i + 1]

        gap_minutes = (next_.start - curr.end).total_seconds() / 60

        loc_curr  = curr.location.strip().lower()
        loc_next  = next_.location.strip().lower()
        diff_locs = bool(loc_curr and loc_next and loc_curr != loc_next)

        if diff_locs and gap_minutes < buffer:
            warnings.append(TransitionWarning(
                from_event=curr,
                to_event=next_,
                gap_minutes=gap_minutes,
                different_locations=True,
                message=(
                    f"Siirtymä {curr.title} → {next_.title}: "
                    f"vain {gap_minutes:.0f} min väliä, eri sijainnit "
                    f"({curr.location} → {next_.location})"
                ),
            ))
        elif gap_minutes < 5 and curr.is_physical and next_.is_physical and not diff_locs:
            warnings.append(TransitionWarning(
                from_event=curr,
                to_event=next_,
                gap_minutes=gap_minutes,
                different_locations=False,
                message=(
                    f"Tiukka aikataulu: {curr.title} loppuu "
                    f"{curr.end.strftime('%H:%M')}, "
                    f"{next_.title} alkaa {next_.start.strftime('%H:%M')}"
                ),
            ))

    return warnings


# ── Apufunktiot logiikalle ────────────────────────────────────────────────────

def total_meeting_hours(events: list[CalendarEvent]) -> float:
    """Laskee aikataulutettujen tapahtumien kokonaiskeston tunteina."""
    total = 0.0
    for e in events:
        if e.start and e.end and not e.all_day:
            total += (e.end - e.start).total_seconds() / 3600
    return round(total, 1)


def has_multiple_locations(events: list[CalendarEvent]) -> bool:
    """Palauttaa True jos päivässä on useita eri sijainteja."""
    locations = {
        e.location.strip().lower()
        for e in events
        if e.location and not e.all_day
    }
    return len(locations) > 1


def first_morning_meeting(events: list[CalendarEvent]) -> Optional[CalendarEvent]:
    """Palauttaa päivän ensimmäisen aikataulutetun tapahtuman."""
    timed = sorted(
        [e for e in events if e.start and not e.all_day],
        key=lambda e: e.start,
    )
    return timed[0] if timed else None
