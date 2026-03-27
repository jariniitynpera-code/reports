"""
brief_main.py — Huomisen briiffi -orkestroija

Prosessi:
  1. Idempotenssisuoja
  2. Kalenteritapahtumien haku (valinnainen)
  3. ClickUp-tehtävien haku
  4. Shopify-signaalien haku
  5. Briiffin generointi
  6. Julkaisu ClickUpiin
  7. Tallennus Supabaseen
  8. Lokitus

Käyttö:
  python brief_main.py                      # Huominen (oletuspäivä)
  python brief_main.py --date 2026-03-28    # Tietty päivä
  python brief_main.py --dry-run            # Generoi, älä julkaise
  python brief_main.py --force              # Aja uudelleen vaikka on jo
  python brief_main.py --debug              # Lisää lokeja
"""

import argparse
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import config

log = logging.getLogger(__name__)


# ── Loggaus ───────────────────────────────────────────────────────────────────

def setup_logging(brief_date: date, debug: bool = False) -> None:
    log_file = config.LOG_DIR / f"daily_brief_{brief_date.strftime('%Y%m%d')}.log"
    level    = logging.DEBUG if debug else logging.INFO

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)-20s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )
    for noisy in ("httpx", "httpcore", "urllib3", "requests", "googleapiclient"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# ── Apufunktiot ───────────────────────────────────────────────────────────────

def _get_tomorrow() -> date:
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo
    tz = ZoneInfo(config.TIMEZONE)
    return datetime.now(tz).date() + timedelta(days=1)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Huomisen briiffi — generoi ja julkaisee päivän suunnitelman"
    )
    parser.add_argument(
        "--date",
        type=lambda s: date.fromisoformat(s),
        default=None,
        help="Briiffipäivä YYYY-MM-DD (oletus: huominen)",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Generoi briiffi mutta älä julkaise ClickUpiin")
    parser.add_argument("--force", action="store_true",
                        help="Aja uudelleen vaikka briiffi on jo olemassa")
    parser.add_argument("--debug", action="store_true",
                        help="Lisää debug-lokit")
    return parser.parse_args()


# ── Pääprosessi ───────────────────────────────────────────────────────────────

def run(
    brief_date: date,
    dry_run:    bool = False,
    force:      bool = False,
) -> int:
    """Ajaa koko briiffiprosessin.

    Returns:
        0 = onnistui
        1 = virhe
        2 = ohitettu (duplikaatti)
    """
    import brief_db
    from brief_calendar import get_tomorrow_events
    from brief_tasks    import get_prioritized_tasks
    from brief_logic    import generate_brief
    from brief_publisher import publish_brief, get_brief_task_name
    from clickup_client import ClickUpClient

    log.info("=" * 60)
    log.info(f"HUOMISEN BRIIFFI — {brief_date} (dry_run={dry_run})")
    log.info("=" * 60)

    # ── 1. Idempotenssisuoja ──────────────────────────────────────────────────
    if not force and brief_db.check_run_exists(brief_date):
        log.info(f"Briiffi päivälle {brief_date} on jo olemassa — ohitetaan")
        log.info("Käytä --force ajaaksesi uudelleen.")
        return 2

    run_id = brief_db.create_run(brief_date)
    log.info(f"Ajo aloitettu: run_id={run_id}")

    try:
        # ── 2. Kalenteritapahtumat ────────────────────────────────────────────
        log.info(f"Haetaan kalenteritapahtumat päivälle {brief_date}...")
        events = get_tomorrow_events(brief_date)
        log.info(f"Kalenteritapahtumia: {len(events)} kpl")

        # ── 3. ClickUp-tehtävät ───────────────────────────────────────────────
        task_list_ids = _get_task_list_ids()
        tasks = []
        if task_list_ids:
            log.info(f"Haetaan tehtävät {len(task_list_ids)} listalta...")
            cu    = ClickUpClient()
            tasks = get_prioritized_tasks(cu, task_list_ids, brief_date)
            log.info(f"Tehtäviä haettu: {len(tasks)} kpl")
        else:
            log.info("BRIEF_CLICKUP_TASKS_LIST_ID ei asetettu — ohitetaan tehtävähaku")
            cu = ClickUpClient()

        # ── 4. Shopify-signaalit ──────────────────────────────────────────────
        shopify_signals = brief_db.get_shopify_signals(brief_date)
        if shopify_signals.has_open_alerts:
            log.info(f"Shopify-alertteja: {len(shopify_signals.alert_descriptions)} kpl")

        # ── 5. Briiffin generointi ────────────────────────────────────────────
        log.info("Generoidaan briiffi...")
        brief = generate_brief(
            tomorrow=brief_date,
            events=events,
            tasks=tasks,
            shopify_signals=shopify_signals,
            max_tasks=config.BRIEF_MAX_TASKS,
        )
        log.info(f"Briiffi generoitu: {brief.day_load.value}, "
                 f"{len(brief.selected_tasks)} tehtävää")

        # ── 6. Dry-run tai julkaisu ───────────────────────────────────────────
        clickup_task_id  = None
        clickup_task_url = None
        publish_action   = "dry-run"

        if dry_run:
            log.info("DRY-RUN — ei julkaista ClickUpiin")
            _print_dry_run(brief)
        else:
            list_id = config.BRIEF_CLICKUP_LIST_ID
            if not list_id:
                log.warning("BRIEF_CLICKUP_LIST_ID ei asetettu — ohitetaan julkaisu")
            else:
                log.info("Julkaistaan briiffi ClickUpiin...")
                clickup_task_id, clickup_task_url, publish_action = publish_brief(
                    brief=brief,
                    clickup=cu,
                    list_id=list_id,
                )
                log.info(f"ClickUp: {publish_action} — {clickup_task_url}")

        # ── 7. Tallennus Supabaseen ───────────────────────────────────────────
        # Tallennetaan myös dry-runissa (ilman ClickUp-tietoja)
        brief_db.save_brief(
            brief_date=brief_date,
            brief_text=brief.brief_text,
            day_load=brief.day_load.value,
            clickup_task_id=clickup_task_id,
            clickup_task_url=clickup_task_url,
            approval_status="suggested" if publish_action != "skipped" else "approved",
            source_summary=brief.source_summary,
        )

        # ── Ajo valmis ────────────────────────────────────────────────────────
        brief_db.finish_run(
            run_id=run_id,
            status="success",
            run_metadata={
                "day_load":        brief.day_load.value,
                "tasks_selected":  len(brief.selected_tasks),
                "events_count":    len(events),
                "publish_action":  publish_action,
                "clickup_task_id": clickup_task_id,
                "dry_run":         dry_run,
            },
        )

        log.info("=" * 60)
        log.info(f"BRIIFFI VALMIS — {brief_date} [{brief.day_load.value.upper()}]")
        if clickup_task_url:
            log.info(f"ClickUp: {clickup_task_url}")
        log.info("=" * 60)
        return 0

    except Exception as e:
        log.exception(f"Odottamaton virhe briiffiprosessissa: {e}")
        brief_db.finish_run(run_id, status="failed", error_message=str(e))
        return 1


def _get_task_list_ids() -> list[str]:
    """Palauttaa listan ClickUp-lista-ID:istä tehtävähakua varten."""
    raw = getattr(config, "BRIEF_CLICKUP_TASKS_LIST_ID", "") or ""
    if not raw:
        return []
    # Tukee pilkulla erotettua listaa: "id1,id2,id3"
    return [lid.strip() for lid in raw.split(",") if lid.strip()]


def _print_dry_run(brief) -> None:
    print("\n" + "=" * 70)
    print(" DRY-RUN — GENEROITU BRIIFFI (ei julkaistu)")
    print("=" * 70)
    print(brief.brief_text)
    print("=" * 70)
    print(f"\nPäivän kuorma: {brief.day_load.value.upper()}")
    print(f"Valitut tehtävät: {len(brief.selected_tasks)}")
    print(f"Kalenteritapahtumat: {len(brief.meetings)}")
    if brief.transition_warnings:
        print(f"Siirtymävaroitukset: {len(brief.transition_warnings)}")
    print("=" * 70)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    args = _parse_args()

    try:
        config.validate_config()
    except RuntimeError as e:
        print(f"\nVIRHE: {e}\n")
        sys.exit(1)

    brief_date = args.date or _get_tomorrow()
    setup_logging(brief_date, debug=args.debug)

    log.info(f"Briiffipäivä: {brief_date} (aikavyöhyke: {config.TIMEZONE})")

    exit_code = run(
        brief_date=brief_date,
        dry_run=args.dry_run,
        force=args.force,
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
