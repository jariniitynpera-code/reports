"""
meeting_notes_main.py — Kokousmuistiot → ClickUp-tehtävät -orkestroija

Prosessi:
  1. Idempotenssisuoja (force-lippu ohittaa)
  2. Muistion luku (teksti / tiedosto / Google Drive)
  3. Action itemien tunnistus (Claude API tai sääntöpohjainen)
  4. Duplikaattitarkistus per item
  5. Tehtävien luonti / päivitys ClickUpissa
  6. Tallennus Supabaseen

Käyttö:
  python meeting_notes_main.py --text muistio.txt
  python meeting_notes_main.py --text muistio.txt --dry-run
  python meeting_notes_main.py --gdoc https://docs.google.com/document/d/...
  python meeting_notes_main.py --stdin < muistio.txt
  python meeting_notes_main.py --text muistio.txt --show-extraction
  python meeting_notes_main.py --text muistio.txt --force
  python meeting_notes_main.py --text muistio.txt --list-id 901234567890
  python meeting_notes_main.py --text muistio.txt --date 2026-03-28
  python meeting_notes_main.py --text muistio.txt --debug
"""

import argparse
import logging
import sys
from datetime import date, datetime
from pathlib import Path

import config

log = logging.getLogger(__name__)


# ── Loggaus ───────────────────────────────────────────────────────────────────

def setup_logging(debug: bool = False) -> None:
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)-24s %(message)s",
        handlers=[logging.StreamHandler()],
    )
    for noisy in ("httpx", "httpcore", "urllib3", "requests", "googleapiclient"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# ── CLI-argumentit ────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Kokousmuistiot → ClickUp-tehtävät"
    )

    # Lähde (yksi pakollinen)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--text",
        metavar="FILE",
        help="Paikallinen tekstitiedosto (txt, md)",
    )
    source.add_argument(
        "--gdoc",
        metavar="URL_OR_ID",
        help="Google Drive -dokumentti (URL tai file ID)",
    )
    source.add_argument(
        "--stdin",
        action="store_true",
        help="Lue muistio stdinistä",
    )

    # Metatiedot
    parser.add_argument(
        "--date",
        type=lambda s: date.fromisoformat(s),
        default=None,
        help="Kokouksen päivämäärä YYYY-MM-DD (oletus: päätellään lähteestä)",
    )
    parser.add_argument(
        "--title",
        default=None,
        help="Kokouksen otsikko (valinnainen, käytetään tehtävien kuvauksissa)",
    )
    parser.add_argument(
        "--attendees",
        default="",
        help="Osallistujat pilkulla erotettuina (valinnainen)",
    )

    # ClickUp-kohde
    parser.add_argument(
        "--list-id",
        default=None,
        help="ClickUp-lista-ID (oletus: MEETING_NOTES_CLICKUP_LIST_ID)",
    )

    # Ajotavat
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Tunnista tehtävät mutta älä luo ClickUpiin",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Aja uudelleen vaikka sama muistio on jo käsitelty",
    )
    parser.add_argument(
        "--show-extraction",
        action="store_true",
        help="Tulosta extraction-tulos konsolille",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Lisää debug-lokit",
    )

    return parser.parse_args()


# ── Pääprosessi ───────────────────────────────────────────────────────────────

def run(
    source_type:    str,
    source_value:   str,
    meeting_date:   date | None    = None,
    meeting_title:  str | None     = None,
    attendees:      list[str]      = None,
    list_id:        str | None     = None,
    dry_run:        bool           = False,
    force:          bool           = False,
    show_extraction: bool          = False,
) -> int:
    """Ajaa koko kokousmuistio-prosessin.

    Returns:
        0 = onnistui
        1 = virhe
        2 = ohitettu (sama muistio jo käsitelty)
    """
    import meeting_notes_db
    import meeting_notes_reader as reader
    import meeting_notes_extractor as extractor
    import meeting_notes_tasks as tasks
    from clickup_client import ClickUpClient

    target_list_id = list_id or getattr(config, "MEETING_NOTES_CLICKUP_LIST_ID", "")
    if not target_list_id and not dry_run:
        log.error(
            "MEETING_NOTES_CLICKUP_LIST_ID ei asetettu. "
            "Lisää se .env-tiedostoon tai anna --list-id."
        )
        return 1

    log.info("=" * 60)
    log.info(f"KOKOUSMUISTIO-PROSESSOINTI — {source_type.upper()} (dry_run={dry_run})")
    log.info("=" * 60)

    # ── 1. Lue muistio ────────────────────────────────────────────────────────
    try:
        if source_type == "text":
            note = reader.read_from_file(source_value, meeting_date)
        elif source_type == "gdoc":
            note = reader.read_from_gdoc(source_value, meeting_date)
        elif source_type == "stdin":
            note = reader.read_from_text(
                source_value, title=meeting_title, meeting_date=meeting_date,
                attendees=attendees or []
            )
        else:
            log.error(f"Tuntematon lähdetyyppi: {source_type}")
            return 1
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        log.error(f"Muistion luku epäonnistui: {e}")
        return 1

    # Täydennä metatiedot komentoriviltä
    if meeting_title and not note.source_title:
        note.source_title = meeting_title
    if meeting_date and not note.meeting_date:
        note.meeting_date = meeting_date
    if attendees:
        note.attendees = attendees

    log.info(f"Muistio luettu: '{note.source_title}' ({note.source_type}), "
             f"{len(note.content)} merkkiä, päivä={note.meeting_date}")

    # ── 2. Idempotenssisuoja ──────────────────────────────────────────────────
    existing_source = None
    try:
        existing_source = meeting_notes_db.get_source_by_id(note.source_id)
    except Exception as e:
        log.debug(f"Lähteen tarkistus epäonnistui: {e}")

    if existing_source and not force and not dry_run:
        log.info(
            f"Muistio '{note.source_title}' on jo käsitelty "
            f"(source_id={note.source_id}) — ohitetaan."
        )
        log.info("Käytä --force ajaaksesi uudelleen.")
        return 2

    # ── 3. Tallennus Supabaseen (lähde ja ajo) ────────────────────────────────
    source_db_id = None
    run_id       = None

    try:
        source_db_id = meeting_notes_db.create_source(
            source_type=note.source_type,
            source_id=note.source_id,
            source_url=note.source_url,
            source_title=note.source_title,
            meeting_date=note.meeting_date,
            attendees=note.attendees,
            content_preview=note.content_preview,
            calendar_meta=note.calendar_meta,
        )
        run_id = meeting_notes_db.create_run(
            source_db_id=source_db_id,
            dry_run=dry_run,
            extraction_method="",
        )
        log.info(f"Ajo aloitettu: run_id={run_id}, source_db_id={source_db_id}")
    except Exception as e:
        log.warning(f"Supabase-tallennus epäonnistui (jatketaan silti): {e}")

    try:
        # ── 4. Extraction ─────────────────────────────────────────────────────
        log.info("Tunnistetaan action itemit...")
        extraction = extractor.extract_items(note)

        log.info(
            f"Extraction: {len(extraction.items)} kohtaa tunnistettu "
            f"(menetelmä: {extraction.extraction_method}, "
            f"malli: {extraction.model_used or '–'})"
        )

        # Tulosta extraction jos pyydetty
        if show_extraction:
            _print_extraction(extraction)

        # ── 5. Julkaisu ClickUpiin ────────────────────────────────────────────
        cu      = ClickUpClient()
        results = tasks.publish_extraction(extraction, cu, target_list_id, dry_run)

        # ── 6. Tallennus Supabaseen ───────────────────────────────────────────
        if source_db_id and run_id:
            try:
                _save_results_to_db(run_id, source_db_id, extraction, results)
            except Exception as e:
                log.warning(f"Audit trail -tallennus epäonnistui: {e}")

        # ── Yhteenveto ────────────────────────────────────────────────────────
        created = sum(1 for r in results if r.action == "created")
        updated = sum(1 for r in results if r.action == "updated")
        skipped = sum(1 for r in results if r.action == "skipped")
        errors  = sum(1 for r in results if r.action == "error")

        if run_id:
            try:
                meeting_notes_db.finish_run(
                    run_id=run_id,
                    status="success",
                    items_found=len(extraction.items),
                    tasks_created=created,
                    tasks_updated=updated,
                    tasks_skipped=skipped + sum(1 for r in results if r.action in ("dry_run",)),
                    model_used=extraction.model_used,
                    run_metadata={
                        "extraction_method": extraction.extraction_method,
                        "extraction_errors": extraction.extraction_errors,
                        "errors": errors,
                    },
                )
            except Exception as e:
                log.debug(f"finish_run epäonnistui: {e}")

        log.info("=" * 60)
        log.info(f"VALMIS — luotu: {created}, päivitetty: {updated}, "
                 f"ohitettu: {skipped}, virheitä: {errors}")
        if dry_run:
            log.info("DRY-RUN: ei muutoksia ClickUpissa")
        log.info("=" * 60)
        return 0

    except Exception as e:
        log.exception(f"Odottamaton virhe: {e}")
        if run_id:
            try:
                meeting_notes_db.finish_run(run_id, "failed", error_message=str(e))
            except Exception:
                pass
        return 1


def _save_results_to_db(run_id, source_db_id, extraction, results) -> None:
    """Tallentaa kaikki extraction-kohdat ja task-mappaukset Supabaseen."""
    import meeting_notes_db

    for item in extraction.items:
        try:
            extraction_id = meeting_notes_db.save_extraction(run_id, source_db_id, item)
        except Exception as e:
            log.debug(f"Extraction-tallennus epäonnistui ({item.title[:30]}): {e}")
            continue

        # Etsi vastaava TaskResult
        for result in results:
            if result.fingerprint == item.fingerprint and result.task_id:
                try:
                    meeting_notes_db.save_task_mapping(
                        extraction_id=extraction_id,
                        fingerprint=item.fingerprint,
                        clickup_task_id=result.task_id,
                        clickup_task_url=result.task_url,
                        action=result.action,
                    )
                except Exception as e:
                    log.debug(f"Task-mapping-tallennus epäonnistui: {e}")


def _print_extraction(extraction) -> None:
    """Tulostaa extraction-tuloksen ihmisluettavassa muodossa."""
    note = extraction.meeting_note
    print("\n" + "=" * 70)
    print(f" EXTRACTION-TULOS — {note.source_title}")
    print(f" Päivä: {note.meeting_date} | Menetelmä: {extraction.extraction_method}")
    print("=" * 70)

    if not extraction.items:
        print("  (ei tunnistettuja kohtia)")
    for i, item in enumerate(extraction.items, 1):
        create_flag = "✓ TEHTÄVÄKSI" if item.should_create_task else "✗ EI TEHTÄVÄKSI"
        print(f"\n{i}. [{item.item_type.upper()}] {item.title}")
        print(f"   Confidence: {item.confidence:.0%} | {create_flag}")
        if item.owner:
            print(f"   Vastuuhenkilö: {item.owner}")
        if item.due_hint:
            date_str = f" ({item.due_date_normalized})" if item.due_date_normalized else ""
            print(f"   Deadline: {item.due_hint}{date_str}")
        if item.source_quote:
            quote = item.source_quote[:120] + ("…" if len(item.source_quote) > 120 else "")
            print(f"   Lainaus: \"{quote}\"")
        if item.reason_if_not_created:
            print(f"   Syy: {item.reason_if_not_created}")

    if extraction.extraction_errors:
        print("\nVirheet extraction-vaiheessa:")
        for err in extraction.extraction_errors:
            print(f"  ! {err}")
    print("=" * 70)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    args = _parse_args()
    setup_logging(debug=args.debug)

    # Lue muistiosisältö
    source_type  = "text" if args.text else ("gdoc" if args.gdoc else "stdin")
    source_value = args.text or args.gdoc or ""

    if args.stdin:
        log.info("Luetaan muistio stdinistä...")
        try:
            source_value = sys.stdin.read()
        except KeyboardInterrupt:
            print("\nKeskeytettiin.")
            sys.exit(1)
        if not source_value.strip():
            print("Virhe: stdin on tyhjä.")
            sys.exit(1)

    attendees = [a.strip() for a in args.attendees.split(",") if a.strip()] if args.attendees else []

    exit_code = run(
        source_type=source_type,
        source_value=source_value,
        meeting_date=args.date,
        meeting_title=args.title,
        attendees=attendees,
        list_id=args.list_id,
        dry_run=args.dry_run,
        force=args.force,
        show_extraction=args.show_extraction,
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
