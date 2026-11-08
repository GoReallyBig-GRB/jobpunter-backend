# Acquisition live-canary runbook

## Purpose

A live canary verifies that a specifically approved source works now. It does not certify every job board, every query, or future reliability. Standard CI remains offline and deterministic; live tests are opt-in.

## 1. Run the simulated regression tests

From the backend repository root:

```bash
python -m pip install -e ".[dev]"
pytest -q tests/unit/test_jobspy_connector.py tests/unit/test_playwright_connector.py tests/unit/test_acquisition_failure_simulations.py
```

The full CI suite continues to run through the existing verification workflow. Live tests are skipped unless explicitly enabled.

## 2. Approval and safety checks before a live run

1. Select one source whose terms, access rules, and rate limits permit the intended test.
2. Use a dedicated staging environment and a small query/batch. Do not use real candidate data.
3. For JobSpy, explicitly configure `site_names`, search terms, location, and a low `results_wanted` value. JobSpy may contact multiple external sites depending on configuration.
4. For Playwright, configure exact `start_urls`, matching `allowed_domains`, and selectors for the approved site. The canary rejects reserved/local hostnames and checks DNS answers are public before navigation.
5. **Run Playwright canaries only in an egress-restricted staging environment.** The DNS preflight is a guardrail, not complete protection against DNS rebinding or a destination changing after validation.
6. Do not bypass CAPTCHA, authentication barriers, anti-bot controls, or rate limits. Stop on 401/403/407/429 responses or unexpected access restrictions.
7. Do not place credentials, candidate personal data, or secrets in the JSON configuration or logs.

## 3. Run one canary

Set these environment variables in a local shell or controlled staging runner. The JSON below is a **shape example only**, not an approved source or a ready-to-run target.

```bash
export JOBPUNTER_RUN_LIVE_CANARIES=1
export JOBPUNTER_LIVE_CANARY_APPROVAL=I_APPROVE_EXTERNAL_REQUESTS
export JOBPUNTER_LIVE_CANARY_CONFIG='{"connector":"JOBSPY","context":{"site_names":["indeed"],"search_terms":["Technical Product Manager"],"location":"Nigeria","results_wanted":3,"hours_old":72},"minimum_records":1}'
pytest -q -m live tests/live/test_acquisition_live_canaries.py
```

For Playwright, install the browser runtime in the environment first:

```bash
python -m playwright install chromium
```

The Playwright `context` object must contain the approved site's `start_urls`, exact `allowed_domains`, selectors, and bounded `max_pages`/timeouts. Do not substitute a domain until it has been reviewed and approved. Use only one connector/source per run.

The two explicit opt-in variables are required. Without them, the live test is skipped. Invalid configuration fails the test rather than silently falling back to defaults.

### Greenhouse public job board
The Greenhouse connector uses only unauthenticated GET requests to the public board API and needs just a board token (no selectors, no browser):

```bash
export JOBPUNTER_LIVE_CANARY_CONFIG='{"connector":"GREENHOUSE","context":{"board_token":"<approved-board-token>"},"minimum_records":1}'
```

Use only a board whose owner has approved the test; confirm it exists at `https://boards-api.greenhouse.io/v1/boards/<token>`.

## 4. Acceptance criteria

A canary passes only when:
- At least the configured minimum number of records are returned.
- Each record has an HTTP(S) source URL and a 64-character content hash.
- At least one record contains a non-empty job title.
- The run completes without access-denied/rate-limit evasions or unexpected targets. Outcome must be `SUCCEEDED`, or `PARTIAL` whose only failures are `REDIRECT_BLOCKED` (off-domain requests blocked on an otherwise readable page).

This verifies acquisition output only. It does **not** by itself prove persistence, canonicalisation, candidate evaluation, deduplication, mission completion, scheduling, authentication, or storage. Those require separate staging integration/end-to-end tests and execution evidence.

## 5. Evidence to retain

Record the commit SHA, connector/version, approved source and configuration (with secrets removed), date/time, test command, record count, result, CI/staging run link, and known limitations. Never mark a connector production-verified from a mocked test or a skipped live canary.

## Evidence log

### 2026-11-07 — JobSpy / Indeed — PASSED (limited)
- Commit: 25b9ef8 (`dep-02/bounded-worker-execution`).
- Connector: `JOBSPY`; site `indeed`; search term "Technical Product Manager"; location "Nigeria"; `results_wanted` 3; `hours_old` 72; `minimum_records` 1. No credentials used.
- Command: `pytest -q -m live tests/live/test_acquisition_live_canaries.py` — result `1 passed in 7.91s`.
- Asserted by the test: outcome `SUCCEEDED`; at least 1 record; every record has an HTTP(S) source URL, a 64-character content hash and non-empty raw content; at least one non-empty job title.
- Limitations: one run only; exact record count was not captured; run from a developer Windows machine, not an egress-restricted staging environment; Indeed terms/rate-limit review not recorded here; says nothing about persistence, evaluation, scheduling, the Playwright connector, or the Greenhouse connector. Not production verification.

### 2026-11-07 — Greenhouse public board API — PASSED (limited)
- Commit: 4f9d3d3 (`dep-02/bounded-worker-execution`).
- Connector: `GREENHOUSE`; board token `greenhouse` (Greenhouse's own public board); `minimum_records` 1. Unauthenticated public GET requests only; no credentials used.
- Command: `pytest -q -m live tests/live/test_acquisition_live_canaries.py` — result `1 passed in 4.08s`.
- Asserted by the test: outcome `SUCCEEDED`; at least 1 record; every record has an HTTP(S) source URL, a 64-character content hash and non-empty raw content; at least one non-empty job title.
- Limitations: one run only; exact record count not captured; run from a developer Windows machine, not an egress-restricted staging environment; says nothing about persistence, evaluation or scheduling; the Playwright connector has no live canary yet. Not production verification.

### 2026-11-07 — Playwright / avion-labs.com careers page — PASSED (limited)
- Page supplied by the project owner for this test. Connector `PLAYWRIGHT`; `start_urls` `https://avion-labs.com/careers`; `allowed_domains` `avion-labs.com`; `max_pages` 1; `timeout_ms` 30000; `request_delay_seconds` 2; `minimum_records` 1. Selectors: `ready`, `title` and `job_url` = `a[href^='/careers/']`; `job_card` = XPath for the nearest ancestor of each job-title link that contains a "View Details" link. The page is JavaScript-rendered with no `<article>` cards.
- Run at commit ffbd069 (strict `SUCCEEDED` criterion): outcome `PARTIAL`, `records=8`, four `REDIRECT_BLOCKED` failures. The connector aborts every off-domain request (third-party fonts, scripts, images) and reports each as `REDIRECT_BLOCKED`; this is the connector's tested contract (`tests/integration/test_playwright_runtime.py`, scenario `off_domain_subresource` expects `PARTIAL`). Selectors worked.
- A connector change that stopped counting blocked subresources as failures (05d421e) made the canary pass (`1 passed in 6.00s`) but contradicted that tested contract, failed CI, and was reverted. That pass is therefore **not** evidence for the current code.
- Canary acceptance now accepts `SUCCEEDED`, or `PARTIAL` where every failure is `REDIRECT_BLOCKED` and at least the minimum records were read. Any other failure category still fails the canary. 
- **Re-run at commit 5dde0a8 (current connector and canary criteria): `1 passed in 14.97s`.** The test does not print which accepted outcome occurred (`SUCCEEDED`, or `PARTIAL` with only `REDIRECT_BLOCKED`) or the record count; the earlier run at ffbd069 read 8 records with the same selectors.
- Limitations: one run only; run from a developer Windows machine, not an egress-restricted staging environment; the site's terms/robots policy were not reviewed here; says nothing about persistence, evaluation or scheduling. Not production verification.
- Product decision (2026-11-08, user-approved option B): a refused third-party **subresource** (image, script, font, frame) is still blocked but no longer marks the run `PARTIAL`; it is counted in the run's `metrics.diagnostics.blocked_off_domain_subresources`. A refused **main-frame navigation** (the job page redirecting off-domain) still gives `REDIRECT_BLOCKED` / `RESTRICTED`. Verified by real-Chromium tests and the persisted-run test. The live avion-labs canary has NOT been re-run against this change from the sandbox (no egress); expect `SUCCEEDED` with a non-zero blocked count.

### 2026-11-07 — Scrapy / marketmasterusa.com positions page — PASSED (limited)
- Commit: d412139 (`dep-02/bounded-worker-execution`). Page supplied by the project owner for this test.
- Pre-check from the owner's machine: HTTP 200; the job title is present in the raw (server-rendered) HTML; `robots.txt` returns 404 (no rules); one open position listed.
- Connector: `SCRAPY`; `start_urls` `https://marketmasterusa.com/positions`; `allowed_domains` `marketmasterusa.com`; `max_pages` 1; `download_delay_seconds` 2; `timeout_seconds` 60; `minimum_records` 1. Selectors: `job_card` = `a[href^='/positions/']`, `title` = `h2::text`, `job_url` = `::attr(href)` (the card is the link itself). Selectors were checked offline against the owner-pasted markup with Scrapy's parser before the run.
- Command: `pytest -q -m live tests/live/test_acquisition_live_canaries.py` — result `1 passed in 9.79s`.
- Asserted by the test: outcome `SUCCEEDED` (or `PARTIAL` with only `REDIRECT_BLOCKED`); at least 1 record; every record has an HTTP(S) source URL, a 64-character content hash and non-empty raw content; at least one non-empty job title.
- Limitations: one run only; one page, one expected record; record count not printed by the test; run from a developer Windows machine, not an egress-restricted staging environment; says nothing about persistence, evaluation, scheduling or multi-page pagination. Not production verification.
