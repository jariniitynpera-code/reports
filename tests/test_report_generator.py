"""
test_report_generator.py — Yksikkötestit raporttigeneraattorille

Testaa raportin muotoilua, pakollisia osia ja sisältöä.
"""

import sys
import os
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analyzer import AnalysisResult, DayMetrics, Alert, Comparison
from report_generator import generate_report, get_task_name


def _make_minimal_result(
    report_date: date = date(2026, 3, 26),
    status_level: str = "green",
    total_orders: int = 5,
    gross_revenue: float = 500.0,
    alerts: list = None,
) -> AnalysisResult:
    m = DayMetrics(
        report_date=report_date,
        total_orders=total_orders,
        paid_orders=total_orders,
        gross_revenue=gross_revenue,
        net_revenue=gross_revenue,
        avg_order_value=gross_revenue / max(total_orders, 1),
        new_customers=3,
        returning_customers=2,
        top_products=[
            {"title": "Kompassi Pro", "sku": "KOMP-001", "quantity": 2, "revenue": 300.0},
            {"title": "Pelastusliivit", "sku": "PELL-002", "quantity": 1, "revenue": 200.0},
        ],
    )
    return AnalysisResult(
        metrics=m,
        status_level=status_level,
        alerts=alerts or [],
        observations=[
            "Eilen tehtiin 5 tilausta.",
            "Kokonaisliikevaihto oli 500,00€.",
        ],
        risks=[],
        recommendation="Ei toimenpiteitä — päivä sujui normaalisti.",
    )


class TestReportGeneration:

    def test_report_contains_date(self):
        result = _make_minimal_result(report_date=date(2026, 3, 26))
        report = generate_report(result)
        assert "2026-03-26" in report

    def test_report_contains_all_sections(self):
        result = _make_minimal_result()
        report = generate_report(result)
        assert "1. Yhteenveto" in report
        assert "2. Keskeiset havainnot" in report
        assert "3. Top-tuotteet" in report
        assert "4. Riskit ja poikkeamat" in report
        assert "5. Tämän päivän suositus" in report
        assert "6. Automaattiset toimenpiteet" in report
        assert "7. Vertailu normaaliin" in report

    def test_green_status_shown(self):
        result = _make_minimal_result(status_level="green")
        report = generate_report(result)
        assert "NORMAALI" in report or "green" in report.lower()

    def test_red_status_shown(self):
        alerts = [Alert("low_sales", "critical", "Myynti pudonnut")]
        result = _make_minimal_result(status_level="red", alerts=alerts)
        report = generate_report(result)
        assert "TOIMENPITEITÄ" in report or "red" in report.lower()

    def test_revenue_in_report(self):
        result = _make_minimal_result(gross_revenue=1234.56)
        report = generate_report(result)
        # Python käyttää oletuksena ,-erottimia tuhansille: 1,234.56
        assert "1,234" in report or "1 234" in report

    def test_no_orders_handled_gracefully(self):
        result = _make_minimal_result(total_orders=0, gross_revenue=0.0)
        report = generate_report(result)
        assert report is not None
        assert len(report) > 100

    def test_top_products_appear(self):
        result = _make_minimal_result()
        report = generate_report(result)
        assert "Kompassi Pro" in report
        assert "Pelastusliivit" in report

    def test_followup_tasks_listed_when_present(self):
        alerts = [
            Alert(
                "high_refunds", "critical",
                "Palautuksia liian paljon",
                create_task=True,
                task_name="Tarkista palautukset",
            )
        ]
        result = _make_minimal_result(status_level="red", alerts=alerts)
        report = generate_report(result)
        assert "Tarkista palautukset" in report

    def test_no_followup_tasks_says_so(self):
        result = _make_minimal_result(alerts=[])
        report = generate_report(result)
        assert "Ei automaattisia tehtäviä" in report

    def test_comparison_shown_when_available(self):
        result = _make_minimal_result()
        result.comparison_yesterday = Comparison(
            label="Eilen",
            revenue_delta_pct=15.5,
            orders_delta_pct=-5.0,
            aov_delta_pct=20.0,
            revenue_ref=433.0,
        )
        report = generate_report(result)
        assert "Eilen" in report or "+15" in report or "15.5" in report

    def test_task_name_format(self):
        name = get_task_name(date(2026, 3, 26))
        assert name == "Shopify päiväraportti 2026-03-26"

    def test_weekday_in_report(self):
        # 2026-03-26 on torstai
        result = _make_minimal_result(report_date=date(2026, 3, 26))
        report = generate_report(result)
        assert "Torstai" in report or "torstai" in report

    def test_risks_appear_when_alerts_present(self):
        alerts = [
            Alert("payment_issues", "warning", "Maksuongelmia havaittu: 5 kpl.")
        ]
        result = _make_minimal_result(
            status_level="yellow",
            alerts=alerts,
        )
        result.risks = [a.description for a in alerts]
        report = generate_report(result)
        assert "Maksuongelmia" in report

    def test_empty_risks_section_positive(self):
        result = _make_minimal_result(alerts=[])
        report = generate_report(result)
        assert "Ei havaittuja riskejä" in report
