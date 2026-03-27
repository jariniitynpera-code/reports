"""
test_brief_calendar.py — Testit brief_calendar-moduulille
"""

import sys
import os
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brief_calendar import (
    CalendarEvent,
    TransitionWarning,
    detect_transitions,
    total_meeting_hours,
    has_multiple_locations,
    first_morning_meeting,
    _looks_physical,
)


# ── Apufunktiot ───────────────────────────────────────────────────────────────

def _make_event(title, hour_start, hour_end, location="", all_day=False, is_physical=None):
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo
    tz = ZoneInfo("Europe/Helsinki")
    d  = date(2026, 3, 30)
    if all_day:
        return CalendarEvent(
            title=title, start=None, end=None,
            location=location, all_day=True,
            description="", is_physical=False,
        )
    start = datetime(d.year, d.month, d.day, hour_start, 0, tzinfo=tz)
    end   = datetime(d.year, d.month, d.day, hour_end,   0, tzinfo=tz)
    phys  = bool(location) if is_physical is None else is_physical
    return CalendarEvent(
        title=title, start=start, end=end,
        location=location, all_day=False,
        description="", is_physical=phys,
    )


# ── total_meeting_hours ───────────────────────────────────────────────────────

class TestTotalMeetingHours:

    def test_empty_list_is_zero(self):
        assert total_meeting_hours([]) == 0.0

    def test_one_hour_event(self):
        assert total_meeting_hours([_make_event("K", 10, 11)]) == 1.0

    def test_two_hour_events_sum(self):
        events = [_make_event("K1", 9, 10), _make_event("K2", 13, 15)]
        assert total_meeting_hours(events) == 3.0

    def test_all_day_event_not_counted(self):
        events = [_make_event("Loma", 0, 0, all_day=True)]
        assert total_meeting_hours(events) == 0.0

    def test_mixed_all_day_and_timed(self):
        events = [
            _make_event("Loma", 0, 0, all_day=True),
            _make_event("Kokous", 10, 11),
        ]
        assert total_meeting_hours(events) == 1.0


# ── has_multiple_locations ────────────────────────────────────────────────────

class TestHasMultipleLocations:

    def test_no_events_false(self):
        assert has_multiple_locations([]) is False

    def test_no_locations_false(self):
        events = [_make_event("K1", 9, 10), _make_event("K2", 11, 12)]
        assert has_multiple_locations(events) is False

    def test_same_location_false(self):
        events = [
            _make_event("K1", 9, 10, location="Toimisto"),
            _make_event("K2", 11, 12, location="Toimisto"),
        ]
        assert has_multiple_locations(events) is False

    def test_different_locations_true(self):
        events = [
            _make_event("K1", 9,  10, location="Toimisto"),
            _make_event("K2", 12, 13, location="Ravintola"),
        ]
        assert has_multiple_locations(events) is True

    def test_case_insensitive(self):
        events = [
            _make_event("K1", 9,  10, location="TOIMISTO"),
            _make_event("K2", 12, 13, location="toimisto"),
        ]
        assert has_multiple_locations(events) is False

    def test_all_day_location_ignored(self):
        events = [
            _make_event("Loma", 0, 0, location="Helsinki", all_day=True),
            _make_event("K1",   9, 10, location="Toimisto"),
        ]
        assert has_multiple_locations(events) is False


# ── first_morning_meeting ─────────────────────────────────────────────────────

class TestFirstMorningMeeting:

    def test_empty_returns_none(self):
        assert first_morning_meeting([]) is None

    def test_returns_earliest(self):
        events = [
            _make_event("Myöhäinen", 14, 15),
            _make_event("Varhainen",  8,  9),
            _make_event("Keski",     11, 12),
        ]
        result = first_morning_meeting(events)
        assert result.title == "Varhainen"

    def test_all_day_skipped(self):
        events = [
            _make_event("Loma", 0, 0, all_day=True),
            _make_event("Kokous", 10, 11),
        ]
        result = first_morning_meeting(events)
        assert result.title == "Kokous"

    def test_only_all_day_returns_none(self):
        events = [_make_event("Loma", 0, 0, all_day=True)]
        assert first_morning_meeting(events) is None


# ── _looks_physical ───────────────────────────────────────────────────────────

class TestLooksPhysical:

    def test_kokous_is_physical(self):
        assert _looks_physical("Kokous Helsinkiin", "") is True

    def test_lounas_is_physical(self):
        assert _looks_physical("Lounas", "") is True

    def test_zoom_call_not_physical(self):
        assert _looks_physical("Zoom call", "") is False

    def test_palaveri_is_physical(self):
        assert _looks_physical("Palaveri", "") is True

    def test_description_match(self):
        assert _looks_physical("Project update", "Toimistolla klo 10") is True


# ── detect_transitions ────────────────────────────────────────────────────────

class TestDetectTransitions:

    def test_no_events_no_warnings(self):
        assert detect_transitions([]) == []

    def test_one_event_no_warnings(self):
        assert detect_transitions([_make_event("K", 10, 11)]) == []

    def test_different_locations_tight_gap_warns(self):
        events = [
            _make_event("K1", 9,  10, location="Toimisto"),
            _make_event("K2", 10, 11, location="Ravintola"),
        ]
        warnings = detect_transitions(events)
        assert len(warnings) == 1
        assert warnings[0].different_locations is True

    def test_same_location_large_gap_no_warning(self):
        # Sama sijainti, 60 min väli → ei varoitusta (ei eri sijainti, väli riittävä)
        events = [
            _make_event("K1", 9,  10, location="Toimisto"),
            _make_event("K2", 11, 12, location="Toimisto"),
        ]
        assert detect_transitions(events) == []

    def test_same_location_tight_gap_warns_if_physical(self):
        # Sama sijainti, 0 min väli, fyysiset → < 5 min -varoitus
        events = [
            _make_event("K1", 9,  10, location="Toimisto"),
            _make_event("K2", 10, 11, location="Toimisto"),
        ]
        warnings = detect_transitions(events)
        assert len(warnings) == 1
        assert warnings[0].different_locations is False

    def test_different_locations_large_gap_no_warning(self):
        events = [
            _make_event("K1", 9,  10, location="Toimisto"),
            _make_event("K2", 11, 12, location="Ravintola"),
        ]
        # 60 min gap > 20 min buffer → no warning
        assert detect_transitions(events) == []

    def test_physical_events_under_5min_gap_warns(self):
        try:
            from zoneinfo import ZoneInfo
        except ImportError:
            from backports.zoneinfo import ZoneInfo
        tz = ZoneInfo("Europe/Helsinki")
        d  = date(2026, 3, 30)
        e1 = CalendarEvent(
            title="Kokous 1",
            start=datetime(d.year, d.month, d.day, 10, 0, tzinfo=tz),
            end=datetime(d.year, d.month, d.day, 11, 0, tzinfo=tz),
            location="", all_day=False, description="",
            is_physical=True,
        )
        e2 = CalendarEvent(
            title="Kokous 2",
            start=datetime(d.year, d.month, d.day, 11, 3, tzinfo=tz),
            end=datetime(d.year, d.month, d.day, 12, 0, tzinfo=tz),
            location="", all_day=False, description="",
            is_physical=True,
        )
        warnings = detect_transitions([e1, e2])
        assert len(warnings) == 1
        assert warnings[0].different_locations is False
        assert warnings[0].gap_minutes == 3.0

    def test_all_day_events_ignored(self):
        events = [
            _make_event("Loma",  0,  0, location="Helsinki", all_day=True),
            _make_event("Kokous", 10, 11, location="Ravintola"),
        ]
        assert detect_transitions(events) == []

    def test_gap_minutes_calculated_correctly(self):
        events = [
            _make_event("K1", 9,  10, location="Paikka A"),
            _make_event("K2", 10, 11, location="Paikka B"),
        ]
        warnings = detect_transitions(events)
        assert len(warnings) == 1
        assert warnings[0].gap_minutes == 0.0

    def test_warning_message_contains_event_titles(self):
        events = [
            _make_event("Hallituksen kokous", 9, 10, location="Toimisto"),
            _make_event("Asiakaslounas",      10, 11, location="Ravintola"),
        ]
        warnings = detect_transitions(events)
        assert len(warnings) == 1
        assert "Hallituksen kokous" in warnings[0].message
        assert "Asiakaslounas" in warnings[0].message
