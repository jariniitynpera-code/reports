"""
test_status_classification.py — Yksikkötestit status-luokittelulle

Testaa kattavasti kaikki status-luokituksen skenaariot:
green / yellow / red ja rajatapaukset.
"""

import sys
import os
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analyzer import (
    _classify_status,
    _detect_anomalies,
    analyze,
    Alert,
    DayMetrics,
)
from config import THRESHOLDS


TEST_DATE = date(2026, 3, 26)

# Normaali historiadatapiste
def _normal_history(n: int = 7) -> list[dict]:
    return [
        {
            "report_date": f"2026-03-{18 + i:02d}",
            "total_orders": 10,
            "gross_revenue": 1000.0,
            "avg_order_value": 100.0,
            "paid_orders": 10,
            "refunded_orders": 0,
            "total_refunds": 0,
            "refund_amount": 0.0,
            "refund_rate_pct": 0.0,
            "cancellation_rate_pct": 0.0,
            "payment_issues": 0,
        }
        for i in range(n)
    ]


class TestStatusClassify:

    def test_green_no_alerts(self):
        assert _classify_status([]) == "green"

    def test_yellow_single_warning(self):
        assert _classify_status([Alert("X", "warning", "X")]) == "yellow"

    def test_red_single_critical(self):
        assert _classify_status([Alert("X", "critical", "X")]) == "red"

    def test_red_overrides_warning(self):
        alerts = [
            Alert("A", "warning", "A"),
            Alert("B", "critical", "B"),
            Alert("C", "warning", "C"),
        ]
        assert _classify_status(alerts) == "red"

    def test_multiple_warnings_stays_yellow(self):
        alerts = [Alert(f"alert{i}", "warning", "W") for i in range(5)]
        assert _classify_status(alerts) == "yellow"


class TestAnomalyDetection:

    def test_normal_day_no_alerts(self):
        m = DayMetrics(
            report_date=TEST_DATE,
            total_orders=10,
            paid_orders=10,
            gross_revenue=1000.0,
            refund_rate_pct=0.0,
            cancellation_rate_pct=0.0,
            payment_issues=0,
        )
        history = _normal_history()
        alerts = _detect_anomalies(m, history)
        assert len(alerts) == 0

    def test_revenue_drop_30pct_is_warning(self):
        m = DayMetrics(
            report_date=TEST_DATE,
            total_orders=8,
            paid_orders=8,
            gross_revenue=690.0,  # -31% normaalista (1000€)
        )
        alerts = _detect_anomalies(m, _normal_history())
        low_sales_alerts = [a for a in alerts if a.alert_type == "low_sales"]
        assert len(low_sales_alerts) == 1
        assert low_sales_alerts[0].severity == "warning"

    def test_revenue_drop_60pct_is_critical(self):
        m = DayMetrics(
            report_date=TEST_DATE,
            total_orders=4,
            paid_orders=4,
            gross_revenue=350.0,  # -65% normaalista
        )
        alerts = _detect_anomalies(m, _normal_history())
        critical = [a for a in alerts if a.alert_type == "low_sales" and a.severity == "critical"]
        assert len(critical) == 1

    def test_refund_rate_10pct_is_warning(self):
        m = DayMetrics(
            report_date=TEST_DATE,
            total_orders=10,
            paid_orders=9,
            refunded_orders=1,
            gross_revenue=1000.0,
            total_refunds=1,
            refund_rate_pct=10.0,
        )
        alerts = _detect_anomalies(m, _normal_history())
        refund_alerts = [a for a in alerts if a.alert_type == "high_refunds"]
        assert len(refund_alerts) == 1
        assert refund_alerts[0].severity == "warning"

    def test_refund_rate_20pct_is_critical(self):
        m = DayMetrics(
            report_date=TEST_DATE,
            total_orders=10,
            paid_orders=8,
            refunded_orders=2,
            gross_revenue=800.0,
            total_refunds=2,
            refund_rate_pct=20.0,
        )
        alerts = _detect_anomalies(m, _normal_history())
        critical_refunds = [
            a for a in alerts
            if a.alert_type == "high_refunds" and a.severity == "critical"
        ]
        assert len(critical_refunds) == 1

    def test_payment_issues_3_is_warning(self):
        m = DayMetrics(
            report_date=TEST_DATE,
            total_orders=10,
            paid_orders=7,
            gross_revenue=700.0,
            payment_issues=3,
        )
        alerts = _detect_anomalies(m, _normal_history())
        payment_alerts = [a for a in alerts if a.alert_type == "payment_issues"]
        assert len(payment_alerts) == 1
        assert payment_alerts[0].severity == "warning"

    def test_payment_issues_8_is_critical(self):
        m = DayMetrics(
            report_date=TEST_DATE,
            total_orders=15,
            paid_orders=7,
            gross_revenue=700.0,
            payment_issues=8,
        )
        alerts = _detect_anomalies(m, _normal_history())
        critical_payment = [
            a for a in alerts
            if a.alert_type == "payment_issues" and a.severity == "critical"
        ]
        assert len(critical_payment) == 1

    def test_no_alerts_without_history(self):
        # Ilman historiadataa ei voi tunnistaa poikkeamia
        m = DayMetrics(
            report_date=TEST_DATE,
            total_orders=1,
            paid_orders=1,
            gross_revenue=100.0,
        )
        alerts = _detect_anomalies(m, [])
        # Vain alle min_orders_for_pct_rules -tilauksia → ei prosenttipoikkeamia
        assert len(alerts) == 0

    def test_cancellation_rate_15pct_is_warning(self):
        m = DayMetrics(
            report_date=TEST_DATE,
            total_orders=20,
            paid_orders=17,
            cancelled_orders=3,
            gross_revenue=1700.0,
            cancellation_rate_pct=15.0,
        )
        alerts = _detect_anomalies(m, _normal_history())
        cancel_alerts = [a for a in alerts if a.alert_type == "high_cancellations"]
        assert len(cancel_alerts) == 1
        assert cancel_alerts[0].severity == "warning"

    def test_sales_spike_creates_alert(self):
        m = DayMetrics(
            report_date=TEST_DATE,
            total_orders=25,
            paid_orders=25,
            gross_revenue=3000.0,  # +200% normaalista
        )
        alerts = _detect_anomalies(m, _normal_history())
        spike_alerts = [a for a in alerts if a.alert_type == "sales_spike"]
        assert len(spike_alerts) == 1

    def test_no_duplicate_alerts_same_type(self):
        # Varmista, ettei sama alert_type tule kahdesti
        m = DayMetrics(
            report_date=TEST_DATE,
            total_orders=10,
            paid_orders=8,
            refunded_orders=2,
            gross_revenue=800.0,
            total_refunds=2,
            refund_rate_pct=20.0,
        )
        alerts = _detect_anomalies(m, _normal_history())
        refund_types = [a.alert_type for a in alerts if a.alert_type == "high_refunds"]
        assert len(refund_types) == 1  # Vain yksi, vaikka ylittää molemmat rajat


class TestFollowUpTaskTriggers:
    """Testaa milloin automaattiset tehtävät luodaan."""

    def test_critical_refund_triggers_task(self):
        m = DayMetrics(
            report_date=TEST_DATE,
            total_orders=10,
            paid_orders=8,
            refunded_orders=2,
            gross_revenue=800.0,
            total_refunds=2,
            refund_rate_pct=20.0,
        )
        alerts = _detect_anomalies(m, _normal_history())
        task_alerts = [a for a in alerts if a.create_task]
        assert len(task_alerts) > 0

    def test_warning_refund_no_task(self):
        # 10% palautusaste → warning mutta ei välttämättä tehtävää
        # (TASK_RULES.refund_rate_task_threshold = 15%)
        m = DayMetrics(
            report_date=TEST_DATE,
            total_orders=10,
            paid_orders=9,
            refunded_orders=1,
            gross_revenue=900.0,
            total_refunds=1,
            refund_rate_pct=10.0,
        )
        alerts = _detect_anomalies(m, _normal_history())
        task_alerts = [a for a in alerts if a.create_task and a.alert_type == "high_refunds"]
        assert len(task_alerts) == 0

    def test_critical_sales_drop_triggers_task(self):
        m = DayMetrics(
            report_date=TEST_DATE,
            total_orders=3,
            paid_orders=3,
            gross_revenue=200.0,  # -80% normaalista (1000€)
        )
        alerts = _detect_anomalies(m, _normal_history())
        task_alerts = [a for a in alerts if a.create_task and a.alert_type == "low_sales"]
        assert len(task_alerts) > 0
        assert task_alerts[0].task_name is not None
        assert task_alerts[0].task_description is not None
