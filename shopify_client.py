"""
shopify_client.py — Shopify Admin REST API -asiakas

Hakee tilaukset, asiakkaat ja palautukset valitulta päivältä.
Käyttää samaa OAuth client_credentials -autentikointia kuin muutkin
projektin Shopify-integraatiot.

Käyttö:
    client = ShopifyClient()
    orders = client.get_orders_for_date(date(2026, 3, 26))
"""

import time
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import requests

import config

log = logging.getLogger(__name__)

# Token-välimuisti — haetaan uusi 23h välein (kuten muissa integraatioissa)
_token_cache: dict = {"token": None, "expires_at": 0.0}


def _get_access_token() -> str:
    """Hakee Shopify Admin API -tokenin client credentials -virralla.
    Välimuistitetaan 23 tunnin ajan."""
    cache = _token_cache
    if cache["token"] and time.time() < cache["expires_at"]:
        return cache["token"]

    url = f"https://{config.SHOPIFY_SHOP}/admin/oauth/access_token"
    resp = requests.post(
        url,
        json={
            "grant_type":    "client_credentials",
            "client_id":     config.SHOPIFY_CLIENT_ID,
            "client_secret": config.SHOPIFY_CLIENT_SECRET,
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    token = data.get("access_token")
    if not token:
        raise RuntimeError(f"Shopify token-haku epäonnistui: {data}")

    cache["token"]      = token
    cache["expires_at"] = time.time() + 23 * 3600
    log.info("Shopify access token haettu")
    return token


def _shopify_rest_get(path: str, params: dict | None = None, retries: int = 4) -> dict:
    """Tekee Shopify Admin REST GET -pyynnön exponential backoff -retryllä."""
    url = f"https://{config.SHOPIFY_SHOP}/admin/api/{config.SHOPIFY_API_VERSION}/{path}"
    headers = {"X-Shopify-Access-Token": _get_access_token()}

    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=30)

            # Rate limit — Shopify palauttaa 429
            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", 2.0))
                log.warning(f"Shopify rate limit — odotetaan {retry_after}s")
                time.sleep(retry_after)
                continue

            resp.raise_for_status()
            return resp
        except requests.HTTPError as e:
            wait = 2 ** attempt
            log.warning(f"Shopify HTTP virhe (yritys {attempt + 1}/{retries}): {e} — {wait}s")
            if attempt < retries - 1:
                time.sleep(wait)
            else:
                raise
        except requests.RequestException as e:
            wait = 2 ** attempt
            log.warning(f"Shopify yhteysongelma (yritys {attempt + 1}/{retries}): {e} — {wait}s")
            if attempt < retries - 1:
                time.sleep(wait)
            else:
                raise

    raise RuntimeError("Shopify API -kutsu epäonnistui kaikkien yritysten jälkeen")


def _paginate(path: str, root_key: str, params: dict) -> list:
    """Iteroi kaikki sivut Shopify REST API:sta Link-headerin avulla."""
    results = []
    url = f"https://{config.SHOPIFY_SHOP}/admin/api/{config.SHOPIFY_API_VERSION}/{path}"
    headers = {"X-Shopify-Access-Token": _get_access_token()}

    current_params = dict(params)

    while url:
        for attempt in range(4):
            try:
                resp = requests.get(url, headers=headers, params=current_params, timeout=30)

                if resp.status_code == 429:
                    retry_after = float(resp.headers.get("Retry-After", 2.0))
                    log.warning(f"Rate limit — odotetaan {retry_after}s")
                    time.sleep(retry_after)
                    continue

                resp.raise_for_status()
                break
            except requests.RequestException as e:
                if attempt == 3:
                    raise
                time.sleep(2 ** attempt)

        data = resp.json()
        page_items = data.get(root_key, [])
        results.extend(page_items)
        log.debug(f"Haettu {len(page_items)} kpl (yht. {len(results)})")

        # Tarkista seuraava sivu Link-headerista
        link_header = resp.headers.get("Link", "")
        next_url = _parse_next_link(link_header)
        url = next_url
        current_params = {}  # URL sisältää jo parametrit cursor-sivutuksessa

    return results


def _parse_next_link(link_header: str) -> Optional[str]:
    """Jäsentää seuraavan sivun URL:n Shopify Link -headerista.
    Muoto: <https://...?page_info=...>; rel="next" """
    if not link_header:
        return None
    for part in link_header.split(","):
        part = part.strip()
        if 'rel="next"' in part:
            url_part = part.split(";")[0].strip()
            return url_part.strip("<>")
    return None


def _date_range_utc(report_date: date, tz_name: str) -> tuple[str, str]:
    """Palauttaa päivän alku- ja loppuajan UTC-aikana ISO 8601 -muodossa.

    Esim. 2026-03-26 Europe/Helsinki (UTC+3) →
        min = 2026-03-25T21:00:00Z
        max = 2026-03-26T20:59:59Z
    """
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo

    tz = ZoneInfo(tz_name)
    day_start_local = datetime(report_date.year, report_date.month, report_date.day,
                                0, 0, 0, tzinfo=tz)
    day_end_local   = datetime(report_date.year, report_date.month, report_date.day,
                                23, 59, 59, tzinfo=tz)

    return (
        day_start_local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        day_end_local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


class ShopifyClient:
    """Shopify Admin REST API -asiakas päiväraportointia varten."""

    def get_orders_for_date(self, report_date: date) -> list[dict]:
        """Hakee kaikki tilaukset annetulta kalenteripäivältä (paikallinen aika).

        Palauttaa listan raakoja Shopify-tilausobjekteja.
        Sisältää myös peruutetut ja palautetut tilaukset.
        """
        created_min, created_max = _date_range_utc(report_date, config.TIMEZONE)
        log.info(
            f"Haetaan tilaukset {report_date} "
            f"(UTC-ikkuna: {created_min} — {created_max})"
        )

        orders = _paginate(
            path="orders.json",
            root_key="orders",
            params={
                "status":           "any",
                "created_at_min":   created_min,
                "created_at_max":   created_max,
                "limit":            250,
                "fields": ",".join([
                    "id", "name", "email", "created_at", "updated_at",
                    "total_price", "subtotal_price", "total_tax",
                    "total_discounts", "financial_status", "fulfillment_status",
                    "cancelled_at", "cancel_reason",
                    "customer", "line_items", "refunds",
                    "payment_gateway", "tags",
                ]),
            },
        )

        log.info(f"Haettu yhteensä {len(orders)} tilausta päivälle {report_date}")
        return orders

    def get_refunds_for_date(self, report_date: date) -> list[dict]:
        """Hakee palautukset jotka luotiin annetulla päivällä.

        Huom: Palautetut tilaukset näkyvät myös get_orders_for_date()-tuloksissa
        refunds-kentässä. Tätä metodia käytetään jos tarvitaan tarkempaa dataa
        palautuksista jotka kohdistuvat eri päivän tilauksiin.
        """
        created_min, created_max = _date_range_utc(report_date, config.TIMEZONE)
        log.info(f"Haetaan palautukset {report_date}")

        # Haetaan tilaukset joissa on palautuksia tällä päivällä
        # Shopify ei salli suoraa refunds-endpoint-hakua päivämäärällä
        # joten haetaan updated_at-filtterillä ja suodatetaan
        refunded_orders = _paginate(
            path="orders.json",
            root_key="orders",
            params={
                "status":           "any",
                "updated_at_min":   created_min,
                "updated_at_max":   created_max,
                "financial_status": "refunded,partially_refunded",
                "limit":            250,
                "fields":           "id,name,refunds,total_price,financial_status",
            },
        )

        log.info(f"Haettu {len(refunded_orders)} palautettu/osin palautettu tilaus")
        return refunded_orders

    def test_connection(self) -> bool:
        """Testaa Shopify-yhteyden. Palauttaa True jos yhteys toimii."""
        try:
            resp = _shopify_rest_get("shop.json", params={"fields": "id,name,email"})
            shop = resp.json().get("shop", {})
            log.info(f"Shopify-yhteys OK: {shop.get('name')} ({shop.get('email')})")
            return True
        except Exception as e:
            log.error(f"Shopify-yhteys epäonnistui: {e}")
            return False
