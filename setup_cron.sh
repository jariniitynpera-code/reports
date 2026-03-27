#!/bin/bash
# ============================================================
# setup_cron.sh — Asettaa päiväraporttiajastuksen crontabiin
#
# Käyttö:
#   chmod +x setup_cron.sh
#   ./setup_cron.sh
#
# Oletuksena raportti ajetaan klo 07:30 joka aamu.
# Muuta CRON_TIME-muuttujaa haluamaksesi ajaksi.
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CRON_SCRIPT="$SCRIPT_DIR/run_report.sh"
CRON_TIME="${CRON_TIME:-30 7}"  # minuutti tunti (07:30)
PYTHON="${PYTHON:-/usr/bin/python3}"

if [ ! -f "$CRON_SCRIPT" ]; then
    echo "VIRHE: $CRON_SCRIPT ei löydy"
    exit 1
fi

chmod +x "$CRON_SCRIPT"

CRON_LINE="$CRON_TIME * * * $CRON_SCRIPT >> $SCRIPT_DIR/logs/cron.log 2>&1"
CRON_MARKER="# shopify-daily-report"

echo "Asetetaan cron-ajo: $CRON_LINE"
echo ""

# Lue nykyinen crontab, poista vanha merkintä, lisää uusi
(
    crontab -l 2>/dev/null | grep -v "$CRON_MARKER" || true
    echo "$CRON_MARKER"
    echo "$CRON_LINE"
) | crontab -

echo "✅ Cron asetettu onnistuneesti!"
echo ""
echo "Tarkista ajastus:"
crontab -l | grep -A1 "$CRON_MARKER"
echo ""
echo "Voit muuttaa aikaa ajamalla:"
echo "  CRON_TIME='0 8' ./setup_cron.sh   # klo 08:00"
echo "  CRON_TIME='30 6' ./setup_cron.sh  # klo 06:30"
