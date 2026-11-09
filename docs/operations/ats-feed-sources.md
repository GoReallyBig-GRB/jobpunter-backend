# Lever and Ashby job boards (zero-cost sources)

Both are configured as `JSON_API` sources. There are no new connector types, no database migration and
no new code path for network safety: the generic connector already enforces HTTPS, public-address
pinning, no redirects, size/time bounds, forbidden-target rules and live policy revalidation.
The only code change is `static_fields` (company/location constants), because neither feed names the
company. A feed's own value always wins over the constant.

## Source configuration

Lever (US). For EU-hosted boards use `api.eu.lever.co`. `<site>` is the last path segment of the
company's `https://jobs.lever.co/<site>` page. Lever keeps duties and requirements in `lists` and the
closing text in `additionalPlain`, outside `descriptionPlain`, so the description is assembled from all
three (live canary before this: only 41/85 Spotify and 207/305 Palantir jobs had a usable description).
```json
{
  "endpoint_url": "https://api.lever.co/v0/postings/<site>",
  "base_params": {"mode": "json"},
  "max_response_bytes": 8000000,
  "field_mapping": {"external_id": "id", "title": "text", "job_url": "hostedUrl",
                    "application_url": "applyUrl", "location": "categories.location"},
  "description_paths": ["descriptionPlain", "lists", "additionalPlain"],
  "omit_fields": ["description", "descriptionBody", "descriptionBodyPlain", "opening", "additional",
                  "descriptionPlain", "lists", "additionalPlain"],
  "static_fields": {"company": "<Company name>"}
}
```
Ashby. `<board>` is the last path segment of `https://jobs.ashbyhq.com/<board>`.
```json
{
  "endpoint_url": "https://api.ashbyhq.com/posting-api/job-board/<board>",
  "items_path": "jobs",
  "max_response_bytes": 8000000,
  "field_mapping": {"external_id": "id", "title": "title", "job_url": "jobUrl",
                    "application_url": "applyUrl", "location": "location",
                    "description": "descriptionPlain"},
  "omit_fields": ["descriptionHtml", "descriptionPlain"],
  "static_fields": {"company": "<Company name>"}
}
```
`omit_fields` drops feed keys from the stored record. The description is kept once, at the top level,
so omitting `descriptionPlain` and the HTML copy loses nothing the job pipeline uses.

Each source still needs the normal admin permission review before it can run.

## Large boards
Big employers return every posting with its full description in one response, which can exceed the
connector's default 2 MB response cap (the connector then fails closed with `JSON_API_RESPONSE_TOO_LARGE`).
Add `"max_response_bytes": 8000000` to the configuration for such boards (allowed range 100000 to
10000000). The cap stays bounded and the 500-record limit still applies.

## Cost evidence (zero-cost gate)
| Question | Lever | Ashby |
|---|---|---|
| Key, account or card needed to read published jobs | No (secondary sources) | No (secondary sources) |
| Billing exposure | None found; read-only GET | None found; read-only GET |
| Documented rate limit on the public read endpoint | None found. (The 10 req/s limit reported for Lever applies to its authenticated Data API, not this endpoint.) | None found |
| Our request volume | One request per source per refresh | One request per source per refresh |
| Behaviour at a limit | HTTP 429 or 5xx is mapped by the connector to RESTRICTED/UNAVAILABLE with bounded retry; no evasion | same |

**Evidence quality: SECONDARY, NOT VERIFIED against the providers' official documentation.** The official
pages (github.com/lever/postings-api, developers.ashbyhq.com) are outside the build sandbox's network
allowlist. Sources used: a developer article on each public API, and a vendor blog noting that the Lever
public GET "needs no auth" and may occasionally return HTML instead of JSON (the connector treats a
non-JSON body as `json_api_invalid_json`, a retryable failure). Re-check the official pages and terms
before scaling up, and confirm each employer's board is meant to be consumed publicly.

Known limits: Lever's `descriptionPlain` is the posting opening; the body is in separate fields
(`descriptionBodyPlain`, `lists`, `additionalPlain`), so descriptions may be shorter than the web page.
Requirement extraction runs on what is stored, so check a sample before relying on it.

## Live canary (run on a machine with internet access)
Pick any company you can see at `jobs.lever.co/<site>` and `jobs.ashbyhq.com/<board>`, then:
```powershell
cd C:\Users\user\jobpunter-backend; .\.venv\Scripts\Activate.ps1
$env:JOBPUNTER_RUN_LIVE_CANARIES="1"; $env:JOBPUNTER_LIVE_CANARY_APPROVAL="I_APPROVE_EXTERNAL_REQUESTS"
$env:JOBPUNTER_LIVE_CANARY_CONFIG='{"connector":"JSON_API","context":{"endpoint_url":"https://api.lever.co/v0/postings/SITE","base_params":{"mode":"json"},"field_mapping":{"external_id":"id","title":"text","job_url":"hostedUrl","application_url":"applyUrl","location":"categories.location","description":"descriptionPlain"},"static_fields":{"company":"COMPANY"}},"minimum_records":1}'
python -m pytest tests/live/test_acquisition_live_canaries.py -q -s
$env:JOBPUNTER_LIVE_CANARY_CONFIG='{"connector":"JSON_API","context":{"endpoint_url":"https://api.ashbyhq.com/posting-api/job-board/BOARD","items_path":"jobs","field_mapping":{"external_id":"id","title":"title","job_url":"jobUrl","application_url":"applyUrl","location":"location","description":"descriptionPlain"},"static_fields":{"company":"COMPANY"}},"minimum_records":1}'
python -m pytest tests/live/test_acquisition_live_canaries.py -q -s
```
Replace `SITE`, `BOARD` and `COMPANY` first. `-s` prints one `CANARY ...` line with status, record count
and first title; paste it back so the result is recorded here.

## Evidence log
- 2026-11-08: unit-level mapping tests with representative payloads (not live): PASSED.
- 2026-11-08 first live run (user's Windows machine, code at 7219fa8, default 2 MB cap):
  - Lever `spotify`: connector SUCCEEDED, 85 records, first title "Account Executive - Commercial,
    Spotify Technology". The test then failed on a canary title check that did not look in the
    JSON_API record layout (test bug, fixed after this run).
  - Lever `palantir`, Ashby `ramp`, `notion`, `openai`: FAILED `JSON_API_RESPONSE_TOO_LARGE` (response
    over 2 MB, failed closed). Addressed by the configurable `max_response_bytes`; re-run pending.
  - Lever `mistral`: FAILED `UPSTREAM_REQUEST_FAILED`; cause not diagnosed (slug may not exist).
  - (Superseded by the re-run below.)
- 2026-11-08 re-run (user's Windows machine, code at 7af29ef, `max_response_bytes` 8000000):
  - Lever `spotify`: SUCCEEDED, 85 records. Lever `palantir`: SUCCEEDED, 305 records (first title
    "Administrative Business Partner").
  - Ashby `ramp`: SUCCEEDED, 169 records. Ashby `notion`: SUCCEEDED, 154 records.
  - Ashby `openai`: FAILED `JSON_API_RESPONSE_TOO_LARGE` even at 8 MB (failed closed, 0 records). Not
    retried at the 10 MB ceiling; Ashby's board endpoint has no documented pagination, so very large
    boards may need either the 10 MB ceiling or a different approach.
  - Canary evidence only: connector fetch and mapping against live feeds. Persistence, evaluation,
    scheduling and any admin-created Lever/Ashby source were NOT exercised live.

## Defect found by the end-to-end test (2026-11-08)
The live canaries only proved the connector fetched and mapped feeds. A PostgreSQL end-to-end test
(`tests/integration/test_ats_feed_to_canonical_job_postgres.py`: connector, outbox, canonicalization)
showed the downstream job normalizer read none of the mapped values: a Lever job had an empty title, no
company, no description and no location, and was INCOMPLETE. The connector now also writes the mapped
`title`, `company`, `location` and `description` to the top level of the stored record (this also
fixes any existing JSON_API source whose feed uses non-default field names; their stored
content hashes change once, so the next refresh records those jobs as CHANGED). Both new tests fail
without the fix (verified by reverting it). Lever caveat to verify with live data: `descriptionPlain`
may be only the opening; the canary now prints `usable_descriptions=N/M` so this can be measured.

## Review: raising the response-size ceiling (currently 10 MB per source, default 2 MB)
Measured on the build sandbox with a synthetic Ashby-shaped board (12 KB plain + 12 KB HTML description
per job; one run each, so indicative only):

| Payload | Jobs | Peak memory in the connector | Time |
|---|---|---|---|
| 2.0 MB | 81 | 8 MB | 0.09 s |
| 7.8 MB | 321 | 31 MB | 0.16 s |
| 9.7 MB | 402 | 39 MB | 0.22 s |

Stored size: one 24 KB feed job is stored as about 73 KB of `raw_content` (3.0x), because the record is
kept as the feed's native fields, a `source_record` copy, the normalized fields and now the top-level
description. Unchanged jobs are not stored again (content-hash dedupe); only new or changed jobs add rows.

**Merits of a higher ceiling:** fewer fail-closed misses on the largest employers (OpenAI exceeded 8 MB);
no per-source tuning; CPU and time are negligible at these sizes.

**Demerits:**
- Memory: about 4x the payload at peak. Fine at 10 MB (about 40 MB) but 50 MB would be about 200 MB, which
  does not fit beside the other workers on a 512 MB host (the whole host measured 570 to 590 MB earlier).
- Database storage is the real constraint on a zero-cost plan: a 10 MB board is about 30 MB of raw rows on
  first ingest, so roughly 15 such boards per 500 MB of free database. The provider and quota are not chosen yet (DEP-01/05).
- The cap is a safety control: it bounds what a hostile or misconfigured endpoint can make us download and
  hold each refresh. A larger default weakens that for every source.
- It does not remove the 500-record cap, and Ashby offers no way to request fewer fields or pages.

**Recommendation:** keep the 2 MB default and the 10 MB per-source ceiling; do not raise either now. Treat
boards above 10 MB as unsupported until measured need and the database quota are known. If you want more
headroom cheaply, the better lever is storage, not the cap: stop duplicating the `source_record` copy and
the unused HTML description (about 3x down to about 1.2x). That changes working code, so it needs your
authorization first.

## Storage trimming (user-approved 2026-11-08) and description assembly
- The stored record no longer contains a full `source_record` copy. Only native values the connector
  overwrites (for example the feed's own `url`) are kept, under `overwritten_source_fields`, and only when
  they differ. `jobpunter_normalized` no longer repeats the description.
- New optional settings: `omit_fields` (drop unused feed keys) and `description_paths` (build the
  description from several fields; lists of `{text, content}` sections are flattened, HTML is reduced to
  text without being interpreted).
- Measured, synthetic Ashby-shaped job (24 KB feed): stored 73 KB before, 36 KB after the trimming (1.5x),
  12 KB with `omit_fields` (0.5x of the feed size). On a 500 MB database that moves a 10 MB board from
  about 30 MB to about 5 MB of raw rows.
- Stored content hashes change for existing JSON_API sources, so their next refresh records those jobs as
  CHANGED once.
- Verified by unit tests and a PostgreSQL end-to-end test (a Lever posting whose duties and requirements
  exist only in `lists` becomes a USABLE job). Live: NOT yet re-run with the new Lever configuration.
- Live results before this change: Ashby ramp 168/169 and notion 154/154 usable; Lever spotify 41/85 and
  palantir 207/305 usable; Ashby openai still too large at 8 MB.

## Live re-run after description assembly and storage trimming (2026-11-09, developer machine)
Config: Lever `description_paths ["descriptionPlain","lists","additionalPlain"]` with the Lever `omit_fields`
(see above), `max_response_bytes 8000000`. Connector fetch and mapping only; persistence was not exercised live.

| Board | Before (descriptionPlain only) | After |
|---|---|---|
| Lever spotify | usable_descriptions 41/85 | 85/85 (SUCCEEDED, 85 records) |
| Lever palantir | 207/305 | 305/305 (SUCCEEDED, 305 records) |

Conclusion: the duties and requirements were in `lists`/`additionalPlain`, as assumed; field names are now
confirmed against live Lever data for these two boards. Not re-run in this pass: Ashby ramp and notion with
`omit_fields`, Ashby openai at 10 MB, Lever mistral (earlier UPSTREAM_REQUEST_FAILED, undiagnosed).
