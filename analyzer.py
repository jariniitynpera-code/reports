"""
analyzer.py — Tilausdatan analyysi ja poikkeamien tunnistus

Laskee päivämetriikat, vertaa historiaan ja tunnistaa poikkeamat.
Palauttaa AnalysisResult-olion joka sisältää kaiken raporttiin tarvittavan.
"""

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import config
from config import THRESHOLDS, TASK_RULES

log = logging.getLogger(__name__)


# ── Datatyypit ────────────────────────────────────────────────────────────────

@dataclass
class DayMetrics:
    """Yhden päivän aggregoitu myyntidata."""
    report_date:          date
    total_orders:         int   = 0
    paid_orders:          int   = 0
    cancelled_orders:     int   = 0
    refunded_orders:      int   = 0
    pending_orders:       int   = 0
    fulfilled_orders:     int   = 0
    gross_revenue:        float = 0.0
    net_revenue:          float = 0.0
    avg_order_value:      float = 0.0
    total_discounts:      float = 0.0
    new_customers:        int   = 0
    returning_customers:  int   = 0
    total_refunds:        int   = 0
    refund_amount:        float = 0.0
    refund_rate_pct:      float = 0.0
    cancellation_rate_pct: float = 0.0
    payment_issues:       int   = 0
    top_products:         list  = field(default_factory=list)


@dataclass
class Comparison:
    """Vertailu edelliseen päivään tai jaksoon."""
    label:                str
    revenue_delta_pct:    Optional[float] = None
    orders_delta_pct:     Optional[float] = None
    aov_delta_pct:        Optional[float] = None
    revenue_ref:          Optional[float] = None
    orders_ref:           Optional[float] = None


@dataclass
class Alert:
    """Tunnistettu poikkeama tai riski."""
    alert_type:      str
    severity:        str   # warning / critical
    description:     str
    metric_value:    Optional[float] = None
    threshold_value: Optional[float] = None
    create_task:     bool = False
    task_name:       Optional[str] = None
    task_description: Optional[str] = None


@dataclass
class AnalysisResult:
    """Täydellinen analyysin tulos. Sisältää kaiken raporttiä varten."""
    metrics:           DayMetrics
    status_level:      str            # green / yellow / red
    alerts:            list[Alert]    = field(default_factory=list)
    observations:      list[str]      = field(default_factory=list)
    risks:             list[str]      = field(default_factory=list)
    recommendation:    str            = ""
    comparison_yesterday: Optional[Comparison] = None
    comparison_7d:     Optional[Comparison] = None
    comparison_weekday: Optional[Comparison] = None  # Sama viikonpäivä
    calendar_events:   list[dict]     = field(default_factory=list)
    calendar_context:  Optional[str]  = None  # Selitys kalenterista
    inventory_risks:   list           = field(default_factory=list)


# ── Pääanalyysi ───────────────────────────────────────────────────────────────

def analyze(
    report_date: date,
    orders: list[dict],
    historical_metrics: list[dict],
    calendar_events: list[dict] | None = None,
    check_inventory: bool = True,
) -> AnalysisResult:
    """Analysoi päivän tilaukset ja palauttaa täydellisen analyysin.

    Args:
        report_date:        Analysoitava päivä
        orders:             Raaka Shopify-tilauslista
        historical_metrics: Aiemmat päivät Supabasesta (vanhimmasta uusimpaan)
        calendar_events:    Google Calendar -tapahtumat (valinnainen)
        check_inventory:    Tarkistetaanko varastoriskit Supabasesta
    """
    calendar_events = calendar_events or []

    metrics = _compute_metrics(report_date, orders)
    alerts  = _detect_anomalies(metrics, historical_metrics)

    # ── Varastoriskit ────────────────────────────────────────────────────────
    inventory_risks: list = []
    if check_inventory and metrics.top_products:
        try:
            from inventory_client import check_inventory_risks, risks_to_alerts
            inventory_risks = check_inventory_risks(metrics.top_products)
            if inventory_risks:
                alerts.extend(risks_to_alerts(inventory_risks))
                log.info(f"Varastoriskit lisätty: {len(inventory_risks)} kpl")
        except Exception as e:
            log.warning(f"Varastoriskitarkistus epäonnistui: {e}")

    # ── Vertailut ────────────────────────────────────────────────────────────
    comp_yesterday = None
    comp_7d        = None
    comp_weekday   = None

    if historical_metrics:
        yesterday_data = _find_yesterday(report_date, historical_metrics)
        if yesterday_data:
            comp_yesterday = _build_comparison("Eilen", yesterday_data, metrics)

        avg_7d = _compute_7d_average(historical_metrics)
        if avg_7d:
            comp_7d = _build_comparison("7pv ka.", avg_7d, metrics)

        # Viikonpäiväkohtainen vertailu (aktivoituu kun dataa on riittävästi)
        avg_weekday = _compute_weekday_average(report_date, historical_metrics)
        if avg_weekday:
            from datetime import date as _date
            weekday_names = ["maanantait", "tiistait", "keskiviikot",
                             "torstaita", "perjantait", "lauantait", "sunnuntait"]
            label = f"Sama viikonpäivä (ka. {weekday_names[report_date.weekday()]})"
            comp_weekday = _build_comparison(label, avg_weekday, metrics)

    # ── Kalenterikonteksti ───────────────────────────────────────────────────
    calendar_context = None
    if calendar_events:
        try:
            from gcal_client import explain_anomaly
            status_tmp = _classify_status(alerts)
            calendar_context = explain_anomaly(calendar_events, status_tmp)
        except Exception as e:
            log.debug(f"Kalenterikontekstin haku epäonnistui: {e}")

    status_level = _classify_status(alerts)
    observations = _build_observations(
        metrics, comp_yesterday, comp_7d, calendar_context
    )
    risks        = _build_risks(alerts)
    recommendation = _build_recommendation(status_level, alerts, metrics)

    log.info(
        f"Analyysi valmis: {report_date} — "
        f"status={status_level}, alertit={len(alerts)}, "
        f"tilauksia={metrics.total_orders}, liikevaihto={metrics.gross_revenue:.2f}€"
    )

    return AnalysisResult(
        metrics=metrics,
        status_level=status_level,
        alerts=alerts,
        observations=observations,
        risks=risks,
        recommendation=recommendation,
        comparison_yesterday=comp_yesterday,
        comparison_7d=comp_7d,
        comparison_weekday=comp_weekday,
        calendar_events=calendar_events,
        calendar_context=calendar_context,
        inventory_risks=inventory_risks,
    )


# ── Metriikoiden laskenta ─────────────────────────────────────────────────────

def _compute_metrics(report_date: date, orders: list[dict]) -> DayMetrics:
    """Laskee kaikki metriikat raakatilauksista."""
    m = DayMetrics(report_date=report_date)

    product_sales: dict[str, dict] = {}

    for order in orders:
        m.total_orders += 1
        price = float(order.get("total_price") or 0)
        discounts = float(order.get("total_discounts") or 0)
        m.total_discounts += discounts

        fin_status = order.get("financial_status", "")
        ful_status = order.get("fulfillment_status") or "unfulfilled"
        is_cancelled = bool(order.get("cancelled_at") or order.get("is_cancelled"))

        # Peruutetut
        if is_cancelled:
            m.cancelled_orders += 1
            continue  # Peruutetut eivät laske liikevaihtoon

        # Liikevaihto
        m.gross_revenue += price

        # Tilausten status
        if fin_status in ("paid", "partially_paid", "authorized"):
            m.paid_orders += 1
        elif fin_status in ("pending",):
            m.pending_orders += 1
            m.payment_issues += 1
        elif fin_status in ("voided",):
            m.payment_issues += 1
        elif fin_status in ("refunded", "partially_refunded"):
            m.refunded_orders += 1

        # Palautukset
        refunds = order.get("refunds") or []
        if refunds and fin_status in ("refunded", "partially_refunded"):
            m.total_refunds += 1
            refund_total = _sum_refund_amount(refunds)
            m.refund_amount += refund_total

        # Toimitukset
        if ful_status == "fulfilled":
            m.fulfilled_orders += 1

        # Asiakkaat — tuetaan sekä raakaa Shopify-formaattia (customer.orders_count)
        # että normalisoitua formaattia (customer_orders_count)
        customer = order.get("customer") or {}
        customer_orders_count = int(
            order.get("customer_orders_count")
            or customer.get("orders_count")
            or 0
        )
        if customer_orders_count <= 1:
            m.new_customers += 1
        else:
            m.returning_customers += 1

        # Tuotteet
        for item in order.get("line_items") or []:
            key = item.get("product_id") or item.get("title", "?")
            if key not in product_sales:
                product_sales[key] = {
                    "title":    item.get("title", "?"),
                    "sku":      item.get("sku", ""),
                    "quantity": 0,
                    "revenue":  0.0,
                }
            product_sales[key]["quantity"] += int(item.get("quantity", 0))
            product_sales[key]["revenue"]  += float(item.get("price", 0)) * int(item.get("quantity", 0))

    # Nettoliikevaihto (brutto - palautukset)
    m.net_revenue = m.gross_revenue - m.refund_amount

    # Keskiostos (ei-peruutetut, brutto)
    active_orders = m.total_orders - m.cancelled_orders
    if active_orders > 0:
        m.avg_order_value = m.gross_revenue / active_orders

    # Palautusaste
    if m.paid_orders + m.refunded_orders > 0:
        m.refund_rate_pct = (m.total_refunds / (m.paid_orders + m.refunded_orders)) * 100

    # Peruutusaste
    if m.total_orders > 0:
        m.cancellation_rate_pct = (m.cancelled_orders / m.total_orders) * 100

    # Top tuotteet (lajiteltu myynnin mukaan, max 10)
    m.top_products = sorted(
        product_sales.values(),
        key=lambda x: x["revenue"],
        reverse=True,
    )[:10]

    return m


def _sum_refund_amount(refunds: list[dict]) -> float:
    """Laskee palautusten yhteissumman.

    Yrittää ensin transactions-summaa, sitten estimoi order total:sta.
    """
    total = 0.0
    for refund in refunds:
        for txn in refund.get("transactions") or []:
            amount = float(txn.get("amount") or 0)
            kind   = txn.get("kind", "")
            # Refund-transaktio (ei capture tai sale)
            if kind in ("refund", ""):
                total += amount
    return total


# ── Historiallinen vertailu ───────────────────────────────────────────────────

def _find_yesterday(report_date: date, historical: list[dict]) -> Optional[dict]:
    """Etsii eilen-metriikat historialisesta datasta."""
    from datetime import timedelta
    yesterday_str = (report_date - timedelta(days=1)).isoformat()
    for row in historical:
        if row.get("report_date") == yesterday_str:
            return row
    return None


def _compute_weekday_average(report_date: date, historical: list[dict]) -> Optional[dict]:
    """Laskee saman viikonpäivän keskiarvon historiallisesta datasta.

    Aktivoituu kun vähintään 4 viikon dataa on saatavilla
    (vähintään 3 samaa viikonpäivää).
    """
    target_weekday = report_date.weekday()
    same_day_rows = [
        row for row in historical
        if date.fromisoformat(row["report_date"]).weekday() == target_weekday
    ]
    if len(same_day_rows) < 3:
        return None

    n = len(same_day_rows)
    return {
        "total_orders":    sum(r.get("total_orders", 0) for r in same_day_rows) / n,
        "gross_revenue":   sum(float(r.get("gross_revenue", 0)) for r in same_day_rows) / n,
        "avg_order_value": sum(float(r.get("avg_order_value", 0)) for r in same_day_rows) / n,
    }


def _compute_7d_average(historical: list[dict]) -> Optional[dict]:
    """Laskee 7 päivän keskiarvot. Palauttaa None jos dataa on alle 3 päivältä."""
    if len(historical) < THRESHOLDS.min_days_for_comparison:
        return None

    # Käytä enintään 7 viimeisintä päivää
    recent = historical[-7:] if len(historical) > 7 else historical

    n = len(recent)
    return {
        "total_orders":   sum(r.get("total_orders", 0) for r in recent) / n,
        "gross_revenue":  sum(float(r.get("gross_revenue", 0)) for r in recent) / n,
        "avg_order_value": sum(float(r.get("avg_order_value", 0)) for r in recent) / n,
    }


def _build_comparison(
    label: str,
    reference: dict,
    current: DayMetrics,
) -> Comparison:
    """Rakentaa vertailuobjektin viitearvoon nähden."""
    ref_revenue = float(reference.get("gross_revenue", 0))
    ref_orders  = float(reference.get("total_orders", 0))
    ref_aov     = float(reference.get("avg_order_value", 0))

    rev_delta = _pct_change(ref_revenue, current.gross_revenue)
    ord_delta = _pct_change(ref_orders,  float(current.total_orders - current.cancelled_orders))
    aov_delta = _pct_change(ref_aov,     current.avg_order_value)

    return Comparison(
        label=label,
        revenue_delta_pct=rev_delta,
        orders_delta_pct=ord_delta,
        aov_delta_pct=aov_delta,
        revenue_ref=ref_revenue,
        orders_ref=ref_orders,
    )


def _pct_change(reference: float, current: float) -> Optional[float]:
    """Laskee prosentuaalisen muutoksen. Palauttaa None jos referenssi on 0."""
    if reference == 0:
        return None
    return round(((current - reference) / reference) * 100, 1)


# ── Poikkeamien tunnistus ─────────────────────────────────────────────────────

def _detect_anomalies(
    m: DayMetrics,
    historical: list[dict],
) -> list[Alert]:
    """Tunnistaa poikkeamat metriikoista kynnysarvojen perusteella."""
    alerts: list[Alert] = []
    active_orders = m.total_orders - m.cancelled_orders

    # Laske 7pv vertailudata
    avg_7d = _compute_7d_average(historical)
    has_history = avg_7d is not None

    # ── Liikevaihdon pudotus ─────────────────────────────────────────────────
    if has_history and avg_7d["gross_revenue"] > 0:
        rev_change = _pct_change(avg_7d["gross_revenue"], m.gross_revenue)
        if rev_change is not None:
            if rev_change <= -THRESHOLDS.revenue_drop_critical:
                alerts.append(Alert(
                    alert_type="low_sales",
                    severity="critical",
                    description=f"Liikevaihto on pudonnut {abs(rev_change):.0f}% alle 7pv keskiarvon.",
                    metric_value=m.gross_revenue,
                    threshold_value=avg_7d["gross_revenue"],
                    create_task=rev_change <= -TASK_RULES.revenue_drop_task_threshold,
                    task_name=f"Tarkista myynnin lasku {m.report_date}",
                    task_description=(
                        f"Liikevaihto {m.report_date}: {m.gross_revenue:.2f}€\n"
                        f"7pv ka.: {avg_7d['gross_revenue']:.2f}€\n"
                        f"Muutos: {rev_change:.1f}%\n\n"
                        f"Tarkista: onko tekninen ongelma, kampanjapäättyminen "
                        f"tai muu syy?"
                    ),
                ))
            elif rev_change <= -THRESHOLDS.revenue_drop_warning:
                alerts.append(Alert(
                    alert_type="low_sales",
                    severity="warning",
                    description=f"Liikevaihto on {abs(rev_change):.0f}% alle 7pv keskiarvon.",
                    metric_value=m.gross_revenue,
                    threshold_value=avg_7d["gross_revenue"],
                ))
            elif rev_change >= THRESHOLDS.revenue_spike_warning:
                alerts.append(Alert(
                    alert_type="sales_spike",
                    severity="warning",
                    description=f"Liikevaihto on {rev_change:.0f}% yli 7pv keskiarvon — tarkista syy.",
                    metric_value=m.gross_revenue,
                    threshold_value=avg_7d["gross_revenue"],
                    create_task=rev_change >= TASK_RULES.sales_spike_task_threshold,
                    task_name=f"Tarkista myynnin piikki {m.report_date}",
                    task_description=(
                        f"Poikkeuksellinen myyntipiikki: {m.gross_revenue:.2f}€\n"
                        f"7pv ka.: {avg_7d['gross_revenue']:.2f}€ (+{rev_change:.0f}%)\n\n"
                        f"Varmista, että tilaukset ovat aitoja ja toimitus kestää."
                    ),
                ))

    # ── Palautusaste ─────────────────────────────────────────────────────────
    if active_orders >= THRESHOLDS.min_orders_for_pct_rules:
        if m.refund_rate_pct >= THRESHOLDS.refund_rate_critical:
            alerts.append(Alert(
                alert_type="high_refunds",
                severity="critical",
                description=(
                    f"Palautusaste {m.refund_rate_pct:.1f}% on kriittisellä tasolla "
                    f"({THRESHOLDS.refund_rate_critical:.0f}% raja)."
                ),
                metric_value=m.refund_rate_pct,
                threshold_value=THRESHOLDS.refund_rate_critical,
                create_task=m.refund_rate_pct >= TASK_RULES.refund_rate_task_threshold,
                task_name=f"Tarkista palautussyyt {m.report_date}",
                task_description=(
                    f"Palautusaste: {m.refund_rate_pct:.1f}% ({m.total_refunds} palautusta)\n"
                    f"Palautusten arvo: {m.refund_amount:.2f}€\n\n"
                    f"Tarkista mitkä tuotteet palautettiin ja miksi."
                ),
            ))
        elif m.refund_rate_pct >= THRESHOLDS.refund_rate_warning:
            alerts.append(Alert(
                alert_type="high_refunds",
                severity="warning",
                description=(
                    f"Palautuksia tuli hieman normaalia enemmän "
                    f"({m.refund_rate_pct:.1f}% tilauksista)."
                ),
                metric_value=m.refund_rate_pct,
                threshold_value=THRESHOLDS.refund_rate_warning,
            ))

    # ── Peruutusaste ─────────────────────────────────────────────────────────
    if m.total_orders >= THRESHOLDS.min_orders_for_pct_rules:
        if m.cancellation_rate_pct >= THRESHOLDS.cancellation_rate_critical:
            alerts.append(Alert(
                alert_type="high_cancellations",
                severity="critical",
                description=(
                    f"Peruutusaste {m.cancellation_rate_pct:.1f}% on poikkeuksellisen korkea."
                ),
                metric_value=m.cancellation_rate_pct,
                threshold_value=THRESHOLDS.cancellation_rate_critical,
                create_task=m.cancellation_rate_pct >= TASK_RULES.cancellation_rate_task_threshold,
                task_name=f"Selvitä peruutussyyt {m.report_date}",
                task_description=(
                    f"Peruutuksia: {m.cancelled_orders}/{m.total_orders} "
                    f"({m.cancellation_rate_pct:.1f}%)\n\n"
                    f"Tarkista peruutussyyt ja onko yhteistä tekijää."
                ),
            ))
        elif m.cancellation_rate_pct >= THRESHOLDS.cancellation_rate_warning:
            alerts.append(Alert(
                alert_type="high_cancellations",
                severity="warning",
                description=(
                    f"Peruutuksia normaalia enemmän "
                    f"({m.cancelled_orders} kpl, {m.cancellation_rate_pct:.1f}%)."
                ),
                metric_value=m.cancellation_rate_pct,
                threshold_value=THRESHOLDS.cancellation_rate_warning,
            ))

    # ── Maksuongelmat ─────────────────────────────────────────────────────────
    if m.payment_issues >= THRESHOLDS.payment_issue_critical:
        alerts.append(Alert(
            alert_type="payment_issues",
            severity="critical",
            description=f"Maksuongelmia: {m.payment_issues} kpl (pending/voided-tilaukset).",
            metric_value=float(m.payment_issues),
            threshold_value=float(THRESHOLDS.payment_issue_critical),
            create_task=m.payment_issues >= TASK_RULES.payment_issues_task_threshold,
            task_name=f"Tarkista maksuongelmat {m.report_date}",
            task_description=(
                f"Maksuongelmia havaittu: {m.payment_issues} kpl\n\n"
                f"Tarkista Shopify-maksuportaali ja varmista, "
                f"että maksuprosessori toimii normaalisti."
            ),
        ))
    elif m.payment_issues >= THRESHOLDS.payment_issue_warning:
        alerts.append(Alert(
            alert_type="payment_issues",
            severity="warning",
            description=f"Muutama maksuongelma: {m.payment_issues} tilausta pending/voided.",
            metric_value=float(m.payment_issues),
            threshold_value=float(THRESHOLDS.payment_issue_warning),
        ))

    return alerts


# ── Status-luokitus ───────────────────────────────────────────────────────────

def _classify_status(alerts: list[Alert]) -> str:
    """Luokittelee päivän statuksen alerttien perusteella.

    green  = ei alertteja tai vain minor-huomioita
    yellow = vähintään yksi warning
    red    = vähintään yksi critical
    """
    if any(a.severity == "critical" for a in alerts):
        return "red"
    if any(a.severity == "warning" for a in alerts):
        return "yellow"
    return "green"


# ── Havaintojen rakentaminen ───────────────────────────────────────────────────

def _build_observations(
    m: DayMetrics,
    yesterday: Optional[Comparison],
    avg_7d: Optional[Comparison],
    calendar_context: Optional[str] = None,
) -> list[str]:
    """Rakentaa 3–7 tärkeintä luonnollisen kielen havaintoa."""
    obs: list[str] = []

    active_orders = m.total_orders - m.cancelled_orders

    # Tilausmäärä
    if active_orders == 0:
        obs.append("Eilen ei tullut yhtään tilausta.")
    elif active_orders == 1:
        obs.append("Eilen tuli yksi tilaus.")
    else:
        obs.append(f"Eilen tehtiin {active_orders} tilausta.")

    # Liikevaihto
    if m.gross_revenue > 0:
        obs.append(f"Kokonaisliikevaihto oli {m.gross_revenue:,.2f}€.")

    # Vertailu eiliseen
    if yesterday and yesterday.revenue_delta_pct is not None:
        delta = yesterday.revenue_delta_pct
        if delta > 5:
            obs.append(
                f"Liikevaihto kasvoi {delta:.0f}% verrattuna edelliseen päivään "
                f"({yesterday.revenue_ref:,.2f}€)."
            )
        elif delta < -5:
            obs.append(
                f"Liikevaihto laski {abs(delta):.0f}% verrattuna edelliseen päivään "
                f"({yesterday.revenue_ref:,.2f}€)."
            )
        else:
            obs.append("Liikevaihto oli samalla tasolla kuin edellisenä päivänä.")

    # Asiakkaat
    if m.new_customers > 0 and m.returning_customers > 0:
        obs.append(
            f"Asiakkaista {m.new_customers} oli uusia ja "
            f"{m.returning_customers} palaavia."
        )
    elif m.new_customers > 0:
        obs.append(f"Kaikki {m.new_customers} asiakasta olivat uusia.")
    elif m.returning_customers > 0:
        obs.append(f"Kaikki {m.returning_customers} asiakasta olivat palaavia.")

    # Palautukset
    if m.total_refunds > 0:
        obs.append(
            f"Palautuksia: {m.total_refunds} kpl (yhteensä {m.refund_amount:,.2f}€)."
        )
    else:
        obs.append("Palautuksia ei tullut.")

    # Peruutukset
    if m.cancelled_orders > 0:
        obs.append(f"Peruutettuja tilauksia: {m.cancelled_orders} kpl.")

    # Top-tuote
    if m.top_products:
        top = m.top_products[0]
        obs.append(
            f"Myydyin tuote oli \"{top['title']}\" "
            f"({top['quantity']} kpl, {top['revenue']:,.2f}€)."
        )

    # 7pv vertailu
    if avg_7d and avg_7d.revenue_delta_pct is not None:
        delta = avg_7d.revenue_delta_pct
        if abs(delta) > 10:
            suunta = "yli" if delta > 0 else "alle"
            obs.append(
                f"Liikevaihto oli {abs(delta):.0f}% {suunta} 7 päivän keskiarvon "
                f"({avg_7d.revenue_ref:,.2f}€/pv)."
            )

    # Kalenterikonteksti selityksenä
    if calendar_context:
        obs.append(f"Kalenterimerkintä: {calendar_context}")

    return obs[:7]  # Maksimissaan 7 havaintoa


def _build_risks(alerts: list[Alert]) -> list[str]:
    """Muuntaa alertit riskikuvauksiksi."""
    return [a.description for a in alerts if a.severity in ("warning", "critical")]


def _build_recommendation(
    status_level: str,
    alerts: list[Alert],
    m: DayMetrics,
) -> str:
    """Rakentaa yhden selkeän toimintasuosituksen."""
    critical_alerts = [a for a in alerts if a.severity == "critical"]
    warning_alerts  = [a for a in alerts if a.severity == "warning"]

    if not alerts:
        return "Ei toimenpiteitä — päivä sujui normaalisti."

    if critical_alerts:
        # Palauta tärkein kriittinen suositus
        a = critical_alerts[0]
        type_map = {
            "high_refunds":       "Tarkista palautussyyt välittömästi.",
            "low_sales":          "Selvitä myynnin laskun syy — onko tekninen ongelma?",
            "high_cancellations": "Tarkista peruutussyyt ja ota yhteyttä asiakkaisiin.",
            "payment_issues":     "Tarkista maksuportaali ja maksujen käsittely.",
            "sales_spike":        "Varmista, että toimitus kestää poikkeuksellisen volyymin.",
        }
        return type_map.get(a.alert_type, "Vaatii huomion tänään.")

    if warning_alerts:
        a = warning_alerts[0]
        type_map = {
            "high_refunds":       f"Suosittelen tarkistamaan palautukset ({m.total_refunds} kpl).",
            "low_sales":          "Seuraa myyntiä lähipäivinä.",
            "high_cancellations": f"Tarkista peruutussyyt ({m.cancelled_orders} kpl).",
            "payment_issues":     f"Seuraa maksutilanteen kehittymistä ({m.payment_issues} kpl).",
            "sales_spike":        "Tarkista piikki — liikevaihto selvästi normaalia korkeampi.",
        }
        return type_map.get(a.alert_type, "Seuraa tilannetta.")

    return "Ei toimenpiteitä."


# ── Metriikat Supabase-muotoon ────────────────────────────────────────────────

def metrics_to_db_row(m: DayMetrics) -> dict:
    """Muuntaa DayMetrics-olion Supabase-tietokantariviksi."""
    return {
        "report_date":          m.report_date.isoformat(),
        "total_orders":         m.total_orders,
        "paid_orders":          m.paid_orders,
        "cancelled_orders":     m.cancelled_orders,
        "refunded_orders":      m.refunded_orders,
        "pending_orders":       m.pending_orders,
        "fulfilled_orders":     m.fulfilled_orders,
        "gross_revenue":        round(m.gross_revenue, 2),
        "net_revenue":          round(m.net_revenue, 2),
        "avg_order_value":      round(m.avg_order_value, 2),
        "total_discounts":      round(m.total_discounts, 2),
        "new_customers":        m.new_customers,
        "returning_customers":  m.returning_customers,
        "total_refunds":        m.total_refunds,
        "refund_amount":        round(m.refund_amount, 2),
        "refund_rate_pct":      round(m.refund_rate_pct, 2),
        "cancellation_rate_pct": round(m.cancellation_rate_pct, 2),
        "payment_issues":       m.payment_issues,
        "top_products":         m.top_products,
    }
