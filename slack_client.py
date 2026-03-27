"""
slack_client.py — Slack-ilmoitukset päiväraporteista

Lähettää tiivistetyn ilmoituksen Slack-kanavaan kun raportin status
on yellow tai red. Green-raportit ovat hiljaisia oletuksena.

Konfiguraatio:
  SLACK_WEBHOOK_URL  — Incoming Webhook URL
  SLACK_NOTIFY_GREEN — "true" jos haluat ilmoituksen myös vihreistä

Luo Slack Incoming Webhook:
  api.slack.com → Your Apps → Incoming Webhooks → Activate
  Kopioi Webhook URL → .env → SLACK_WEBHOOK_URL
"""

import logging
import os
from datetime import date
from typing import Optional

import requests

log = logging.getLogger(__name__)

STATUS_EMOJI = {"green": "🟢", "yellow": "🟡", "red": "🔴"}
STATUS_COLOR = {"green": "#36a64f", "yellow": "#f0a500", "red": "#e01e5a"}
STATUS_LABEL = {"green": "Normaali", "yellow": "Huomioitavaa", "red": "Toimenpiteitä"}


def send_report_notification(
    report_date: date,
    status_level: str,
    summary_lines: list[str],
    recommendation: str,
    clickup_url: Optional[str] = None,
    alerts_count: int = 0,
) -> bool:
    """Lähettää Slack-ilmoituksen päiväraportista.

    Lähettää aina red/yellow-statuksella.
    Green lähetetään vain jos SLACK_NOTIFY_GREEN=true.

    Palauttaa True jos lähetys onnistui.
    """
    webhook_url = os.getenv("SLACK_WEBHOOK_URL", "")
    if not webhook_url:
        log.debug("SLACK_WEBHOOK_URL ei asetettu — ohitetaan Slack-ilmoitus")
        return False

    notify_green = os.getenv("SLACK_NOTIFY_GREEN", "false").lower() == "true"

    if status_level == "green" and not notify_green:
        log.debug("Green-raportti — ei Slack-ilmoitusta (SLACK_NOTIFY_GREEN=false)")
        return False

    payload = _build_payload(
        report_date=report_date,
        status_level=status_level,
        summary_lines=summary_lines,
        recommendation=recommendation,
        clickup_url=clickup_url,
        alerts_count=alerts_count,
    )

    try:
        resp = requests.post(
            webhook_url,
            json=payload,
            timeout=10,
        )
        resp.raise_for_status()
        log.info(f"Slack-ilmoitus lähetetty (status={status_level})")
        return True
    except requests.RequestException as e:
        log.warning(f"Slack-ilmoitus epäonnistui: {e}")
        return False


def _build_payload(
    report_date: date,
    status_level: str,
    summary_lines: list[str],
    recommendation: str,
    clickup_url: Optional[str],
    alerts_count: int,
) -> dict:
    """Rakentaa Slack Block Kit -ilmoituksen."""
    emoji  = STATUS_EMOJI.get(status_level, "⚪")
    color  = STATUS_COLOR.get(status_level, "#aaaaaa")
    label  = STATUS_LABEL.get(status_level, "")

    weekdays = ["Ma", "Ti", "Ke", "To", "Pe", "La", "Su"]
    weekday  = weekdays[report_date.weekday()]
    date_str = f"{weekday} {report_date.strftime('%-d.%-m.%Y')}"

    # Pääotsikko
    header_text = f"{emoji} *Shopify-päiväraportti* — {date_str}  |  {label}"

    # Yhteenveto (max 4 riviä)
    summary_text = "\n".join(f"• {line}" for line in summary_lines[:4])

    # Suositus
    suositus_text = f"*Suositus:* {recommendation}"

    # Rakenna blocks
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"Shopify {date_str} — {label}"},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": header_text},
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": summary_text or "_Ei tilauksia._"},
        },
    ]

    if alerts_count > 0:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"⚠️ *Alertteja:* {alerts_count} kpl",
            },
        })

    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": suositus_text},
    })

    # ClickUp-linkki
    if clickup_url:
        blocks.append({
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Avaa raportti ClickUpissa"},
                    "url": clickup_url,
                    "style": "primary" if status_level == "red" else "default",
                }
            ],
        })

    # Käytä attachment väriä status-indikaattorina
    return {
        "text": f"Shopify päiväraportti {report_date} — {label}",
        "attachments": [
            {
                "color": color,
                "blocks": blocks,
                "fallback": f"Shopify päiväraportti {report_date}: {label}. {recommendation}",
            }
        ],
    }
