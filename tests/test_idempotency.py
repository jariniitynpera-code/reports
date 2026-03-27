"""
test_idempotency.py — Idempotenssi- ja duplikaattisuojaustestit

Testaa, että sama ajo voidaan toistaa turvallisesti ilman
tietokantaongelmia tai duplikaatteja.

Nämä testit vaativat toimivan Supabase-yhteyden.
Ajetaan erillisellä merkinnällä: pytest tests/test_idempotency.py -m integration
"""

import sys
import os
import pytest
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── Yksikkötason idempotenssi (ei tietokantaa) ────────────────────────────────

class TestDateRangeCalculation:
    """Testaa aikavyöhykelaskennan oikeellisuutta."""

    def test_helsinki_timezone_winter(self):
        """UTC+2 talvella: 2026-01-15 → UTC 22:00-21:59"""
        from shopify_client import _date_range_utc
        min_dt, max_dt = _date_range_utc(date(2026, 1, 15), "Europe/Helsinki")
        assert min_dt == "2026-01-14T22:00:00Z"
        assert max_dt == "2026-01-15T21:59:59Z"

    def test_helsinki_timezone_summer(self):
        """UTC+3 kesällä: 2026-07-01 → UTC 21:00-20:59"""
        from shopify_client import _date_range_utc
        min_dt, max_dt = _date_range_utc(date(2026, 7, 1), "Europe/Helsinki")
        assert min_dt == "2026-06-30T21:00:00Z"
        assert max_dt == "2026-07-01T20:59:59Z"

    def test_utc_timezone(self):
        """UTC: päivä pysyy samana"""
        from shopify_client import _date_range_utc
        min_dt, max_dt = _date_range_utc(date(2026, 3, 26), "UTC")
        assert min_dt == "2026-03-26T00:00:00Z"
        assert max_dt == "2026-03-26T23:59:59Z"


class TestOrderNormalization:
    """Testaa tilausten normalisointia ilman tietokantaa."""

    def test_normalize_basic_order(self):
        from db import _normalize_order
        order = {
            "id": "12345",
            "name": "#1001",
            "created_at": "2026-03-26T10:00:00Z",
            "total_price": "199.00",
            "subtotal_price": "189.00",
            "total_tax": "10.00",
            "total_discounts": "0.00",
            "financial_status": "paid",
            "fulfillment_status": "fulfilled",
            "cancelled_at": None,
            "cancel_reason": None,
            "customer": {"id": "c1", "email": "test@example.com", "orders_count": 1},
            "line_items": [
                {"product_id": "p1", "title": "Tuote", "sku": "T-001",
                 "quantity": 2, "price": "94.50"},
            ],
            "refunds": [],
            "payment_gateway": "shopify_payments",
        }
        row = _normalize_order(date(2026, 3, 26), order)

        assert row["report_date"] == "2026-03-26"
        assert row["order_id"] == "12345"
        assert row["total_price"] == 199.0
        assert row["is_cancelled"] is False
        assert row["items_count"] == 2
        assert len(row["line_items"]) == 1
        assert row["line_items"][0]["sku"] == "T-001"

    def test_normalize_cancelled_order(self):
        from db import _normalize_order
        order = {
            "id": "99999",
            "name": "#1002",
            "created_at": "2026-03-26T11:00:00Z",
            "total_price": "0.00",
            "subtotal_price": "0.00",
            "total_tax": "0.00",
            "total_discounts": "0.00",
            "financial_status": "voided",
            "fulfillment_status": None,
            "cancelled_at": "2026-03-26T12:00:00Z",
            "cancel_reason": "customer",
            "customer": None,
            "line_items": [],
            "refunds": [],
            "payment_gateway": None,
        }
        row = _normalize_order(date(2026, 3, 26), order)
        assert row["is_cancelled"] is True
        assert row["cancel_reason"] == "customer"
        assert row["customer_id"] is None

    def test_normalize_order_with_no_customer(self):
        from db import _normalize_order
        order = {
            "id": "88888",
            "name": "#1003",
            "created_at": "2026-03-26T13:00:00Z",
            "total_price": "50.00",
            "subtotal_price": "50.00",
            "total_tax": "0.00",
            "total_discounts": "0.00",
            "financial_status": "paid",
            "fulfillment_status": None,
            "cancelled_at": None,
            "cancel_reason": None,
            "customer": None,  # Vieras ostaja
            "line_items": [],
            "refunds": [],
            "payment_gateway": "shopify_payments",
        }
        row = _normalize_order(date(2026, 3, 26), order)
        assert row["customer_id"] is None
        assert row["customer_email"] is None
        assert row["customer_orders_count"] == 0


class TestMetricsDbRow:
    """Testaa metriikoiden muuntamista tietokantariviksi."""

    def test_metrics_to_db_row_all_fields(self):
        from analyzer import DayMetrics, metrics_to_db_row
        m = DayMetrics(
            report_date=date(2026, 3, 26),
            total_orders=10,
            paid_orders=9,
            cancelled_orders=0,
            refunded_orders=1,
            pending_orders=0,
            fulfilled_orders=7,
            gross_revenue=1000.0,
            net_revenue=950.0,
            avg_order_value=100.0,
            total_discounts=50.0,
            new_customers=5,
            returning_customers=5,
            total_refunds=1,
            refund_amount=50.0,
            refund_rate_pct=10.0,
            cancellation_rate_pct=0.0,
            payment_issues=0,
        )
        row = metrics_to_db_row(m)

        assert row["report_date"] == "2026-03-26"
        assert row["total_orders"] == 10
        assert row["gross_revenue"] == 1000.0
        assert row["refund_rate_pct"] == 10.0
        assert "top_products" in row

    def test_metrics_to_db_row_rounds_decimals(self):
        from analyzer import DayMetrics, metrics_to_db_row
        m = DayMetrics(
            report_date=date(2026, 3, 26),
            gross_revenue=333.33333333,
            avg_order_value=111.11111111,
        )
        row = metrics_to_db_row(m)
        assert row["gross_revenue"] == 333.33
        assert row["avg_order_value"] == 111.11


class TestDuplicateProtectionLogic:
    """Testaa duplikaattisuojauslogiikkaa yksikkötasolla
    (ilman oikeaa Supabase-yhteyttä)."""

    def test_check_run_exists_returns_false_empty_data(self, monkeypatch):
        """check_run_exists palauttaa False jos dataa ei löydy."""
        import db

        class MockResult:
            data = []

        class MockQuery:
            def select(self, *a): return self
            def eq(self, *a): return self
            def limit(self, *a): return self
            def execute(self): return MockResult()

        class MockTable:
            def __call__(self, *a): return MockQuery()

        monkeypatch.setattr(db, "get_db", lambda: type("DB", (), {"table": MockTable()})())
        result = db.check_run_exists(date(2026, 3, 26))
        assert result is False

    def test_check_run_exists_returns_true_with_data(self, monkeypatch):
        """check_run_exists palauttaa True jos success-rivi löytyy."""
        import db

        class MockResult:
            data = [{"id": 1}]

        class MockQuery:
            def select(self, *a): return self
            def eq(self, *a): return self
            def limit(self, *a): return self
            def execute(self): return MockResult()

        class MockDB:
            def table(self, name): return MockQuery()

        monkeypatch.setattr(db, "get_db", lambda: MockDB())
        result = db.check_run_exists(date(2026, 3, 26))
        assert result is True
