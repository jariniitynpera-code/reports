"""
task_creator.py — Automaattisten ClickUp-tehtävien hallinta

Käy läpi analyysituloksen alertit ja luo tarvittavat follow-up-tehtävät.
Idempotenssi: sama tehtävätyyppi per päivä luodaan vain kerran.
Jos avoin tehtävä on jo olemassa, lisätään siihen kommentti.
"""

import logging
from datetime import date
from typing import Optional

import db
from clickup_client import ClickUpClient
from analyzer import Alert, AnalysisResult
import config

log = logging.getLogger(__name__)


def create_followup_tasks(
    result: AnalysisResult,
    clickup: ClickUpClient,
    list_id: str,
) -> list[dict]:
    """Luo tarvittavat follow-up-tehtävät ClickUpiin analyysituloksen perusteella.

    Palauttaa listan luoduista/päivitetyistä tehtävistä:
    [{"task_id": str, "task_name": str, "action": "created"/"updated"}]
    """
    rd      = result.metrics.report_date
    results = []

    tasks_to_create = [a for a in result.alerts if a.create_task and a.task_name]

    if not tasks_to_create:
        log.info(f"Ei automaattisia tehtäviä päivälle {rd}")
        return []

    for alert in tasks_to_create:
        task_result = _handle_alert_task(alert, rd, clickup, list_id)
        if task_result:
            results.append(task_result)

    log.info(f"Follow-up-tehtäviä käsitelty: {len(results)} kpl")
    return results


def _handle_alert_task(
    alert: Alert,
    report_date: date,
    clickup: ClickUpClient,
    list_id: str,
) -> Optional[dict]:
    """Luo tai päivittää yhden tehtävän alertin perusteella.

    Logiikka:
    1. Tarkista onko kantaan tallennettu alert jo olemassa → onko task_id?
    2. Tarkista ClickUpista onko saman tyypin avoin tehtävä
    3. Jos löytyy → lisää kommentti (ei duplikaattia)
    4. Jos ei löydy → luo uusi tehtävä
    """
    tag = f"alert-{alert.alert_type}"

    # Tarkista onko sama alert jo tallennettu kantaan
    existing_alert = db.get_open_alert(report_date, alert.alert_type)
    if existing_alert and existing_alert.get("clickup_task_id"):
        # Tehtävä on jo olemassa — lisää kommentti
        task_id = existing_alert["clickup_task_id"]
        comment = (
            f"Uusi havainto {report_date.isoformat()}: {alert.description}\n"
            f"Arvo: {alert.metric_value}, Raja: {alert.threshold_value}"
        )
        try:
            clickup.add_comment(task_id, comment)
            db.log_clickup_action(
                action="add_comment",
                status="success",
                report_date=report_date,
                clickup_task_id=task_id,
            )
            log.info(f"Kommentti lisätty olemassa olevaan tehtävään: {task_id}")
            return {"task_id": task_id, "task_name": alert.task_name, "action": "commented"}
        except Exception as e:
            log.warning(f"Kommentin lisäys epäonnistui ({task_id}): {e}")
            return None

    # Tarkista ClickUpista saman tyypin avoin tehtävä
    existing_cu_task = clickup.find_open_task_by_type(list_id, tag)
    if existing_cu_task:
        task_id = existing_cu_task["id"]
        comment = (
            f"📋 Uusi havainto {report_date.isoformat()}: {alert.description}"
        )
        try:
            clickup.add_comment(task_id, comment)
            # Päivitä alert-kanta viittaamaan tähän tehtävään
            db.upsert_alert(
                report_date=report_date,
                alert_type=alert.alert_type,
                severity=alert.severity,
                description=alert.description,
                metric_value=alert.metric_value,
                threshold_value=alert.threshold_value,
                clickup_task_id=task_id,
            )
            db.log_clickup_action(
                action="add_comment",
                status="success",
                report_date=report_date,
                clickup_task_id=task_id,
                clickup_list_id=list_id,
            )
            log.info(f"Kommentti lisätty ClickUp-tehtävään: {task_id} ({tag})")
            return {"task_id": task_id, "task_name": alert.task_name, "action": "commented"}
        except Exception as e:
            log.warning(f"ClickUp kommentti epäonnistui ({task_id}): {e}")

    # Luo uusi tehtävä
    priority = "high" if alert.severity == "critical" else "normal"
    description = _build_task_description(alert, report_date)

    try:
        task_data = clickup.create_followup_task(
            list_id=list_id,
            task_name=alert.task_name,
            description=description,
            alert_type=alert.alert_type,
            priority=priority,
        )
        task_id  = task_data.get("id")
        task_url = task_data.get("url", "")

        # Tallenna alert kantaan
        db.upsert_alert(
            report_date=report_date,
            alert_type=alert.alert_type,
            severity=alert.severity,
            description=alert.description,
            metric_value=alert.metric_value,
            threshold_value=alert.threshold_value,
            clickup_task_id=task_id,
        )
        db.log_clickup_action(
            action="create_task",
            status="success",
            report_date=report_date,
            clickup_task_id=task_id,
            clickup_list_id=list_id,
            request_body={"name": alert.task_name, "alert_type": alert.alert_type},
            response_body={"id": task_id, "url": task_url},
        )

        log.info(f"Follow-up-tehtävä luotu: {task_id} — {alert.task_name}")
        return {"task_id": task_id, "task_name": alert.task_name, "action": "created"}

    except Exception as e:
        log.error(f"Tehtävän luonti epäonnistui ({alert.task_name}): {e}")
        db.log_clickup_action(
            action="create_task",
            status="failed",
            report_date=report_date,
            clickup_list_id=list_id,
            error_message=str(e),
        )
        return None


def _build_task_description(alert: Alert, report_date: date) -> str:
    """Rakentaa tehtävän kuvauksen alertin tiedoista."""
    severity_label = "🔴 Kriittinen" if alert.severity == "critical" else "🟡 Varoitus"

    lines = [
        f"**Automaattinen tehtävä** — luotu Shopify-päiväraportista {report_date.isoformat()}",
        "",
        f"**Vakavuusaste:** {severity_label}",
        f"**Syy:** {alert.description}",
        "",
    ]

    if alert.metric_value is not None and alert.threshold_value is not None:
        lines.append(f"**Havaittu arvo:** {alert.metric_value}")
        lines.append(f"**Kynnysarvo:** {alert.threshold_value}")
        lines.append("")

    if alert.task_description:
        lines.append("**Lisätiedot:**")
        lines.append(alert.task_description)
        lines.append("")

    lines.append("---")
    lines.append(
        "_Tämä tehtävä on luotu automaattisesti. "
        "Merkitse tehtävä valmiiksi kun asia on selvitetty._"
    )

    return "\n".join(lines)
