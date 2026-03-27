"""
inventory_client.py — Varastoriskien tunnistus

Vertaa päivän top-myytyjä tuotteita olemassa olevaan varastodataan
Supabasessa (supplier_product_variant-taulusta, jota Osculati FTP -sync ylläpitää).

Jos tuotteen varasto on alhainen samaan aikaan kun se myy hyvin,
generoidaan varastoriskiwarning analyysiin.

Palauttaa varastoriskialertit jotka lisätään analyysiin.
"""

import logging
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)

# Varaston hälytysraja (kappaletta). Säädä config.py:ssä tai tässä.
DEFAULT_LOW_STOCK_THRESHOLD = 5


@dataclass
class InventoryRisk:
    """Yksittäisen tuotteen varastoriski."""
    sku:          str
    title:        str
    stock_qty:    int
    daily_sold:   int
    days_of_stock: Optional[float]  # Kuinka monta päivää varastoa riittää
    severity:     str               # warning / critical


def check_inventory_risks(
    top_products: list[dict],
    low_stock_threshold: int = DEFAULT_LOW_STOCK_THRESHOLD,
) -> list[InventoryRisk]:
    """Tarkistaa varastoriskit top-myytyjille tuotteille.

    Args:
        top_products:        Analyysin top_products-lista [{title, sku, quantity, revenue}]
        low_stock_threshold: Varaston hälytysraja kappaleissa

    Palauttaa listan InventoryRisk-olioista.
    """
    if not top_products:
        return []

    # Kerää SKUt joilla on myyntiä
    skus_with_sales = [
        p for p in top_products
        if p.get("sku") and p.get("quantity", 0) > 0
    ]
    if not skus_with_sales:
        return []

    # Hae varastosaldot Supabasesta
    try:
        stock_data = _fetch_stock_levels([p["sku"] for p in skus_with_sales])
    except Exception as e:
        log.warning(f"Varastodatan haku epäonnistui: {e}")
        return []

    if not stock_data:
        log.debug("Varastodataa ei löytynyt — varastotarkistus ohitetaan")
        return []

    # Analysoi riskit
    risks: list[InventoryRisk] = []
    for product in skus_with_sales:
        sku       = product["sku"]
        stock_qty = stock_data.get(sku)

        if stock_qty is None:
            continue  # Tuotetta ei Supabasessa — ohitetaan

        daily_sold   = int(product.get("quantity", 0))
        days_of_stock = (stock_qty / daily_sold) if daily_sold > 0 else None

        if stock_qty <= 0:
            risks.append(InventoryRisk(
                sku=sku,
                title=product.get("title", sku),
                stock_qty=stock_qty,
                daily_sold=daily_sold,
                days_of_stock=0,
                severity="critical",
            ))
        elif stock_qty <= low_stock_threshold:
            severity = "critical" if stock_qty <= 2 else "warning"
            risks.append(InventoryRisk(
                sku=sku,
                title=product.get("title", sku),
                stock_qty=stock_qty,
                daily_sold=daily_sold,
                days_of_stock=days_of_stock,
                severity=severity,
            ))

    log.info(f"Varastoriskit: {len(risks)} kpl löytyi {len(skus_with_sales)} SKUsta")
    return risks


def _fetch_stock_levels(skus: list[str]) -> dict[str, int]:
    """Hakee varastosaldot Supabasesta SKU-listalle.

    Käyttää olemassa olevaa supplier_product_variant-taulua.
    Palauttaa {sku: stock_qty}.
    """
    from db import get_db
    db = get_db()

    result = (
        db.table("supplier_product_variant")
        .select("sku, stock_qty")
        .in_("sku", skus)
        .eq("is_active", True)
        .execute()
    )

    return {
        row["sku"]: int(row.get("stock_qty") or 0)
        for row in result.data
    }


def format_risks_for_report(risks: list[InventoryRisk]) -> Optional[str]:
    """Muotoilee varastoriskit raporttimuotoon."""
    if not risks:
        return None

    lines = []
    for r in risks:
        if r.stock_qty <= 0:
            lines.append(
                f"🚨 **{r['title']}** (SKU: {r.sku}) — varasto lopussa! "
                f"Eilen myytiin {r.daily_sold} kpl."
            )
        elif r.days_of_stock is not None:
            lines.append(
                f"⚠️ **{r.title}** (SKU: {r.sku}) — varastoa {r.stock_qty} kpl, "
                f"riittää n. {r.days_of_stock:.1f} päivää nykyisellä tahdilla."
            )
        else:
            lines.append(
                f"⚠️ **{r.title}** (SKU: {r.sku}) — varastoa vain {r.stock_qty} kpl."
            )

    return "\n".join(lines)


def risks_to_alerts(risks: list[InventoryRisk]) -> list[dict]:
    """Muuntaa varastoriskit analyzer.Alert-yhteensopiviksi diktiksi.

    Käytetään kun varastoriskit lisätään analyysiin.
    """
    from analyzer import Alert

    alerts = []
    for r in risks:
        if r.stock_qty <= 0:
            desc = (
                f"Tuotteen \"{r.title}\" varasto on lopussa "
                f"ja sitä myytiin eilen {r.daily_sold} kpl."
            )
            create_task = True
            task_name   = f"Tilaa tuotetta: {r.title} (varasto loppu)"
        else:
            days_str = f"(riittää n. {r.days_of_stock:.0f} pv)" if r.days_of_stock else ""
            desc = (
                f"Tuotteen \"{r.title}\" varasto on alhainen: "
                f"{r.stock_qty} kpl {days_str}."
            )
            create_task = r.severity == "critical"
            task_name   = f"Tarkista varasto: {r.title} ({r.stock_qty} kpl jäljellä)"

        alerts.append(Alert(
            alert_type="inventory_risk",
            severity=r.severity,
            description=desc,
            metric_value=float(r.stock_qty),
            threshold_value=float(DEFAULT_LOW_STOCK_THRESHOLD),
            create_task=create_task,
            task_name=task_name,
            task_description=(
                f"Tuote: {r.title}\n"
                f"SKU: {r.sku}\n"
                f"Varastossa: {r.stock_qty} kpl\n"
                f"Myyty eilen: {r.daily_sold} kpl\n\n"
                f"Tarkista tilaustarve ja täydennä varasto tarvittaessa."
            ),
        ))

    return alerts
