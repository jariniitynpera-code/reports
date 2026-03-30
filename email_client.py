"""
email_client.py — Sähköposti-ilmoitukset päiväraporteista ja briiffeistä

Lähettää tiivistetyn HTML-yhteenvedon sähköpostiin.
Lähettää oletuksena red- ja yellow-statuksella.
Green voidaan kytkeä päälle EMAIL_NOTIFY_GREEN=true.

Konfiguraatio .env-tiedostossa:
  ALERT_EMAIL        — Vastaanottajan sähköpostiosoite
  SMTP_HOST          — SMTP-palvelin (esim. smtp.gmail.com)
  SMTP_PORT          — SMTP-portti (587 = TLS, 465 = SSL)
  SMTP_USER          — SMTP-käyttäjänimi / sähköpostiosoite
  SMTP_PASS          — SMTP-salasana tai app password
  EMAIL_NOTIFY_GREEN — "true" lähettääksesi myös green-raportit

Gmail-ohje:
  SMTP_HOST=smtp.gmail.com
  SMTP_PORT=587
  SMTP_USER=sinun@gmail.com
  SMTP_PASS=[Google App Password — myaccount.google.com/apppasswords]
"""

import logging
import os
import smtplib
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

log = logging.getLogger(__name__)

STATUS_COLOR = {"green": "#2eb886", "yellow": "#f0a500", "red": "#e01e5a"}
STATUS_LABEL = {"green": "🟢 Normaali", "yellow": "🟡 Huomioitavaa", "red": "🔴 Toimenpiteitä"}


def send_report_email(
    report_date: date,
    status_level: str,
    report_text: str,
    summary_lines: list[str],
    recommendation: str,
    clickup_url: Optional[str] = None,
    alerts_count: int = 0,
) -> bool:
    """Lähettää raportin sähköpostitse.

    Palauttaa True jos lähetys onnistui.
    """
    alert_email = os.getenv("ALERT_EMAIL", "")
    smtp_host   = os.getenv("SMTP_HOST", "")

    if not alert_email or not smtp_host:
        log.debug("ALERT_EMAIL tai SMTP_HOST puuttuu — ohitetaan sähköposti")
        return False

    notify_green = os.getenv("EMAIL_NOTIFY_GREEN", "false").lower() == "true"
    if status_level == "green" and not notify_green:
        log.debug("Green-raportti — ei sähköpostia (EMAIL_NOTIFY_GREEN=false)")
        return False

    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER", alert_email)
    smtp_pass = os.getenv("SMTP_PASS", "")

    weekdays = ["maanantai", "tiistai", "keskiviikko", "torstai",
                "perjantai", "lauantai", "sunnuntai"]
    weekday  = weekdays[report_date.weekday()]
    subject  = (
        f"[{STATUS_LABEL.get(status_level, '')}] "
        f"Shopify {weekday} {report_date.strftime('%-d.%-m.%Y')}"
    )

    html_body = _build_html(
        report_date=report_date,
        status_level=status_level,
        summary_lines=summary_lines,
        recommendation=recommendation,
        report_text=report_text,
        clickup_url=clickup_url,
        alerts_count=alerts_count,
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = smtp_user
    msg["To"]      = alert_email

    # Plain text fallback
    plain = f"Shopify päiväraportti {report_date}\n\n" + "\n".join(summary_lines)
    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        if smtp_port == 465:
            with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=15) as server:
                if smtp_pass:
                    server.login(smtp_user, smtp_pass)
                server.send_message(msg)
        else:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                if smtp_pass:
                    server.login(smtp_user, smtp_pass)
                server.send_message(msg)

        log.info(f"Sähköposti lähetetty: {alert_email} (status={status_level})")
        return True

    except Exception as e:
        log.warning(f"Sähköpostin lähetys epäonnistui: {e}")
        return False


def _build_html(
    report_date: date,
    status_level: str,
    summary_lines: list[str],
    recommendation: str,
    report_text: str,
    clickup_url: Optional[str],
    alerts_count: int,
) -> str:
    """Rakentaa HTML-sähköpostin raporttista."""
    color      = STATUS_COLOR.get(status_level, "#888888")
    label      = STATUS_LABEL.get(status_level, "")
    date_str   = report_date.strftime("%-d.%-m.%Y")
    weekdays   = ["Maanantai", "Tiistai", "Keskiviikko", "Torstai",
                  "Perjantai", "Lauantai", "Sunnuntai"]
    weekday    = weekdays[report_date.weekday()]

    # Muunna Markdown-listaukset HTML-listaksi
    summary_items = "\n".join(
        f"        <li>{line}</li>" for line in summary_lines
    )

    clickup_btn = ""
    if clickup_url:
        clickup_btn = f"""
        <p style="margin-top:20px;">
          <a href="{clickup_url}"
             style="background:{color};color:#fff;padding:10px 20px;
                    border-radius:4px;text-decoration:none;font-weight:bold;">
            Avaa täydellinen raportti ClickUpissa →
          </a>
        </p>"""

    alerts_badge = ""
    if alerts_count > 0:
        alerts_badge = f"""
        <p style="color:{color};font-weight:bold;">
          ⚠️ Alertteja: {alerts_count} kpl — tarkista raportti
        </p>"""

    return f"""<!DOCTYPE html>
<html lang="fi">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width"></head>
<body style="font-family:Arial,sans-serif;background:#f5f5f5;margin:0;padding:0;">
  <table width="100%" cellpadding="0" cellspacing="0" bgcolor="#f5f5f5">
    <tr><td align="center" style="padding:20px 0;">
      <table width="600" cellpadding="0" cellspacing="0"
             style="background:#fff;border-radius:8px;overflow:hidden;
                    box-shadow:0 2px 8px rgba(0,0,0,0.1);">

        <!-- Otsikkopalki -->
        <tr>
          <td style="background:{color};padding:20px 24px;">
            <h1 style="color:#fff;margin:0;font-size:20px;">
              Shopify päiväraportti — {weekday} {date_str}
            </h1>
            <p style="color:rgba(255,255,255,0.85);margin:4px 0 0;font-size:14px;">
              {label}
            </p>
          </td>
        </tr>

        <!-- Yhteenveto -->
        <tr>
          <td style="padding:24px;">
            <h2 style="font-size:16px;color:#333;margin:0 0 12px;">
              Päivän havainnot
            </h2>
            <ul style="margin:0;padding-left:20px;color:#444;line-height:1.7;">
{summary_items}
            </ul>

            {alerts_badge}

            <!-- Suositus -->
            <div style="margin-top:20px;padding:16px;background:#f9f9f9;
                        border-left:4px solid {color};border-radius:0 4px 4px 0;">
              <strong>Tämän päivän suositus:</strong><br>
              <span style="color:#333;">{recommendation}</span>
            </div>

            {clickup_btn}

            <hr style="margin:24px 0;border:none;border-top:1px solid #eee;">
            <p style="font-size:12px;color:#999;margin:0;">
              Automaattinen raportti — Schooner Marine Supply<br>
              Luotu: {report_date.isoformat()}
            </p>
          </td>
        </tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""


# ── Briiffi-sähköposti ─────────────────────────────────────────────────────────

DAY_LOAD_COLOR = {
    "light":  "#2eb886",   # vihreä
    "normal": "#0073ea",   # sininen
    "tight":  "#f0a500",   # keltainen
    "moving": "#e01e5a",   # punainen
}
DAY_LOAD_EMOJI = {
    "light":  "🟢",
    "normal": "🔵",
    "tight":  "🟡",
    "moving": "🔴",
}
DAY_LOAD_LABEL_FI = {
    "light":  "Kevyt päivä",
    "normal": "Normaali päivä",
    "tight":  "Tiukka päivä",
    "moving": "Liikkuva päivä",
}


def send_brief_email(
    brief_date:   date,
    brief_text:   str,
    day_load:     str,              # "light" | "normal" | "tight" | "moving"
    tasks_count:  int       = 0,
    meetings_count: int     = 0,
    clickup_url:  Optional[str] = None,
    warnings:     list[str] = None,
) -> bool:
    """Lähettää huomisen briiffin sähköpostitse.

    Aina lähetetään riippumatta päivän kuormasta.
    Palauttaa True jos lähetys onnistui.
    """
    alert_email = os.getenv("ALERT_EMAIL", "")
    smtp_host   = os.getenv("SMTP_HOST", "")

    if not alert_email or not smtp_host:
        log.debug("ALERT_EMAIL tai SMTP_HOST puuttuu — ohitetaan briiffi-sähköposti")
        return False

    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER", alert_email)
    smtp_pass = os.getenv("SMTP_PASS", "")

    weekdays = ["maanantai", "tiistai", "keskiviikko", "torstai",
                "perjantai", "lauantai", "sunnuntai"]
    weekday  = weekdays[brief_date.weekday()]
    emoji    = DAY_LOAD_EMOJI.get(day_load, "🔵")
    label    = DAY_LOAD_LABEL_FI.get(day_load, "Normaali päivä")
    date_str = brief_date.strftime("%-d.%-m.")

    # Otsikko: selkeä, luettavissa suoraan inboxista
    subject = f"📋 Huominen {weekday} {date_str} — {emoji} {label}"
    if tasks_count:
        subject += f" | {tasks_count} tehtävää"

    html_body = _build_brief_html(
        brief_date=brief_date,
        brief_text=brief_text,
        day_load=day_load,
        tasks_count=tasks_count,
        meetings_count=meetings_count,
        clickup_url=clickup_url,
        warnings=warnings or [],
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = smtp_user
    msg["To"]      = alert_email

    plain = f"Huomisen briiffi {brief_date}\n\n{brief_text}"
    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    return _send_smtp(msg, smtp_host, smtp_port, smtp_user, smtp_pass, alert_email)


def _send_smtp(
    msg:       MIMEMultipart,
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_pass: str,
    recipient: str,
) -> bool:
    """Yhteinen SMTP-lähetyslogiikka."""
    try:
        if smtp_port == 465:
            with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=15) as server:
                if smtp_pass:
                    server.login(smtp_user, smtp_pass)
                server.send_message(msg)
        else:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                if smtp_pass:
                    server.login(smtp_user, smtp_pass)
                server.send_message(msg)

        log.info(f"Sähköposti lähetetty: {recipient}")
        return True
    except Exception as e:
        log.warning(f"Sähköpostin lähetys epäonnistui: {e}")
        return False


def _build_brief_html(
    brief_date:     date,
    brief_text:     str,
    day_load:       str,
    tasks_count:    int,
    meetings_count: int,
    clickup_url:    Optional[str],
    warnings:       list[str],
) -> str:
    """Rakentaa HTML-sähköpostin briiffistä."""
    color    = DAY_LOAD_COLOR.get(day_load, "#0073ea")
    emoji    = DAY_LOAD_EMOJI.get(day_load, "🔵")
    label    = DAY_LOAD_LABEL_FI.get(day_load, "Normaali päivä")
    weekdays = ["Maanantai", "Tiistai", "Keskiviikko", "Torstai",
                "Perjantai", "Lauantai", "Sunnuntai"]
    weekday  = weekdays[brief_date.weekday()]
    date_str = brief_date.strftime("%-d.%-m.%Y")

    # Muunna Markdown rivit HTML:ksi (yksinkertainen)
    import re
    brief_html = brief_text
    # **bold**
    brief_html = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", brief_html)
    # # otsikot
    brief_html = re.sub(r"^#{1,3}\s+(.+)$", r"<h3>\1</h3>", brief_html, flags=re.MULTILINE)
    # - listat
    brief_html = re.sub(r"^[-•]\s+(.+)$", r"<li>\1</li>", brief_html, flags=re.MULTILINE)
    brief_html = re.sub(r"(<li>.*</li>\n?)+", r"<ul>\g<0></ul>", brief_html, flags=re.DOTALL)
    # rivinvaihdot
    brief_html = re.sub(r"\n\n+", "</p><p>", brief_html)
    brief_html = f"<p>{brief_html}</p>"

    clickup_btn = ""
    if clickup_url:
        clickup_btn = f"""
        <p style="margin-top:20px;">
          <a href="{clickup_url}"
             style="background:{color};color:#fff;padding:10px 20px;
                    border-radius:4px;text-decoration:none;font-weight:bold;">
            Avaa briiffi ClickUpissa →
          </a>
        </p>"""

    warnings_html = ""
    if warnings:
        items = "\n".join(f"<li>⚠️ {w}</li>" for w in warnings)
        warnings_html = f"""
        <div style="margin-top:16px;padding:12px;background:#fff8e1;
                    border-left:4px solid #f0a500;border-radius:0 4px 4px 0;">
          <strong>Siirtymävaroitukset:</strong>
          <ul style="margin:8px 0 0;padding-left:20px;">{items}</ul>
        </div>"""

    stats = []
    if meetings_count:
        stats.append(f"📅 {meetings_count} kokousta")
    if tasks_count:
        stats.append(f"✅ {tasks_count} tehtävää")
    stats_html = " &nbsp;·&nbsp; ".join(stats)

    return f"""<!DOCTYPE html>
<html lang="fi">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width"></head>
<body style="font-family:Arial,sans-serif;background:#f5f5f5;margin:0;padding:0;">
  <table width="100%" cellpadding="0" cellspacing="0" bgcolor="#f5f5f5">
    <tr><td align="center" style="padding:20px 0;">
      <table width="600" cellpadding="0" cellspacing="0"
             style="background:#fff;border-radius:8px;overflow:hidden;
                    box-shadow:0 2px 8px rgba(0,0,0,0.1);">

        <tr>
          <td style="background:{color};padding:20px 24px;">
            <h1 style="color:#fff;margin:0;font-size:20px;">
              📋 Huominen — {weekday} {date_str}
            </h1>
            <p style="color:rgba(255,255,255,0.9);margin:6px 0 0;font-size:15px;">
              {emoji} {label} &nbsp;·&nbsp; {stats_html}
            </p>
          </td>
        </tr>

        <tr>
          <td style="padding:24px;">
            <div style="color:#333;line-height:1.7;font-size:15px;">
              {brief_html}
            </div>

            {warnings_html}
            {clickup_btn}

            <hr style="margin:24px 0;border:none;border-top:1px solid #eee;">
            <p style="font-size:12px;color:#999;margin:0;">
              Automaattinen briiffi — Schooner Marine Supply<br>
              Lähetetty: {brief_date.isoformat()}
            </p>
          </td>
        </tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""
