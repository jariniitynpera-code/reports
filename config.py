"""
config.py — Shopify Daily Report -konfiguraatio

Kaikki muuttujat luetaan .env-tiedostosta tai ympäristömuuttujista.
Säädä THRESHOLDS- ja TASK_RULES-luokkia muuttaaksesi kynnysarvoja
ilman koodimuutoksia itse logiikkaan.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

# Lataa .env samasta hakemistosta kuin tämä tiedosto
_env_path = Path(__file__).parent / ".env"
load_dotenv(_env_path)

# ── Pakolliset ympäristömuuttujat ─────────────────────────────────────────────

REQUIRED_VARS = [
    "SHOPIFY_SHOP",
    "SHOPIFY_CLIENT_ID",
    "SHOPIFY_CLIENT_SECRET",
    "SUPABASE_URL",
    "SUPABASE_SERVICE_KEY",
    "CLICKUP_API_KEY",
    "CLICKUP_LIST_ID",
]


def validate_config() -> None:
    """Tarkistaa, että pakolliset ympäristömuuttujat on asetettu.
    Nostaa RuntimeError:n puuttuvista muuttujista."""
    missing = [v for v in REQUIRED_VARS if not os.getenv(v)]
    if missing:
        raise RuntimeError(
            f"Puuttuvat ympäristömuuttujat: {', '.join(missing)}\n"
            f"Kopioi .env.example → .env ja täytä arvot."
        )


# ── Shopify ───────────────────────────────────────────────────────────────────

SHOPIFY_SHOP          = os.getenv("SHOPIFY_SHOP", "")
SHOPIFY_CLIENT_ID     = os.getenv("SHOPIFY_CLIENT_ID", "")
SHOPIFY_CLIENT_SECRET = os.getenv("SHOPIFY_CLIENT_SECRET", "")
SHOPIFY_API_VERSION   = os.getenv("SHOPIFY_API_VERSION", "2025-01")

# ── Supabase ──────────────────────────────────────────────────────────────────

SUPABASE_URL         = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")

# ── ClickUp ───────────────────────────────────────────────────────────────────

CLICKUP_API_KEY           = os.getenv("CLICKUP_API_KEY", "")
CLICKUP_LIST_ID           = os.getenv("CLICKUP_LIST_ID", "")
# Follow-up-tehtävät samaan listaan oletuksena, voi vaihtaa
CLICKUP_TASKS_LIST_ID     = os.getenv("CLICKUP_TASKS_LIST_ID", "") or CLICKUP_LIST_ID

# ── Aikavyöhyke ───────────────────────────────────────────────────────────────

TIMEZONE = os.getenv("TIMEZONE", "Europe/Helsinki")

# ── Hakemistot ────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
LOG_DIR  = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)


# ── Poikkeamien kynnysarvot ───────────────────────────────────────────────────
#
# Muuta näitä arvoja säätääksesi raportointiherkkyyttä.
# Kaikki prosenttiarvot ovat suhteellisia 7pv keskiarvoon.

@dataclass
class Thresholds:
    # Liikevaihdon poikkeama (%) verrattuna 7pv keskiarvoon
    revenue_drop_warning:    float = 30.0   # -30 % → yellow
    revenue_drop_critical:   float = 60.0   # -60 % → red
    revenue_spike_warning:   float = 150.0  # +150 % → yellow (tarkista syy)

    # Tilausmäärän poikkeama (%)
    orders_drop_warning:     float = 40.0
    orders_drop_critical:    float = 70.0

    # Palautusaste: % kaikista maksullisista tilauksista
    refund_rate_warning:     float = 10.0   # ≥ 10 % → yellow
    refund_rate_critical:    float = 20.0   # ≥ 20 % → red

    # Peruutusaste: % kaikista tilauksista
    cancellation_rate_warning:  float = 15.0
    cancellation_rate_critical: float = 30.0

    # Maksuongelmat (absoluuttinen määrä: pending + voided -tilaukset)
    payment_issue_warning:   int = 3
    payment_issue_critical:  int = 8

    # Minimipäivien data ennen kuin prosenttivertailu on mielekäs
    min_days_for_comparison: int = 3

    # Minimipäivän tilausmäärä ennen kuin prosenttisäännöt aktivoituvat
    min_orders_for_pct_rules: int = 2


THRESHOLDS = Thresholds()


# ── Automaattisten follow-up-tehtävien säännöt ────────────────────────────────
#
# Nämä ohjaavat, milloin järjestelmä luo ClickUp-tehtävän automaattisesti.

@dataclass
class TaskRules:
    # Luo tehtävä jos palautusaste ylittää tämän (%)
    refund_rate_task_threshold:        float = 15.0

    # Luo tehtävä jos liikevaihto putoaa yli tämän (% 7pv keskiarvosta)
    revenue_drop_task_threshold:       float = 50.0

    # Luo tehtävä jos maksuongelmia enemmän kuin tämä (kpl)
    payment_issues_task_threshold:     int   = 5

    # Luo tehtävä jos peruutusaste ylittää tämän (%)
    cancellation_rate_task_threshold:  float = 20.0

    # Luo tehtävä jos myynnissä poikkeuksellinen piikki (%)
    sales_spike_task_threshold:        float = 200.0


TASK_RULES = TaskRules()


# ── Google Calendar ───────────────────────────────────────────────────────────

# Palvelutilin JSON-avaintiedosto (tyhjä = kalenteriintegraatio pois)
GCAL_CREDENTIALS_FILE = os.getenv("GCAL_CREDENTIALS_FILE", "")
GCAL_CALENDAR_ID      = os.getenv("GCAL_CALENDAR_ID", "primary")

# ── Slack ─────────────────────────────────────────────────────────────────────

SLACK_WEBHOOK_URL  = os.getenv("SLACK_WEBHOOK_URL", "")
# "true" = lähetetään myös green-raportit Slackiin
SLACK_NOTIFY_GREEN = os.getenv("SLACK_NOTIFY_GREEN", "false")

# ── Sähköposti ────────────────────────────────────────────────────────────────

ALERT_EMAIL       = os.getenv("ALERT_EMAIL", "")
SMTP_HOST         = os.getenv("SMTP_HOST", "")
SMTP_PORT         = int(os.getenv("SMTP_PORT") or "587")
SMTP_USER         = os.getenv("SMTP_USER", "")
SMTP_PASS         = os.getenv("SMTP_PASS", "")
EMAIL_NOTIFY_GREEN = os.getenv("EMAIL_NOTIFY_GREEN", "false")

# ── Varasto ───────────────────────────────────────────────────────────────────

# Varaston hälytysraja kappaleissa (0 = varastoriskitarkistus pois)
INVENTORY_LOW_STOCK_THRESHOLD = int(os.getenv("INVENTORY_LOW_STOCK_THRESHOLD", "5"))
# "false" = ei tarkisteta varastoja (nopeuttaa ajoa jos ei tarvita)
INVENTORY_CHECK_ENABLED = os.getenv("INVENTORY_CHECK_ENABLED", "true").lower() == "true"
