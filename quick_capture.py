"""
quick_capture.py — Pikasyötön pääorkestraattori

Käyttö:
  python quick_capture.py --text "Selvitä Matilta uudet toimitusajat ensi perjantaihin"
  python quick_capture.py --text "Idea: venekategoriaan subscription-paketti" --type idea
  python quick_capture.py --stdin
  python quick_capture.py --text "..." --dry-run

GitHub Actions käynnistää tämän repository_dispatch-eventillä.
Katso .github/workflows/quick-capture.yml.

Paluukoodit:
  0 = onnistui
  1 = virhe
"""

import argparse
import json
import logging
import sys
from datetime import date, datetime, timezone
from typing import Optional

import config
import quick_capture_db
from quick_capture_extractor import CaptureExtraction, extract

log = logging.getLogger(__name__)

CLICKUP_BASE_URL = "https://api.clickup.com/api/v2"


# ── Pääfunktio ─────────────────────────────────────────────────────────────────

def run(
    text:         str,
    capture_type: str = "tehtava",
    dry_run:      bool = False,
    today:        Optional[date] = None,
) -> int:
    """Prosessoi yhden pikasyötön. Palauttaa exit-koodin."""
    if today is None:
        today = date.today()

    log.info(f"Pikasyöttö [{capture_type}]: {text[:80]}...")

    # 1. Extraction
    try:
        extraction = extract(text, capture_type=capture_type, today=today)
        log.info(
            f"Extraction: '{extraction.title}' → lista={extraction.category} "
            f"prio={extraction.priority} deadline={extraction.due_date} "
            f"kalenteri={extraction.needs_calendar} method={extraction.extraction_method}"
        )
    except Exception as e:
        log.error(f"Extraction epäonnistui: {e}")
        quick_capture_db.save_capture(
            capture_type=capture_type,
            raw_text=text,
            status="error",
            error_message=str(e),
            dry_run=dry_run,
        )
        return 1

    _print_extraction(extraction)

    if dry_run:
        print("\n[DRY-RUN] Ei luoda tehtävää eikä kalenteritapahtumaa.")
        quick_capture_db.save_capture(
            capture_type=capture_type,
            raw_text=text,
            extraction=extraction,
            status="dry_run",
            dry_run=True,
        )
        return 0

    # 2. Resolve assignee → ClickUp user ID
    assignee_id: Optional[str] = None
    if extraction.assignee_name:
        assignee_id = _resolve_assignee(extraction.assignee_name)

    # 3. Luo ClickUp-tehtävä
    task_id: Optional[str] = None
    task_url: Optional[str] = None
    try:
        task_data = _create_clickup_task(extraction, assignee_id)
        task_id   = task_data.get("id")
        task_url  = task_data.get("url")
        print(f"\nClickUp-tehtävä luotu: {task_url}")
    except Exception as e:
        log.error(f"ClickUp-tehtävän luonti epäonnistui: {e}")
        quick_capture_db.save_capture(
            capture_type=capture_type,
            raw_text=text,
            extraction=extraction,
            status="error",
            error_message=str(e),
            dry_run=dry_run,
        )
        return 1

    # 4. Luo Google Calendar -tapahtuma (jos tarpeen)
    cal_event_id:  Optional[str] = None
    cal_event_url: Optional[str] = None
    if extraction.needs_calendar and extraction.due_date:
        try:
            cal_result    = _create_calendar_event(extraction, task_url)
            cal_event_id  = cal_result.get("id")
            cal_event_url = cal_result.get("htmlLink")
            print(f"Kalenteritapahtuma luotu: {cal_event_url}")
        except Exception as e:
            # Kalenteri-virhe ei estä onnistumista — tehtävä on luotu
            log.warning(f"Kalenteritapahtuman luonti epäonnistui: {e}")

    # 5. Tallenna Supabaseen
    quick_capture_db.save_capture(
        capture_type=capture_type,
        raw_text=text,
        extraction=extraction,
        clickup_task_id=task_id,
        clickup_task_url=task_url,
        calendar_event_id=cal_event_id,
        calendar_event_url=cal_event_url,
        status="success",
        dry_run=dry_run,
        assignee_id=assignee_id,
    )

    return 0


# ── ClickUp ────────────────────────────────────────────────────────────────────

def _create_clickup_task(extraction: CaptureExtraction, assignee_id: Optional[str]) -> dict:
    """Luo ClickUp-tehtävän extraction-tuloksesta."""
    import requests

    headers = {
        "Authorization": config.CLICKUP_API_KEY,
        "Content-Type":  "application/json",
    }

    # Kuvaus
    description_parts = []
    if extraction.description:
        description_parts.append(extraction.description)
    description_parts.append(f"\n---\n*Luotu voice capture -pikasyötöllä*")
    if extraction.due_date:
        description_parts.append(f"Deadline: {extraction.due_date.isoformat()}")
    if extraction.assignee_name:
        description_parts.append(f"Vastuuhenkilö: {extraction.assignee_name}")
    description_parts.append(f"Alkuperäinen sanelu: _{extraction.raw_text}_")

    description = "\n".join(description_parts)

    # Tagit: capture-type + extraktion tagit
    tags = [f"voice-capture", extraction.category] + extraction.tags

    payload: dict = {
        "name":        extraction.title,
        "description": description,
        "priority":    extraction.priority,
        "tags":        tags,
    }

    # Deadline millisekunteina
    if extraction.due_date:
        dt = datetime(
            extraction.due_date.year,
            extraction.due_date.month,
            extraction.due_date.day,
            17, 0, 0,   # klo 17:00 — päivän loppuun mennessä
            tzinfo=timezone.utc,
        )
        payload["due_date"] = int(dt.timestamp() * 1000)

    # Assignee
    if assignee_id:
        payload["assignees"] = [int(assignee_id)]

    url = f"{CLICKUP_BASE_URL}/list/{extraction.list_id}/task"
    resp = requests.post(url, headers=headers, json=payload, timeout=20)
    resp.raise_for_status()
    result = resp.json()
    log.info(f"ClickUp: luotu {result.get('id')} — {extraction.title}")
    return result


def _resolve_assignee(name: str) -> Optional[str]:
    """Hakee ClickUp-käyttäjä-ID:n nimellä.

    Palauttaa None jos ei löydy tai haku epäonnistuu.
    """
    import requests

    try:
        headers = {"Authorization": config.CLICKUP_API_KEY}
        resp = requests.get(
            f"{CLICKUP_BASE_URL}/team",
            headers=headers,
            timeout=10,
        )
        resp.raise_for_status()
        teams = resp.json().get("teams", [])
        name_lower = name.lower()
        for team in teams:
            for member in team.get("members", []):
                user = member.get("user", {})
                username  = (user.get("username") or "").lower()
                email     = (user.get("email") or "").lower()
                full_name = (user.get("profilePicture") or "")  # not name
                # Tarkista etu- tai sukunimi
                if name_lower in username or name_lower in email.split("@")[0]:
                    uid = str(user.get("id"))
                    log.info(f"Assignee '{name}' → ClickUp user {uid}")
                    return uid
    except Exception as e:
        log.debug(f"Assignee-haku epäonnistui '{name}': {e}")

    return None


# ── Google Calendar ────────────────────────────────────────────────────────────

def _create_calendar_event(extraction: CaptureExtraction, task_url: Optional[str]) -> dict:
    """Luo Google Calendar -tapahtuman Jarille kun deadline on asetettu.

    Tapahtuma luodaan due_date-päivälle klo 09:00 Helsinki-aikaa.
    Käyttää service account -tunnuksia (sama kuin brief_calendar.py).
    """
    import json as json_mod

    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo

    SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

    credentials_file = config.GCAL_CREDENTIALS_FILE
    credentials_json = config.GCAL_CREDENTIALS_JSON
    calendar_id      = config.GCAL_CALENDAR_ID

    if credentials_json:
        info  = json_mod.loads(credentials_json)
        creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    elif credentials_file:
        creds = service_account.Credentials.from_service_account_file(
            credentials_file, scopes=SCOPES
        )
    else:
        raise RuntimeError("Ei Google Calendar -tunnuksia (GCAL_CREDENTIALS_FILE tai GCAL_CREDENTIALS_JSON)")

    service = build("calendar", "v3", credentials=creds, cache_discovery=False)

    tz      = ZoneInfo(config.TIMEZONE)
    due     = extraction.due_date
    dur_min = extraction.calendar_duration_minutes

    start_dt = datetime(due.year, due.month, due.day, 9, 0, 0, tzinfo=tz)
    end_dt   = datetime(
        due.year, due.month, due.day,
        9 + dur_min // 60,
        dur_min % 60,
        0,
        tzinfo=tz,
    )

    description_parts = [f"Tehtävä luotu voice capture -pikasyötöllä."]
    if task_url:
        description_parts.append(f"ClickUp: {task_url}")
    if extraction.description:
        description_parts.append(extraction.description)
    description_parts.append(f"\nAlkuperäinen sanelu: {extraction.raw_text}")

    event_body = {
        "summary":     extraction.title,
        "description": "\n".join(description_parts),
        "start":       {"dateTime": start_dt.isoformat(), "timeZone": config.TIMEZONE},
        "end":         {"dateTime": end_dt.isoformat(),   "timeZone": config.TIMEZONE},
        "reminders":   {"useDefault": True},
    }

    result = service.events().insert(calendarId=calendar_id, body=event_body).execute()
    log.info(f"Kalenteritapahtuma luotu: {result.get('htmlLink')}")
    return result


# ── Konsolituloste ─────────────────────────────────────────────────────────────

def _print_extraction(e: CaptureExtraction) -> None:
    """Tulostaa extraction-tuloksen konsoliin."""
    print(f"\n{'='*55}")
    print(f"  {e.title}")
    print(f"{'='*55}")
    print(f"  Lista:     {e.category} (id: {e.list_id})")
    print(f"  Priorit.:  {['','KIIREINEN','TÄRKEÄ','NORMAALI','MATALA'][e.priority]}")
    if e.assignee_name:
        print(f"  Vastuuhenk: {e.assignee_name}")
    if e.due_date:
        print(f"  Deadline:  {e.due_date.isoformat()}")
    if e.needs_calendar:
        print(f"  Kalenteri: kyllä ({e.calendar_duration_minutes} min)")
    if e.description:
        print(f"  Kuvaus:    {e.description[:120]}")
    if e.tags:
        print(f"  Tagit:     {', '.join(e.tags)}")
    print(f"  Metodi:    {e.extraction_method}")
    print()


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Pikasyöttö: puhe → ClickUp-tehtävä + kalenteri"
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--text",  help="Saneltu teksti suoraan")
    src.add_argument("--stdin", action="store_true", help="Lue teksti stdinistä")

    parser.add_argument(
        "--type", dest="capture_type",
        choices=["tehtava", "idea"],
        default="tehtava",
        help="Syötteen tyyppi (oletus: tehtava)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Ei luoda tehtäviä")
    parser.add_argument("--debug",   action="store_true", help="Verbose-loggaus")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(levelname)s  %(name)s  %(message)s",
    )

    if args.stdin:
        text = sys.stdin.read().strip()
    else:
        text = args.text.strip()

    if not text:
        print("Virhe: teksti on tyhjä.", file=sys.stderr)
        return 1

    return run(text, capture_type=args.capture_type, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
