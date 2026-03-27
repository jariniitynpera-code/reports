"""
test_extensions.py — Testit uusille integraatioille

Testaa viikonpäivävertailua, kalenterikontekstia, varastoriskejä
sekä Slack/email -rakenteiden muodostumista.
"""

import sys
import os
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── Viikonpäivävertailu ───────────────────────────────────────────────────────

class TestWeekdayComparison:

    def _make_history(self, n_weeks: int = 4) -> list[dict]:
        """Luo n viikon historiaa — joka viikonpäivä edustettuna."""
        from datetime import timedelta
        base = date(2026, 1, 5)  # Maanantai
        rows = []
        for week in range(n_weeks):
            for day_offset in range(7):
                d = base + timedelta(days=week * 7 + day_offset)
                rows.append({
                    "report_date":    d.isoformat(),
                    "total_orders":   10,
                    "gross_revenue":  1000.0,
                    "avg_order_value": 100.0,
                })
        return rows

    def test_weekday_average_with_enough_data(self):
        from analyzer import _compute_weekday_average
        history = self._make_history(4)
        # 2026-03-26 on torstai (weekday=3)
        result = _compute_weekday_average(date(2026, 3, 26), history)
        assert result is not None
        assert result["gross_revenue"] == 1000.0

    def test_weekday_average_insufficient_data(self):
        from analyzer import _compute_weekday_average
        # Vain 2 torstai-datapistettä — ei riitä (tarvitaan 3)
        history = self._make_history(2)
        result = _compute_weekday_average(date(2026, 3, 26), history)
        assert result is None

    def test_weekday_only_uses_same_weekday(self):
        """Varmistaa, että keskiarvo lasketaan vain saman viikonpäivän datasta."""
        from analyzer import _compute_weekday_average
        from datetime import timedelta

        # Torstait saavat 2000€, muut 500€
        base = date(2026, 1, 5)  # Maanantai
        rows = []
        for week in range(5):
            for day_offset in range(7):
                d = base + timedelta(days=week * 7 + day_offset)
                revenue = 2000.0 if d.weekday() == 3 else 500.0
                rows.append({
                    "report_date":    d.isoformat(),
                    "total_orders":   10,
                    "gross_revenue":  revenue,
                    "avg_order_value": revenue / 10,
                })

        result = _compute_weekday_average(date(2026, 3, 26), rows)  # Torstai
        assert result is not None
        assert result["gross_revenue"] == 2000.0

    def test_weekday_in_analysis_result(self):
        from analyzer import analyze
        from datetime import timedelta

        history = self._make_history(5)
        orders = [
            {"id": str(i), "name": f"#{1000+i}",
             "total_price": "100.00", "subtotal_price": "100.00",
             "total_tax": "0.00", "total_discounts": "0.00",
             "financial_status": "paid", "fulfillment_status": "fulfilled",
             "cancelled_at": None, "cancel_reason": None,
             "customer": {"id": "c1", "email": "t@t.com", "orders_count": 1},
             "line_items": [], "refunds": [], "payment_gateway": "shopify"}
            for i in range(10)
        ]
        result = analyze(date(2026, 3, 26), orders, history, check_inventory=False)
        # Pitäisi saada viikonpäivävertailu kun dataa on 5 viikkoa
        assert result.comparison_weekday is not None


# ── Kalenteritapahtumien luokittelu ──────────────────────────────────────────

class TestCalendarEvents:

    def test_campaign_event_classified_correctly(self):
        from gcal_client import _classify_event
        assert _classify_event("Kevään kampanja -20%", "") == "campaign"
        assert _classify_event("Spring Campaign Launch", "") == "campaign"
        assert _classify_event("Black Friday Sale", "") == "campaign"

    def test_holiday_classified_correctly(self):
        from gcal_client import _classify_event
        assert _classify_event("Joulupyhät", "") == "holiday"
        assert _classify_event("Kesäloma", "") == "holiday"
        assert _classify_event("Store Closed", "") == "holiday"

    def test_maintenance_classified_correctly(self):
        from gcal_client import _classify_event
        assert _classify_event("Shopify huoltoikkuna", "") == "maintenance"
        assert _classify_event("System Maintenance", "") == "maintenance"

    def test_unknown_event_is_other(self):
        from gcal_client import _classify_event
        assert _classify_event("Tiimipalaveri", "") == "other"
        assert _classify_event("Jarin syntymäpäivä", "") == "other"

    def test_format_events_for_report_empty(self):
        from gcal_client import format_events_for_report
        assert format_events_for_report([]) is None

    def test_format_events_for_report_with_events(self):
        from gcal_client import format_events_for_report
        events = [
            {"title": "Kevään ale", "type": "campaign", "all_day": True, "description": ""},
            {"title": "Juhannusaatto", "type": "holiday", "all_day": True, "description": ""},
        ]
        text = format_events_for_report(events)
        assert text is not None
        assert "Kevään ale" in text
        assert "Juhannusaatto" in text

    def test_explain_anomaly_holiday_with_red_status(self):
        from gcal_client import explain_anomaly
        events = [{"title": "Joulu", "type": "holiday", "all_day": True, "description": ""}]
        result = explain_anomaly(events, "red")
        assert result is not None
        assert "Joulu" in result

    def test_explain_anomaly_no_events(self):
        from gcal_client import explain_anomaly
        assert explain_anomaly([], "red") is None


# ── Varastoriskit ─────────────────────────────────────────────────────────────

class TestInventoryRisks:

    def test_risk_detected_low_stock(self, monkeypatch):
        from inventory_client import check_inventory_risks, _fetch_stock_levels

        monkeypatch.setattr(
            "inventory_client._fetch_stock_levels",
            lambda skus: {"KOMP-001": 3, "PELL-002": 50},
        )

        top_products = [
            {"title": "Kompassi", "sku": "KOMP-001", "quantity": 2, "revenue": 300.0},
            {"title": "Liivit",   "sku": "PELL-002", "quantity": 1, "revenue": 200.0},
        ]
        risks = check_inventory_risks(top_products, low_stock_threshold=5)

        assert len(risks) == 1
        assert risks[0].sku == "KOMP-001"
        assert risks[0].stock_qty == 3
        assert risks[0].severity == "warning"

    def test_critical_risk_zero_stock(self, monkeypatch):
        from inventory_client import check_inventory_risks

        monkeypatch.setattr(
            "inventory_client._fetch_stock_levels",
            lambda skus: {"KOMP-001": 0},
        )

        top_products = [
            {"title": "Kompassi", "sku": "KOMP-001", "quantity": 5, "revenue": 500.0},
        ]
        risks = check_inventory_risks(top_products, low_stock_threshold=5)

        assert len(risks) == 1
        assert risks[0].severity == "critical"
        assert risks[0].stock_qty == 0

    def test_no_risk_sufficient_stock(self, monkeypatch):
        from inventory_client import check_inventory_risks

        monkeypatch.setattr(
            "inventory_client._fetch_stock_levels",
            lambda skus: {"KOMP-001": 99},
        )

        top_products = [
            {"title": "Kompassi", "sku": "KOMP-001", "quantity": 2, "revenue": 200.0},
        ]
        risks = check_inventory_risks(top_products, low_stock_threshold=5)
        assert len(risks) == 0

    def test_days_of_stock_calculated(self, monkeypatch):
        from inventory_client import check_inventory_risks

        monkeypatch.setattr(
            "inventory_client._fetch_stock_levels",
            lambda skus: {"TUOTE": 4},
        )

        top_products = [
            {"title": "Tuote", "sku": "TUOTE", "quantity": 2, "revenue": 100.0},
        ]
        risks = check_inventory_risks(top_products, low_stock_threshold=5)

        assert len(risks) == 1
        assert risks[0].days_of_stock == 2.0  # 4 kpl / 2 kpl/pv

    def test_no_sku_products_ignored(self, monkeypatch):
        from inventory_client import check_inventory_risks

        monkeypatch.setattr(
            "inventory_client._fetch_stock_levels",
            lambda skus: {},
        )

        top_products = [
            {"title": "Tuote ilman SKUa", "sku": "", "quantity": 5, "revenue": 100.0},
        ]
        risks = check_inventory_risks(top_products, low_stock_threshold=5)
        assert len(risks) == 0

    def test_risks_converted_to_alerts(self, monkeypatch):
        from inventory_client import check_inventory_risks, risks_to_alerts

        monkeypatch.setattr(
            "inventory_client._fetch_stock_levels",
            lambda skus: {"KOMP-001": 2},
        )

        top_products = [
            {"title": "Kompassi", "sku": "KOMP-001", "quantity": 3, "revenue": 300.0},
        ]
        risks = check_inventory_risks(top_products, low_stock_threshold=5)
        alerts = risks_to_alerts(risks)

        assert len(alerts) == 1
        assert alerts[0].alert_type == "inventory_risk"
        assert alerts[0].severity == "critical"  # 2 <= 2 (critical threshold)
        assert alerts[0].create_task is True


# ── Slack-payload ─────────────────────────────────────────────────────────────

class TestSlackPayload:

    def test_slack_payload_structure(self):
        from slack_client import _build_payload
        payload = _build_payload(
            report_date=date(2026, 3, 26),
            status_level="red",
            summary_lines=["Tilauksia: 5", "Liikevaihto: 500 €"],
            recommendation="Tarkista palautukset.",
            clickup_url="https://app.clickup.com/t/abc123",
            alerts_count=2,
        )
        assert "attachments" in payload
        assert len(payload["attachments"]) == 1
        assert payload["attachments"][0]["color"] == "#e01e5a"  # red

    def test_slack_green_color(self):
        from slack_client import _build_payload
        payload = _build_payload(
            report_date=date(2026, 3, 26),
            status_level="green",
            summary_lines=[],
            recommendation="Ei toimenpiteitä.",
            clickup_url=None,
            alerts_count=0,
        )
        assert payload["attachments"][0]["color"] == "#36a64f"

    def test_slack_no_webhook_returns_false(self, monkeypatch):
        import os
        monkeypatch.setenv("SLACK_WEBHOOK_URL", "")
        from slack_client import send_report_notification
        result = send_report_notification(
            report_date=date(2026, 3, 26),
            status_level="red",
            summary_lines=[],
            recommendation="",
        )
        assert result is False

    def test_slack_green_skipped_by_default(self, monkeypatch):
        monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/test")
        monkeypatch.setenv("SLACK_NOTIFY_GREEN", "false")
        from slack_client import send_report_notification
        result = send_report_notification(
            report_date=date(2026, 3, 26),
            status_level="green",
            summary_lines=[],
            recommendation="",
        )
        assert result is False  # Ei lähetetty

    def test_build_summary_lines(self):
        from report_generator import build_summary_lines
        from analyzer import AnalysisResult, DayMetrics, Comparison

        m = DayMetrics(
            report_date=date(2026, 3, 26),
            total_orders=5,
            gross_revenue=500.0,
            avg_order_value=100.0,
        )
        result = AnalysisResult(
            metrics=m,
            status_level="green",
            recommendation="Ei toimenpiteitä.",
            comparison_7d=Comparison(
                label="7pv ka.",
                revenue_delta_pct=10.0,
                orders_delta_pct=5.0,
                revenue_ref=450.0,
            ),
        )
        lines = build_summary_lines(result)
        assert any("500" in line for line in lines)
        assert any("+10" in line or "↑" in line for line in lines)


# ── Raportti uusilla osioilla ─────────────────────────────────────────────────

class TestReportWithExtensions:

    def _make_result(self, **kwargs):
        from analyzer import AnalysisResult, DayMetrics
        m = DayMetrics(
            report_date=date(2026, 3, 26),
            total_orders=5,
            gross_revenue=500.0,
            top_products=[
                {"title": "Kompassi Pro", "sku": "KOMP-001", "quantity": 2, "revenue": 300.0},
            ],
        )
        return AnalysisResult(
            metrics=m,
            status_level="green",
            recommendation="Ei toimenpiteitä.",
            **kwargs,
        )

    def test_report_with_calendar_events(self):
        from report_generator import generate_report
        events = [{"title": "Kevään ale", "type": "campaign", "all_day": True, "description": ""}]
        result = self._make_result(calendar_events=events)
        report = generate_report(result)
        assert "Kevään ale" in report

    def test_report_without_calendar_events(self):
        from report_generator import generate_report
        result = self._make_result(calendar_events=[])
        report = generate_report(result)
        # Kalenteriosio ei saa näkyä jos ei tapahtumia
        assert "kalenterissa" not in report.lower()

    def test_report_with_inventory_risks(self):
        from report_generator import generate_report
        from inventory_client import InventoryRisk

        risks = [
            InventoryRisk(
                sku="KOMP-001",
                title="Kompassi Pro",
                stock_qty=3,
                daily_sold=2,
                days_of_stock=1.5,
                severity="warning",
            )
        ]
        result = self._make_result(inventory_risks=risks)
        report = generate_report(result)
        assert "Varastoriskit" in report
        assert "Kompassi Pro" in report

    def test_report_with_weekday_comparison(self):
        from report_generator import generate_report
        from analyzer import Comparison

        result = self._make_result(
            comparison_weekday=Comparison(
                label="Sama viikonpäivä (ka. torstaita)",
                revenue_delta_pct=-15.0,
                orders_delta_pct=-10.0,
                revenue_ref=600.0,
            )
        )
        report = generate_report(result)
        assert "torstaita" in report or "Sama viikonpäivä" in report
