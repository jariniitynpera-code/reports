"""
test_meeting_notes_tasks.py — Testit tehtäväluontilogiikalle
"""

import sys
import os
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from meeting_notes_reader import MeetingNote
from meeting_notes_extractor import ExtractedItem, ExtractionResult
from meeting_notes_tasks import (
    TaskResult,
    publish_extraction,
    format_task_description,
    _compute_task_tags,
    _item_to_priority,
    _build_task_payload,
)


# ── Apufunktiot ───────────────────────────────────────────────────────────────

def _make_note(title="Kokous 28.3.2026"):
    return MeetingNote(
        content="Matti tarkistaa hinnaston.",
        source_type="text",
        source_id="test_src_001",
        source_url="https://docs.google.com/d/fake",
        source_title=title,
        meeting_date=date(2026, 3, 28),
        attendees=["Jari", "Matti"],
    )


def _make_item(
    item_type="action_item",
    title="Tarkista hinnasto",
    owner="Matti",
    confidence=0.9,
    should_create_task=True,
    due_date=None,
    source_quote="Matti tarkistaa hinnaston",
    fingerprint="fp_abc123456789012",
):
    return ExtractedItem(
        item_type=item_type,
        title=title,
        description="Päivitettävä ennen huhtikuuta.",
        owner=owner,
        due_hint="perjantaihin mennessä" if due_date else None,
        due_date_normalized=due_date,
        source_quote=source_quote,
        confidence=confidence,
        should_create_task=should_create_task,
        reason_if_not_created=None if should_create_task else "Low confidence",
        fingerprint=fingerprint,
    )


def _make_extraction(items, note=None):
    return ExtractionResult(
        meeting_note=note or _make_note(),
        items=items,
        extraction_method="claude",
        model_used="claude-haiku-4-5-20251001",
        raw_response=None,
    )


def _mock_clickup():
    cu = MagicMock()
    cu._request.return_value = {
        "id":  "task_clickup_001",
        "url": "https://app.clickup.com/t/task_clickup_001",
    }
    return cu


# ── format_task_description ───────────────────────────────────────────────────

class TestFormatTaskDescription:

    def test_includes_meeting_title(self):
        item = _make_item()
        note = _make_note("Q1 Strategiakokous")
        desc = format_task_description(item, note)
        assert "Q1 Strategiakokous" in desc

    def test_includes_meeting_date(self):
        item = _make_item()
        note = _make_note()
        desc = format_task_description(item, note)
        assert "28.3.2026" in desc

    def test_includes_source_url(self):
        item = _make_item()
        note = _make_note()
        desc = format_task_description(item, note)
        assert "docs.google.com" in desc

    def test_includes_owner(self):
        item = _make_item(owner="Matti")
        desc = format_task_description(item, _make_note())
        assert "Matti" in desc

    def test_includes_due_hint(self):
        item = _make_item(due_date=date(2026, 4, 3))
        desc = format_task_description(item, _make_note())
        assert "perjantaihin mennessä" in desc

    def test_includes_source_quote(self):
        item = _make_item(source_quote="Matti tarkistaa hinnaston perjantaihin")
        desc = format_task_description(item, _make_note())
        assert "Matti tarkistaa hinnaston perjantaihin" in desc

    def test_includes_confidence_label(self):
        item = _make_item(confidence=0.9)
        desc = format_task_description(item, _make_note())
        assert "korkea" in desc or "90%" in desc

    def test_no_owner_no_owner_line(self):
        item = _make_item(owner=None)
        desc = format_task_description(item, _make_note())
        assert "Vastuuhenkilö" not in desc

    def test_no_source_url_no_link(self):
        item = _make_item()
        note = _make_note()
        note.source_url = None
        desc = format_task_description(item, note)
        assert "Muistio:" not in desc


# ── _compute_task_tags ────────────────────────────────────────────────────────

class TestComputeTaskTags:

    def test_always_includes_meeting_notes_tag(self):
        item = _make_item()
        tags = _compute_task_tags(item)
        assert "meeting-notes" in tags

    def test_action_item_has_action_tag(self):
        item = _make_item(item_type="action_item")
        tags = _compute_task_tags(item)
        assert "action-item" in tags

    def test_follow_up_has_followup_tag(self):
        item = _make_item(item_type="follow_up")
        tags = _compute_task_tags(item)
        assert "follow-up" in tags

    def test_decision_has_decision_tag(self):
        item = _make_item(item_type="decision")
        tags = _compute_task_tags(item)
        assert "decision-derived" in tags

    def test_high_confidence_tag(self):
        item = _make_item(confidence=0.9)
        tags = _compute_task_tags(item)
        assert "confidence-high" in tags

    def test_medium_confidence_tag(self):
        item = _make_item(confidence=0.7)
        tags = _compute_task_tags(item)
        assert "confidence-medium" in tags

    def test_low_confidence_tag(self):
        item = _make_item(confidence=0.4)
        tags = _compute_task_tags(item)
        assert "confidence-low" in tags


# ── _item_to_priority ─────────────────────────────────────────────────────────

class TestItemToPriority:

    def test_high_confidence_action_item_is_high_priority(self):
        item = _make_item(item_type="action_item", confidence=0.9)
        assert _item_to_priority(item) == 2  # high

    def test_follow_up_is_normal_priority(self):
        item = _make_item(item_type="follow_up", confidence=0.9)
        assert _item_to_priority(item) == 3  # normal

    def test_low_confidence_action_item_is_normal_priority(self):
        item = _make_item(item_type="action_item", confidence=0.7)
        assert _item_to_priority(item) == 3  # normal


# ── publish_extraction ────────────────────────────────────────────────────────

class TestPublishExtraction:

    def test_dry_run_returns_dry_run_action(self):
        items = [_make_item()]
        extraction = _make_extraction(items)
        cu = _mock_clickup()

        with patch("meeting_notes_tasks.meeting_notes_db") as mock_db:
            mock_db.get_task_by_fingerprint.return_value = None
            results = publish_extraction(extraction, cu, "list999", dry_run=True)

        assert all(r.action == "dry_run" for r in results)
        # Ei pitäisi tehdä API-kutsua
        cu._request.assert_not_called()

    def test_skips_items_without_should_create(self):
        items = [_make_item(should_create_task=False)]
        extraction = _make_extraction(items)
        cu = _mock_clickup()

        with patch("meeting_notes_tasks.meeting_notes_db") as mock_db:
            mock_db.get_task_by_fingerprint.return_value = None
            results = publish_extraction(extraction, cu, "list999")

        assert results[0].action == "skipped"
        cu._request.assert_not_called()

    def test_creates_new_task_when_no_existing(self):
        items = [_make_item()]
        extraction = _make_extraction(items)
        cu = _mock_clickup()

        with patch("meeting_notes_tasks.meeting_notes_db") as mock_db:
            mock_db.get_task_by_fingerprint.return_value = None
            results = publish_extraction(extraction, cu, "list999")

        assert results[0].action == "created"
        assert results[0].task_id == "task_clickup_001"

    def test_updates_existing_task_when_fingerprint_found(self):
        items = [_make_item()]
        extraction = _make_extraction(items)
        cu = _mock_clickup()
        existing = {
            "clickup_task_id": "existing_task_999",
            "clickup_task_url": "https://app.clickup.com/t/999",
        }

        with patch("meeting_notes_tasks.meeting_notes_db") as mock_db:
            mock_db.get_task_by_fingerprint.return_value = existing
            results = publish_extraction(extraction, cu, "list999")

        assert results[0].action == "updated"
        assert results[0].task_id == "existing_task_999"

    def test_create_calls_correct_endpoint(self):
        items = [_make_item()]
        extraction = _make_extraction(items)
        cu = _mock_clickup()

        with patch("meeting_notes_tasks.meeting_notes_db") as mock_db:
            mock_db.get_task_by_fingerprint.return_value = None
            publish_extraction(extraction, cu, "list999")

        call_args = cu._request.call_args
        assert call_args[0][0] == "POST"
        assert "list/list999/task" in call_args[0][1]

    def test_create_payload_includes_title(self):
        items = [_make_item(title="Lähetä sopimusluonnos")]
        extraction = _make_extraction(items)
        cu = _mock_clickup()

        with patch("meeting_notes_tasks.meeting_notes_db") as mock_db:
            mock_db.get_task_by_fingerprint.return_value = None
            publish_extraction(extraction, cu, "list999")

        payload = cu._request.call_args[1]["json"]
        assert payload["name"] == "Lähetä sopimusluonnos"

    def test_create_payload_includes_tags(self):
        items = [_make_item()]
        extraction = _make_extraction(items)
        cu = _mock_clickup()

        with patch("meeting_notes_tasks.meeting_notes_db") as mock_db:
            mock_db.get_task_by_fingerprint.return_value = None
            publish_extraction(extraction, cu, "list999")

        payload = cu._request.call_args[1]["json"]
        assert "meeting-notes" in payload["tags"]

    def test_multiple_items_all_processed(self):
        items = [
            _make_item(title="T1", fingerprint="fp1000000000000000", should_create_task=True),
            _make_item(title="T2", fingerprint="fp2000000000000000", should_create_task=False),
            _make_item(title="T3", fingerprint="fp3000000000000000", should_create_task=True),
        ]
        extraction = _make_extraction(items)
        cu = _mock_clickup()

        with patch("meeting_notes_tasks.meeting_notes_db") as mock_db:
            mock_db.get_task_by_fingerprint.return_value = None
            results = publish_extraction(extraction, cu, "list999")

        assert len(results) == 3
        actions = {r.item.title: r.action for r in results}
        assert actions["T1"] == "created"
        assert actions["T2"] == "skipped"
        assert actions["T3"] == "created"

    def test_error_in_one_item_does_not_crash_others(self):
        items = [
            _make_item(title="T1", fingerprint="fp1000000000000000"),
            _make_item(title="T2", fingerprint="fp2000000000000000"),
        ]
        extraction = _make_extraction(items)
        cu = _mock_clickup()
        # Ensimmäinen kutsu epäonnistuu, toinen onnistuu
        cu._request.side_effect = [Exception("API fail"), {"id": "ok", "url": "url"}]

        with patch("meeting_notes_tasks.meeting_notes_db") as mock_db:
            mock_db.get_task_by_fingerprint.return_value = None
            results = publish_extraction(extraction, cu, "list999")

        actions = [r.action for r in results]
        assert "error" in actions
        assert "created" in actions


# ── _build_task_payload ───────────────────────────────────────────────────────

class TestBuildTaskPayload:

    def test_payload_has_required_fields(self):
        item = _make_item()
        note = _make_note()
        payload = _build_task_payload(item, note)
        assert "name" in payload
        assert "description" in payload
        assert "priority" in payload
        assert "tags" in payload

    def test_due_date_included_as_timestamp(self):
        item = _make_item(due_date=date(2026, 4, 3))
        note = _make_note()
        payload = _build_task_payload(item, note)
        assert "due_date" in payload
        assert isinstance(payload["due_date"], int)
        assert payload["due_date"] > 0

    def test_no_due_date_no_due_date_field(self):
        item = _make_item(due_date=None)
        note = _make_note()
        payload = _build_task_payload(item, note)
        assert "due_date" not in payload
