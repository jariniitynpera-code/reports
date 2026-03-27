"""
meeting_notes_extractor.py — Action itemien tunnistus kokousmuistiosta

Ensisijainen menetelmä: Anthropic Claude API (strukturoitu JSON-output).
Varamenetelmä: sääntöpohjainen (regex + heuristiikat), jos API-avain puuttuu.

Extraction-logiikka:
  - action_item: selkeä tehtävä tekijällä ja verbillä → luo tehtävä jos confidence >= 0.7
  - follow_up:   epäselvempi asia jota pitää seurata → luo jos confidence >= 0.6
  - decision:    kokouksessa tehty päätös → EI luo tehtävää, ellei vaadi selkeää toimenpidettä

Konservatiivisuusperiaate:
  Parempi jättää tunnistamatta kuin luoda epämääräinen tehtävä.
  Kaikki tunnistetut kohdat tallennetaan audit trailiin.
"""

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

import config
from meeting_notes_reader import MeetingNote

log = logging.getLogger(__name__)

# ── Datatyypit ────────────────────────────────────────────────────────────────

@dataclass
class ExtractedItem:
    """Yksi muistiosta tunnistettu kohta (action item, päätös tai follow-up)."""
    item_type:              str             # action_item | decision | follow_up
    title:                  str             # Selkeä, toiminnallinen otsikko
    description:            str             # Lisäkonteksti
    owner:                  Optional[str]   # Vastuuhenkilö tai None
    due_hint:               Optional[str]   # Alkuperäinen muotoilu ("perjantaihin")
    due_date_normalized:    Optional[date]  # Normalisoitu päivämäärä tai None
    source_quote:           str             # Alkuperäinen lainaus muistiosta
    confidence:             float           # 0.0 – 1.0
    should_create_task:     bool
    reason_if_not_created:  Optional[str]
    fingerprint:            str = ""        # Asetetaan extraction jälkeen


@dataclass
class ExtractionResult:
    """Koko extraction-operaation tulos."""
    meeting_note:       MeetingNote
    items:              list[ExtractedItem]
    extraction_method:  str             # "claude" | "rule_based"
    model_used:         Optional[str]
    raw_response:       Optional[str]   # Claude-vastaus debuggausta varten
    extraction_errors:  list[str]       = field(default_factory=list)


# ── Pääfunktio ────────────────────────────────────────────────────────────────

def extract_items(
    meeting_note:      MeetingNote,
    min_confidence:    float = None,
    followup_min_conf: float = None,
) -> ExtractionResult:
    """Tunnistaa action itemit, päätökset ja follow-upit muistiosta.

    Yrittää ensin Claude API:ta. Jos ANTHROPIC_API_KEY puuttuu tai kutsu
    epäonnistuu, käyttää sääntöpohjaista varamenetelmää.

    Args:
        meeting_note:      Luettu kokousmuistio
        min_confidence:    Raja action item -tehtäville (oletus: config)
        followup_min_conf: Raja follow-up-tehtäville (oletus: config)

    Returns:
        ExtractionResult jossa kaikki tunnistetut kohdat ja metatiedot
    """
    min_conf_action  = min_confidence    or getattr(config, "MEETING_NOTES_MIN_CONFIDENCE", 0.7)
    min_conf_followup = followup_min_conf or getattr(config, "MEETING_NOTES_FOLLOWUP_MIN_CONFIDENCE", 0.6)

    api_key = getattr(config, "ANTHROPIC_API_KEY", "") or ""

    if api_key:
        try:
            return _extract_with_claude(meeting_note, api_key, min_conf_action, min_conf_followup)
        except Exception as e:
            log.warning(f"Claude-extraction epäonnistui, siirrytään sääntöpohjaiseen: {e}")

    return _extract_rule_based(meeting_note, min_conf_action, min_conf_followup)


# ── Claude-pohjainen extraction ───────────────────────────────────────────────

def _extract_with_claude(
    note:      MeetingNote,
    api_key:   str,
    min_conf:  float,
    fu_conf:   float,
) -> ExtractionResult:
    """Käyttää Claude API:ta strukturoituun extraction-pohjaiseen jäsennykseen."""
    try:
        import anthropic
    except ImportError:
        raise RuntimeError(
            "anthropic-kirjasto ei asennettu. Aja: pip install anthropic"
        )

    model = getattr(config, "ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
    client = anthropic.Anthropic(api_key=api_key)

    system_prompt = _build_system_prompt()
    user_content  = _build_user_prompt(note)

    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=system_prompt,
        messages=[{"role": "user", "content": user_content}],
    )

    raw_text = response.content[0].text
    log.debug(f"Claude-vastaus ({len(raw_text)} merkkiä)")

    items, errors = _parse_claude_response(raw_text, note, min_conf, fu_conf)
    _set_fingerprints(items, note.source_id)

    return ExtractionResult(
        meeting_note=note,
        items=items,
        extraction_method="claude",
        model_used=model,
        raw_response=raw_text,
        extraction_errors=errors,
    )


def _build_system_prompt() -> str:
    return """\
You are an expert at extracting structured action items from meeting notes.
Your task is to identify tasks, decisions, and follow-ups that need tracking.

CRITICAL RULES:
1. ONLY extract items clearly present in the text — do NOT invent tasks
2. Be conservative: a missed item is better than a false one
3. An action_item needs a clear verb (what to do) — vague statements are NOT action items
4. Owner: only if explicitly named — do NOT guess
5. Due date: only if explicitly stated or directly calculable from the meeting date
6. Meeting notes can be in Finnish or English — return titles in the same language as the notes

RETURN FORMAT — respond with ONLY valid JSON, no other text:
{
  "items": [
    {
      "item_type": "action_item",
      "title": "Short, clear, actionable title (max 80 chars)",
      "description": "Brief context — what, why (1-2 sentences)",
      "owner": "Full name or null",
      "due_hint": "Original phrasing e.g. 'by Friday' / 'perjantaihin mennessä' or null",
      "due_date_normalized": "YYYY-MM-DD or null",
      "source_quote": "Verbatim text from notes this was extracted from (max 300 chars)",
      "confidence": 0.85,
      "should_create_task": true,
      "reason_if_not_created": null
    }
  ]
}

ITEM TYPES:
- action_item: Clear task with a verb, assignable to someone (best for ClickUp tasks)
- follow_up: Something to revisit, less defined scope
- decision: Decision made — include ONLY if it explicitly requires follow-through action

CONFIDENCE GUIDE:
- 0.9+: Unambiguous, explicit task with clear scope
- 0.7–0.9: Clear task, minor ambiguity about scope or owner
- 0.5–0.7: Unclear scope or vague — usually should NOT create task
- < 0.5: Too vague — do not create task

TASK CREATION RULES (set should_create_task accordingly):
- action_item with confidence >= 0.7 → should_create_task: true
- follow_up with confidence >= 0.6 → should_create_task: true
- decision without clear action → should_create_task: false
- Anything with confidence < 0.5 → should_create_task: false

If should_create_task is false, explain why in reason_if_not_created.\
"""


def _build_user_prompt(note: MeetingNote) -> str:
    lines = [
        f"Meeting title: {note.source_title or 'Unknown'}",
        f"Meeting date: {note.meeting_date.isoformat() if note.meeting_date else 'Unknown'}",
    ]
    if note.attendees:
        lines.append(f"Attendees: {', '.join(note.attendees)}")
    lines.append("")
    lines.append("--- MEETING NOTES ---")
    lines.append(note.content)
    lines.append("--- END OF NOTES ---")
    lines.append("")
    lines.append(
        "Extract all action items, decisions, and follow-ups from the notes above. "
        "Be conservative — only extract items that are clearly stated."
    )
    return "\n".join(lines)


def _parse_claude_response(
    raw:      str,
    note:     MeetingNote,
    min_conf: float,
    fu_conf:  float,
) -> tuple[list[ExtractedItem], list[str]]:
    """Parsii Claude-vastauksen JSON:sta ExtractedItem-listaksi."""
    errors: list[str] = []

    # Etsi JSON-lohko vastauksesta
    json_text = _extract_json_block(raw)
    if not json_text:
        errors.append("Ei JSON-lohkoa Claude-vastauksessa")
        log.warning(f"Claude palautti ei-JSON-vastauksen: {raw[:200]}")
        return [], errors

    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as e:
        errors.append(f"JSON-parsaus epäonnistui: {e}")
        log.warning(f"Virheellinen JSON: {json_text[:200]}")
        return [], errors

    raw_items = data.get("items", [])
    if not isinstance(raw_items, list):
        errors.append("'items' ei ole lista")
        return [], errors

    items: list[ExtractedItem] = []
    for i, raw_item in enumerate(raw_items):
        try:
            item = _normalize_claude_item(raw_item, note, min_conf, fu_conf)
            if item:
                items.append(item)
        except Exception as e:
            errors.append(f"Item {i}: {e}")
            log.debug(f"Item-normalisointi epäonnistui (item {i}): {e}")

    log.info(
        f"Claude extraction: {len(items)} kohtaa tunnistettu "
        f"({sum(1 for it in items if it.should_create_task)} tehtäväksi)"
    )
    return items, errors


def _normalize_claude_item(
    raw:      dict,
    note:     MeetingNote,
    min_conf: float,
    fu_conf:  float,
) -> Optional[ExtractedItem]:
    """Normalisoi yhden Claude-vastauksen kohdan."""
    if not isinstance(raw, dict):
        return None

    item_type  = str(raw.get("item_type", "action_item")).strip()
    title      = str(raw.get("title", "")).strip()
    confidence = float(raw.get("confidence", 0.0))

    if not title:
        return None
    if item_type not in ("action_item", "decision", "follow_up"):
        item_type = "action_item"

    # Due date normalisointi
    due_date = None
    raw_date = raw.get("due_date_normalized")
    if raw_date:
        try:
            due_date = date.fromisoformat(str(raw_date))
        except ValueError:
            pass

    # Sovella confidence-kynnyksiä jos Claude ei sitä tehnyt
    should_create = bool(raw.get("should_create_task", False))
    reason        = raw.get("reason_if_not_created") or None

    if should_create:
        if item_type == "action_item" and confidence < min_conf:
            should_create = False
            reason = f"Confidence {confidence:.2f} alle rajan {min_conf}"
        elif item_type == "follow_up" and confidence < fu_conf:
            should_create = False
            reason = f"Confidence {confidence:.2f} alle follow-up-rajan {fu_conf}"
        elif item_type == "decision":
            should_create = False
            reason = "Päätös ilman selkeää toimenpidettä"

    return ExtractedItem(
        item_type=item_type,
        title=title[:80],
        description=str(raw.get("description", "")).strip(),
        owner=raw.get("owner") or None,
        due_hint=raw.get("due_hint") or None,
        due_date_normalized=due_date,
        source_quote=str(raw.get("source_quote", ""))[:300].strip(),
        confidence=confidence,
        should_create_task=should_create,
        reason_if_not_created=reason,
    )


def _extract_json_block(text: str) -> Optional[str]:
    """Etsii JSON-lohkon mahdollisesta markdown code blockista tai suoraan."""
    # ```json ... ```
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        return m.group(1)
    # Suora JSON-objekti
    m = re.search(r"(\{.*\})", text, re.DOTALL)
    if m:
        return m.group(1)
    return None


# ── Sääntöpohjainen varamenetelmä ─────────────────────────────────────────────

_ACTION_MARKERS = re.compile(
    r"^[\s\-\*•]*(?:\[[ x]\]|TODO:|ACTION:|Tehtävä:|Vastuut?:|action item[s]?:?)\s*",
    re.IGNORECASE | re.MULTILINE,
)

_SECTION_HEADERS = re.compile(
    r"^(action items?|tehtävät|vastuut|todo[s]?|follow[\s-]?ups?|jatkotoimet)[:\s]*$",
    re.IGNORECASE,
)

_CHECKBOX = re.compile(r"^\s*[-*•]?\s*\[[ ]\]\s*(.+)", re.IGNORECASE)

_OWNER_PATTERNS = [
    re.compile(r"^([A-ZÄÖÅ][a-zäöå]+(?:\s[A-ZÄÖÅ][a-zäöå]+)?)\s*:(.+)"),
    re.compile(
        r"^([A-ZÄÖÅ][a-zäöå]+(?:\s[A-ZÄÖÅ][a-zäöå]+)?)\s+"
        r"(tekee|tarkistaa|selvittää|lähettää|sopii|varaa|ottaa|kysyy|päivittää|will|should|needs to)\b",
        re.IGNORECASE
    ),
]

_DUE_HINTS = [
    (re.compile(r"\b(maanantai(?:hin)?|monday)\b", re.I), 0),
    (re.compile(r"\b(tiistai(?:hin)?|tuesday)\b", re.I),  1),
    (re.compile(r"\b(keskiviikko(?:on)?|wednesday)\b", re.I), 2),
    (re.compile(r"\b(torstai(?:hin)?|thursday)\b", re.I), 3),
    (re.compile(r"\b(perjantai(?:hin)?|friday)\b", re.I), 4),
    (re.compile(r"\b(huominen|huomiseen|tomorrow)\b", re.I), None),  # erityiskäsittely
    (re.compile(r"\b(ensi viikolla|next week)\b", re.I),   None),
]


def _extract_rule_based(
    note:     MeetingNote,
    min_conf: float,
    fu_conf:  float,
) -> ExtractionResult:
    """Sääntöpohjainen extraction — käytetään kun Claude ei ole saatavilla.

    Löytää:
    - Eksplisiittiset checkbox-kohdat ([ ])
    - Otsikko-osioiden (Action items, Tehtävät…) alta löytyvät rivit
    - Nimetyt vastuut (Matti: tarkistaa...)
    """
    items: list[ExtractedItem] = []
    lines = note.content.splitlines()

    in_action_section = False

    for i, raw_line in enumerate(lines):
        line = raw_line.strip()
        if not line:
            continue

        # Otsikkosektioiden tunnistus
        if _SECTION_HEADERS.match(line):
            in_action_section = True
            continue

        # Uusi otsikko (## tai ===) lopettaa action-sektioin
        if re.match(r"^(#{1,3}|\={3,}|\-{3,})", line) and not in_action_section:
            in_action_section = False

        # Checkbox-kohta
        m = _CHECKBOX.match(raw_line)
        if m:
            task_text = m.group(1).strip()
            item = _make_rule_item(task_text, raw_line, note, 0.8, "action_item", min_conf, fu_conf)
            items.append(item)
            continue

        # Bullet-kohta action-sektiossa
        if in_action_section and re.match(r"^\s*[-*•]\s+", raw_line):
            task_text = re.sub(r"^\s*[-*•]\s+", "", raw_line).strip()
            if task_text and len(task_text) > 5:
                item = _make_rule_item(task_text, raw_line, note, 0.7, "action_item", min_conf, fu_conf)
                items.append(item)
                continue

        # Nimetty vastuu (Matti: tekee jotain)
        for pat in _OWNER_PATTERNS:
            m = pat.match(line)
            if m:
                task_text = line
                item = _make_rule_item(task_text, raw_line, note, 0.75, "action_item", min_conf, fu_conf)
                items.append(item)
                break

    _set_fingerprints(items, note.source_id)
    log.info(
        f"Sääntöpohjainen extraction: {len(items)} kohtaa "
        f"({sum(1 for it in items if it.should_create_task)} tehtäväksi)"
    )
    return ExtractionResult(
        meeting_note=note,
        items=items,
        extraction_method="rule_based",
        model_used=None,
        raw_response=None,
    )


def _make_rule_item(
    task_text: str,
    source_quote: str,
    note: MeetingNote,
    confidence: float,
    item_type: str,
    min_conf: float,
    fu_conf: float,
) -> ExtractedItem:
    """Muodostaa ExtractedItem-olion sääntöpohjaiselle kohdalle."""
    # Yritä erottaa omistaja
    owner = None
    title = task_text
    for pat in _OWNER_PATTERNS:
        m = pat.match(task_text)
        if m:
            owner = m.group(1).strip()
            title = task_text[len(owner):].lstrip(": ").strip()
            break

    # Normalisoi otsikko
    title = title[:80].strip()
    if not title:
        title = task_text[:80]

    # Due date -vihje
    due_hint, due_date = _extract_due_hint(task_text, note.meeting_date)

    # Päätä luodaanko tehtävä
    should_create = confidence >= (min_conf if item_type == "action_item" else fu_conf)
    reason = None if should_create else f"Confidence {confidence:.2f} alle rajan"

    return ExtractedItem(
        item_type=item_type,
        title=title,
        description="",
        owner=owner,
        due_hint=due_hint,
        due_date_normalized=due_date,
        source_quote=source_quote.strip()[:300],
        confidence=confidence,
        should_create_task=should_create,
        reason_if_not_created=reason,
    )


def _extract_due_hint(text: str, meeting_date: Optional[date]) -> tuple[Optional[str], Optional[date]]:
    """Etsii deadline-vihjeen tekstistä ja normalisoi päivämääräksi."""
    for pattern, weekday in _DUE_HINTS:
        m = pattern.search(text)
        if not m:
            continue
        hint = m.group(0)
        if meeting_date is None:
            return hint, None
        if hint.lower() in ("huominen", "huomiseen", "tomorrow"):
            return hint, meeting_date + timedelta(days=1)
        if hint.lower() in ("ensi viikolla", "next week"):
            days_ahead = 7 - meeting_date.weekday()
            return hint, meeting_date + timedelta(days=days_ahead)
        if weekday is not None:
            days_until = (weekday - meeting_date.weekday()) % 7
            if days_until == 0:
                days_until = 7
            return hint, meeting_date + timedelta(days=days_until)
    return None, None


# ── Fingerprint ───────────────────────────────────────────────────────────────

def _set_fingerprints(items: list[ExtractedItem], source_id: str) -> None:
    """Laskee deterministisen fingerprintin jokaiselle kohdalle.

    Fingerprint = SHA256(source_id + "::" + source_quote[:200].lower().strip())

    Sama muistio + sama lainaus = sama fingerprint jokaisella ajolla.
    Mahdollistaa duplikaattisuojauksen uusinta-ajoissa.
    """
    for item in items:
        key = f"{source_id}::{item.source_quote[:200].lower().strip()}"
        item.fingerprint = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
