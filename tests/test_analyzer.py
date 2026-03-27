"""
test_analyzer.py — Yksikkötestit analyysilogiikalle

Testaa metriikoiden laskentaa, poikkeamien tunnistusta
ja status-luokitusta.
"""

import sys
import os
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analyzer import (
    _compute_metrics,
    _compute_7d_average,
    _classify_status,
    _build_comparison,
    _pct_change,
    analyze,
    Alert,
    DayMetrics,
)


# ── Testidatan apufunktiot ────────────────────────────────────────────────────

def make_order(
    order_id="1001",
    total_price="100.00",
    subtotal_price="100.00",
    financial_status="paid",
    fulfillment_status="fulfilled",
    cancelled_at=None,
    customer_orders_count=1,
    line_items=None,
    refunds=None,
):
    return {
        "id": order_id,
        "name": f"#{order_id}",
        "total_price": total_price,
        "subtotal_price": subtotal_price,
        "total_tax": "0.00",
        "total_discounts": "0.00",
        "financial_status": financial_status,
        "fulfillment_status": fulfillment_status,
        "cancelled_at": cancelled_at,
        "cancel_reason": None,
        "customer": {
            "id": "cust_1",
            "email": "test@example.com",
            "orders_count": customer_orders_count,
        },
        "line_items": line_items or [
            {"product_id": "prod_1", "title": "Testilaite", "sku": "TEST-001",
             "quantity": 1, "price": total_price},
        ],
        "refunds": refunds or [],
        "payment_gateway": "shopify_payments",
    }


TEST_DATE = date(2026, 3, 26)

HISTORICAL_7D = [
    {
        "report_date": f"2026-03-{18 + i:02d}",
        "total_orders": 10,
        "gross_revenue": 1000.0,
        "avg_order_value": 100.0,
        "paid_orders": 9,
        "refunded_orders": 1,
        "total_refunds": 1,
        "refund_amount": 50.0,
        "refund_rate_pct": 10.0,
        "cancellation_rate_pct": 5.0,
        "payment_issues": 0,
    }
    for i in range(7)
]


# ── Metriikoiden laskenta ─────────────────────────────────────────────────────

class TestComputeMetrics:

    def test_empty_orders(self):
        m = _compute_metrics(TEST_DATE, [])
        assert m.total_orders == 0
        assert m.gross_revenue == 0.0
        assert m.avg_order_value == 0.0

    def test_single_paid_order(self):
        orders = [make_order(total_price="150.00")]
        m = _compute_metrics(TEST_DATE, orders)
        assert m.total_orders == 1
        assert m.gross_revenue == 150.0
        assert m.avg_order_value == 150.0
        assert m.paid_orders == 1
        assert m.new_customers == 1
        assert m.returning_customers == 0

    def test_returning_customer(self):
        orders = [make_order(customer_orders_count=5)]
        m = _compute_metrics(TEST_DATE, orders)
        assert m.new_customers == 0
        assert m.returning_customers == 1

    def test_cancelled_order_excluded_from_revenue(self):
        orders = [
            make_order(order_id="1001", total_price="200.00"),
            make_order(order_id="1002", total_price="100.00", cancelled_at="2026-03-26T10:00:00Z"),
        ]
        m = _compute_metrics(TEST_DATE, orders)
        assert m.total_orders == 2
        assert m.cancelled_orders == 1
        assert m.gross_revenue == 200.0  # Vain ei-peruutettu lasketaan

    def test_refunded_order(self):
        orders = [
            make_order(
                order_id="1001",
                total_price="100.00",
                financial_status="refunded",
                refunds=[{"transactions": [{"amount": "100.00", "kind": "refund"}]}],
            )
        ]
        m = _compute_metrics(TEST_DATE, orders)
        assert m.refunded_orders == 1
        assert m.total_refunds == 1
        assert m.refund_amount == 100.0

    def test_pending_order_counts_as_payment_issue(self):
        orders = [make_order(financial_status="pending")]
        m = _compute_metrics(TEST_DATE, orders)
        assert m.payment_issues == 1
        assert m.pending_orders == 1

    def test_avg_order_value_calculation(self):
        orders = [
            make_order(order_id="1", total_price="100.00"),
            make_order(order_id="2", total_price="200.00"),
            make_order(order_id="3", total_price="300.00"),
        ]
        m = _compute_metrics(TEST_DATE, orders)
        assert m.avg_order_value == 200.0  # (100 + 200 + 300) / 3

    def test_refund_rate_calculation(self):
        # 1 palautunut / 4 maksettua = 25%
        orders = [
            make_order(order_id="1", financial_status="paid"),
            make_order(order_id="2", financial_status="paid"),
            make_order(order_id="3", financial_status="paid"),
            make_order(
                order_id="4",
                financial_status="refunded",
                refunds=[{"transactions": [{"amount": "50.00", "kind": "refund"}]}],
            ),
        ]
        m = _compute_metrics(TEST_DATE, orders)
        assert m.refund_rate_pct == 25.0

    def test_top_products_sorted_by_revenue(self):
        orders = [
            make_order(
                order_id="1",
                total_price="300.00",
                line_items=[
                    {"product_id": "A", "title": "Tuote A", "sku": "A-001", "quantity": 1, "price": "300.00"},
                    {"product_id": "B", "title": "Tuote B", "sku": "B-001", "quantity": 2, "price": "50.00"},
                ]
            ),
        ]
        m = _compute_metrics(TEST_DATE, orders)
        assert len(m.top_products) == 2
        assert m.top_products[0]["title"] == "Tuote A"  # Korkein myynti ensin
        assert m.top_products[1]["title"] == "Tuote B"

    def test_cancellation_rate(self):
        orders = [
            make_order(order_id="1"),
            make_order(order_id="2"),
            make_order(order_id="3", cancelled_at="2026-03-26T10:00:00Z"),
        ]
        m = _compute_metrics(TEST_DATE, orders)
        assert round(m.cancellation_rate_pct, 1) == 33.3


# ── 7pv keskiarvo ─────────────────────────────────────────────────────────────

class TestHistoricalComparison:

    def test_7d_average_calculation(self):
        avg = _compute_7d_average(HISTORICAL_7D)
        assert avg is not None
        assert avg["gross_revenue"] == 1000.0
        assert avg["total_orders"] == 10.0

    def test_7d_average_returns_none_with_insufficient_data(self):
        # Alle 3 päivää → ei vertailua
        avg = _compute_7d_average(HISTORICAL_7D[:2])
        assert avg is None

    def test_7d_average_uses_last_7_days(self):
        # 10 päivän data — pitäisi käyttää vain viimeistä 7
        extended = [
            {"report_date": f"2026-03-{10 + i:02d}",
             "total_orders": 20,
             "gross_revenue": 2000.0,
             "avg_order_value": 100.0}
            for i in range(3)
        ] + HISTORICAL_7D  # 7 päivää á 1000€

        avg = _compute_7d_average(extended)
        assert avg["gross_revenue"] == 1000.0  # Vain viimeiset 7

    def test_pct_change_positive(self):
        result = _pct_change(100.0, 150.0)
        assert result == 50.0

    def test_pct_change_negative(self):
        result = _pct_change(100.0, 70.0)
        assert result == -30.0

    def test_pct_change_zero_reference(self):
        result = _pct_change(0.0, 100.0)
        assert result is None


# ── Status-luokitus ───────────────────────────────────────────────────────────

class TestStatusClassification:

    def test_no_alerts_is_green(self):
        assert _classify_status([]) == "green"

    def test_warning_alert_is_yellow(self):
        alerts = [Alert("high_refunds", "warning", "Palautuksia")]
        assert _classify_status(alerts) == "yellow"

    def test_critical_alert_is_red(self):
        alerts = [Alert("low_sales", "critical", "Myynti pudonnut")]
        assert _classify_status(alerts) == "red"

    def test_mixed_alerts_is_red_when_any_critical(self):
        alerts = [
            Alert("high_refunds",  "warning",  "Palautuksia"),
            Alert("low_sales",     "critical", "Myynti pudonnut"),
        ]
        assert _classify_status(alerts) == "red"

    def test_multiple_warnings_is_yellow_not_red(self):
        alerts = [
            Alert("high_refunds",     "warning", "Palautuksia"),
            Alert("payment_issues",   "warning", "Maksuongelmia"),
            Alert("high_cancellations","warning","Peruutuksia"),
        ]
        assert _classify_status(alerts) == "yellow"


# ── Kokonaisanalyysi ──────────────────────────────────────────────────────────

class TestFullAnalysis:

    def test_normal_day_is_green(self):
        # Normaali päivä — kuten historiassa
        orders = [make_order(order_id=str(i), total_price="100.00") for i in range(10)]
        result = analyze(TEST_DATE, orders, HISTORICAL_7D)
        assert result.status_level == "green"
        assert result.metrics.total_orders == 10
        assert len(result.observations) > 0

    def test_zero_orders_day(self):
        result = analyze(TEST_DATE, [], HISTORICAL_7D)
        # 0 tilauksia on iso pudotus → pitäisi olla vähintään yellow
        assert result.status_level in ("yellow", "red")

    def test_no_history_green_by_default(self):
        # Ei historiadataa → ei voi tunnistaa poikkeamia → green
        orders = [make_order()]
        result = analyze(TEST_DATE, orders, [])
        assert result.status_level == "green"

    def test_high_refund_rate_triggers_alert(self):
        # 3 palautettua / 5 tilausta = 60% → pitäisi triggeröidä
        orders = [
            make_order(order_id=str(i), financial_status="paid") for i in range(2)
        ] + [
            make_order(
                order_id=str(i + 10),
                financial_status="refunded",
                refunds=[{"transactions": [{"amount": "50.00", "kind": "refund"}]}],
            )
            for i in range(3)
        ]
        result = analyze(TEST_DATE, orders, HISTORICAL_7D)
        assert any(a.alert_type == "high_refunds" for a in result.alerts)

    def test_recommendation_is_not_empty(self):
        orders = [make_order()]
        result = analyze(TEST_DATE, orders, HISTORICAL_7D)
        assert result.recommendation != ""

    def test_analysis_result_has_all_fields(self):
        orders = [make_order()]
        result = analyze(TEST_DATE, orders, HISTORICAL_7D)
        assert result.metrics is not None
        assert result.status_level in ("green", "yellow", "red")
        assert isinstance(result.alerts, list)
        assert isinstance(result.observations, list)
        assert isinstance(result.risks, list)
        assert result.recommendation != ""
