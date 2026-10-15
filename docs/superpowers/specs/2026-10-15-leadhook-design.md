# LeadHook — Design

- **Data:** 2026-10-15
- **Stato:** approvato in chat, in attesa di revisione della spec
- **Nome:** "LeadHook" è provvisorio

## 1. Obiettivo

Connettore **open source e self-hosted** per Facebook/Instagram Lead Ads: riceve i lead da **più pagine e più form contemporaneamente** tramite un'unica app Meta e li invia a destinazioni webhook configurabili (n8n, Make, CRM con API, ecc.).

Nasce per sostituire un servizio a pagamento (LeadSync) usato perché il trigger Facebook Lead Ads di n8n gestisce una sola sottoscrizione alla volta (Meta accetta un solo callback URL per app), e perché un router n8n con gli ID dei form scritti a mano rende faticoso aggiungere nuove commesse.

### Criteri di successo

1. Aggiungere una nuova campagna/commessa non richiede di modificare codice né workflow: al massimo si assegna una destinazione a un form da un elenco.
2. Nessun lead perso: ogni evento è salvato prima di rispondere a Meta; una riconciliazione periodica recupera i lead i cui webhook non sono mai arrivati.
3. I workflow n8n che oggi ricevono lead da LeadSync / dal trigger FB di n8n funzionano senza modifiche (payload compatibile).
4. Installazione con `docker compose up` e configurazione guidata dall'admin.

### Fuori ambito (v1)

- Multi-tenant / pagine di terzi via Facebook Login (richiede Advanced Access Meta). Il modello dati è predisposto (`Workspace`) ma la v1 ne usa uno solo.
- Destinazioni diverse dal webhook (email, Google Sheets, Telegram): previste in futuro tramite l'astrazione `Destination`.
- Regole di instradamento condizionali sui valori dei campi (si fanno a valle, es. in n8n).
- Mappatura campi con interfaccia grafica (in futuro, sopra i template).
- Versione cloud ospitata.

## 2. Decisioni chiave

| Tema | Decisione |
|---|---|
| Modello di distribuzione | Self-hosted, "porta la tua app Meta": ogni installazione usa la propria app Meta e le proprie pagine |
| Stack | Python, Django (admin come interfaccia principale) |
| Lavori in background | Celery + Redis (worker + beat) |
| Database | PostgreSQL in produzione, SQLite solo per sviluppo |
| Destinazioni v1 | Solo webhook HTTP, con payload standard o template personalizzato |
| Instradamento | Form → N destinazioni; fallback su destinazioni default della pagina; altrimenti `unrouted` |
| Licenza | AGPL-3.0 |

## 3. Architettura

```
             Meta (webhook leadgen)
                      │ POST /webhooks/meta/<uuid>/
                      ▼
┌──────────────── web (Django + gunicorn) ─────────────────┐
│ • verifica firma X-Hub-Signature-256 + hub.challenge     │
│ • salva l'evento grezzo → risponde 200 subito            │
│ • admin: app Meta, pagine, form, destinazioni, lead, log │
└────────────────────────────┬─────────────────────────────┘
                             │ task Celery
                ┌────────────▼──────────────┐
  Redis ◄──────►│     worker (Celery)        │
 (broker)       │ 1. fetch_lead: Graph API   │
                │ 2. route: regole → dest.   │
                │ 3. deliver: POST webhook   │──► n8n / CRM / …
                │    retry con backoff       │
                └────────────▲──────────────┘
                             │ periodico
                ┌────────────┴──────────────┐
                │     beat (Celery)          │
                │ • riconciliazione lead     │
                │ • sync form                │
                │ • controllo token/iscriz.  │
                │ • pulizia dati (retention) │
                └───────────────────────────┘
                     PostgreSQL
```

- **Una sola immagine Docker** avviata in 3 ruoli: `web`, `worker`, `beat`. `docker-compose.yml` include anche `redis` e `postgres`.
- **Pipeline a task separati:** `process_event` → `fetch_lead` → `route_lead` → `deliver` (un task per ogni coppia lead-destinazione). Un fallimento verso una destinazione ritenta solo quella consegna.
- **Astrazione `Destination`:** classe base con `render(lead) -> Request` e `send(request) -> Result`; v1 implementa solo `WebhookDestination`.

### Moduli (app Django)

| App | Responsabilità |
|---|---|
| `core` | `Workspace`, cifratura segreti, impostazioni, healthcheck |
| `meta` | `MetaApp`, `Page`, `Form`; client Graph API; endpoint webhook; sync form; controllo iscrizioni e token |
| `leads` | `WebhookEvent`, `Lead`; parsing evento, fetch lead, riconciliazione, retention |
| `routing` | Logica form → destinazioni |
| `destinations` | `Destination`, `Delivery`; rendering template, invio, retry, condizione di successo, protezione SSRF |
| `alerts` | Avvisi email con raggruppamento |
| `dashboard` | Home dell'admin, procedura guidata di onboarding |

## 4. Modello dati

🔒 = campo cifrato (Fernet, chiave da `LEADHOOK_ENCRYPTION_KEY`).

### Configurazione

**`Workspace`**: `name`. Uno solo in v1, creato da migrazione.

**`MetaApp`**: `workspace`, `app_id`, `app_secret`🔒, `verify_token`, `webhook_uuid`, `access_token`🔒, `token_type` (`system_user` | `user`), `token_expires_at` (null per system user), `graph_version` (es. `v21.0`).

**`Page`**: `meta_app`, `page_id` (unico), `name`, `page_token`🔒, `subscribed` (bool), `last_checked_at`, `last_lead_at`, `error` (testo, vuoto se ok), `default_destinations` (M2M → `Destination`).

**`Form`**: `page`, `form_id` (unico), `name`, `status` (`ACTIVE` | `ARCHIVED` | …), `destinations` (M2M → `Destination`), `last_synced_at`.

**`Destination`**: `workspace`, `name`, `kind` (solo `webhook` in v1), `url`, `method` (`POST` | `PUT`), `auth_type` (`none` | `header` | `bearer` | `hmac`), `auth_header_name`, `secret`🔒, `extra_headers` (JSON), `payload_template` (testo; vuoto = formato standard), `success_condition` (testo; vuoto = qualsiasi 2xx), `timeout_s` (default 15), `active`.

### Dati operativi

**`WebhookEvent`**: `received_at`, `payload` (JSON), `signature_ok`, `processed_at`, `error`.

**`Lead`**: `leadgen_id` (unico), `page`, `form`, `created_time`, `ad_id`, `ad_name`, `adset_id`, `adset_name`, `campaign_id`, `campaign_name`, `is_organic`, `field_data` (JSON grezzo Meta), `data` (JSON semplificato), `source` (`webhook` | `reconcile` | `manual`), `status`, `error`.

Stati del lead: `received` → `fetched` → `routed` | `unrouted` → `delivered` | `partially_failed` | `failed`.

**`Delivery`**: `lead`, `destination`, `status` (`pending` | `success` | `failed`), `attempts`, `next_retry_at`, `last_status_code`, `last_error`, `request_body`, `response_body` (troncato a 4 KB), `delivered_at`. Unico su (`lead`, `destination`).

### Regole

- **Instradamento:** se `form.destinations` è non vuoto → quelle; altrimenti `page.default_destinations`; se vuoto → lead `unrouted`. Solo destinazioni `active`. Quando si assegnano destinazioni a un form con lead `unrouted`, l'admin propone di instradarli.
- **Form sconosciuto:** se arriva un lead per un `form_id` non presente, il form viene creato al volo leggendolo dalla Graph API.
- **Deduplica:** `Lead.leadgen_id` unico; un lead già presente (da webhook o riconciliazione) non viene ricreato né reinviato.
- **Stato aggregato del lead:** tutte le `Delivery` `success` → `delivered`; tutte `failed` → `failed`; mix → `partially_failed`.
- **Retention:** `LEAD_RETENTION_DAYS` (default 90). Un task giornaliero svuota `field_data`, `data`, `request_body`, `response_body` e `WebhookEvent.payload` più vecchi, mantenendo i metadati.

## 5. Flusso

1. **Ricezione.** `POST /webhooks/meta/<webhook_uuid>/`: verifica `X-Hub-Signature-256` sul body grezzo con `app_secret`; salva `WebhookEvent`; accoda `process_event`; risponde 200. Firma non valida → salva con `signature_ok=false`, non processa, risponde 200.
2. **`process_event`.** Estrae ogni `entry[].changes[]` con `field == "leadgen"` → (`leadgen_id`, `page_id`, `form_id`). Crea `Lead` (`received`) se non esiste; accoda `fetch_lead`.
3. **`fetch_lead`.** `GET /{leadgen_id}?fields=id,created_time,field_data,ad_id,ad_name,adset_id,adset_name,campaign_id,campaign_name,form_id,is_organic` con il token della pagina. Costruisce `data`. Stato `fetched`; accoda `route_lead`.
4. **`route_lead`.** Applica le regole; crea una `Delivery` per destinazione; accoda `deliver` per ognuna.
5. **`deliver`.** Renderizza il payload, invia, valuta l'esito, aggiorna `Delivery` e lo stato aggregato del lead.

### Task periodici (beat)

| Task | Frequenza | Cosa fa |
|---|---|---|
| `reconcile_leads` | 15 min | Per ogni form `ACTIVE` di pagine collegate: `GET /{form_id}/leads?filtering=[time_created > ultimo_controllo − 1h]`; crea i lead mancanti con `source=reconcile` |
| `sync_forms` | 1 h | Aggiorna l'elenco form di ogni pagina |
| `check_pages` | 1 giorno | Verifica token (debug_token) e iscrizione `subscribed_apps`; reiscrive se manca; segnala errori |
| `check_silence` | 1 h | Avvisa se una pagina con lead abituali non ne riceve da `SILENCE_ALERT_HOURS` (default 24) |
| `purge_old_data` | 1 giorno | Retention |
| `heartbeat` | 1 min | Aggiorna il timestamp letto da `/health/` |

## 6. Payload verso i webhook

### Formato standard (template vuoto)

Il primo livello è identico all'output "Simplify" del trigger Facebook Lead Ads di n8n; i campi aggiuntivi sono solo in aggiunta.

```json
{
  "id": "1234567890123456",
  "created_time": "2026-10-15T08:30:00+0000",
  "data": { "nome_e_cognome": "Mario Rossi", "numero_di_telefono": "+393331234567" },
  "form":     { "id": "…", "name": "…", "locale": "it_IT", "status": "ACTIVE" },
  "page":     { "id": "…", "name": "…" },
  "ad":       { "id": "…", "name": "…" },
  "adset":    { "id": "…", "name": "…" },
  "campaign": { "id": "…", "name": "…" },
  "is_organic": false,
  "field_data": [ { "name": "nome_e_cognome", "values": ["Mario Rossi"] } ],
  "_leadhook": { "version": 1, "delivery_id": "…", "attempt": 1, "source": "webhook", "sent_at": "…" }
}
```

In `data`: valore singolo → stringa; valori multipli → array.

### Template personalizzato

- Motore: **Jinja2 `SandboxedEnvironment`**, `autoescape` disattivato, `StrictUndefined` disattivato (variabile mancante → vuoto).
- Contesto: gli stessi campi del formato standard (`id`, `data`, `form`, `page`, `ad`, `adset`, `campaign`, `created_time`, `is_organic`, `field_data`, `_leadhook`).
- Il risultato deve essere JSON valido, altrimenti la consegna fallisce senza retry.
- Filtri forniti: `tojson`, `first_word`, `rest_words`, `phone_it` (rimuove spazi e prefisso `+39`/`0039`), `date(tz, formato)`, `map(dizionario)` (valore non mappato → invariato), `default`.
- Admin: anteprima del payload renderizzato su un lead reale a scelta.

### Header

| Header | Valore |
|---|---|
| `Content-Type` | `application/json` |
| `Idempotency-Key` | `leadgen_id` |
| `X-LeadHook-Timestamp` | timestamp Unix |
| `X-LeadHook-Signature` | `sha256=HMAC_SHA256(secret, timestamp + "." + body)`, solo con `auth_type=hmac` |
| `Authorization: Bearer <secret>` | con `auth_type=bearer` |
| `<auth_header_name>: <secret>` | con `auth_type=header` |
| `extra_headers` | aggiunti così come configurati |

### Esito e retry

- **Successo:** 2xx **e**, se impostata, `success_condition` vera. La condizione è un'espressione Jinja sandbox valutata con `response` (body JSON, `None` se non JSON) e `status_code`, es. `response.success == true`.
- **Retry:** errore di rete, timeout, 5xx, 408, 429, oppure 2xx con condizione di successo falsa. Backoff: 1 min, 5 min, 30 min, 2 h, 6 h, 24 h (7 tentativi totali); `Retry-After` rispettato se presente.
- **Fallimento immediato:** altri 4xx, errore di template, JSON renderizzato non valido.
- **Rinvio manuale:** azione admin su singole consegne o in blocco (ricrea un tentativo con `attempt` successivo).

## 7. Sicurezza

- **Webhook Meta:** `GET` risponde a `hub.challenge` solo se `hub.verify_token` corrisponde; `POST` verifica HMAC sul body grezzo con confronto a tempo costante; URL con UUID per installazione.
- **Admin:** login Django; in produzione `SECURE_SSL_REDIRECT`, HSTS, cookie `Secure`/`HttpOnly`.
- **Segreti:** cifrati con Fernet; avvio bloccato se `LEADHOOK_ENCRYPTION_KEY` manca; nell'admin si vedono solo le ultime 4 cifre; mai nei log.
- **Log:** nessun dato personale dei lead, solo ID.
- **SSRF:** risoluzione DNS e blocco di IP privati, loopback, link-local e metadata cloud, salvo `ALLOW_PRIVATE_DESTINATIONS=true`; redirect non seguiti.

## 8. Gestione errori

| Problema | Comportamento |
|---|---|
| Fetch lead fallito (rete, 5xx, rate limit Meta) | Retry Celery con backoff; la riconciliazione fa da rete di sicurezza |
| Token non valido (errore Meta 190) | Stop retry per quella pagina, `Page.error` valorizzato, avviso; i lead restano `received` e ripartono quando il token viene aggiornato (azione "Riprocessa") |
| Pagina non iscritta | `check_pages` la reiscrive; se fallisce, avviso |
| Errore template / JSON non valido | `Delivery` `failed` senza retry, avviso |
| Destinazione irraggiungibile | Retry fino a 24 h, poi `failed` + avviso |
| Lead non instradato | Stato `unrouted`, avviso |

## 9. Monitoraggio e avvisi

- **Dashboard** (home admin): lead oggi / 7 giorni per form, consegne fallite, lead non instradati, stato di ogni pagina (token, iscrizione, ultimo lead).
- **Avvisi email** (SMTP Django, destinatari in `ALERT_EMAILS`): consegna fallita definitivamente, errore token/iscrizione, lead non instradato, silenzio anomalo. Raggruppati: massimo un'email ogni 15 minuti per tipo.
- **`GET /health/`**: verifica database, Redis e heartbeat beat (< 5 minuti); 200 o 503.

## 10. Onboarding

Procedura guidata nell'admin, finché la configurazione è incompleta:

1. Creazione utente admin (`createsuperuser` o procedura).
2. Inserimento `app_id`, `app_secret`, token (System User consigliato), con link alla guida.
3. Visualizzazione URL di callback e verify token da incollare nell'app Meta; pulsante "Verifica" che interroga `/{app_id}/subscriptions`.
4. Elenco pagine accessibili dal token; selezione → iscrizione `subscribed_apps` (`leadgen`) + sync form.
5. Creazione prima destinazione e assegnazione come default di una pagina.
6. "Invia lead di test": crea un lead di test sul form scelto e mostra l'avanzamento nella pipeline.

Il README include la guida completa alla configurazione dell'app Meta (Business Verification, permessi `leads_retrieval`, `pages_manage_metadata`, `pages_show_list`, `pages_read_engagement`, `pages_manage_ads`, System User, Accesso ai lead nel Business Manager, modalità Live).

## 11. Configurazione (variabili d'ambiente)

`DJANGO_SECRET_KEY`, `LEADHOOK_ENCRYPTION_KEY`, `DATABASE_URL`, `REDIS_URL`, `ALLOWED_HOSTS`, `PUBLIC_BASE_URL`, `EMAIL_URL`, `ALERT_EMAILS`, `LEAD_RETENTION_DAYS` (90), `SILENCE_ALERT_HOURS` (24), `ALLOW_PRIVATE_DESTINATIONS` (false), `META_GRAPH_VERSION` (default per nuove app).

## 12. Test

- `pytest` + `pytest-django`; Celery `task_always_eager` nei test.
- Graph API simulata con `responses` e fixture JSON anonimizzate (lead, form, pagine, errore 190, rate limit).
- Casi obbligatori: firma webhook (valida, non valida, body alterato) e challenge; deduplica webhook + riconciliazione; instradamento (form → default → unrouted, destinazioni inattive); stato aggregato; backoff e classificazione retry/non-retry; `Retry-After`; template, filtri e anteprima; condizione di successo; blocco SSRF; cifratura segreti; retention.
- Test end-to-end: evento Meta → fetch simulato → consegna a un server HTTP locale di test.
- CI GitHub Actions: `ruff`, test, build immagine Docker.

## 13. Repository

- Licenza **AGPL-3.0**.
- Struttura: `leadhook/` (progetto Django), app in `leadhook/<app>/`, `tests/`, `docker/`, `docs/`.
- File: `README.md` (italiano + inglese), `LICENSE`, `CONTRIBUTING.md`, `SECURITY.md`, `docker-compose.yml`, `.env.example`.
