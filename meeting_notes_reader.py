"""
meeting_notes_reader.py — Kokousmuistioiden lukeminen

Tukee kolmea lähdettä:
  1. Suora teksti (merkkijono / stdin)
  2. Paikallinen tekstitiedosto
  3. Google Drive -dokumentti (vie plain textiksi Drive API:n kautta)

Google Drive -tuki käyttää samoja tunnuksia kuin brief_calendar.py:
  - GCAL_CREDENTIALS_FILE tai GCAL_CREDENTIALS_JSON

Graceful degradation: jos Google Drive -tunnukset puuttuvat,
vain teksti/tiedostolähde toimii. Ei kaadu hiljaisi.
"""

import hashlib
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# Tarvittavat Google API -scopet (Drive + Docs)
_GDRIVE_SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
]


# ── Datatyypit ────────────────────────────────────────────────────────────────

@dataclass
class MeetingNote:
    """Yksi kokousmuistio luettuna mistä tahansa lähteestä."""
    content:        str
    source_type:    str                # "text" | "file" | "gdoc"
    source_id:      str                # Deterministinen tunniste (hash tai Drive file ID)
    source_url:     Optional[str]
    source_title:   Optional[str]
    meeting_date:   Optional[date]
    attendees:      list[str]          = field(default_factory=list)
    calendar_meta:  Optional[dict]     = None

    @property
    def content_preview(self) -> str:
        """Ensimmäiset 500 merkkiä sisällöstä (tallennusta varten)."""
        return self.content[:500]


# ── Julkiset lukufunktiot ─────────────────────────────────────────────────────

def read_from_text(
    text:         str,
    title:        Optional[str]       = None,
    meeting_date: Optional[date]      = None,
    attendees:    Optional[list[str]] = None,
) -> MeetingNote:
    """Lukee muistion suoraan merkkijonosta (CLI / stdin / testi).

    source_id on SHA256-hash sisällöstä — sama teksti → sama ID.
    """
    if not text.strip():
        raise ValueError("Muistioteksti on tyhjä.")
    source_id = _hash_text(text)
    return MeetingNote(
        content=text.strip(),
        source_type="text",
        source_id=source_id,
        source_url=None,
        source_title=title or "Kokousmuistio",
        meeting_date=meeting_date,
        attendees=attendees or [],
    )


def read_from_file(path: str, meeting_date: Optional[date] = None) -> MeetingNote:
    """Lukee muistion paikallisesta tekstitiedostosta (txt, md).

    Yrittää päätellä kokouspäivän tiedostonimestä (YYYY-MM-DD jne.).
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Tiedostoa ei löydy: {path}")
    if not p.is_file():
        raise ValueError(f"Polku ei ole tiedosto: {path}")

    content = p.read_text(encoding="utf-8")
    if not content.strip():
        raise ValueError(f"Tiedosto on tyhjä: {path}")

    inferred_date = meeting_date or _infer_date_from_text(p.name)
    title = p.stem.replace("_", " ").replace("-", " ").title()

    return MeetingNote(
        content=content.strip(),
        source_type="file",
        source_id=_hash_text(content),
        source_url=str(p.resolve()),
        source_title=title,
        meeting_date=inferred_date,
        attendees=[],
    )


def read_from_gdoc(
    url_or_id:    str,
    meeting_date: Optional[date] = None,
) -> MeetingNote:
    """Lukee muistion Google Drive -dokumentista.

    Tukee:
      - https://docs.google.com/document/d/{ID}/...
      - Suora Drive file ID
    """
    file_id = _extract_gdoc_id(url_or_id)
    if not file_id:
        raise ValueError(
            f"Ei voi tunnistaa Google Doc ID:tä: '{url_or_id}'\n"
            "Anna URL muodossa https://docs.google.com/document/d/FILE_ID/... "
            "tai suora file ID."
        )

    creds = _get_google_credentials()
    if creds is None:
        raise RuntimeError(
            "Google Drive -tunnuksia ei löydy. "
            "Aseta GCAL_CREDENTIALS_FILE tai GCAL_CREDENTIALS_JSON."
        )

    try:
        from googleapiclient.discovery import build
    except ImportError:
        raise RuntimeError(
            "google-api-python-client ei asennettu. "
            "Aja: pip install google-api-python-client google-auth"
        )

    drive = build("drive", "v3", credentials=creds, cache_discovery=False)

    # Hae metatiedot
    try:
        meta = drive.files().get(
            fileId=file_id,
            fields="id,name,createdTime,modifiedTime,webViewLink,mimeType",
        ).execute()
    except Exception as e:
        raise RuntimeError(f"Google Drive -metatietojen haku epäonnistui: {e}")

    title      = meta.get("name", "Google Doc")
    source_url = meta.get("webViewLink") or f"https://docs.google.com/document/d/{file_id}/"
    mime_type  = meta.get("mimeType", "")

    # Vie dokumentti plain textiksi
    # Google Docs: application/vnd.google-apps.document → export as text/plain
    # Muut Drive-tiedostot: lataa suoraan
    try:
        if "google-apps" in mime_type:
            raw = drive.files().export(fileId=file_id, mimeType="text/plain").execute()
        else:
            raw = drive.files().get_media(fileId=file_id).execute()
        content = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
    except Exception as e:
        raise RuntimeError(f"Dokumentin luku epäonnistui ({file_id}): {e}")

    if not content.strip():
        raise ValueError(f"Google Doc on tyhjä tai ei luettavissa: {title}")

    # Päättele kokouspäivä
    inferred_date = (
        meeting_date
        or _infer_date_from_text(title)
        or _date_from_iso(meta.get("createdTime"))
    )

    log.info(f"Google Doc luettu: '{title}' ({file_id}), {len(content)} merkkiä")

    return MeetingNote(
        content=content.strip(),
        source_type="gdoc",
        source_id=file_id,
        source_url=source_url,
        source_title=title,
        meeting_date=inferred_date,
        attendees=[],
    )


# ── Sisäiset apufunktiot ──────────────────────────────────────────────────────

def _hash_text(text: str) -> str:
    """Laskee deterministisen SHA256-tunnisteen tekstille (16 hex-merkkiä)."""
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:16]


def _extract_gdoc_id(url_or_id: str) -> Optional[str]:
    """Palauttaa Google Drive file ID:n URL:sta tai suoraan.

    Tunnistaa:
      https://docs.google.com/document/d/{ID}/edit
      https://drive.google.com/file/d/{ID}/view
      Suora ID (ei sisällä /)
    """
    url_or_id = url_or_id.strip()
    if not url_or_id:
        return None
    # URL-muoto: /d/{ID}/
    m = re.search(r"/d/([a-zA-Z0-9_-]{25,})", url_or_id)
    if m:
        return m.group(1)
    # Suora ID: ei välimerkkejä, sopivan pitkä
    if re.fullmatch(r"[a-zA-Z0-9_-]{25,}", url_or_id):
        return url_or_id
    return None


def _infer_date_from_text(text: str) -> Optional[date]:
    """Yrittää poimia päivämäärän tiedostonimestä tai otsikosta.

    Tunnistaa: YYYY-MM-DD, YYYY.MM.DD, YYYYMMDD, DD.MM.YYYY
    """
    patterns = [
        (r"(\d{4})[-._](\d{2})[-._](\d{2})", lambda m: (int(m.group(1)), int(m.group(2)), int(m.group(3)))),
        (r"(\d{4})(\d{2})(\d{2})",            lambda m: (int(m.group(1)), int(m.group(2)), int(m.group(3)))),
        (r"(\d{2})\.(\d{2})\.(\d{4})",        lambda m: (int(m.group(3)), int(m.group(2)), int(m.group(1)))),
    ]
    for pat, extractor in patterns:
        m = re.search(pat, text)
        if m:
            try:
                y, mo, d = extractor(m)
                return date(y, mo, d)
            except ValueError:
                continue
    return None


def _date_from_iso(iso_str: Optional[str]) -> Optional[date]:
    """Muuntaa ISO 8601 -merkkijonon date-olioksi."""
    if not iso_str:
        return None
    try:
        return datetime.fromisoformat(iso_str.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _get_google_credentials():
    """Palauttaa Google API -tunnukset (sama logiikka kuin brief_calendar.py)."""
    import json as _json

    credentials_file = os.getenv("GCAL_CREDENTIALS_FILE", "")
    credentials_json = os.getenv("GCAL_CREDENTIALS_JSON", "")

    if not credentials_file and not credentials_json:
        return None

    try:
        from google.oauth2 import service_account

        if credentials_json:
            info = _json.loads(credentials_json)
            return service_account.Credentials.from_service_account_info(
                info, scopes=_GDRIVE_SCOPES
            )
        if credentials_file:
            if not os.path.isfile(credentials_file):
                log.warning(f"Tunnustiedostoa ei löydy: {credentials_file}")
                return None
            return service_account.Credentials.from_service_account_file(
                credentials_file, scopes=_GDRIVE_SCOPES
            )
    except ImportError:
        log.warning("google-auth ei asennettu. Aja: pip install google-auth")
        return None
    except Exception as e:
        log.warning(f"Google-tunnusten lataus epäonnistui: {e}")
        return None

    return None
