# Plan 2 backlog (carried over from Plan 1 reviews)

> **Status:** triaged in `2026-10-19-plan-2-operations.md` (section "Backlog triage"); every item is either implemented there, already fixed, or dropped with its ruling.

Items deferred during the Plan 1 (core pipeline) reviews. Plan 2 must address or explicitly drop each.

- Task 1-5: minor (deferred): malformed LEADHOOK_ENCRYPTION_KEY only fails on first use (validate Fernet key in settings)
- Task 1-5: minor (deferred): InvalidToken on wrong/rotated key breaks admin pages with raw traceback
- Task 1-5: minor (deferred): parsing.py not robust to non-dict change.value / nameless field / malformed created_time (docstring promises skipping malformed)
- Task 1-5: minor (deferred): encrypted-at-rest tests only assert != plaintext
- - Ruling: Important "DNS rebinding TOCTOU in SSRF guard" — DEFER to Plan 2 hardening (pin connection to validated IP) — destination URLs are admin-entered, so exploiting needs admin access — cost if wrong: an admin-configured hostile hostname could reach an internal address.
- Task 6-9: minor (deferred): response.text read fully before truncation (use stream); leadgen_id not url-quoted; template recompiled per send; test gaps (AuthType.NONE, other 3xx, DNS OSError retry, filter failure)
- Task 10: minor (deferred): _get_or_create_form swallows GraphError (form saved blank, never re-synced → Plan 2 sync_forms covers); fetch exhaustion silent (Plan 2 reconciliation/alerts); test gaps (task-level token error, Retry-After via deliver, fetch exhaustion); filler test test_destination_kind_is_webhook_by_default
- Task 6-9: minor (deferred): ALLOW_PRIVATE + bad IDNA → retry not fail; Retry-After >4300 digits int() ValueError; "scheme://None" message; trust_env=False ignores REQUESTS_CA_BUNDLE; DNS rebinding (Plan 2)
- Task 11: minor (deferred): stored-but-unenqueued events need a sweeper (Plan 2); lone surrogate \ud800 in JSON not examined (Postgres may reject); broker-failure test doesn't assert log
- Task 12-14: minor (deferred): DestinationAdmin stashes preview_lead on shared admin instance (thread-unsafe); extra_headers shows clear text (add help-text warning); secret-masking tests only for MetaApp; Dockerfile chown layer dup + .dockerignore should exclude .superpowers/tests/.github; worker/beat may start before migrate; README "up to 24 hours" wording (~32 h total); e2e server_close missing
- Task 10: minor (deferred): crashed mid-send delivery stays PENDING after lease expiry with no trigger → Plan 2 sweeper; lease overrun overwrite; no exhaustion test under lease
- - Ruling: remaining deferred minors left for Plan 2 per reviewer triage (InvalidToken page, nameless field/created_time parsing, encrypted test strength, streaming read, leadgen_id quote, test gaps, _get_or_create_form, fetch exhaustion alert, IDNA+ALLOW_PRIVATE, REQUESTS_CA_BUNDLE, DNS rebinding, sweepers, preview stash, masking tests, worker-before-migrate, lease overrun, exhaustion test, URL secrets in last_error); dropped: template recompile, filler test, scheme://None, log-assert, server_close.
