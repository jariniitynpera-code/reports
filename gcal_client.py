"""
gcal_client.py — Google Calendar -integraatio

Hakee kalenteritapahtumat raporttipäivälle ja tunnistaa kampanjat,
lomat ja muut poikkeamat, jotka selittävät myynnin vaihtelua.

Autentikointi:
  - Palvelutilin JSON-avaintiedosto (suositeltu automatisointiin)
  - Aseta GCAL_CREDENTIALS_FILE ja GCAL_CALENDAR_ID .env-tiedostoon

Riippuvuudet:
  pip install google-api-python-client google-auth

Jos tunnuksia ei ole asetettu, moduuli palauttaa tyhjän listan
ilman virhettä (graceful degradation).
"""

import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import Optional

log = logging.getLogger(__name__)


def get_calendar_events(report_date: date) -> list[dict]:
    """Hakee Google Kalenterin tapahtumat raporttipäivälle.

    Palauttaa listan tapahtumista muodossa:
    [{"title": str, "type": str, "all_day": bool, "description": str}]

    Jos kalenteria ei ole konfigutu, palauttaa tyhjän listan.
    """
    credentials_file = os.getenv("GCAL_CREDENTIALS_FILE", "")
    calendar_id      = os.getenv("GCAL_CALENDAR_ID", "primary")

    if not credentials_file:
        log.debug("GCAL_CREDENTIALS_FILE ei asetettu — ohitetaan kalenteriintegraatio")
        return []

    if not os.path.isfile(credentials_file):
        log.warning(f"Kalenteri-tunnustiedostoa ei löydy: {credentials_file}")
        return []

    try:
        return _fetch_events(report_date, credentials_file, calendar_id)
    except ImportError:
        log.warning(
            "google-api-python-client ei asennettu. "
            "Aja: pip install google-api-python-client google-auth"
        )
        return []
    except Exception as e:
        log.warning(f"Google Calendar -haku epäonnistui: {e}")
        return []


def _fetch_events(report_date: date, credentials_file: str, calendar_id: str) -> list[dict]:
    """Varsinainen API-kutsu Google Calendariin."""
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]

    creds = service_account.Credentials.from_service_account_file(
        credentials_file, scopes=SCOPES
    )

    service = build("calendar", "v3", credentials=creds, cache_discovery=False)

    # Päivän alku ja loppu UTC:ssä
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo

    import config
    tz = ZoneInfo(config.TIMEZONE)
    day_start = datetime(report_date.year, report_date.month, report_date.day,
                         0, 0, 0, tzinfo=tz)
    day_end   = day_start + timedelta(days=1)

    result = service.events().list(
        calendarId=calendar_id,
        timeMin=day_start.isoformat(),
        timeMax=day_end.isoformat(),
        singleEvents=True,
        orderBy="startTime",
        maxResults=50,
    ).execute()

    events = result.get("items", [])
    log.info(f"Google Calendar: {len(events)} tapahtumaa päivälle {report_date}")

    return [_normalize_event(e) for e in events]


def _normalize_event(event: dict) -> dict:
    """Normalisoi Google Calendar -tapahtuman yksinkertaiseen muotoon."""
    start = event.get("start", {})
    all_day = "date" in start and "dateTime" not in start

    title       = event.get("summary", "")
    description = event.get("description", "")

    # Arvaa tapahtuman tyyppi otsikon perusteella
    event_type = _classify_event(title, description)

    return {
        "title":       title,
        "type":        event_type,
        "all_day":     all_day,
        "description": description,
    }


def _classify_event(title: str, description: str) -> str:
    """Luokittelee kalenteritapahtuman tyypin.

    Palauttaa yhden: campaign / holiday / sale / maintenance / other
    """
    text = (title + " " + description).lower()

    campaign_keywords = [
        "kampanja", "campaign", "tarjous", "alennusmyynti",
        "ale", "promo", "black friday", "cyber monday",
        "flash sale", "myynti",
    ]
    holiday_keywords = [
        "loma", "holiday", "pyhä", "vapaa", "suljettu", "closed",
        "joulu", "pääsiäinen", "juhannus", "uusivuosi",
    ]
    sale_keywords = [
        "huutokauppa", "auction", "clearance", "loppuunmyynti",
    ]
    maintenance_keywords = [
        "huolto", "maintenance", "päivitys", "update", "downtime",
    ]

    if any(kw in text for kw in campaign_keywords):
        return "campaign"
    if any(kw in text for kw in holiday_keywords):
        return "holiday"
    if any(kw in text for kw in sale_keywords):
        return "sale"
    if any(kw in text for kw in maintenance_keywords):
        return "maintenance"
    return "other"


def format_events_for_report(events: list[dict]) -> Optional[str]:
    """Muotoilee tapahtumat raporttiin sopivaksi tekstiksi.

    Palauttaa None jos ei tapahtumia.
    """
    if not events:
        return None

    parts = []
    for e in events:
        type_labels = {
            "campaign":    "📣 Kampanja",
            "holiday":     "📅 Loma/Pyhäpäivä",
            "sale":        "🏷️ Alennusmyynti",
            "maintenance": "🔧 Huolto",
            "other":       "📌",
        }
        label = type_labels.get(e["type"], "📌")
        parts.append(f"{label}: **{e['title']}**")

    return "\n".join(parts)


def explain_anomaly(events: list[dict], status_level: str) -> Optional[str]:
    """Palauttaa kalenteripohjaisen selityksen poikkeamalle.

    Käytetään analyysissa kontekstin antamiseen.
    """
    if not events:
        return None

    campaign_events = [e for e in events if e["type"] in ("campaign", "sale")]
    holiday_events  = [e for e in events if e["type"] == "holiday"]

    explanations = []

    if campaign_events and status_level in ("green",):
        names = ", ".join(e["title"] for e in campaign_events[:2])
        explanations.append(f"Kampanja aktiivinen: {names}")

    if holiday_events:
        names = ", ".join(e["title"] for e in holiday_events[:2])
        if status_level == "red":
            explanations.append(f"Mahdollinen selitys myynnin laskulle: {names}")
        else:
            explanations.append(f"Tänä päivänä: {names}")

    return " | ".join(explanations) if explanations else None
