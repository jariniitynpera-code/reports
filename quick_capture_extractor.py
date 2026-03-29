"""
quick_capture_extractor.py — Pikasyötön Claude-extraction

Muuntaa vapaamuotoisen puhutun tekstin (Siri → teksti) strukturoiduksi
ClickUp-tehtäväksi. Ensisijainen metodi: Claude API. Varasuunnitelma:
sääntöpohjainen parseri ilman API-avainta.

ClickUp-listojen reititys (CATEGORY_LIST_MAP) vastaa työtilan rakennetta.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

log = logging.getLogger(__name__)

# ── Lista-reititys ─────────────────────────────────────────────────────────────
# Avain = kategoria-nimi jonka Claude palauttaa
# Arvo  = ClickUp list ID

CATEGORY_LIST_MAP: dict[str, str] = {
    "tehtavat":             "901522468792",   # Tehtävät (uusi)
    "ideat":                "901522468793",   # Ideat (uusi)
    "kpi":                  "901518729782",
    "ecosystem":            "901518725125",
    "3pl":                  "901518728129",
    "suppliers":            "901518728716",
    "category_management":  "901518728608",
    "sales_campaigns":      "901518728836",
    "marketing":            "901518728927",
    "competitive_advantage":"901518729118",
    "loyalty":              "901518728979",
    "kokoustehtavat":       "901522453032",
}

DEFAULT_CATEGORY = "tehtavat"


# ── Datatyypit ─────────────────────────────────────────────────────────────────

@dataclass
class CaptureExtraction:
    """Strukturoitu tulos puhutusta pikasyötöstä."""
    title:                    str
    description:              str
    category:                 str            # avain CATEGORY_LIST_MAP:ssa
    list_id:                  str            # ClickUp list ID
    assignee_name:            Optional[str]  # "Jari", "Matti", jne.
    priority:                 int            # 1=urgent, 2=high, 3=normal, 4=low
    due_date:                 Optional[date]
    needs_calendar:           bool           # Jarin oma toimenpide + deadline
    calendar_duration_minutes: int           # default 30
    tags:                     list[str]      = field(default_factory=list)
    raw_text:                 str            = ""
    extraction_method:        str            = "claude"
    model_used:               str            = ""


# ── Pääfunktio ─────────────────────────────────────────────────────────────────

def extract(
    text: str,
    capture_type: str = "tehtava",  # 'tehtava' | 'idea'
    today: Optional[date] = None,
) -> CaptureExtraction:
    """Muuntaa vapaamuotoisen tekstin strukturoiduksi tehtäväksi.

    Yrittää ensin Claude API:a. Epäonnistuessa tai ilman API-avainta
    käyttää sääntöpohjaista varamenetelmää.
    """
    if today is None:
        today = date.today()

    import config
    if config.ANTHROPIC_API_KEY:
        try:
            return _extract_with_claude(text, capture_type, today)
        except Exception as e:
            log.warning(f"Claude-extraction epäonnistui, käytetään varamenetelmää: {e}")

    return _extract_rule_based(text, capture_type, today)


# ── Claude-extraction ──────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
Olet assistentti joka muuntaa vapaamuotoisen puhutun tekstin (saneltu Sirille)
strukturoiduksi ClickUp-tehtäväksi. Käyttäjä on Jari, suomalaisen verkkokaupan
(veneilytarvikkeet) toimitusjohtaja.

Palauta VAIN JSON-objekti ilman selityksiä tai markdown-koodia.

JSON-kentät:
- title (string, max 100 merkkiä): Tiivis tehtävän otsikko. Älä lisää päivämäärää.
- description (string): Täydentävä konteksti. Tyhjä jos ei ole.
- category (string): Yksi seuraavista arvoista:
    tehtavat, ideat, kpi, ecosystem, 3pl, suppliers, category_management,
    sales_campaigns, marketing, competitive_advantage, loyalty, kokoustehtavat
- assignee_name (string | null): Kuka tekee? "Jari" jos käyttäjä sanoo "minä/itse",
    null jos ei mainita.
- priority (int): 1=kiireinen, 2=tärkeä, 3=normaali, 4=matala
- due_date (string YYYY-MM-DD | null): Deadline. Tulkitse luonnolliset ilmaisut
    (huomenna, ensi perjantai, ensi viikolla, viikon sisällä jne.)
- needs_calendar (bool): true JOS DUE_DATE on asetettu JA assignee on Jari (tai null).
    Eli: Jarin oma henkilökohtainen toimenpide jolla on deadline → true.
- calendar_duration_minutes (int): Arvioitu kesto minuuteissa. 30 jos epäselvä.
- tags (array of strings): Relevantteja tageja, esim. ["toimittajat", "neuvottelu"].
    Korkeintaan 3 tagia.

Kategoriaohjeet:
- tehtavat: yleiset toimenpiteet, hallinto, ops, muut
- ideat: uudet ideat, konseptit, kokeilemisen arvoiset asiat
- kpi: mittarit, tavoitteet, raportointi
- 3pl: logistiikka, varasto, toimitus, fulfillment
- suppliers: toimittajat, tilaukset, neuvottelut, hankinta
- category_management: tuotekategoriat, valikoima, tuotesuunnittelu
- sales_campaigns: kampanjat, alennukset, myyntitapahtumat
- marketing: markkinointi, mainonta, sisällöt, some, SEO
- competitive_advantage: kilpailija-analyysi, erottautuminen, positiointi
- loyalty: kanta-asiakasohjelma, asiakaspysyvyys, palkinnot
- kokoustehtavat: palavereista syntyneet toimenpiteet, follow-upit
"""


def _extract_with_claude(text: str, capture_type: str, today: date) -> CaptureExtraction:
    """Kutsuu Claude API:a structured extraction -promptilla."""
    import anthropic
    import config

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    user_prompt = (
        f"Tänään on {today.isoformat()}. "
        f"Syötteen tyyppi: {'idea' if capture_type == 'idea' else 'tehtävä'}.\n\n"
        f"Saneltu teksti:\n{text}"
    )

    response = client.messages.create(
        model=config.ANTHROPIC_MODEL,
        max_tokens=512,
        messages=[{"role": "user", "content": user_prompt}],
        system=_SYSTEM_PROMPT,
    )

    raw = response.content[0].text.strip()
    log.debug(f"Claude response: {raw}")

    data = _parse_json(raw)
    return _normalize(data, text, capture_type, today, model=config.ANTHROPIC_MODEL)


def _parse_json(text: str) -> dict:
    """Parsii JSON Claude-vastauksesta — sietää markdown-koodiblokeja."""
    # Poista mahdolliset ```json ... ``` ympäriltä
    match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if match:
        text = match.group(1)
    return json.loads(text)


def _normalize(
    data: dict,
    raw_text: str,
    capture_type: str,
    today: date,
    model: str,
) -> CaptureExtraction:
    """Validoi ja normalisoi Claude-vastauksen."""
    title = str(data.get("title") or raw_text[:80]).strip()
    description = str(data.get("description") or "").strip()

    # Kategoria: idea-tyyppi pakotetaan ideat-kategoriaan jos Claude ei tiedä paremmin
    category = str(data.get("category") or DEFAULT_CATEGORY).lower().strip()
    if capture_type == "idea" and category == "tehtavat":
        category = "ideat"
    if category not in CATEGORY_LIST_MAP:
        category = "ideat" if capture_type == "idea" else DEFAULT_CATEGORY
    list_id = CATEGORY_LIST_MAP[category]

    assignee_name = data.get("assignee_name") or None
    if isinstance(assignee_name, str):
        assignee_name = assignee_name.strip() or None

    priority = int(data.get("priority") or 3)
    priority = max(1, min(4, priority))

    due_date: Optional[date] = None
    raw_due = data.get("due_date")
    if raw_due:
        try:
            due_date = date.fromisoformat(str(raw_due))
        except (ValueError, TypeError):
            due_date = _parse_due_hint(str(raw_due), today)

    needs_calendar = bool(data.get("needs_calendar", False))
    # Pakota false jos ei due_date
    if not due_date:
        needs_calendar = False

    duration = int(data.get("calendar_duration_minutes") or 30)
    duration = max(15, min(480, duration))

    raw_tags = data.get("tags") or []
    tags = [str(t).lower().strip() for t in raw_tags if t][:3]

    return CaptureExtraction(
        title=title[:100],
        description=description,
        category=category,
        list_id=list_id,
        assignee_name=assignee_name,
        priority=priority,
        due_date=due_date,
        needs_calendar=needs_calendar,
        calendar_duration_minutes=duration,
        tags=tags,
        raw_text=raw_text,
        extraction_method="claude",
        model_used=model,
    )


# ── Sääntöpohjainen varamenetelmä ──────────────────────────────────────────────

# Allatiivimuodot → nominatiivi (suomen kielioppi syö kaksoiskonsonantit)
# "Matille" → regex kaappaa "Mati", mutta oikea nimi on "Matti"
_ALLATIVE_MAP: dict[str, str] = {
    "jarille":   "Jari",
    "matille":   "Matti",
    "annalle":   "Anna",
    "pekalle":   "Pekka",
    "mikolle":   "Mikko",
    "sannalle":  "Sanna",
    "liisalle":  "Liisa",
    "tomille":   "Tomi",
    "petterille":"Petteri",
    "juholle":   "Juho",
    "esalle":    "Esa",
    "timolla":   "Timo",   # adessive, ei allatiivi — lisätty varmuuden vuoksi
}

_ASSIGNEE_PATTERNS = [
    r"\b([A-ZÄÖÅ][a-zäöå]+lle)\b",        # "Matille" (koko sana, lookup korjaa)
    r"\btekee\s+([A-ZÄÖÅ][a-zäöå]+)\b",   # "tekee Matti"
    r"\bvastuuhenkilö[:\s]+([A-ZÄÖÅ][a-zäöå]+)", # "vastuuhenkilö: Matti"
]


def _resolve_allative(word: str) -> str:
    """Muuntaa allatiivisanan nominatiiviksi jos mahdollista.
    "Matille" → "Matti", tuntematon → palautetaan sellaisenaan.
    """
    return _ALLATIVE_MAP.get(word.lower(), word)

_PRIORITY_KEYWORDS = {
    1: ["kiireinen", "kiireellinen", "urgent", "heti", "asap", "tänään"],
    2: ["tärkeä", "tärkeää", "tärkeätä", "prioriteetti", "ensin"],
    4: ["matala prioriteetti", "ei kiire", "jossain vaiheessa", "myöhemmin"],
}

_DUE_PATTERNS = [
    (r"\bhuomenna\b",           lambda t: t + timedelta(days=1)),
    (r"\bylihuomenna\b",        lambda t: t + timedelta(days=2)),
    (r"\btänään\b",             lambda t: t),
    (r"\bensi viikolla\b",      lambda t: _next_monday(t)),
    (r"\bviikon (sisällä|päästä)\b", lambda t: t + timedelta(weeks=1)),
    (r"\bensi maanantai(na)?\b", lambda t: _next_weekday(t, 0)),
    (r"\bensi tiistai(na)?\b",  lambda t: _next_weekday(t, 1)),
    (r"\bensi keskiviikko(na)?\b", lambda t: _next_weekday(t, 2)),
    (r"\bensi torstai(na)?\b",  lambda t: _next_weekday(t, 3)),
    (r"\bensi perjantai(na)?\b", lambda t: _next_weekday(t, 4)),
    (r"\bperjantaihin\b",       lambda t: _next_weekday(t, 4)),
    (r"\bmaanantaihin\b",       lambda t: _next_weekday(t, 0)),
    (r"\bkuun loppuun\b",       lambda t: _end_of_month(t)),
]


def _extract_rule_based(text: str, capture_type: str, today: date) -> CaptureExtraction:
    """Sääntöpohjainen varamenetelmä ilman API-avainta."""
    lower = text.lower()

    # Kategoria: capture_type ohjaa ensisijaisesti
    category = "ideat" if capture_type == "idea" else DEFAULT_CATEGORY

    # Assignee
    assignee_name: Optional[str] = None
    if any(w in lower for w in ["minä teen", "itse teen", "minun pitää", "minun täytyy"]):
        assignee_name = "Jari"
    else:
        for pat in _ASSIGNEE_PATTERNS:
            m = re.search(pat, text)
            if m:
                raw_name = m.group(1)
                name = _resolve_allative(raw_name)
                if len(name) > 2:
                    assignee_name = name
                    break

    # Prioriteetti
    priority = 3
    for prio, keywords in _PRIORITY_KEYWORDS.items():
        if any(kw in lower for kw in keywords):
            priority = prio
            break

    # Deadline
    due_date = _parse_due_hint(text, today)

    # Kalenteritarve: Jarin oma tehtävä + deadline
    needs_calendar = bool(
        due_date
        and (assignee_name in (None, "Jari"))
    )

    # Otsikko: ensimmäinen 80 merkkiä siistittynä
    title = re.sub(r"\s+", " ", text).strip()[:80]

    return CaptureExtraction(
        title=title,
        description="",
        category=category,
        list_id=CATEGORY_LIST_MAP[category],
        assignee_name=assignee_name,
        priority=priority,
        due_date=due_date,
        needs_calendar=needs_calendar,
        calendar_duration_minutes=30,
        tags=[],
        raw_text=text,
        extraction_method="fallback",
        model_used="",
    )


# ── Apufunktiot päivämäärille ──────────────────────────────────────────────────

def _parse_due_hint(text: str, today: date) -> Optional[date]:
    """Etsii luonnollisen kielen päivämäärävihjeen tekstistä."""
    lower = text.lower()
    for pattern, resolver in _DUE_PATTERNS:
        if re.search(pattern, lower):
            try:
                return resolver(today)
            except Exception:
                pass
    # ISO-päivämäärä suoraan ("2025-04-15")
    m = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
    if m:
        try:
            return date.fromisoformat(m.group(1))
        except ValueError:
            pass
    return None


def _next_weekday(today: date, weekday: int) -> date:
    """Seuraava tietty viikonpäivä (0=ma, 4=pe). Ei koskaan tänään."""
    days_ahead = weekday - today.weekday()
    if days_ahead <= 0:
        days_ahead += 7
    return today + timedelta(days=days_ahead)


def _next_monday(today: date) -> date:
    return _next_weekday(today, 0)


def _end_of_month(today: date) -> date:
    if today.month == 12:
        return date(today.year + 1, 1, 1) - timedelta(days=1)
    return date(today.year, today.month + 1, 1) - timedelta(days=1)
