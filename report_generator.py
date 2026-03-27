"""
report_generator.py — Päiväraportin tekstigeneraattori

Tuottaa johtajatasoisen suomenkielisen raportin analyysituloksista.
Raportti on muotoiltu MarkDownina ClickUpia varten.
"""

from datetime import date
from analyzer import AnalysisResult, DayMetrics, Comparison, Alert

# Viikonpäivät suomeksi
WEEKDAYS_FI = {
    0: "maanantai",
    1: "tiistai",
    2: "keskiviikko",
    3: "torstai",
    4: "perjantai",
    5: "lauantai",
    6: "sunnuntai",
}

STATUS_LABELS = {
    "green":  "🟢 NORMAALI",
    "yellow": "🟡 HUOMIOITAVAA",
    "red":    "🔴 TOIMENPITEITÄ",
}


def build_summary_lines(result: AnalysisResult) -> list[str]:
    """Palauttaa lyhyen yhteenvetolistan Slack/email-ilmoituksia varten."""
    m    = result.metrics
    lines = []
    active = m.total_orders - m.cancelled_orders

    if active == 0:
        lines.append("Tilauksia: 0 kpl")
    else:
        lines.append(f"Tilauksia: {active} kpl")

    if m.gross_revenue:
        lines.append(f"Liikevaihto: {m.gross_revenue:,.2f} €")

    if m.avg_order_value:
        lines.append(f"Keskiostos: {m.avg_order_value:,.2f} €")

    if m.total_refunds:
        lines.append(f"Palautuksia: {m.total_refunds} kpl ({m.refund_amount:,.2f} €)")

    if result.comparison_7d and result.comparison_7d.revenue_delta_pct is not None:
        delta = result.comparison_7d.revenue_delta_pct
        arrow = "↑" if delta > 0 else "↓"
        lines.append(f"vs. 7pv ka.: {arrow} {delta:+.1f}%")

    return lines


def generate_report(result: AnalysisResult) -> str:
    """Generoi täydellisen päiväraportin AnalysisResult-olioista.

    Palauttaa Markdown-muotoisen tekstin ClickUp-julkaisua varten.
    """
    m       = result.metrics
    rd      = result.metrics.report_date
    weekday = WEEKDAYS_FI[rd.weekday()].capitalize()

    sections: list[str] = []

    sections.append(_section_header(rd, weekday, result.status_level))
    sections.append(_section_calendar(result.calendar_events))
    sections.append(_section_summary(m))
    sections.append(_section_observations(result.observations))
    sections.append(_section_top_products(m))
    sections.append(_section_inventory_risks(result.inventory_risks))
    sections.append(_section_risks(result.risks, result.alerts))
    sections.append(_section_recommendation(result.recommendation, result.status_level))
    sections.append(_section_followup_tasks(result.alerts))
    sections.append(_section_comparison(
        result.comparison_yesterday,
        result.comparison_7d,
        result.comparison_weekday,
        rd,
    ))

    return "\n\n---\n\n".join(s for s in sections if s.strip())


def get_task_name(report_date: date) -> str:
    """Palauttaa ClickUp-tehtävän nimen päivämäärän perusteella."""
    return f"Shopify päiväraportti {report_date.isoformat()}"


# ── Raportin osat ─────────────────────────────────────────────────────────────

def _section_calendar(calendar_events: list[dict]) -> str:
    """Kalenteritapahtumat raporttipäivältä — näytetään vain jos on tapahtumia."""
    if not calendar_events:
        return ""

    try:
        from gcal_client import format_events_for_report
        text = format_events_for_report(calendar_events)
    except Exception:
        return ""

    if not text:
        return ""

    return f"> **Tänään kalenterissa:**\n> {text}"


def _section_header(rd: date, weekday: str, status_level: str) -> str:
    status_label = STATUS_LABELS.get(status_level, "")
    return (
        f"# Shopify päiväraportti – {rd.isoformat()}\n"
        f"**{weekday} | Status: {status_label}**"
    )


def _section_summary(m: DayMetrics) -> str:
    active_orders = m.total_orders - m.cancelled_orders
    lines = [
        "## 1. Yhteenveto",
        "",
        f"| Mittari | Arvo |",
        f"|---------|------|",
        f"| **Tilaukset yhteensä** | {m.total_orders} kpl |",
        f"| **Aktiivia tilauksia** | {active_orders} kpl |",
        f"| **Liikevaihto (brutto)** | {m.gross_revenue:,.2f} € |",
        f"| **Nettoliikevaihto** | {m.net_revenue:,.2f} € |",
        f"| **Keskiostos (AOV)** | {m.avg_order_value:,.2f} € |",
        f"| **Uudet asiakkaat** | {m.new_customers} kpl |",
        f"| **Palaavat asiakkaat** | {m.returning_customers} kpl |",
        f"| **Palautukset** | {m.total_refunds} kpl ({m.refund_amount:,.2f} €) |",
        f"| **Peruutetut tilaukset** | {m.cancelled_orders} kpl |",
        f"| **Maksuongelmat** | {m.payment_issues} kpl |",
    ]

    if m.total_discounts > 0:
        lines.append(f"| **Alennukset yhteensä** | {m.total_discounts:,.2f} € |")

    return "\n".join(lines)


def _section_observations(observations: list[str]) -> str:
    if not observations:
        return "## 2. Keskeiset havainnot\n\nEi erityisiä havaintoja."

    lines = ["## 2. Keskeiset havainnot", ""]
    for obs in observations:
        lines.append(f"- {obs}")

    return "\n".join(lines)


def _section_top_products(m: DayMetrics) -> str:
    if not m.top_products:
        return "## 3. Top-tuotteet\n\nEi tuotedataa saatavilla."

    lines = ["## 3. Top-tuotteet", ""]

    # Taulukointi jos enemmän kuin 3 tuotetta
    if len(m.top_products) >= 3:
        lines.append("| Tuote | SKU | Kpl | Myynti |")
        lines.append("|-------|-----|-----|--------|")
        for p in m.top_products[:8]:
            sku = p.get("sku") or "—"
            lines.append(
                f"| {p['title'][:50]} | {sku} | {p['quantity']} | {p['revenue']:,.2f} € |"
            )
    else:
        for i, p in enumerate(m.top_products, 1):
            sku_str = f" (SKU: {p['sku']})" if p.get("sku") else ""
            lines.append(
                f"{i}. **{p['title']}**{sku_str} — "
                f"{p['quantity']} kpl, {p['revenue']:,.2f} €"
            )

    return "\n".join(lines)


def _section_inventory_risks(inventory_risks: list) -> str:
    """Varastoriskit — näytetään vain jos löytyy riskejä."""
    if not inventory_risks:
        return ""

    lines = ["## 4a. Varastoriskit", ""]
    for r in inventory_risks:
        if r.stock_qty <= 0:
            lines.append(f"🚨 **{r.title}** (SKU: {r.sku}) — varasto lopussa!")
        elif r.days_of_stock is not None:
            lines.append(
                f"⚠️ **{r.title}** (SKU: {r.sku}) — "
                f"varastoa {r.stock_qty} kpl "
                f"(riittää n. {r.days_of_stock:.1f} pv nykyisellä tahdilla)"
            )
        else:
            lines.append(
                f"⚠️ **{r.title}** (SKU: {r.sku}) — varastoa vain {r.stock_qty} kpl"
            )

    return "\n".join(lines)


def _section_risks(risks: list[str], alerts: list[Alert]) -> str:
    if not risks:
        return "## 4. Riskit ja poikkeamat\n\nEi havaittuja riskejä tai poikkeamia. ✓"

    lines = ["## 4. Riskit ja poikkeamat", ""]

    critical = [a for a in alerts if a.severity == "critical"]
    warnings = [a for a in alerts if a.severity == "warning"]

    if critical:
        lines.append("**Kriittiset:**")
        for a in critical:
            lines.append(f"- ⚠️ {a.description}")
        lines.append("")

    if warnings:
        lines.append("**Huomioitavaa:**")
        for a in warnings:
            lines.append(f"- ℹ️ {a.description}")

    return "\n".join(lines)


def _section_recommendation(recommendation: str, status_level: str) -> str:
    icon = {"green": "✅", "yellow": "⚡", "red": "🚨"}.get(status_level, "")
    return (
        f"## 5. Tämän päivän suositus\n\n"
        f"{icon} **{recommendation}**"
    )


def _section_followup_tasks(alerts: list[Alert]) -> str:
    tasks_to_create = [a for a in alerts if a.create_task and a.task_name]

    lines = ["## 6. Automaattiset toimenpiteet"]

    if not tasks_to_create:
        lines.append("")
        lines.append("Ei automaattisia tehtäviä tämän raportin perusteella.")
        return "\n".join(lines)

    lines.append("")
    lines.append("Seuraavat tehtävät luodaan tai päivitetään ClickUpiin:")
    lines.append("")
    for a in tasks_to_create:
        severity_label = "🔴 Kriittinen" if a.severity == "critical" else "🟡 Huomio"
        lines.append(f"- **{a.task_name}** ({severity_label})")

    return "\n".join(lines)


def _section_comparison(
    yesterday: Comparison | None,
    avg_7d: Comparison | None,
    comp_weekday: Comparison | None,
    report_date: date,
) -> str:
    lines = ["## 7. Vertailu normaaliin", ""]

    has_data = yesterday or avg_7d or comp_weekday

    if not has_data:
        lines.append(
            "_Ei riittävästi historiadataa vertailuun. "
            "Vertailu aktivoituu kun vähintään 3 päivän data on kerätty._"
        )
        return "\n".join(lines)

    if yesterday:
        lines.append("**Verrattuna eiliseen:**")
        lines.append(_format_comparison_row(yesterday))
        lines.append("")

    if avg_7d:
        lines.append("**Verrattuna 7 päivän keskiarvoon:**")
        lines.append(_format_comparison_row(avg_7d))
        lines.append("")

    if comp_weekday:
        lines.append(f"**{comp_weekday.label}:**")
        lines.append(_format_comparison_row(comp_weekday))
        lines.append("")
    else:
        weekday = WEEKDAYS_FI[report_date.weekday()]
        lines.append(
            f"_Viikonpäivävertailu ({weekday}) aktivoituu kun "
            f"saman viikonpäivän dataa on vähintään 3 viikon ajalta._"
        )

    return "\n".join(lines)


def _format_comparison_row(comp: Comparison) -> str:
    """Muotoilee yksittäisen vertailun tekstiksi."""
    parts = []

    if comp.revenue_delta_pct is not None:
        arrow = "↑" if comp.revenue_delta_pct > 0 else "↓" if comp.revenue_delta_pct < 0 else "→"
        parts.append(f"Liikevaihto {arrow} {comp.revenue_delta_pct:+.1f}%")

    if comp.orders_delta_pct is not None:
        arrow = "↑" if comp.orders_delta_pct > 0 else "↓" if comp.orders_delta_pct < 0 else "→"
        parts.append(f"Tilaukset {arrow} {comp.orders_delta_pct:+.1f}%")

    if comp.aov_delta_pct is not None:
        arrow = "↑" if comp.aov_delta_pct > 0 else "↓" if comp.aov_delta_pct < 0 else "→"
        parts.append(f"AOV {arrow} {comp.aov_delta_pct:+.1f}%")

    if not parts:
        return "Ei vertailutietoja saatavilla."

    return " | ".join(parts)
