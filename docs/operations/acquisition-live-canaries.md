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

## 4. Acceptance criteria

A canary passes only when:
- At least the configured minimum number of records are returned.
- Each record has an HTTP(S) source URL and a 64-character content hash.
- At least one record contains a non-empty job title.
- The run completes without access-denied/rate-limit evasions or unexpected targets.

This verifies acquisition output only. It does **not** by itself prove persistence, canonicalisation, candidate evaluation, deduplication, mission completion, scheduling, authentication, or storage. Those require separate staging integration/end-to-end tests and execution evidence.

## 5. Evidence to retain

Record the commit SHA, connector/version, approved source and configuration (with secrets removed), date/time, test command, record count, result, CI/staging run link, and known limitations. Never mark a connector production-verified from a mocked test or a skipped live canary.
