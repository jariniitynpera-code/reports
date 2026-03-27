"""
main.py — Shopify Daily Report -orkestroija

Pääprosessi joka koordinoi kaikki vaiheet:
  1. Idempotenssisuoja (estetään tupla-ajo)
  2. Shopify-datan haku
  3. Datan tallennus Supabaseen
  4. Analyysi ja poikkeamien tunnistus
  5. Raportin generointi
  6. Julkaisu ClickUpiin
  7. Follow-up-tehtävien luonti
  8. Lokitus ja virheenkäsittely

Käyttö:
  python main.py                   # Eilen (oletuspäivä)
  python main.py --date 2026-03-25 # Tietty päivä
  python main.py --dry-run         # Generoi raportti, älä julkaise ClickUpiin
  python main.py --force           # Aja uudelleen vaikka raportti on jo olemassa
  python main.py --test-connection # Testaa Shopify ja ClickUp -yhteydet
"""

import argparse
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import config
from config import TIMEZONE

log = logging.getLogger(__name__)


# ── Loggauksen alustus ────────────────────────────────────────────────────────

def setup_logging(report_date: date) -> None:
    """Alustaa lokituksen sekä konsoliin että päiväkohtaiseen tiedostoon."""
    log_file = config.LOG_DIR / f"daily_report_{report_date.strftime('%Y%m%d')}.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)-20s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )

    # Hiljennetään verbose-kirjastot
    for noisy in ("httpx", "httpcore", "urllib3", "requests"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# ── Apufunktiot ───────────────────────────────────────────────────────────────

def _get_yesterday() -> date:
    """Palauttaa eilisen päivän paikallisen aikavyöhykkeen mukaan."""
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo
    tz = ZoneInfo(TIMEZONE)
    return datetime.now(tz).date() - timedelta(days=1)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Shopify Daily Report — generoi ja julkaisee päiväraportin",
    )
    parser.add_argument(
        "--date",
        type=lambda s: date.fromisoformat(s),
        default=None,
        help="Raporttipäivä muodossa YYYY-MM-DD (oletus: eilen)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generoi raportti mutta älä julkaise ClickUpiin",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Aja uudelleen vaikka raportti on jo olemassa",
    )
    parser.add_argument(
        "--test-connection",
        action="store_true",
        help="Testaa Shopify- ja ClickUp-yhteydet",
    )
    return parser.parse_args()


# ── Yhteyksien testaus ────────────────────────────────────────────────────────

def test_connections() -> bool:
    """Testaa kaikki kriittiset yhteydet. Palauttaa True jos kaikki OK."""
    from shopify_client import ShopifyClient
    from clickup_client import ClickUpClient

    print("Testataan yhteydet...")

    shopify_ok = ShopifyClient().test_connection()
    clickup_ok = ClickUpClient().test_connection()

    # Testaa myös ClickUp-lista
    if clickup_ok:
        cu = ClickUpClient()
        list_info = cu.get_list_info(config.CLICKUP_LIST_ID)
        if list_info:
            print(f"  ClickUp-lista: {list_info.get('name')} (id: {config.CLICKUP_LIST_ID})")
        else:
            print(f"  VAROITUS: ClickUp-listaa {config.CLICKUP_LIST_ID} ei löydy!")
            clickup_ok = False

    # Testaa Supabase
    try:
        from db import get_db
        db = get_db()
        db.table("automation_runs").select("id").limit(1).execute()
        print("  Supabase: OK")
        supabase_ok = True
    except Exception as e:
        print(f"  Supabase: VIRHE — {e}")
        supabase_ok = False

    all_ok = shopify_ok and clickup_ok and supabase_ok
    print(f"\nTulos: {'✅ Kaikki yhteydet toimivat' if all_ok else '❌ Ongelmia yhteyksissä'}")
    return all_ok


# ── Pääprosessi ───────────────────────────────────────────────────────────────

def run(report_date: date, dry_run: bool = False, force: bool = False) -> int:
    """Ajaa koko raportointiprosessin.

    Palauttaa exit-koodin:
      0 = onnistui
      1 = virhe
      2 = ohitettu (duplikaatti)
    """
    import db
    from shopify_client import ShopifyClient
    from clickup_client import ClickUpClient
    from analyzer import analyze, metrics_to_db_row
    from report_generator import generate_report, get_task_name
    from task_creator import create_followup_tasks

    log.info("=" * 60)
    log.info(f"SHOPIFY PÄIVÄRAPORTTI — {report_date} (dry_run={dry_run})")
    log.info("=" * 60)

    # ── 1. Idempotenssisuoja ──────────────────────────────────────────────────
    if not force and db.check_run_exists(report_date):
        log.info(f"Raportti päivälle {report_date} on jo olemassa — ohitetaan")
        log.info("Käytä --force ajaaksesi uudelleen.")
        return 2

    run_id = db.create_run(report_date)
    log.info(f"Ajo aloitettu: run_id={run_id}")

    try:
        # ── 2. Shopify-datan haku ─────────────────────────────────────────────
        log.info("Haetaan tilaukset Shopifysta...")
        shopify = ShopifyClient()
        orders  = shopify.get_orders_for_date(report_date)
        log.info(f"Tilauksia haettu: {len(orders)} kpl")

        # ── 3. Datan tallennus Supabaseen ─────────────────────────────────────
        log.info("Tallennetaan tilaukset Supabaseen...")
        db.upsert_orders(report_date, orders)

        # ── 4. Analyysia edeltävät integraatiot ──────────────────────────────
        log.info("Haetaan historiadata vertailua varten...")
        # 60 päivää takaisin viikonpäivävertailua varten (tarvitaan 4+ viikkoa)
        historical = db.get_historical_metrics(report_date, days=60)
        log.info(f"Historiadataa: {len(historical)} päivää")

        # Google Calendar -tapahtumat (valinnainen)
        calendar_events = []
        if config.GCAL_CREDENTIALS_FILE:
            log.info("Haetaan kalenteritapahtumat...")
            try:
                from gcal_client import get_calendar_events
                calendar_events = get_calendar_events(report_date)
                log.info(f"Kalenteritapahtumia: {len(calendar_events)} kpl")
            except Exception as e:
                log.warning(f"Kalenterihaku epäonnistui: {e}")

        log.info("Analysoidaan...")
        result = analyze(
            report_date,
            orders,
            historical,
            calendar_events=calendar_events,
            check_inventory=config.INVENTORY_CHECK_ENABLED,
        )

        # Tallennetaan metriikat
        metrics_row = metrics_to_db_row(result.metrics)
        db.upsert_metrics(metrics_row)

        # ── 5. Raportin generointi ────────────────────────────────────────────
        log.info("Generoidaan raportti...")
        from report_generator import build_summary_lines
        report_text   = generate_report(result)
        task_name     = get_task_name(report_date)
        summary_lines = build_summary_lines(result)

        log.info(f"Status: {result.status_level.upper()}")
        log.info(f"Havaintoja: {len(result.observations)}")
        log.info(f"Alertteja: {len(result.alerts)}")

        # ── 6. Julkaisu ClickUpiin ────────────────────────────────────────────
        clickup_task_id  = None
        clickup_task_url = None

        if dry_run:
            log.info("DRY-RUN — ei julkaista ClickUpiin")
            _print_dry_run_output(report_text, result)
        else:
            log.info("Julkaistaan raportti ClickUpiin...")
            cu = ClickUpClient()
            list_id = config.CLICKUP_LIST_ID

            # Etsi duplikaatti
            existing_task = cu.find_task_by_name(list_id, task_name)

            if existing_task:
                task_id = existing_task["id"]
                log.info(f"Olemassa oleva ClickUp-tehtävä löytyi: {task_id} — päivitetään")
                cu.update_report_task(task_id, report_text, result.status_level)
                clickup_task_id  = task_id
                clickup_task_url = existing_task.get("url", "")
                db.log_clickup_action(
                    action="update_report",
                    status="success",
                    report_date=report_date,
                    clickup_task_id=task_id,
                    clickup_list_id=list_id,
                )
            else:
                task_data = cu.create_report_task(
                    list_id=list_id,
                    task_name=task_name,
                    description=report_text,
                    status_level=result.status_level,
                    report_date_str=report_date.isoformat(),
                )
                clickup_task_id  = task_data.get("id")
                clickup_task_url = task_data.get("url", "")
                db.log_clickup_action(
                    action="create_report",
                    status="success",
                    report_date=report_date,
                    clickup_task_id=clickup_task_id,
                    clickup_list_id=list_id,
                    response_body={"id": clickup_task_id, "url": clickup_task_url},
                )
                log.info(f"ClickUp-raporttitehtävä luotu: {clickup_task_id}")

            # ── 7. Follow-up-tehtävät ─────────────────────────────────────────
            log.info("Tarkistetaan follow-up-tehtävät...")
            tasks_list_id = config.CLICKUP_TASKS_LIST_ID
            followup_results = create_followup_tasks(result, cu, tasks_list_id)
            if followup_results:
                log.info(
                    f"Follow-up-tehtäviä: "
                    + ", ".join(f"{r['action']} ({r['task_name']})" for r in followup_results)
                )

            # ── 8a. Slack-ilmoitus ────────────────────────────────────────────
            if config.SLACK_WEBHOOK_URL:
                log.info("Lähetetään Slack-ilmoitus...")
                from slack_client import send_report_notification
                send_report_notification(
                    report_date=report_date,
                    status_level=result.status_level,
                    summary_lines=summary_lines,
                    recommendation=result.recommendation,
                    clickup_url=clickup_task_url,
                    alerts_count=len(result.alerts),
                )

            # ── 8b. Sähköposti-ilmoitus ───────────────────────────────────────
            if config.ALERT_EMAIL and config.SMTP_HOST:
                log.info("Lähetetään sähköposti-ilmoitus...")
                from email_client import send_report_email
                send_report_email(
                    report_date=report_date,
                    status_level=result.status_level,
                    report_text=report_text,
                    summary_lines=summary_lines,
                    recommendation=result.recommendation,
                    clickup_url=clickup_task_url,
                    alerts_count=len(result.alerts),
                )

        # ── 10. Raportin tallennus kantaan ────────────────────────────────────
        db.upsert_report(
            report_date=report_date,
            report_text=report_text,
            status_level=result.status_level,
            clickup_task_id=clickup_task_id,
            clickup_task_url=clickup_task_url,
        )

        # ── Ajo valmis ────────────────────────────────────────────────────────
        db.finish_run(
            run_id,
            status="success",
            orders_fetched=len(orders),
            run_metadata={
                "status_level":    result.status_level,
                "alerts_count":    len(result.alerts),
                "clickup_task_id": clickup_task_id,
                "dry_run":         dry_run,
            },
        )

        log.info("=" * 60)
        log.info(f"RAPORTTI VALMIS — {report_date} [{result.status_level.upper()}]")
        if clickup_task_url:
            log.info(f"ClickUp: {clickup_task_url}")
        log.info("=" * 60)

        return 0

    except Exception as e:
        log.exception(f"Odottamaton virhe raporttiprosessissa: {e}")
        db.finish_run(run_id, status="failed", error_message=str(e))
        return 1


def _print_dry_run_output(report_text: str, result) -> None:
    """Tulostaa dry-run -raportin konsoliin."""
    print("\n" + "=" * 70)
    print(" DRY-RUN — GENEROITU RAPORTTI (ei julkaistu)")
    print("=" * 70)
    print(report_text)
    print("=" * 70)
    print(f"\nStatus: {result.status_level.upper()}")
    print(f"Alertteja: {len(result.alerts)}")
    tasks = [a for a in result.alerts if a.create_task]
    if tasks:
        print(f"Follow-up-tehtäviä luotaisiin: {len(tasks)} kpl")
        for a in tasks:
            print(f"  - {a.task_name}")
    print("=" * 70)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    args = _parse_args()

    # Tarkista konfiguraatio ennen mitään muuta
    try:
        config.validate_config()
    except RuntimeError as e:
        print(f"\nVIRHE: {e}\n")
        sys.exit(1)

    # Yhteystesti
    if args.test_connection:
        ok = test_connections()
        sys.exit(0 if ok else 1)

    # Valitse raporttipäivä
    report_date = args.date or _get_yesterday()
    setup_logging(report_date)

    log.info(f"Raporttipäivä: {report_date} (aikavyöhyke: {TIMEZONE})")

    exit_code = run(
        report_date=report_date,
        dry_run=args.dry_run,
        force=args.force,
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
