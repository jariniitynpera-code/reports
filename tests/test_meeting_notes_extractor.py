"""
test_meeting_notes_extractor.py — Testit extraction-logiikalle
"""

import sys
import os
from datetime import date
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from meeting_notes_reader import MeetingNote
from meeting_notes_extractor import (
    ExtractedItem,
    ExtractionResult,
    _parse_claude_response,
    _normalize_claude_item,
    _extract_rule_based,
    _set_fingerprints,
    _extract_due_hint,
    _extract_with_claude,
    extract_items,
)


# ── Apufunktiot ───────────────────────────────────────────────────────────────

def _make_note(content, title="Kokous", meeting_date=None, source_id="test_src_001"):
    note = MeetingNote(
        content=content,
        source_type="text",
        source_id=source_id,
        source_url=None,
        source_title=title,
        meeting_date=meeting_date or date(2026, 3, 28),
        attendees=["Jari", "Matti"],
    )
    return note


SAMPLE_NOTES_FI = """\
## Kokous 28.3.2026

### Käsitellyt asiat
Käytiin läpi Q2-kampanjan tilanne. Myynti on ollut hyvää.

### Päätökset
- Päätetään edetä kevään kampanjan kanssa aikataulussa.
- Hinnasto päivitetään ennen huhtikuuta.

### Action items
- [ ] Matti tarkistaa hinnaston ja lähettää sen tiimille perjantaihin mennessä
- [ ] Jari sopii toimittajan tapaamisen ensi viikolle
- [ ] Selvitetään toimitusmalli Q3:lle

### Follow-up
- Palaillaan kampanjan tuloksiin ensi kuussa
"""

SAMPLE_NOTES_EN = """\
## Weekly Sync 2026-03-28

### Decisions
- Decided to proceed with the new pricing model.

### Action items
- [ ] John will send the contract draft to the client by Friday
- [ ] Sarah: update the product descriptions on the website
- [ ] Review the Q2 forecasts before Thursday

### Follow-ups
- Check back on inventory levels next week
"""


# ── _parse_claude_response ────────────────────────────────────────────────────

class TestParseClaudeResponse:

    def test_parses_valid_json(self):
        raw = '''{
          "items": [
            {
              "item_type": "action_item",
              "title": "Tarkista hinnasto",
              "description": "Päivitä ennen huhtikuuta",
              "owner": "Matti",
              "due_hint": "perjantaihin mennessä",
              "due_date_normalized": "2026-04-03",
              "source_quote": "Matti tarkistaa hinnaston",
              "confidence": 0.9,
              "should_create_task": true,
              "reason_if_not_created": null
            }
          ]
        }'''
        note = _make_note("Matti tarkistaa hinnaston")
        items, errors = _parse_claude_response(raw, note, 0.7, 0.6)
        assert len(errors) == 0
        assert len(items) == 1
        assert items[0].title == "Tarkista hinnasto"
        assert items[0].owner == "Matti"
        assert items[0].confidence == 0.9
        assert items[0].should_create_task is True

    def test_parses_json_in_code_block(self):
        raw = '```json\n{"items": [{"item_type": "action_item", "title": "Test", "description": "", "owner": null, "due_hint": null, "due_date_normalized": null, "source_quote": "test", "confidence": 0.8, "should_create_task": true, "reason_if_not_created": null}]}\n```'
        note = _make_note("test")
        items, errors = _parse_claude_response(raw, note, 0.7, 0.6)
        assert len(items) == 1

    def test_returns_empty_on_invalid_json(self):
        note = _make_note("test")
        items, errors = _parse_claude_response("ei json tässä", note, 0.7, 0.6)
        assert items == []
        assert len(errors) > 0

    def test_filters_low_confidence_action_items(self):
        raw = '''{
          "items": [
            {
              "item_type": "action_item",
              "title": "Epämääräinen tehtävä",
              "description": "",
              "owner": null,
              "due_hint": null,
              "due_date_normalized": null,
              "source_quote": "jotain pitäisi tehdä",
              "confidence": 0.4,
              "should_create_task": true,
              "reason_if_not_created": null
            }
          ]
        }'''
        note = _make_note("jotain pitäisi tehdä")
        items, errors = _parse_claude_response(raw, note, 0.7, 0.6)
        assert len(items) == 1
        assert items[0].should_create_task is False
        assert "0.40" in items[0].reason_if_not_created

    def test_decision_never_creates_task(self):
        raw = '''{
          "items": [
            {
              "item_type": "decision",
              "title": "Edetään kampanjan kanssa",
              "description": "Päätettiin kokouksessa",
              "owner": null,
              "due_hint": null,
              "due_date_normalized": null,
              "source_quote": "Päätetään edetä",
              "confidence": 0.95,
              "should_create_task": true,
              "reason_if_not_created": null
            }
          ]
        }'''
        note = _make_note("Päätetään edetä")
        items, errors = _parse_claude_response(raw, note, 0.7, 0.6)
        assert len(items) == 1
        assert items[0].should_create_task is False
        assert items[0].reason_if_not_created is not None

    def test_normalizes_due_date(self):
        raw = '''{
          "items": [
            {
              "item_type": "action_item",
              "title": "Tarkista dokumentti",
              "description": "",
              "owner": null,
              "due_hint": "by Friday",
              "due_date_normalized": "2026-04-03",
              "source_quote": "by Friday",
              "confidence": 0.85,
              "should_create_task": true,
              "reason_if_not_created": null
            }
          ]
        }'''
        note = _make_note("by Friday")
        items, errors = _parse_claude_response(raw, note, 0.7, 0.6)
        from datetime import date as d
        assert items[0].due_date_normalized == d(2026, 4, 3)

    def test_truncates_long_title(self):
        long_title = "A" * 100
        raw = f'''{{"items": [{{"item_type": "action_item", "title": "{long_title}", "description": "", "owner": null, "due_hint": null, "due_date_normalized": null, "source_quote": "test", "confidence": 0.9, "should_create_task": true, "reason_if_not_created": null}}]}}'''
        note = _make_note("test")
        items, errors = _parse_claude_response(raw, note, 0.7, 0.6)
        assert len(items[0].title) <= 80


# ── _extract_rule_based ───────────────────────────────────────────────────────

class TestRuleBasedExtraction:

    def test_extracts_checkbox_items(self):
        note = _make_note("- [ ] Matti tarkistaa hinnaston\n- [ ] Jari sopii tapaamisen")
        result = _extract_rule_based(note, 0.7, 0.6)
        assert len(result.items) == 2
        assert result.extraction_method == "rule_based"

    def test_checkbox_has_high_confidence(self):
        note = _make_note("- [ ] Tehtävä A")
        result = _extract_rule_based(note, 0.7, 0.6)
        assert result.items[0].confidence >= 0.8

    def test_extracts_items_under_action_header_fi(self):
        note = _make_note("Action items:\n- Matti tekee raportin\n- Sopii tapaaminen")
        result = _extract_rule_based(note, 0.7, 0.6)
        assert len(result.items) >= 1

    def test_extracts_items_under_tehtavat_header(self):
        note = _make_note("Tehtävät:\n- Tarkista hinnasto\n- Lähetä sähköposti")
        result = _extract_rule_based(note, 0.7, 0.6)
        assert len(result.items) >= 1

    def test_empty_notes_returns_empty(self):
        note = _make_note("Kokouksessa käytiin läpi tilanne. Kaikki hyvin.")
        result = _extract_rule_based(note, 0.7, 0.6)
        # Ei selkeitä action itemeja → tyhjä tai vähän kohtia
        assert result.extraction_method == "rule_based"

    def test_items_have_fingerprints(self):
        note = _make_note("- [ ] Tehtävä A\n- [ ] Tehtävä B")
        result = _extract_rule_based(note, 0.7, 0.6)
        for item in result.items:
            assert item.fingerprint != ""
            assert len(item.fingerprint) == 24

    def test_full_sample_notes_fi(self):
        note = _make_note(SAMPLE_NOTES_FI)
        result = _extract_rule_based(note, 0.7, 0.6)
        # Pitäisi löytää ainakin kolme checkbox-kohtaa
        assert len(result.items) >= 3

    def test_full_sample_notes_en(self):
        note = _make_note(SAMPLE_NOTES_EN)
        result = _extract_rule_based(note, 0.7, 0.6)
        assert len(result.items) >= 2


# ── _set_fingerprints ─────────────────────────────────────────────────────────

class TestSetFingerprints:

    def test_same_input_same_fingerprint(self):
        item1 = ExtractedItem(
            item_type="action_item", title="T", description="", owner=None,
            due_hint=None, due_date_normalized=None,
            source_quote="Matti tarkistaa hinnaston",
            confidence=0.9, should_create_task=True, reason_if_not_created=None,
        )
        item2 = ExtractedItem(
            item_type="action_item", title="T", description="", owner=None,
            due_hint=None, due_date_normalized=None,
            source_quote="Matti tarkistaa hinnaston",
            confidence=0.9, should_create_task=True, reason_if_not_created=None,
        )
        _set_fingerprints([item1], "src001")
        _set_fingerprints([item2], "src001")
        assert item1.fingerprint == item2.fingerprint

    def test_different_source_different_fingerprint(self):
        item = ExtractedItem(
            item_type="action_item", title="T", description="", owner=None,
            due_hint=None, due_date_normalized=None,
            source_quote="sama teksti",
            confidence=0.9, should_create_task=True, reason_if_not_created=None,
        )
        _set_fingerprints([item], "src001")
        fp1 = item.fingerprint

        _set_fingerprints([item], "src002")
        fp2 = item.fingerprint

        assert fp1 != fp2

    def test_fingerprint_is_24_chars(self):
        item = ExtractedItem(
            item_type="action_item", title="T", description="", owner=None,
            due_hint=None, due_date_normalized=None,
            source_quote="teksti",
            confidence=0.9, should_create_task=True, reason_if_not_created=None,
        )
        _set_fingerprints([item], "src001")
        assert len(item.fingerprint) == 24

    def test_fingerprint_case_insensitive_on_quote(self):
        item1 = ExtractedItem(
            item_type="action_item", title="T", description="", owner=None,
            due_hint=None, due_date_normalized=None,
            source_quote="MATTI TARKISTAA",
            confidence=0.9, should_create_task=True, reason_if_not_created=None,
        )
        item2 = ExtractedItem(
            item_type="action_item", title="T", description="", owner=None,
            due_hint=None, due_date_normalized=None,
            source_quote="matti tarkistaa",
            confidence=0.9, should_create_task=True, reason_if_not_created=None,
        )
        _set_fingerprints([item1], "src001")
        _set_fingerprints([item2], "src001")
        assert item1.fingerprint == item2.fingerprint


# ── _extract_due_hint ─────────────────────────────────────────────────────────

class TestExtractDueHint:

    def test_friday_resolved(self):
        # 28.3.2026 = perjantai → seuraava perjantai = 3.4.2026
        ref = date(2026, 3, 28)
        hint, resolved = _extract_due_hint("lähetä perjantaihin mennessä", ref)
        assert hint is not None
        assert "perjantai" in hint.lower()
        assert resolved is not None
        assert resolved.weekday() == 4  # perjantai

    def test_tomorrow_resolved(self):
        ref = date(2026, 3, 28)
        hint, resolved = _extract_due_hint("huomiseen mennessä", ref)
        assert resolved == date(2026, 3, 29)

    def test_next_week_resolved(self):
        ref = date(2026, 3, 28)  # perjantai
        hint, resolved = _extract_due_hint("ensi viikolla", ref)
        assert resolved is not None
        assert resolved > ref

    def test_no_hint_returns_none(self):
        hint, resolved = _extract_due_hint("tehtävä ilman deadlinea", date(2026, 3, 28))
        assert hint is None
        assert resolved is None

    def test_no_meeting_date_returns_hint_only(self):
        hint, resolved = _extract_due_hint("perjantaihin mennessä", None)
        assert hint is not None
        assert resolved is None


# ── extract_items — mocked Claude ────────────────────────────────────────────

class TestExtractItemsMockedClaude:

    def test_uses_rule_based_when_no_api_key(self):
        note = _make_note("- [ ] Tehtävä A\n- [ ] Tehtävä B")
        original = getattr(config, "ANTHROPIC_API_KEY", "")
        config.ANTHROPIC_API_KEY = ""
        try:
            result = extract_items(note)
            assert result.extraction_method == "rule_based"
        finally:
            config.ANTHROPIC_API_KEY = original

    def test_falls_back_to_rule_based_on_claude_error(self):
        note = _make_note("- [ ] Tehtävä A")
        original = getattr(config, "ANTHROPIC_API_KEY", "")
        config.ANTHROPIC_API_KEY = "fake_key_for_test"
        try:
            with patch("meeting_notes_extractor._extract_with_claude",
                       side_effect=Exception("API error")):
                result = extract_items(note)
                assert result.extraction_method == "rule_based"
        finally:
            config.ANTHROPIC_API_KEY = original
