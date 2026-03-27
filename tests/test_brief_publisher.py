"""
test_brief_publisher.py — Testit brief_publisher-moduulille

Käyttää mock-objekteja ClickUpClientille — ei tee oikeita API-kutsuja.
"""

import sys
import os
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brief_publisher import (
    publish_brief,
    get_brief_task_name,
    _get_approval_status,
    APPROVAL_TAGS,
    DAY_LOAD_TAGS,
)
from brief_logic import BriefResult, DayLoad


# ── Apufunktiot ───────────────────────────────────────────────────────────────

def _make_brief(brief_date=None, day_load=DayLoad.NORMAL):
    d = brief_date or date(2026, 3, 30)
    return BriefResult(
        brief_date=d,
        generated_at=datetime.now(timezone.utc),
        day_load=day_load,
        day_load_label="Normaali",
        key_items=["T1"],
        meetings=[],
        transition_warnings=[],
        start_task=None,
        start_task_url=None,
        status_note="Tasainen päivä.",
        brief_text="## Huominen\n\nTestibriiffi.",
        selected_tasks=[],
        source_summary={"events_count": 0, "tasks_fetched": 0},
    )


def _make_task_with_tags(*tag_names):
    return {
        "id":   "abc123",
        "url":  "https://app.clickup.com/t/abc123",
        "tags": [{"name": t} for t in tag_names],
    }


def _mock_clickup(existing_task=None):
    cu = MagicMock()
    cu.find_task_by_name.return_value = existing_task
    cu._request.return_value = {
        "id":   "new123",
        "url":  "https://app.clickup.com/t/new123",
        "tags": [],
    }
    return cu


# ── get_brief_task_name ───────────────────────────────────────────────────────

class TestGetBriefTaskName:

    def test_format(self):
        name = get_brief_task_name(date(2026, 3, 30))
        assert name == "Huomisen briiffi 2026-03-30"

    def test_different_date(self):
        name = get_brief_task_name(date(2026, 12, 1))
        assert name == "Huomisen briiffi 2026-12-01"


# ── _get_approval_status ──────────────────────────────────────────────────────

class TestGetApprovalStatus:

    def test_suggested_tag(self):
        task = _make_task_with_tags("brief-suggested")
        assert _get_approval_status(task) == "suggested"

    def test_approved_tag(self):
        task = _make_task_with_tags("brief-approved")
        assert _get_approval_status(task) == "approved"

    def test_edited_tag(self):
        task = _make_task_with_tags("brief-edited")
        assert _get_approval_status(task) == "edited"

    def test_rejected_tag(self):
        task = _make_task_with_tags("brief-rejected")
        assert _get_approval_status(task) == "rejected"

    def test_no_approval_tag_defaults_to_suggested(self):
        task = _make_task_with_tags("daily-brief", "brief-normal")
        assert _get_approval_status(task) == "suggested"

    def test_empty_tags_defaults_to_suggested(self):
        task = {"id": "x", "tags": []}
        assert _get_approval_status(task) == "suggested"

    def test_multiple_tags_first_match_wins(self):
        # Vain yksi approval-tagi pitäisi olla, mutta varmistetaan toimivuus
        task = _make_task_with_tags("brief-suggested", "brief-normal", "daily-brief")
        assert _get_approval_status(task) == "suggested"


# ── publish_brief — create ────────────────────────────────────────────────────

class TestPublishBriefCreate:

    def test_creates_when_no_existing(self):
        cu    = _mock_clickup(existing_task=None)
        brief = _make_brief()
        task_id, task_url, action = publish_brief(brief, cu, "list999")
        assert action == "created"
        assert task_id == "new123"

    def test_create_posts_to_correct_list(self):
        cu    = _mock_clickup(existing_task=None)
        brief = _make_brief()
        publish_brief(brief, cu, "list999")
        call_args = cu._request.call_args
        assert "list/list999/task" in call_args[0][1]

    def test_create_includes_suggested_tag(self):
        cu    = _mock_clickup(existing_task=None)
        brief = _make_brief()
        publish_brief(brief, cu, "list999")
        payload = cu._request.call_args[1]["json"]
        assert "brief-suggested" in payload["tags"]

    def test_create_includes_day_load_tag(self):
        cu    = _mock_clickup(existing_task=None)
        brief = _make_brief(day_load=DayLoad.TIGHT)
        publish_brief(brief, cu, "list999")
        payload = cu._request.call_args[1]["json"]
        assert "brief-tight" in payload["tags"]

    def test_create_includes_brief_text(self):
        cu    = _mock_clickup(existing_task=None)
        brief = _make_brief()
        publish_brief(brief, cu, "list999")
        payload = cu._request.call_args[1]["json"]
        assert brief.brief_text in payload["description"]


# ── publish_brief — skip (approved/edited) ────────────────────────────────────

class TestPublishBriefSkip:

    def test_skip_if_approved(self):
        existing = _make_task_with_tags("brief-approved")
        cu       = _mock_clickup(existing_task=existing)
        _, _, action = publish_brief(_make_brief(), cu, "list999")
        assert action == "skipped"

    def test_skip_if_edited(self):
        existing = _make_task_with_tags("brief-edited")
        cu       = _mock_clickup(existing_task=existing)
        _, _, action = publish_brief(_make_brief(), cu, "list999")
        assert action == "skipped"

    def test_skip_does_not_call_put(self):
        existing = _make_task_with_tags("brief-approved")
        cu       = _mock_clickup(existing_task=existing)
        publish_brief(_make_brief(), cu, "list999")
        # Ei pitäisi tehdä PUT-kutsua
        put_calls = [c for c in cu._request.call_args_list if c[0][0] == "PUT"]
        assert len(put_calls) == 0

    def test_skip_returns_existing_task_id(self):
        existing = _make_task_with_tags("brief-approved")
        cu       = _mock_clickup(existing_task=existing)
        task_id, _, _ = publish_brief(_make_brief(), cu, "list999")
        assert task_id == "abc123"


# ── publish_brief — update (suggested/rejected) ───────────────────────────────

class TestPublishBriefUpdate:

    def test_update_if_suggested(self):
        existing = _make_task_with_tags("brief-suggested")
        cu       = _mock_clickup(existing_task=existing)
        # _update tarvitsee GET-kutsun tagien poistoon
        cu._request.return_value = {"tags": [{"name": "brief-suggested"}]}
        _, _, action = publish_brief(_make_brief(), cu, "list999")
        assert action == "updated"

    def test_update_if_rejected(self):
        existing = _make_task_with_tags("brief-rejected")
        cu       = _mock_clickup(existing_task=existing)
        cu._request.return_value = {"tags": [{"name": "brief-rejected"}]}
        _, _, action = publish_brief(_make_brief(), cu, "list999")
        assert action == "updated"

    def test_update_calls_put(self):
        existing = _make_task_with_tags("brief-suggested")
        cu       = _mock_clickup(existing_task=existing)
        cu._request.return_value = {"tags": []}
        publish_brief(_make_brief(), cu, "list999")
        put_calls = [c for c in cu._request.call_args_list if c[0][0] == "PUT"]
        assert len(put_calls) >= 1

    def test_update_returns_existing_task_id(self):
        existing = _make_task_with_tags("brief-suggested")
        cu       = _mock_clickup(existing_task=existing)
        cu._request.return_value = {"tags": []}
        task_id, _, _ = publish_brief(_make_brief(), cu, "list999")
        assert task_id == "abc123"
