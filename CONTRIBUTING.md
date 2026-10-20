# Contribuire a LeadHook

Grazie per il tuo aiuto! LeadHook sposta dati personali e token di accesso tra Meta e altri sistemi, quindi preferiamo **modifiche piccole, ben testate e noiose** a quelle brillanti. Questa guida ti porta dal clone alla pull request unita.

- [Regole di base](#regole-di-base)
- [Come contribuire](#come-contribuire)
- [Configurazione locale](#configurazione-locale)
- [Come è organizzato il codice](#come-è-organizzato-il-codice)
- [Fare una modifica](#fare-una-modifica)
- [Test](#test)
- [Convenzioni](#convenzioni)
- [Ricette](#ricette)
- [Checklist della pull request](#checklist-della-pull-request)

## Regole di base

- **Inglese:** codice, identificatori, commenti, log, messaggi di commit, issue e pull request.
- **Italiano:** testi dell'interfaccia admin, email di avviso, errori mostrati all'utente e documentazione. I testi di Django senza traduzione italiana stanno in `leadhook/locale/it/LC_MESSAGES/*.po`: dopo una modifica rigenera i `.mo` con `uv run --with polib python -c "import polib; polib.pofile('leadhook/locale/it/LC_MESSAGES/djangojs.po').save_as_mofile('leadhook/locale/it/LC_MESSAGES/djangojs.mo')"` (stesso comando per `django.po`).
- **Mai dati reali.** Non fare mai commit né incollare lead, ID di pagine, token o app secret reali: né nelle fixture, né nei log, negli screenshot o nelle issue. Le fixture dei test sono anonimizzate (vedi `tests/fixtures.py`).
- **I problemi di sicurezza si segnalano in privato.** Usa il pulsante *Report a vulnerability* di GitHub, mai una issue pubblica. Vedi [SECURITY.md](SECURITY.md).
- **Apri prima una issue** per qualsiasi cosa più grande di una correzione di bug o di un piccolo miglioramento, così concordiamo l'approccio prima che tu scriva codice.
- Contribuendo accetti che il tuo lavoro sia rilasciato sotto licenza [AGPL-3.0](LICENSE).

## Come contribuire

- 🐛 **Segnala un bug**: indica il commit di LeadHook, come lo esegui (con Compose o no), i passaggi per riprodurlo e le righe di log rilevanti *con solo gli ID*.
- 💡 **Proponi una funzionalità**: descrivi prima il problema; la [specifica di progetto](docs/superpowers/specs/2026-10-15-leadhook-design.md) elenca cosa è volutamente escluso dalla v1.
- 📖 **Migliora la documentazione**: la guida alla configurazione Meta nel README guadagna più di tutto da chi l'ha appena seguita.
- 🧪 **Aggiungi test** per i casi limite che hai incontrato con payload Meta reali (anonimizzati).

## Configurazione locale

Requisiti: Python 3.12, [uv](https://github.com/astral-sh/uv); Docker è facoltativo.

```bash
git clone https://github.com/flavioabbinante/leadhook.git && cd leadhook
uv sync                                   # creates .venv with dev dependencies
uv run pytest                             # Celery runs eagerly on in-memory SQLite: no services needed
uv run ruff check . && uv run ruff format --check .
```

Per avviare l'app vera e propria:

```bash
cp .env.example .env
# Set DJANGO_SECRET_KEY, LEADHOOK_ENCRYPTION_KEY (command in the file), DJANGO_DEBUG=true,
# SECURE_SSL_REDIRECT=false, remove DATABASE_URL (SQLite) and set REDIS_URL=redis://localhost:6379/0
docker run -d -p 6379:6379 redis:7-alpine
uv run python manage.py migrate
uv run python manage.py createsuperuser
uv run python manage.py runserver         # http://localhost:8000/admin/
uv run celery -A leadhook worker -l info  # in a second terminal
uv run celery -A leadhook beat -l info    # in a third, for periodic jobs
```

Per sviluppare su PostgreSQL invece che su SQLite, avvia il database e Redis da Compose solo su
localhost e punta `.env` verso di loro:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d postgres redis
# .env: DATABASE_URL=postgres://leadhook:leadhook@localhost:5432/leadhook
#       REDIS_URL=redis://localhost:6379/0
```

Oppure esegui l'intero stack come in produzione: `docker compose up --build`.

Per ricevere webhook reali in locale, esponi la porta 8000 con un tunnel (per esempio `cloudflared tunnel --url http://localhost:8000`), imposta `PUBLIC_BASE_URL` e `ALLOWED_HOSTS` sull'host del tunnel e usa un'app Meta in modalità sviluppo con il Lead Ads Testing Tool.

## Come è organizzato il codice

![Architettura](docs/images/architecture-light.png)

| Percorso | Cosa contiene |
|---|---|
| `leadhook/core/` | Cifratura (`crypto.py`, `fields.py`), avvisi, health/heartbeat, il middleware per la chiave errata |
| `leadhook/meta/` | Modelli di app Meta, pagine e form; client della Graph API (`graph.py`); view del webhook; controlli periodici di pagine e form; configurazione guidata (`setup.py`) |
| `leadhook/leads/` | Eventi webhook e lead; parsing del payload; recupero e riconciliazione; conservazione dei dati; dashboard dell'admin |
| `leadhook/routing/` | `resolve_destinations()`: destinazioni del form, altrimenti quelle predefinite della pagina |
| `leadhook/destinations/` | Destinazioni e consegne; payload/template; HTTP in uscita con protezione SSRF; nuovi tentativi, lease e recupero del lavoro perso |
| `tests/` | Un modulo di test per ogni modulo sorgente, più `test_end_to_end.py` |
| `docs/superpowers/` | Specifica di progetto e piani di implementazione: leggi la specifica prima di cambiare un comportamento |

La pipeline è `process_event → fetch_lead → route_lead → deliver`. La logica di business sta nei moduli `services.py`; i task Celery in `tasks.py` sono wrapper sottili che trasformano i risultati dei servizi in nuovi tentativi. Mantienilo così: i servizi sono facili da testare senza Celery.

## Fare una modifica

1. Crea un branch da `main`: `feat/<topic>`, `fix/<topic>`, `docs/<topic>` o `chore/<topic>`.
2. **Scrivi prima il test che fallisce**, poi il codice che lo fa passare.
3. Tieni la modifica mirata: un comportamento per pull request. I refactoring vanno in una pull request a parte.
4. Esegui il controllo completo prima del push:

   ```bash
   uv run ruff format . && uv run ruff check . && uv run pytest
   DJANGO_SETTINGS_MODULE=tests.settings uv run python manage.py makemigrations --check --dry-run
   ```

5. Fai commit con un messaggio in stile [Conventional Commits](https://www.conventionalcommits.org/), con lo scope dell'app quando aiuta:

   ```
   feat(destinations): support PATCH as delivery method
   fix(meta): keep page token when Meta omits it
   docs: clarify Leads Access step
   ```

6. Apri la pull request e compila il template. La CI esegue ruff, i test su SQLite **e** PostgreSQL 16 e una build Docker; devono essere verdi tutti e tre.

## Test

I test usano `pytest`, `pytest-django` e `responses`. Cose che fanno risparmiare tempo:

- **Factory** in `tests/factories.py` (`make_meta_app`, `make_page`, `make_form`, `make_lead`, `make_destination`) creano oggetti validi; sovrascrivi solo ciò che interessa al tuo test.
- **Fixture** in `tests/fixtures.py` contengono payload anonimizzati della Graph API e `sign()` per le firme dei webhook.
- **Niente rete.** Simula ogni chiamata alla Graph API e alle destinazioni con `responses`. Un test che raggiunge internet senza dirlo è un bug.
- **Il DNS è finto** grazie alla fixture autouse `fake_dns`: gli host sconosciuti si risolvono in un IP pubblico; per associare un host a un IP privato usa `fake_dns["host"] = "10.0.0.1"`.
- **Celery gira in modo eager** (`CELERY_TASK_ALWAYS_EAGER`): chiamare un task lo esegue subito, e un nuovo tentativo solleva `celery.exceptions.Retry`.
- **Le email** finiscono nella fixture `mailoutbox`; la cache (limitazione degli avvisi, heartbeat) viene svuotata prima di ogni test.
- **PostgreSQL:** la CI esegue ogni test anche su PostgreSQL. In locale: `docker run -d -p 5432:5432 -e POSTGRES_USER=leadhook -e POSTGRES_PASSWORD=leadhook postgres:16-alpine` e `LEADHOOK_TEST_DATABASE_URL=postgres://leadhook:leadhook@localhost:5432/leadhook uv run pytest`.
- Quando il codice si protegge da qualcosa (una race, un lease perso, un segreto trapelato), assicurati che il test **fallisca senza la protezione**: rimuovila temporaneamente ed esegui il test una volta.

## Convenzioni

- **Log, errori e avvisi contengono solo ID**: mai nomi, email, numeri di telefono o risposte. I messaggi di errore non devono mai contenere token, app secret, chiavi segrete delle destinazioni o percorsi/query degli URL delle destinazioni (per questo esistono `redact_url()` e `GraphClient._redact()`).
- **I segreti** sono `EncryptedTextField` e i form dell'admin estendono `SecretAwareModelForm`, così un campo lasciato vuoto mantiene il valore salvato.
- **Nuovi tentativi e lease:** le consegne vengono prese in carico con un `UPDATE` condizionale e salvate solo finché si detiene il lease. Leggi `destinations/services.py` prima di toccare lo stato delle consegne.
- **Tutto ciò che è periodico è idempotente:** eseguire un task di beat due volte, o due copie insieme, non deve creare lead duplicati né invii in più.
- **Le migrazioni** hanno un nome descrittivo: `makemigrations <app> --name <what_changed>`. Non modificare mai una migrazione già presente su `main`.
- **I commenti spiegano il *perché*,** non il cosa. Una scorciatoia voluta ha un commento `# shortcut: <limit>, <when to upgrade>`.
- **Lo stile** è quello prodotto da `ruff format` (lunghezza riga 100). Non aggiungere dipendenze per poche righe di codice.

## Ricette

**Aggiungere un task periodico**

1. Scrivi la logica come funzione di servizio e testala direttamente.
2. Aggiungi un `@shared_task` sottile nel `tasks.py` dell'app.
3. Aggiungi una voce a `CELERY_BEAT_SCHEDULE` in `leadhook/settings.py`; `tests/test_settings.py::test_beat_schedule_tasks_exist` verifica che punti a un task reale.
4. Citalo nella sezione *Monitoraggio* del README.

**Aggiungere un'impostazione**

Leggila con `env(...)` in `leadhook/settings.py` con un valore predefinito sicuro, documentala in `.env.example` e nella tabella *Configurazione* del README, e sovrascrivila nei test con la fixture `settings`.

**Aggiungere un avviso**

Aggiungi un membro ad `AlertKind` in `leadhook/core/alerts.py` e chiama `send_alert()` dal servizio in cui il problema viene rilevato. Gli avvisi sono limitati per tipo, quindi metti tutti gli ID coinvolti in una sola email invece di mandarne una per oggetto.

**Aggiornare il diagramma dell'architettura**

Il diagramma nel README è generato con [Archify](https://github.com/tt-a1i/archify) da `docs/architecture.archify.json`. Dopo aver cambiato componenti o flussi, aggiorna il JSON (compresi gli intervalli di righe di `sources` e `meta.repository.revision`), poi:

```bash
node <archify>/bin/archify.mjs finalize architecture docs/architecture.archify.json \
  .archify/leadhook-architecture.html --repo-root . --quality showcase
node <archify>/bin/archify.mjs visual-check .archify/leadhook-architecture.html \
  --summary --require-provenance --out-dir .archify/visual-check
```

Ritaglia le catture chiara e scura a 2048×1320 in `docs/images/architecture-{light,dark}.png`. `.archify/` è ignorata da git.

## Checklist della pull request

- [ ] I test coprono la modifica e falliscono senza di essa
- [ ] `ruff format`, `ruff check`, `pytest` e `makemigrations --check` passano in locale
- [ ] Nessun dato personale o segreto in codice, fixture, log o screenshot
- [ ] Le modifiche visibili all'utente sono documentate (README, `.env.example`, testi di aiuto dell'admin)
- [ ] Una sola modifica mirata, con un titolo in stile Conventional Commits

Grazie per il tuo contributo! 🪝
