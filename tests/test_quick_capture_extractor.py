"""
tests/test_quick_capture_extractor.py — Pikasyöttö extraction -testit

Testaa sekä Claude-vastauksen parsimisen että sääntöpohjaisen
varamenetelmän. Claude-kutsut mockataan — ei oikeaa API-kutsua.
"""

import json
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

from quick_capture_extractor import (
    CATEGORY_LIST_MAP,
    CaptureExtraction,
    _end_of_month,
    _extract_rule_based,
    _next_weekday,
    _normalize,
    _parse_due_hint,
    _parse_json,
    extract,
)

TODAY = date(2025, 4, 7)   # Maanantai


# ── _parse_json ────────────────────────────────────────────────────────────────

class TestParseJson:
    def test_plain_json(self):
        raw = '{"title": "Testi", "priority": 2}'
        assert _parse_json(raw)["title"] == "Testi"

    def test_json_in_code_block(self):
        raw = '```json\n{"title": "Testi"}\n```'
        assert _parse_json(raw)["title"] == "Testi"

    def test_json_in_plain_code_block(self):
        raw = '```\n{"title": "Testi"}\n```'
        assert _parse_json(raw)["title"] == "Testi"

    def test_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            _parse_json("ei ole jsonia")


# ── _normalize ─────────────────────────────────────────────────────────────────

class TestNormalize:
    def _base(self, overrides=None) -> dict:
        base = {
            "title": "Ota yhteyttä toimittajaan",
            "description": "Kysy toimitusajat",
            "category": "suppliers",
            "assignee_name": "Jari",
            "priority": 2,
            "due_date": "2025-04-11",
            "needs_calendar": True,
            "calendar_duration_minutes": 30,
            "tags": ["toimittajat"],
        }
        if overrides:
            base.update(overrides)
        return base

    def test_basic_fields(self):
        e = _normalize(self._base(), "Testi", "tehtava", TODAY, model="haiku")
        assert e.title == "Ota yhteyttä toimittajaan"
        assert e.category == "suppliers"
        assert e.list_id == CATEGORY_LIST_MAP["suppliers"]
        assert e.priority == 2
        assert e.due_date == date(2025, 4, 11)
        assert e.needs_calendar is True
        assert e.assignee_name == "Jari"
        assert e.extraction_method == "claude"

    def test_unknown_category_falls_back(self):
        e = _normalize(self._base({"category": "tuntematon"}), "T", "tehtava", TODAY, "x")
        assert e.category == "tehtavat"

    def test_idea_type_forces_ideat_when_category_is_tehtavat(self):
        e = _normalize(self._base({"category": "tehtavat"}), "T", "idea", TODAY, "x")
        assert e.category == "ideat"

    def test_idea_type_keeps_specific_category(self):
        e = _normalize(self._base({"category": "marketing"}), "T", "idea", TODAY, "x")
        assert e.category == "marketing"

    def test_no_due_date_clears_needs_calendar(self):
        data = self._base({"due_date": None, "needs_calendar": True})
        e = _normalize(data, "T", "tehtava", TODAY, "x")
        assert e.needs_calendar is False

    def test_title_truncated_to_100(self):
        long_title = "A" * 150
        e = _normalize(self._base({"title": long_title}), "T", "tehtava", TODAY, "x")
        assert len(e.title) == 100

    def test_tags_limited_to_3(self):
        e = _normalize(self._base({"tags": ["a", "b", "c", "d", "e"]}), "T", "tehtava", TODAY, "x")
        assert len(e.tags) == 3

    def test_priority_clamped(self):
        e = _normalize(self._base({"priority": 99}), "T", "tehtava", TODAY, "x")
        assert e.priority == 4
        e2 = _normalize(self._base({"priority": -5}), "T", "tehtava", TODAY, "x")
        assert e2.priority == 1

    def test_invalid_due_date_tries_hint(self):
        # Jos ISO-parsinta epäonnistuu, yritetään luonnollinen kieli
        data = self._base({"due_date": "ensi perjantai"})
        e = _normalize(data, "T", "tehtava", TODAY, "x")
        # Ensi perjantai = 2025-04-11
        assert e.due_date == date(2025, 4, 11)


# ── _parse_due_hint ────────────────────────────────────────────────────────────

class TestParseDueHint:
    def _parse(self, text: str) -> date | None:
        return _parse_due_hint(text, TODAY)

    def test_huomenna(self):
        assert self._parse("tehtävä huomenna") == date(2025, 4, 8)

    def test_tanaan(self):
        assert self._parse("hoida tänään") == TODAY

    def test_ensi_viikolla(self):
        # ensi maanantai
        assert self._parse("ensi viikolla") == date(2025, 4, 14)

    def test_ensi_perjantai(self):
        assert self._parse("ensi perjantaina") == date(2025, 4, 11)

    def test_perjantaihin(self):
        assert self._parse("valmis perjantaihin") == date(2025, 4, 11)

    def test_viikon_sisalla(self):
        assert self._parse("viikon sisällä") == TODAY + timedelta(weeks=1)

    def test_iso_date(self):
        assert self._parse("deadline 2025-05-01") == date(2025, 5, 1)

    def test_no_hint_returns_none(self):
        assert self._parse("tee jotain") is None

    def test_ensi_maanantai(self):
        assert self._parse("ensi maanantaina") == date(2025, 4, 14)


# ── _next_weekday ──────────────────────────────────────────────────────────────

class TestNextWeekday:
    def test_next_friday_from_monday(self):
        # TODAY = maanantai 2025-04-07, perjantai = weekday 4
        assert _next_weekday(TODAY, 4) == date(2025, 4, 11)

    def test_next_monday_from_monday_is_next_week(self):
        # Seuraava maanantai ei ole tänään
        assert _next_weekday(TODAY, 0) == date(2025, 4, 14)

    def test_next_weekday_from_friday(self):
        friday = date(2025, 4, 11)
        # Seuraava maanantai = 2025-04-14
        assert _next_weekday(friday, 0) == date(2025, 4, 14)


# ── _end_of_month ──────────────────────────────────────────────────────────────

class TestEndOfMonth:
    def test_april(self):
        assert _end_of_month(date(2025, 4, 1)) == date(2025, 4, 30)

    def test_december(self):
        assert _end_of_month(date(2025, 12, 1)) == date(2025, 12, 31)

    def test_february_leap(self):
        assert _end_of_month(date(2024, 2, 1)) == date(2024, 2, 29)


# ── _extract_rule_based ────────────────────────────────────────────────────────

class TestExtractRuleBased:
    def test_idea_type_sets_ideat_category(self):
        e = _extract_rule_based("Uusi tuotekategoria veneille", "idea", TODAY)
        assert e.category == "ideat"
        assert e.list_id == CATEGORY_LIST_MAP["ideat"]
        assert e.extraction_method == "fallback"

    def test_tehtava_type_sets_tehtavat_category(self):
        e = _extract_rule_based("Soita Matille", "tehtava", TODAY)
        assert e.category == "tehtavat"

    def test_priority_urgent_keyword(self):
        e = _extract_rule_based("Kiireinen asia hoidettava heti", "tehtava", TODAY)
        assert e.priority == 1

    def test_priority_important_keyword(self):
        e = _extract_rule_based("Tärkeää: päivitä hinnasto", "tehtava", TODAY)
        assert e.priority == 2

    def test_priority_default_normal(self):
        e = _extract_rule_based("Järjestä toimisto", "tehtava", TODAY)
        assert e.priority == 3

    def test_assignee_lle_suffix(self):
        e = _extract_rule_based("Delegoi Matille", "tehtava", TODAY)
        assert e.assignee_name == "Matti"

    def test_assignee_mina_teen(self):
        e = _extract_rule_based("Minä teen tämän huomenna", "tehtava", TODAY)
        assert e.assignee_name == "Jari"

    def test_due_date_from_text(self):
        e = _extract_rule_based("Valmis perjantaihin mennessä", "tehtava", TODAY)
        assert e.due_date == date(2025, 4, 11)

    def test_needs_calendar_when_jari_and_deadline(self):
        e = _extract_rule_based("Minä teen tämän huomenna", "tehtava", TODAY)
        assert e.needs_calendar is True

    def test_no_calendar_without_deadline(self):
        e = _extract_rule_based("Soita jossain vaiheessa", "tehtava", TODAY)
        assert e.needs_calendar is False

    def test_title_from_text(self):
        text = "Selvitä uudet toimittajahinnat"
        e = _extract_rule_based(text, "tehtava", TODAY)
        assert e.title == text

    def test_long_text_truncated(self):
        long = "A " * 60
        e = _extract_rule_based(long, "tehtava", TODAY)
        assert len(e.title) <= 80


# ── extract() — mocked Claude ─────────────────────────────────────────────────

class TestExtractTopLevel:
    def _mock_claude_response(self, data: dict):
        """Rakentaa mock Anthropic-vastauksen."""
        mock_content = MagicMock()
        mock_content.text = json.dumps(data)
        mock_response = MagicMock()
        mock_response.content = [mock_content]
        return mock_response

    def test_uses_claude_when_api_key_present(self):
        claude_data = {
            "title": "Tilaa tarvikkeet",
            "description": "Q2 hankinta",
            "category": "suppliers",
            "assignee_name": "Jari",
            "priority": 2,
            "due_date": "2025-04-15",
            "needs_calendar": True,
            "calendar_duration_minutes": 30,
            "tags": ["hankinta"],
        }
        with patch("config.ANTHROPIC_API_KEY", "test-key"), \
             patch("anthropic.Anthropic") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client
            mock_client.messages.create.return_value = self._mock_claude_response(claude_data)

            e = extract("Tilaa tarvikkeet ensi viikolla", today=TODAY)

        assert e.title == "Tilaa tarvikkeet"
        assert e.category == "suppliers"
        assert e.extraction_method == "claude"

    def test_falls_back_when_no_api_key(self):
        with patch("config.ANTHROPIC_API_KEY", ""):
            e = extract("Soita Matille", today=TODAY)
        assert e.extraction_method == "fallback"

    def test_falls_back_on_claude_error(self):
        with patch("config.ANTHROPIC_API_KEY", "test-key"), \
             patch("anthropic.Anthropic") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client
            mock_client.messages.create.side_effect = Exception("API virhe")

            e = extract("Soita Matille", today=TODAY)

        assert e.extraction_method == "fallback"

    def test_idea_type_routed_to_ideat(self):
        with patch("config.ANTHROPIC_API_KEY", ""):
            e = extract("Kokeile subscription-mallia", capture_type="idea", today=TODAY)
        assert e.category == "ideat"

    def test_full_finnish_task(self):
        """Integrointitesti sääntöpohjaiselle: tyypillinen suomenkielinen sanelu."""
        text = (
            "Matille tehtävä: selvitä uusien toimittajien hinnat ja toimitusajat "
            "ensi perjantaihin mennessä, tärkeää"
        )
        with patch("config.ANTHROPIC_API_KEY", ""):
            e = extract(text, capture_type="tehtava", today=TODAY)

        assert e.extraction_method == "fallback"
        assert e.due_date == date(2025, 4, 11)
        assert e.priority == 2  # tärkeää
        assert e.assignee_name == "Matti"

    def test_all_categories_have_list_id(self):
        """Kaikilla kategorioilla on olemassa lista-ID."""
        for cat, list_id in CATEGORY_LIST_MAP.items():
            assert list_id, f"Kategorialla '{cat}' puuttuu list_id"
            assert list_id.isdigit(), f"list_id ei ole numero: {list_id}"
