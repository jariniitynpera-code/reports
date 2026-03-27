"""
test_brief_logic.py — Testit briiffin valinta- ja generointilogiikalle
"""

import sys
import os
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brief_logic import (
    classify_day_load, select_tasks, select_start_task,
    generate_brief, DayLoad, ShopifySignals, BriefResult,
    _effective_max_tasks, _build_key_items, _build_status_note,
)
from brief_calendar import CalendarEvent
from brief_tasks import BriefTask


# ── Apufunktiot ───────────────────────────────────────────────────────────────

def _make_event(title, hour_start, hour_end, location="", all_day=False):
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo
    tz = ZoneInfo("Europe/Helsinki")
    d  = date(2026, 3, 30)
    if all_day:
        return CalendarEvent(title=title, start=None, end=None,
                             location=location, all_day=True,
                             description="", is_physical=False)
    start = datetime(d.year, d.month, d.day, hour_start, 0, tzinfo=tz)
    end   = datetime(d.year, d.month, d.day, hour_end,   0, tzinfo=tz)
    return CalendarEvent(title=title, start=start, end=end,
                         location=location, all_day=False,
                         description="", is_physical=bool(location))


def _make_task(name, priority=3, due_date=None, score=0.0):
    return BriefTask(
        id=name, name=name, priority=priority,
        due_date=due_date, list_name="Test", status="open",
        url=f"https://clickup.com/t/{name}", score=score,
    )


# ── classify_day_load ─────────────────────────────────────────────────────────

class TestClassifyDayLoad:

    def test_no_events_is_light(self):
        assert classify_day_load([]) == DayLoad.LIGHT

    def test_one_short_meeting_is_light(self):
        events = [_make_event("Palaveri", 10, 11)]
        assert classify_day_load(events) == DayLoad.LIGHT

    def test_four_hours_is_tight(self):
        events = [
            _make_event("Kokous 1", 9,  11),
            _make_event("Kokous 2", 11, 13),
        ]
        assert classify_day_load(events) == DayLoad.TIGHT

    def test_multiple_locations_is_moving(self):
        events = [
            _make_event("Kokous A", 9,  10, location="Toimisto, Helsinki"),
            _make_event("Lounas",   12, 13, location="Ravintola, Espoo"),
        ]
        assert classify_day_load(events) == DayLoad.MOVING

    def test_four_timed_events_is_tight(self):
        events = [_make_event(f"K{i}", 9+i, 10+i) for i in range(4)]
        assert classify_day_load(events) == DayLoad.TIGHT

    def test_all_day_event_not_counted_as_hours(self):
        events = [_make_event("Loma", 0, 0, all_day=True)]
        assert classify_day_load(events) == DayLoad.LIGHT

    def test_normal_day_two_hours(self):
        events = [
            _make_event("Kokous", 10, 11),
            _make_event("Check-in", 14, 15),
        ]
        assert classify_day_load(events) == DayLoad.NORMAL


# ── select_tasks ──────────────────────────────────────────────────────────────

class TestSelectTasks:

    def test_max_3_tasks_returned(self):
        tasks = [_make_task(f"T{i}", score=float(10-i)) for i in range(6)]
        result = select_tasks(tasks, DayLoad.NORMAL, 3)
        assert len(result) == 3

    def test_tight_day_only_high_priority(self):
        tasks = [
            _make_task("Urgent",  priority=1, score=40.0),
            _make_task("High",    priority=2, score=30.0),
            _make_task("Normal",  priority=3, score=20.0),
            _make_task("Low",     priority=4, score=5.0),
        ]
        result = select_tasks(tasks, DayLoad.TIGHT, 3)
        names = [t.name for t in result]
        assert "Urgent" in names
        assert "High"   in names
        assert "Normal" not in names
        assert "Low"    not in names

    def test_tight_day_fallback_if_no_high_priority(self):
        tasks = [_make_task("Normal", priority=3, score=20.0)]
        result = select_tasks(tasks, DayLoad.TIGHT, 3)
        assert len(result) == 1

    def test_empty_tasks_returns_empty(self):
        assert select_tasks([], DayLoad.NORMAL, 3) == []

    def test_light_day_allows_3_tasks(self):
        tasks = [_make_task(f"T{i}", score=float(10-i)) for i in range(5)]
        result = select_tasks(tasks, DayLoad.LIGHT, 3)
        assert len(result) == 3


# ── effective_max_tasks ───────────────────────────────────────────────────────

class TestEffectiveMaxTasks:

    def test_light_day_allows_3(self):
        assert _effective_max_tasks(DayLoad.LIGHT, 3) == 3

    def test_tight_day_max_2(self):
        assert _effective_max_tasks(DayLoad.TIGHT, 3) == 2

    def test_moving_day_max_2(self):
        assert _effective_max_tasks(DayLoad.MOVING, 3) == 2

    def test_configured_max_respected(self):
        assert _effective_max_tasks(DayLoad.NORMAL, 1) == 1


# ── select_start_task ─────────────────────────────────────────────────────────

class TestSelectStartTask:

    def test_returns_first_task(self):
        tasks = [_make_task("Tärkein", score=40.0), _make_task("Toinen", score=20.0)]
        result = select_start_task(tasks, [])
        assert result.name == "Tärkein"

    def test_no_tasks_returns_none(self):
        assert select_start_task([], []) is None


# ── build_key_items ───────────────────────────────────────────────────────────

class TestBuildKeyItems:

    def test_task_name_included(self):
        tasks = [_make_task("Sopimusluonnos")]
        items = _build_key_items(tasks, None)
        assert any("Sopimusluonnos" in i for i in items)

    def test_overdue_label_added(self):
        from datetime import date as _d, timedelta
        past = _d.today() - timedelta(days=1)
        tasks = [BriefTask(id="x", name="Myöhässä oleva",
                           priority=2, due_date=past,
                           list_name="", status="", url="", score=30.0)]
        items = _build_key_items(tasks, None)
        assert any("myöhässä" in i.lower() for i in items)

    def test_shopify_signal_added_if_room(self):
        tasks = [_make_task("T1")]
        signals = ShopifySignals(
            has_open_alerts=True,
            alert_descriptions=["Korkea palautusaste"],
        )
        items = _build_key_items(tasks, signals)
        assert any("Shopify" in i for i in items)

    def test_shopify_signal_not_added_if_full(self):
        tasks = [_make_task(f"T{i}") for i in range(3)]
        signals = ShopifySignals(has_open_alerts=True, alert_descriptions=["Alert"])
        items = _build_key_items(tasks, signals)
        assert len(items) <= 3


# ── status_note ───────────────────────────────────────────────────────────────

class TestBuildStatusNote:

    def test_light_no_events(self):
        note = _build_status_note(DayLoad.LIGHT, [], [], None)
        assert "väljyyttä" in note or "kevyt" in note.lower()

    def test_moving_day_note(self):
        note = _build_status_note(DayLoad.MOVING, [], [], None)
        assert "liikkuva" in note.lower() or "siirtymiin" in note.lower()

    def test_tight_with_transitions(self):
        from brief_calendar import TransitionWarning
        w = TransitionWarning(
            from_event=_make_event("A", 9, 10, "Paikka 1"),
            to_event=_make_event("B",  10, 11, "Paikka 2"),
            gap_minutes=5,
            different_locations=True,
            message="Siirtymä A → B",
        )
        note = _build_status_note(DayLoad.TIGHT, [], [w], None)
        assert "siirtymiä" in note.lower() or "tiivis" in note.lower()


# ── generate_brief (integraatio) ─────────────────────────────────────────────

class TestGenerateBrief:

    def test_brief_result_has_text(self):
        tomorrow = date(2026, 3, 30)
        brief    = generate_brief(tomorrow, [], [])
        assert isinstance(brief.brief_text, str)
        assert len(brief.brief_text) > 20

    def test_brief_date_matches(self):
        tomorrow = date(2026, 3, 30)
        brief    = generate_brief(tomorrow, [], [])
        assert brief.brief_date == tomorrow

    def test_brief_with_no_data_is_light(self):
        brief = generate_brief(date(2026, 3, 30), [], [])
        assert brief.day_load == DayLoad.LIGHT

    def test_brief_contains_weekday(self):
        brief = generate_brief(date(2026, 3, 30), [], [])  # Maanantai
        assert "Maanantai" in brief.brief_text

    def test_brief_with_tasks_includes_start(self):
        tasks = [_make_task("Sopimusluonnos", priority=2, score=35.0)]
        brief = generate_brief(date(2026, 3, 30), [], tasks)
        assert "Sopimusluonnos" in brief.brief_text

    def test_brief_max_3_key_items(self):
        tasks = [_make_task(f"T{i}", score=float(30-i)) for i in range(6)]
        brief = generate_brief(date(2026, 3, 30), [], tasks, max_tasks=3)
        assert len(brief.key_items) <= 3

    def test_brief_source_summary_populated(self):
        tasks  = [_make_task("T1")]
        events = [_make_event("Kokous", 10, 11)]
        brief  = generate_brief(date(2026, 3, 30), events, tasks)
        assert brief.source_summary["events_count"] == 1
        assert brief.source_summary["tasks_fetched"] == 1
