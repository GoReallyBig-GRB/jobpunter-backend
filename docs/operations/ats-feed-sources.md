# Lever and Ashby job boards (zero-cost sources)

Both are configured as `JSON_API` sources. There are no new connector types, no database migration and
no new code path for network safety: the generic connector already enforces HTTPS, public-address
pinning, no redirects, size/time bounds, forbidden-target rules and live policy revalidation.
The only code change is `static_fields` (company/location constants), because neither feed names the
company. A feed's own value always wins over the constant.

## Source configuration

Lever (US). For EU-hosted boards use `api.eu.lever.co`. `<site>` is the last path segment of the
company's `https://jobs.lever.co/<site>` page.
```json
{
  "endpoint_url": "https://api.lever.co/v0/postings/<site>",
  "base_params": {"mode": "json"},
  "field_mapping": {"external_id": "id", "title": "text", "job_url": "hostedUrl",
                    "application_url": "applyUrl", "location": "categories.location",
                    "description": "descriptionPlain"},
  "static_fields": {"company": "<Company name>"}
}
```
Ashby. `<board>` is the last path segment of `https://jobs.ashbyhq.com/<board>`.
```json
{
  "endpoint_url": "https://api.ashbyhq.com/posting-api/job-board/<board>",
  "items_path": "jobs",
  "field_mapping": {"external_id": "id", "title": "title", "job_url": "jobUrl",
                    "application_url": "applyUrl", "location": "location",
                    "description": "descriptionPlain"},
  "static_fields": {"company": "<Company name>"}
}
```
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
  - Not yet demonstrated: any Ashby board returning records.
