<div align="center">

<img src="docs/images/banner.jpg" alt="LeadHook" width="800">

**Ogni lead di Facebook e Instagram, consegnato al webhook giusto: da tutte le tue pagine, con una sola app Meta.**

Self-hosted · open source · pensato per n8n, Make, Zapier e qualsiasi CRM con un'API.

[![CI](https://github.com/flavioabbinante/leadhook/actions/workflows/ci.yml/badge.svg)](https://github.com/flavioabbinante/leadhook/actions/workflows/ci.yml)
[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)](LICENSE)
[![PRs welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

[![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://github.com/python/cpython)
[![Django 5.2](https://img.shields.io/badge/Django-5.2_LTS-092E20?logo=django&logoColor=white)](https://github.com/django/django)
[![Celery 5](https://img.shields.io/badge/Celery-5-37814A?logo=celery&logoColor=white)](https://github.com/celery/celery)
[![Redis 7](https://img.shields.io/badge/Redis-7-DC382D?logo=redis&logoColor=white)](https://github.com/redis/redis)
[![PostgreSQL 16](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)](https://github.com/postgres/postgres)
[![Docker Compose](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)](https://github.com/docker/compose)

[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Works with n8n](https://img.shields.io/badge/works_with-n8n-EA4B71?logo=n8n&logoColor=white)](https://github.com/n8n-io/n8n)

[Avvio rapido](#-avvio-rapido) ·
[Come funziona](#-come-funziona) ·
[Configurazione Meta](#-configurazione-dellapp-meta) ·
[Payload](#-payload) ·
[Configurazione](#-configurazione) ·
[Contribuire](CONTRIBUTING.md)

</div>

---

## ✨ Perché LeadHook?

Il trigger Facebook Lead Ads di n8n gestisce **una sola iscrizione per app Meta**. Se hai più pagine o campagne, si contendono lo stesso URL di callback; se instradi in base a ID di form scritti a mano, ogni nuova campagna ti costringe a modificare un workflow.

LeadHook si prende quel callback. Riceve i lead da **ogni pagina e ogni form**, recupera le risposte complete dalla Graph API e inoltra ciascun lead alla destinazione che hai scelto per il suo form, oppure a quella predefinita della pagina, con nuovi tentativi, firme e uno storico verificabile.

| | |
|---|---|
| 🔀 **Una app, pagine illimitate** | Instrada per form, con una destinazione predefinita per pagina come ripiego. I lead senza instradamento restano *non instradati*, mai persi. |
| 🧩 **Sostituto diretto per n8n** | Il payload standard corrisponde a quello del trigger Facebook Lead Ads di n8n. Ti serve altro? Scrivilo con template Jinja2 in sandbox. |
| 🔁 **Consegnato una sola volta per destinazione** | 7 tentativi in circa 1,5 giorni con backoff e `Retry-After`, `Idempotency-Key` su ogni richiesta, reinvio con un clic. |
| 🛟 **Nessun lead perso** | Ogni evento viene salvato prima che Meta riceva un 200. La riconciliazione ogni 15 minuti recupera i lead il cui webhook non è mai arrivato. |
| 🔐 **Sicuro di default** | Webhook firmati, segreti cifrati con Fernet, autenticazione HMAC/Bearer/header, protezione SSRF che blocca anche il DNS rebinding. |
| 📈 **Facile da gestire** | `docker compose up`, una configurazione guidata con "Invia lead di prova", dashboard, avvisi via email e `/health/`. |

## 🧭 Come funziona

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/architecture-dark.png">
  <img alt="Architettura di LeadHook: Meta Lead Ads invia un webhook leadgen firmato all'app web Django, che salva l'evento in PostgreSQL e lo mette in coda su Redis. Un worker Celery recupera il lead dalla Meta Graph API, lo instrada e invia il JSON ai tuoi webhook con una POST, mandando avvisi via email in caso di errori. Celery beat pianifica riconciliazione, recupero del lavoro perso, sincronizzazione dei form, controlli delle pagine, controlli di silenzio, conservazione dei dati e heartbeat." src="docs/images/architecture-light.png">
</picture>

1. **Ricezione**: Meta invia con una POST un evento `leadgen` firmato; LeadHook verifica `X-Hub-Signature-256`, lo salva e risponde subito 200.
2. **Recupero**: un worker legge il lead completo (risposte, inserzione, campagna) dalla Graph API con il token della pagina.
3. **Instradamento**: vincono le destinazioni del form; altrimenti quelle predefinite della pagina; altrimenti il lead resta in attesa come *non instradato*.
4. **Consegna**: una consegna per destinazione, firmata e ritentata; lo stato del lead segue quello delle sue consegne.
5. **Sorveglianza**: beat esegue la riconciliazione, il recupero del lavoro perso, la sincronizzazione dei form, i controlli dei token, gli avvisi di silenzio e la conservazione dei dati.

<sub>Diagramma generato con [Archify](https://github.com/tt-a1i/archify) da `docs/architecture.archify.json`.</sub>

## 🚀 Avvio rapido

Requisiti: Docker con Compose, un URL HTTPS pubblico servito da un reverse proxy (Caddy, Traefik, nginx…) che inoltra alla porta 8000. Compose pubblica il container web solo su `127.0.0.1:8000`, quindi è raggiungibile dall'host (e dal proxy) ma non direttamente da internet; tienilo dietro il proxy.

```bash
git clone https://github.com/flavioabbinante/leadhook.git && cd leadhook
cp .env.example .env
# Fill in DJANGO_SECRET_KEY, LEADHOOK_ENCRYPTION_KEY, PUBLIC_BASE_URL and ALLOWED_HOSTS
docker compose up -d
docker compose exec web python manage.py createsuperuser
```

Apri `https://<your-domain>/admin/`, poi **Configurazione guidata** (`/admin/setup/`, raggiungibile anche dalla dashboard) e segui i passaggi.

## 📘 Configurazione dell'app Meta

LeadHook usa la tua app Meta. Preparazione da fare una volta sola in Meta:

1. **Business Verification.** In Business Settings → Security Center, verifica la tua azienda. Senza verifica l'app non può passare in modalità Live, e le app in modalità sviluppo ricevono solo lead di prova.
2. **App.** In [Meta for Developers](https://developers.facebook.com/apps/) crea un'app di tipo **Business**, aggiungi i prodotti **Facebook Login for Business** e **Webhooks** e annota App ID e App secret (App settings → Basic).
3. **Login.** In Facebook Login for Business → Settings, aggiungi il **Valid OAuth Redirect URI** mostrato nella configurazione guidata di LeadHook (`PUBLIC_BASE_URL/admin/setup/facebook/callback/`). Se vuoi, crea una **configuration** con un *System-user access token* e i permessi `leads_retrieval`, `pages_manage_metadata`, `pages_show_list`, `pages_read_engagement`, `pages_manage_ads`; il suo ID va nel campo *ID configurazione di accesso* dell'app Meta, e così la connessione non scade mai. Senza, LeadHook chiede quei permessi e conserva un token di 60 giorni (i token delle pagine ottenuti da esso non scadono; la dashboard ti avvisa prima che finisca).
4. **Leads Access.** In Business Settings → Integrations → Leads Access, autorizza l'app per ogni pagina; altrimenti Meta restituisce i lead senza risposte.
5. **Modalità Live.** Passa l'app in modalità Live.

Poi in LeadHook apri **Configurazione guidata** (`/admin/setup/`):

1. Aggiungi l'app Meta con App ID e App secret.
2. Premi **Collega con Facebook** e autorizza LeadHook. LeadHook salva il token e registra il webhook sull'app, a meno che l'app non invii già il suo webhook a un altro URL: un'app ha un solo URL di callback, quindi LeadHook non lo sostituisce mai (serve che `PUBLIC_BASE_URL` sia raggiungibile in HTTPS; il passaggio mostra anche URL di callback e token di verifica per configurarlo a mano).
3. Seleziona le tue pagine e premi **Collega le pagine selezionate**: LeadHook iscrive ogni pagina agli eventi dei lead e ne importa i form.
4. Crea una destinazione e collegala in **Routing** a una pagina (predefinita per tutti i suoi form) o a un singolo form.
5. Premi **Invia lead di prova** e guardalo arrivare in Lead e Consegne.

In alternativa al login funziona ancora un token di System User incollato a mano nell'app Meta.

## 📦 Payload

Con un template vuoto, ogni destinazione riceve:

```json
{
  "id": "1234567890123456",
  "created_time": "2026-10-15T08:30:00+0000",
  "data": { "full_name": "Jane Doe", "phone_number": "+15551234567" },
  "form": { "id": "…", "name": "…", "locale": "en_US", "status": "ACTIVE" },
  "page": { "id": "…", "name": "…" },
  "ad": { "id": "…", "name": "…" },
  "adset": { "id": "…", "name": "…" },
  "campaign": { "id": "…", "name": "…" },
  "is_organic": false,
  "field_data": [ { "name": "full_name", "values": ["Jane Doe"] } ],
  "_leadhook": { "version": 1, "delivery_id": "dlv_1", "attempt": 1, "source": "webhook", "sent_at": "…" }
}
```

Header: `Idempotency-Key` (l'ID del lead), `X-LeadHook-Timestamp` e, con l'autenticazione HMAC, `X-LeadHook-Signature: sha256=HMAC_SHA256(secret, timestamp + "." + body)`.

### Template personalizzati

Scrivi qualsiasi JSON con segnaposto Jinja2. Tutti i campi del payload qui sopra sono disponibili come variabili.

```jinja
{
  "first_name": {{ data.full_name | first_word | tojson }},
  "last_name": {{ data.full_name | rest_words | tojson }},
  "mobile": {{ data.phone_number | phone_strip_prefix("+1") | tojson }},
  "created": {{ created_time | date("America/New_York", "%Y-%m-%d %H:%M") | tojson }},
  "plan": {{ data.plan | map_value({"basic_plan": "Basic"}) | tojson }}
}
```

Chiudi sempre ogni valore con `| tojson`. L'admin mostra un'anteprima in tempo reale del payload generato.

**Condizione di successo** (facoltativa): un'espressione Jinja2 valutata sulla risposta interpretata, es. `response.success == true`. Quando è falsa, la consegna viene ritentata.

### Nuovi tentativi

Errori di rete, timeout, 5xx, 408 e 429 vengono ritentati dopo 1 min, 5 min, 30 min, 2 h, 6 h e 24 h (`Retry-After` viene rispettato): 7 tentativi in circa un giorno e mezzo. Le altre risposte 4xx falliscono subito. Le consegne concluse (riuscite o fallite) si possono reinviare da **Consegne** nell'admin, che avvia un nuovo ciclo di 7 tentativi; le consegne ancora in corso vengono saltate.

## 📊 Monitoraggio

- **Statistiche:** lead al giorno, per campagna, gruppo, inserzione e form, e il grafico "Cosa dice Timestral": i lead delle ultime 48 ore e, sulle 6 ore dell'ultimo controllo, la previsione con mediana e banda 5–95%.
- **Dashboard** (home dell'admin): lead di oggi e per form, consegne fallite, lead non instradati, stato delle pagine, stato dei job in background.
- **Avvisi via email** a `ALERT_EMAILS`: consegna fallita definitivamente, lead non instradato, problema con il token o l'iscrizione di una pagina, lead che non è stato possibile recuperare, silenzio insolito, lead in forte calo (sotto il 30%) rispetto a quanti ne prevede [Timestral 28M](https://huggingface.co/mistralai/Timestral-28M) di Mistral dalle 4 settimane precedenti (senza l'extra `forecast`, rispetto alla media delle stesse ore di quelle settimane). Al massimo una email per tipo ogni 15 minuti.
- **`GET /health/`** restituisce 200 con `{"database": "ok", "redis": "ok", "beat": "ok"}`, oppure 503 indicando cosa non va. `beat` diventa `stale` quando i job in background non girano da 5 minuti. L'endpoint è escluso dal redirect HTTPS; l'header `Host` deve comunque essere in `ALLOWED_HOSTS`.
- **Job in background** (Celery beat): riconciliazione (15 min), recupero del lavoro perso (5 min), sincronizzazione dei form (1 h), controllo del silenzio (1 h), controllo di pagine e token (giornaliero), eliminazione per conservazione (giornaliera).
- **Conservazione:** dopo `LEAD_RETENTION_DAYS` vengono cancellati le risposte dei lead, i payload dei webhook e i corpi di richieste e risposte; restano ID, nomi di inserzioni e campagne e stati. I lead eliminati non si possono reinviare.

## 🔧 Configurazione

| Variabile | Predefinito | Descrizione |
|---|---|---|
| `DJANGO_SECRET_KEY` | — | Obbligatoria. |
| `LEADHOOK_ENCRYPTION_KEY` | — | Obbligatoria. Chiave Fernet per i segreti salvati. Non cambiarla mai dopo averla impostata. |
| `PUBLIC_BASE_URL` | `http://localhost:8000` | URL pubblico, usato per costruire l'URL di callback del webhook. |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1` | Nomi host separati da virgole. |
| `DATABASE_URL` | SQLite | Consigliato PostgreSQL. |
| `REDIS_URL` | `redis://localhost:6379/0` | Broker Celery e cache (limitazione degli avvisi, heartbeat). |
| `META_GRAPH_VERSION` | vedi `.env.example` | Versione della Graph API per le nuove app Meta. |
| `ALLOW_PRIVATE_DESTINATIONS` | `false` | Consente destinazioni su reti private (es. n8n nella stessa rete Docker). |
| `CSRF_TRUSTED_ORIGINS` | `PUBLIC_BASE_URL` | Origini separate da virgole autorizzate a inviare i form dell'admin, es. `https://leads.example.com`. |
| `SECURE_SSL_REDIRECT` | `true` | Impostala a `false` solo per test in locale su HTTP. |
| `SECURE_HSTS_SECONDS` | `31536000` | max-age HSTS quando `SECURE_SSL_REDIRECT` è attivo; usa un valore basso mentre fai test. |
| `DJANGO_DEBUG` | `false` | Modalità debug. Non attivarla mai in produzione. |
| `LOG_LEVEL` | `INFO` | Livello di log per i processi web, worker e beat. |
| `WEB_CONCURRENCY` | `3` | Numero di worker gunicorn nel container web. |
| `EMAIL_URL` | `consolemail://` | Server SMTP per gli avvisi, es. `smtp+tls://user:password@smtp.example.com:587`. |
| `DEFAULT_FROM_EMAIL` | `leadhook@localhost` | Mittente delle email di avviso. |
| `ALERT_EMAILS` | — | Destinatari separati da virgole; se vuota, nessun avviso. |
| `LEAD_RETENTION_DAYS` | `90` | Le risposte dei lead, i payload dei webhook e i corpi di richieste e risposte più vecchi di così vengono cancellati. |
| `SILENCE_ALERT_HOURS` | `24` | Avvisa quando una pagina che ha avuto lead negli ultimi 30 giorni non ne riceve per questo intervallo. |
| `REQUESTS_CA_BUNDLE` | — | Bundle di CA per destinazioni dietro un'autorità di certificazione privata. |

## 🧪 Sviluppo

```bash
uv sync
uv run pytest            # ~300 tests, no Redis or PostgreSQL needed
uv run ruff check . && uv run ruff format --check .
```

Configurazione locale, struttura del progetto, convenzioni per i test e checklist della pull request sono in **[CONTRIBUTING.md](CONTRIBUTING.md)**. Hai trovato una vulnerabilità? Leggi **[SECURITY.md](SECURITY.md)**.

## 📄 Licenza

[GNU AGPL-3.0](LICENSE). Se offri una versione modificata come servizio, devi pubblicare le tue modifiche.
