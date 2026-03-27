"""
clickup_client.py — ClickUp API v2 -asiakas

Hallitsee raporttitehtävien luomisen, päivittämisen ja
follow-up-tehtävien idempotentin käsittelyn ClickUpissa.

API-dokumentaatio: https://clickup.com/api/
"""

import logging
import time
from typing import Optional

import requests

import config

log = logging.getLogger(__name__)

CLICKUP_BASE_URL = "https://api.clickup.com/api/v2"

# Prioriteetti: 1=urgent, 2=high, 3=normal, 4=low
PRIORITY_MAP = {
    "red":    2,   # high
    "yellow": 3,   # normal
    "green":  4,   # low
}

# Status-väritunnisteet
STATUS_TAG_MAP = {
    "green":  "status-green",
    "yellow": "status-yellow",
    "red":    "status-red",
}


class ClickUpClient:
    """ClickUp API v2 -asiakas.

    Autentikoi henkilökohtaisella API-avaimella (CLICKUP_API_KEY).
    """

    def __init__(self):
        self._headers = {
            "Authorization": config.CLICKUP_API_KEY,
            "Content-Type":  "application/json",
        }

    def _request(
        self,
        method: str,
        path: str,
        json: dict | None = None,
        params: dict | None = None,
        retries: int = 3,
    ) -> dict:
        """Tekee ClickUp API -kutsun retryllä."""
        url = f"{CLICKUP_BASE_URL}/{path.lstrip('/')}"

        for attempt in range(retries):
            try:
                resp = requests.request(
                    method,
                    url,
                    headers=self._headers,
                    json=json,
                    params=params,
                    timeout=20,
                )

                if resp.status_code == 429:
                    retry_after = float(resp.headers.get("Retry-After", 2.0))
                    log.warning(f"ClickUp rate limit — odotetaan {retry_after}s")
                    time.sleep(retry_after)
                    continue

                resp.raise_for_status()
                return resp.json()

            except requests.HTTPError as e:
                if attempt == retries - 1:
                    raise
                wait = 2 ** attempt
                log.warning(f"ClickUp HTTP virhe (yritys {attempt + 1}/{retries}): {e} — {wait}s")
                time.sleep(wait)

            except requests.RequestException as e:
                if attempt == retries - 1:
                    raise
                wait = 2 ** attempt
                log.warning(f"ClickUp yhteysongelma (yritys {attempt + 1}/{retries}): {e} — {wait}s")
                time.sleep(wait)

        raise RuntimeError("ClickUp API -kutsu epäonnistui")

    # ── Tehtävien haku ────────────────────────────────────────────────────────

    def find_task_by_name(self, list_id: str, task_name: str) -> Optional[dict]:
        """Etsii tehtävän nimellä listasta. Palauttaa ensimmäisen osuman tai None.

        Käytetään duplikaattisuojaukseen: jos raportti on jo olemassa,
        päivitetään se luomisen sijaan.
        """
        try:
            data = self._request(
                "GET",
                f"list/{list_id}/task",
                params={
                    "page":           0,
                    "include_closed": "true",
                },
            )
            tasks = data.get("tasks", [])
            for task in tasks:
                if task.get("name", "").strip() == task_name.strip():
                    return task
            return None
        except Exception as e:
            log.warning(f"Tehtävän haku epäonnistui: {e}")
            return None

    def find_open_task_by_type(
        self, list_id: str, task_type_tag: str
    ) -> Optional[dict]:
        """Etsii avoimen tehtävän tagi-merkin perusteella.

        Käytetään follow-up-tehtävien idempotentissa luomisessa:
        jos saman tyypin tehtävä on jo auki, ei luoda uutta.
        """
        try:
            data = self._request(
                "GET",
                f"list/{list_id}/task",
                params={"page": 0, "include_closed": "false"},
            )
            tasks = data.get("tasks", [])
            for task in tasks:
                tags = [t.get("name", "") for t in task.get("tags", [])]
                if task_type_tag in tags:
                    return task
            return None
        except Exception as e:
            log.warning(f"Avoimen tehtävän haku epäonnistui: {e}")
            return None

    # ── Raporttitehtävän hallinta ─────────────────────────────────────────────

    def create_report_task(
        self,
        list_id: str,
        task_name: str,
        description: str,
        status_level: str,
        report_date_str: str,
    ) -> dict:
        """Luo uuden päiväraporttitehtävän ClickUpiin.

        Palauttaa luodun tehtävän tiedot (id, url).
        """
        tags = [
            "shopify",
            "daily-report",
            STATUS_TAG_MAP.get(status_level, "status-green"),
        ]

        payload = {
            "name":        task_name,
            "description": description,
            "priority":    PRIORITY_MAP.get(status_level, 3),
            "tags":        tags,
        }

        result = self._request("POST", f"list/{list_id}/task", json=payload)
        log.info(f"ClickUp-tehtävä luotu: {result.get('id')} — {task_name}")
        return result

    def update_report_task(
        self,
        task_id: str,
        description: str,
        status_level: str,
    ) -> dict:
        """Päivittää olemassa olevan raporttitehtävän kuvauksen ja tagin."""
        tags = [
            "shopify",
            "daily-report",
            STATUS_TAG_MAP.get(status_level, "status-green"),
        ]

        payload = {
            "description": description,
            "priority":    PRIORITY_MAP.get(status_level, 3),
        }

        result = self._request("PUT", f"task/{task_id}", json=payload)

        # Päivitä tagit erikseen (ClickUp API vaatii tag-operaatiot erikseen)
        self._update_task_tags(task_id, tags)

        log.info(f"ClickUp-tehtävä päivitetty: {task_id}")
        return result

    def _update_task_tags(self, task_id: str, tags: list[str]) -> None:
        """Asettaa tehtävän tagit. Poistaa vanhat status-tagit ensin."""
        # Hae nykyiset tagit
        try:
            task_data = self._request("GET", f"task/{task_id}")
            current_tags = [t.get("name", "") for t in task_data.get("tags", [])]

            # Poista vanhat status-tagit
            for old_tag in ["status-green", "status-yellow", "status-red"]:
                if old_tag in current_tags:
                    try:
                        self._request("DELETE", f"task/{task_id}/tag/{old_tag}")
                    except Exception:
                        pass

            # Lisää uudet tagit
            for tag in tags:
                try:
                    self._request("POST", f"task/{task_id}/tag/{tag}")
                except Exception as e:
                    log.debug(f"Tagin lisäys epäonnistui ({tag}): {e}")
        except Exception as e:
            log.warning(f"Tagien päivitys epäonnistui ({task_id}): {e}")

    def add_comment(self, task_id: str, comment: str) -> dict:
        """Lisää kommentin tehtävään."""
        result = self._request(
            "POST",
            f"task/{task_id}/comment",
            json={"comment_text": comment},
        )
        log.debug(f"Kommentti lisätty tehtävään {task_id}")
        return result

    # ── Follow-up-tehtävät ────────────────────────────────────────────────────

    def create_followup_task(
        self,
        list_id: str,
        task_name: str,
        description: str,
        alert_type: str,
        priority: str = "normal",
    ) -> dict:
        """Luo automaattisen follow-up-tehtävän ClickUpiin.

        alert_type käytetään taggina duplikaattisuojaukseen.
        """
        priority_num = {"urgent": 1, "high": 2, "normal": 3, "low": 4}.get(priority, 3)

        payload = {
            "name":        task_name,
            "description": description,
            "priority":    priority_num,
            "tags":        ["shopify", "auto-task", f"alert-{alert_type}"],
        }

        result = self._request("POST", f"list/{list_id}/task", json=payload)
        log.info(f"Follow-up-tehtävä luotu: {result.get('id')} — {task_name}")
        return result

    # ── Testaus ───────────────────────────────────────────────────────────────

    def test_connection(self) -> bool:
        """Testaa ClickUp-yhteyden. Palauttaa True jos yhteys toimii."""
        try:
            data = self._request("GET", "user")
            user = data.get("user", {})
            log.info(f"ClickUp-yhteys OK: {user.get('username')} ({user.get('email')})")
            return True
        except Exception as e:
            log.error(f"ClickUp-yhteys epäonnistui: {e}")
            return False

    def get_list_info(self, list_id: str) -> Optional[dict]:
        """Hakee listan tiedot. Käytetään konfiguraation tarkistukseen."""
        try:
            return self._request("GET", f"list/{list_id}")
        except Exception as e:
            log.error(f"Listan haku epäonnistui ({list_id}): {e}")
            return None
