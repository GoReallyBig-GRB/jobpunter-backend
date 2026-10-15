# JobPunter acquisition and opportunity feed: frontend handoff

**Status:** integration contract added; backend CI status must be checked on the final branch head. This document describes the repository contract, not proof that live providers or deployed workers are available.

## 1. End-to-end data flow

`configured source / user submission → connector or parser → acquisition run → raw source record → discovery observation → transactional outbox → canonicalization and identity resolution → canonical job + description versions + provenance → candidate-specific evaluation → opportunity feed → candidate-created Punt → candidate-controlled application lifecycle`

The canonical Job Pool is shared. Candidate evaluations, Punts, and application records are candidate-scoped; the backend must not duplicate a canonical job for each candidate.

Every successful connector acquisition now records a `JobDiscoveryEvent`, including unchanged repeats. Observation outcomes are `NEW`, `CHANGED`, and `UNCHANGED`. A raw-record duplicate is not an excuse to lose the new observation timestamp. Once identity is resolved, observation events link to the canonical job and refresh `last_observed_at`. Material JD changes create a new description version through canonicalization.

## 2. Authentication and trust boundaries

- All candidate routes derive the candidate from the authenticated principal. Do not send a client-selected candidate/profile ID as authority.
- Source registry and automated acquisition routes require an admin principal.
- Automated sources must have an explicit approved permission review before health-check/acquisition.
- Connector configuration belongs on the backend. Do not return or log connector configuration/secrets in browser-facing responses.
- An `APPLY` evaluation is a recommendation, not a Punt and not an application submission. Punt creation and application lifecycle are separate, candidate-controlled actions.

## 3. Source registry and acquisition

Base path: `/api/v1/admin`.

| Method and path | Purpose |
|---|---|
| `GET /sources` | List source registry entries and connector readiness/state |
| `POST /sources` | Register a configured connector and publish initial configuration version 1 |
| `GET /sources/{source_id}/connectors/{connector_id}/configuration-versions` | Read immutable configuration history; secret values are redacted |
| `PATCH /sources/{source_id}/connectors/{connector_id}/configuration` | Create a validated configuration draft with `expected_revision` and a reason |
| `POST /sources/{source_id}/connectors/{connector_id}/configuration-versions/{version_number}/publish` | Validate and publish a draft, atomically making it active |
| `POST /sources/{source_id}/connectors/{connector_id}/configuration-versions/{version_number}/activate` | Roll back to a previously published version without mutating its contents |
| `PATCH /sources/{source_id}/permission-review` | Record the explicit permission decision and audit reference |
| `PATCH /sources/{source_id}/status` | Activate/pause a source |
| `POST /sources/{source_id}/connectors/{connector_id}/health-check` | Run a bounded source probe and persist the operational result |
| `POST /sources/{source_id}/acquisition-runs` | Execute a configured connector through the shared acquisition pipeline |
| `GET /sources/{source_id}/acquisition-runs` | Read durable run history |
| `GET /sources/{source_id}/acquisition-runs/{run_id}` | Read one run and downstream dispatch status |

Registered connector types currently wired in the backend:

| Connector type | Required configuration at registration | Discovery wave |
|---|---|---|
| `JOBSPY` | `search_terms`, `location` | `jobspy` |
| `SCRAPY` | `start_urls` | `targeted_scrapers` |
| `PLAYWRIGHT` | `start_urls` | `browser_automation` |
| `GREENHOUSE_JOB_BOARD` | `board_token` | `official_feeds` |
| `JSON_API` | `endpoint_url` | `official_feeds` |
| `RSS_ATOM` | `feed_url` | `official_feeds` |

Example registration request for a JSON API:

```json
{
  "name": "Employer jobs API",
  "connector_type": "JSON_API",
  "configuration": {
    "endpoint_url": "https://jobs.example.com/api/jobs"
  },
  "application_route_type": "DIRECT_EMPLOYER"
}
```

The URL above is illustrative only. The backend does not assert that this example host exists.

After registration, an administrator must review/approve the source permission and then perform a health check before scheduling/triggering automated acquisition. Connector health probes are bounded acquisition probes for connectors without a dedicated health method; a successful probe is not proof that every job was extracted correctly.

Connector configuration follows a draft/publish lifecycle. Draft creation does not change the active configuration. Publishing validates the draft and atomically switches the active version. Published versions are immutable; rollback activates an existing validated version and writes an audit event rather than editing historical content. The registry exposes the active version number and connector revision, but not raw credentials. The version-history endpoint returns configuration with secret-bearing fields redacted. Mutations require the revision returned by the registry; stale edits receive a conflict and must be refreshed.

Every acquisition run created through the registered-source API records the exact `configuration_version_id` used. Run history/detail responses expose that identifier. Idempotent replay returns the original run and original configuration version; a run key already associated with a different connector is rejected.

The acquisition-run response exposes `configuration_version`, `configuration_version_id`, `acquired_count`, `persisted_count`, `duplicate_count`, `observed_count`, `new_count`, `changed_count`, `unchanged_count`, and a `downstream_processing` summary. Treat run state and outbox state as operational telemetry; neither means the resulting job is a fit.

## 4. User-provided URL, JD and email content

`POST /api/v1/candidate/jobs/manual-ingestion`

Supported `channel` values are `MANUAL_URL`, `MANUAL_JD`, and `MANUAL_EMAIL`. Supply an `Idempotency-Key` header. The payload supports title, company, description, source URL, application URL, external ID, location, employment type, fee/relocation facts, route classification, posting date, and email subject/sender/body.

Email content is currently a **user-submitted email body**, not a connected Gmail mailbox. The parser must safely identify exactly one job for this endpoint; ambiguous or multi-job email content returns `EMAIL_REQUIRES_REVIEW` rather than silently selecting one job. Submit each extracted job separately after review.

The response includes the durable acquisition run ID, canonicalization result, idempotent-replay flag, and the observation counts. A repeated idempotency key must not create a second observation; a distinct submission of the same unchanged posting records an `UNCHANGED` observation.

## 5. Candidate discovery controls

Base path: `/api/v1/candidate/discovery`.

- `GET /settings`: read/create the candidate's discovery settings.
- `PUT /settings`: update settings with optimistic revision control.
- `POST /missions`: create an idempotent discovery mission.
- `GET /missions`: read the candidate's mission history.

Mission settings include cadence, target opportunity count, maximum job age, verification requirement, evaluation depth, and source filters. The scheduler and mission/evaluation workers are separate processes; API settings do not prove those processes are deployed or running.

## 6. Candidate opportunity feed

`GET /api/v1/candidate/opportunities`

Query parameters:
- `limit`: 1–50, default 20
- `offset`: non-negative, default 0
- `outcome`: `APPLY | REVIEW | WATCH | REJECT`
- `validity_status`: `CURRENT | STALE`
- `freshness_status`, `lifecycle_status`: optional state filters
- `query`: title, employer, or location search

The feed returns the **latest evaluation per candidate/job/target-role pair**. A job can therefore appear more than once when evaluated for multiple target roles. The response includes:
- canonical job fields, location/work mode, salary where known, application URL, lifecycle and freshness states;
- current application-route verification details, including an explicit `UNKNOWN` state when evidence is absent, stale, or URL-mismatched;
- the latest job-description version, completeness and requirement-extraction state;
- source provenance with source identity, source URL, external job ID, first/last-seen times and observation count;
- the candidate's evaluation outcome, validity, score, hard blockers, evidence summary, explanation and policy version;
- existing Punt status for that candidate/job, if any;
- `pagination.limit`, `pagination.offset`, and `pagination.total`.

Do not display an application CTA as verified-active based only on the existence of `application_url`. Respect `application_route_verification.status`; `UNKNOWN` is not active evidence. Show the evaluation's reasons, hard blockers, gaps, and uncertainty. A candidate explicitly creates a Punt through `POST /api/v1/candidate/punts`; the application lifecycle remains separate.

Existing evaluation and workflow routes remain available:
- `GET/POST /api/v1/candidate/evaluations`
- `POST /api/v1/candidate/evaluations/{evaluation_id}/overrides`
- `GET /api/v1/candidate/punts`, `POST /api/v1/candidate/punts`
- `GET /api/v1/candidate/applications`, `POST /api/v1/candidate/applications`, and application transition/event routes.

## 7. Statuses the UI must not conflate

- Source registry status (`ACTIVE`/paused) is not connector health.
- Operational source states (`AVAILABLE`, `DEGRADED`, `RESTRICTED`, `UNAVAILABLE`) reflect observed/configured source conditions, not extraction completeness.
- Acquisition status and downstream outbox processing status are different.
- Raw records and canonical jobs are different entities.
- Job lifecycle/freshness/verification are separate from candidate fit.
- Evaluation disposition (`APPLY`, `REVIEW`, `WATCH`, `REJECT`) is different from a Punt and from an application state.
- A missing or unverified fact must remain unknown/reviewable; do not infer a pass.

## 8. Explicitly not represented as complete by this handoff

- Connected Gmail OAuth, mailbox permissions, token lifecycle, and revocation. Manual email-body ingestion is available; Gmail integration is not implied.
- Live-source terms/rate-limit review and live canary proof for every configured provider.
- Production deployment, scheduling, worker health, crash/restart recovery and operational alerting in the target environment.
- Independent review/merge of the stacked backend PRs.
- Full browser-based frontend integration and acceptance testing.

These are remaining backend/integration acceptance gates, not features the frontend should assume exist.
