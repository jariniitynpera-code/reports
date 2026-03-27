#!/bin/bash
# ============================================================
# run_report.sh — Shopify Daily Report -käynnistysskripti
#
# Käyttö:
#   ./run_report.sh                    # Eilen (oletuspäivä)
#   ./run_report.sh --date 2026-03-25  # Tietty päivä
#   ./run_report.sh --dry-run          # Testiajo ilman ClickUp-julkaisua
#   ./run_report.sh --force            # Pakota uudelleenajo
#   ./run_report.sh --test-connection  # Testaa yhteydet
# ============================================================

set -euo pipefail

# Skriptin hakemisto (toimii myös cron-ajossa)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Python — käytä samaa ympäristöä kuin muissa projekteissa
PYTHON="${PYTHON:-/usr/bin/python3}"

# Jos virtuaaliympäristö on olemassa, aktivoidaan se
if [ -f "$SCRIPT_DIR/venv/bin/activate" ]; then
    source "$SCRIPT_DIR/venv/bin/activate"
fi

# Lokitiedosto cron-ajoa varten
LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOG_DIR"

TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
LOGFILE="$LOG_DIR/run_${TIMESTAMP}.log"

echo "============================================" | tee -a "$LOGFILE"
echo "Shopify Daily Report käynnistyy: $(date)" | tee -a "$LOGFILE"
echo "============================================" | tee -a "$LOGFILE"

cd "$SCRIPT_DIR"

# Aja Python-skripti, ohjaa ulostulo lokiin
"$PYTHON" main.py "$@" 2>&1 | tee -a "$LOGFILE"
EXIT_CODE=${PIPESTATUS[0]}

echo "" | tee -a "$LOGFILE"
echo "Ajo valmis. Exit-koodi: $EXIT_CODE ($(date))" | tee -a "$LOGFILE"

# Siivoa vanhat lokitiedostot (yli 30pv)
find "$LOG_DIR" -name "run_*.log" -mtime +30 -delete 2>/dev/null || true

exit $EXIT_CODE
