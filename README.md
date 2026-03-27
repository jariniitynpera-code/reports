# Shopify Daily Report

Automaattinen päiväraporttigeneraattori Shopify-tapahtumista.

Joka aamu klo 7:30 järjestelmä hakee edellisen päivän Shopify-tapahtumat,
analysoi ne, generoi johtajatasoisen yhteenvedon suomeksi ja julkaisee
raportin ClickUpiin.

---

## Mitä se tekee

1. **Hakee** edellisen päivän tilaukset, palautukset ja asiakasdata Shopifysta
2. **Tallentaa** datan Supabaseen (normalisoituna ja raakana)
3. **Analysoi** metriikat ja vertaa 7 päivän keskiarvoon
4. **Tunnistaa** poikkeamat (palautuspiikki, myyntilasku, maksuongelmat jne.)
5. **Generoi** suomenkielisen johtajaraportin
6. **Julkaisee** raportin ClickUpiin (uusi tehtävä tai päivitys)
7. **Luo** automaattiset follow-up-tehtävät poikkeamista

---

## Arkkitehtuuri

```
main.py                 ← Pääorkestroija
├── shopify_client.py   ← Shopify REST API (tilaukset)
├── analyzer.py         ← Metriikat + poikkeamien tunnistus
├── report_generator.py ← Suomenkielinen tekstiraportti (Markdown)
├── task_creator.py     ← ClickUp follow-up-tehtävät
├── clickup_client.py   ← ClickUp API v2
├── db.py               ← Supabase-operaatiot
└── config.py           ← Konfiguraatio ja kynnysarvot
```

### Supabase-taulut

| Taulu | Tarkoitus |
|-------|-----------|
| `automation_runs` | Idempotenssisuoja — yksi success/päivä |
| `shopify_daily_orders` | Normalisoidut tilaukset (raakadata) |
| `shopify_daily_metrics` | Aggregoitu päivädata (vertailua varten) |
| `shopify_daily_reports` | Generoidut raportit + ClickUp-viitteet |
| `shopify_alerts` | Poikkeamat + follow-up-tehtävien viitteet |
| `clickup_sync_log` | Kaikki ClickUp API -kutsut (audit trail) |

---

## Käyttöönotto

### 1. Asenna riippuvuudet

```bash
cd DailyReports
pip install -r requirements.txt
```

### 2. Konfiguroi ympäristömuuttujat

```bash
cp .env.example .env
```

Täytä `.env`-tiedostoon:

| Muuttuja | Mistä löytyy |
|----------|-------------|
| `SHOPIFY_SHOP` | Kauppasi Shopify-osoite (esim. `schoonersupply.myshopify.com`) |
| `SHOPIFY_CLIENT_ID` | Shopify Admin → Apps → Develop apps → sovelluksesi |
| `SHOPIFY_CLIENT_SECRET` | Sama kuin yllä |
| `SUPABASE_URL` | Supabase Dashboard → Project Settings → API |
| `SUPABASE_SERVICE_KEY` | Supabase Dashboard → Project Settings → API → service_role |
| `CLICKUP_API_KEY` | ClickUp → Profile → Apps → API Token |
| `CLICKUP_LIST_ID` | Avaa lista ClickUpissa → URL: `.../l/XXXXXXXX` |

**Huom Shopify-kaupasta:** Varmista, että `SHOPIFY_SHOP` osoittaa **tuotantokauppaan**
eikä testistoren osoitteeseen. Testistoressa ei ole oikeita tilauksia.

### 3. Aja Supabase-migraatio

Avaa Supabase Dashboard → SQL Editor ja aja:

```sql
-- Kopioi ja aja koko tiedosto:
migrations/001_daily_report_tables.sql
```

### 4. Testaa yhteydet

```bash
python main.py --test-connection
```

Odotettu tulos:
```
  Shopify: OK — Schooner Marine Supply
  ClickUp: OK — jari.niitynpera
  ClickUp-lista: Päiväraportit (id: ...)
  Supabase: OK
✅ Kaikki yhteydet toimivat
```

### 5. Tee koeajo ilman julkaisua

```bash
python main.py --dry-run --date 2026-03-25
```

Raportti tulostuu konsoliin. ClickUpiin ei tehdä muutoksia.

### 6. Aseta ajastus

```bash
chmod +x setup_cron.sh run_report.sh
./setup_cron.sh
```

Oletuksena raportti ajetaan **klo 07:30** joka aamu.

---

## Käyttö

### Manuaalinen ajo

```bash
# Eilinen päivä (oletus)
python main.py

# Tietty päivä
python main.py --date 2026-03-25

# Dry-run — generoi raportti, älä julkaise
python main.py --dry-run

# Pakota uudelleenajo (ohittaa idempotenssisuojan)
python main.py --force --date 2026-03-25

# Testaa yhteydet
python main.py --test-connection
```

### Shell-skripti (suositeltu cron-ajossa)

```bash
./run_report.sh
./run_report.sh --date 2026-03-25
./run_report.sh --dry-run
```

---

## Raportin muoto

Raportti julkaistaan ClickUpiin Markdown-muodossa, otsikolla:
**`Shopify päiväraportti YYYY-MM-DD`**

Sisältö:
1. **Yhteenveto** — Tilaukset, liikevaihto, AOV, asiakkaat, palautukset
2. **Keskeiset havainnot** — 3–7 luonnollisen kielen havaintoa
3. **Top-tuotteet** — Parhaiten myyneet tuotteet/SKUt
4. **Riskit ja poikkeamat** — Tunnistetut ongelmat
5. **Tämän päivän suositus** — Yksi selkeä toimintasuositus
6. **Automaattiset toimenpiteet** — Mitä tehtäviä luotiin
7. **Vertailu normaaliin** — Eilen + 7pv ka.

### Status-luokitus

| Status | Merkitys | ClickUp-tagi |
|--------|----------|--------------|
| 🟢 NORMAALI | Kaikki olennaisesti normaalia | `status-green` |
| 🟡 HUOMIOITAVAA | Huomioitavaa, ei kriittistä | `status-yellow` |
| 🔴 TOIMENPITEITÄ | Selvä poikkeama, toimenpidetarve | `status-red` |

---

## Kynnysarvojen säätäminen

Avaa `config.py` ja muuta `THRESHOLDS`-luokan arvoja:

```python
@dataclass
class Thresholds:
    revenue_drop_warning:    float = 30.0   # -30% → yellow
    revenue_drop_critical:   float = 60.0   # -60% → red
    refund_rate_warning:     float = 10.0   # 10% → yellow
    refund_rate_critical:    float = 20.0   # 20% → red
    # ... jne.
```

### Follow-up-tehtävien säännöt

```python
@dataclass
class TaskRules:
    refund_rate_task_threshold:       float = 15.0  # Luo tehtävä yli 15%
    revenue_drop_task_threshold:      float = 50.0  # Luo tehtävä yli 50% pudotus
    payment_issues_task_threshold:    int   = 5     # Luo tehtävä yli 5 ongelmaa
    cancellation_rate_task_threshold: float = 20.0  # Luo tehtävä yli 20%
```

---

## ClickUp-rakenne

Raportit menevät yhteen listaan (oletuksena `CLICKUP_LIST_ID`):

- **Yksi tehtävä per päivä** — otsikko `Shopify päiväraportti YYYY-MM-DD`
- **Jos raportti on jo olemassa**, se päivitetään (ei duplikaattia)
- **Tagit**: `shopify`, `daily-report`, `status-green/yellow/red`
- **Prioriteetti**: low=green, normal=yellow, high=red

Follow-up-tehtävät menevät samaan listaan (tai eri listaan jos
`CLICKUP_TASKS_LIST_ID` on asetettu) tageilla `shopify`, `auto-task`,
`alert-{tyyppi}`.

---

## Testit

```bash
# Kaikki testit
python -m pytest tests/ -v

# Vain analyysitestit
python -m pytest tests/test_analyzer.py -v

# Vain status-luokitustestit
python -m pytest tests/test_status_classification.py -v

# Raporttigeneraattorin testit
python -m pytest tests/test_report_generator.py -v
```

---

## Lokitiedostot

| Tiedosto | Sisältö |
|----------|---------|
| `logs/daily_report_YYYYMMDD.log` | Päiväkohtainen ajo |
| `logs/run_TIMESTAMP.log` | Shell-skriptin ajo |
| `logs/cron.log` | Cron-ajot |

---

## Ajastuksen muuttaminen

```bash
# Muuta ajastusaikaa (esim. klo 08:00)
CRON_TIME='0 8' ./setup_cron.sh

# Tarkista nykyinen crontab
crontab -l

# Poista ajastus kokonaan
crontab -l | grep -v 'shopify-daily-report' | crontab -
```

---

## Vianmääritys

### Raportti on jo olemassa
```
Raportti päivälle 2026-03-26 on jo olemassa — ohitetaan
```
Käytä `--force` ajaaksesi uudelleen.

### Shopify API -virhe
Tarkista:
- `SHOPIFY_SHOP` osoittaa oikeaan kauppaan (ei testistoreä)
- Client ID ja Secret ovat oikein
- Custom app on julkaistu Shopify Adminissa

### ClickUp-lista ei löydy
Tarkista `CLICKUP_LIST_ID`:
1. Avaa oikea lista ClickUpissa
2. URL-palkissa on muoto `.../l/XXXXXXXX`
3. Kopioi numerosarja `CLICKUP_LIST_ID`-muuttujaksi

### Ei historiadataa
Normaalia ensimmäisillä ajokerroilla. Vertailu aktivoituu
kun vähintään 3 päivän data on kerätty.

---

## Laajentaminen

Järjestelmä on suunniteltu laajennettavaksi:

- **Google Calendar** — lisää kalenteritapahtumien tarkistus (myyntilomat, kampanjat)
- **Calendly** — tuo asiakastapaamisten vaikutus analyysiin
- **Email-ilmoitukset** — lisää SMTP-konfiguraatio `config.py`:hyn
- **Slack-ilmoitukset** — lisää Slack webhook red-statukselle
- **Varastoriskit** — integroi Osculati FTP -datan kanssa
- **Viikonpäiväkohtainen vertailu** — kerää enemmän dataa → tarkempi analyysi

---

## Tiedostorakenne

```
DailyReports/
├── main.py                 ← Pääorkestroija
├── config.py               ← Konfiguraatio ja kynnysarvot
├── shopify_client.py       ← Shopify REST API -asiakas
├── clickup_client.py       ← ClickUp API v2 -asiakas
├── db.py                   ← Supabase-operaatiot
├── analyzer.py             ← Analyysilogiikka
├── report_generator.py     ← Raporttigeneraattori (suomi)
├── task_creator.py         ← Follow-up-tehtävien hallinta
├── requirements.txt        ← Python-riippuvuudet
├── .env.example            ← Ympäristömuuttujamalli
├── .env                    ← Ympäristömuuttujat (EI versionhallintaan)
├── run_report.sh           ← Shell-käynnistysskripti
├── setup_cron.sh           ← Cron-ajastuksen asennus
├── migrations/
│   └── 001_daily_report_tables.sql
├── tests/
│   ├── test_analyzer.py
│   ├── test_report_generator.py
│   ├── test_status_classification.py
│   └── test_idempotency.py
└── logs/                   ← Lokitiedostot (automaattisesti luotu)
```
