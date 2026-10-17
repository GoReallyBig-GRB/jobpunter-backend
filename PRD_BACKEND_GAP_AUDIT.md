# JobPunter Backend PRD Gap Audit

**Baseline:** JobPunter Product Requirements Document (PRD) + Technical Spec v1.5  
**Repository:** GoReallyBig-GRB/jobpunter-backend  
**Audit evidence baseline (2026-10-17):** Cumulative backend branch `batch-1-invariant-triage`, reconciled against PRD + Technical Spec v1.5. The latest implementation/test commit `5ec242cf0481bea221a844ab93ac87161563d843` passed GitHub Actions [run #1664](https://github.com/GoReallyBig-GRB/jobpunter-backend/actions/runs/39011778265): **1,427 passed, 1 skipped, 1 warning**. The audit-reconciliation parent `10e232657950b4158282cb4cb6e474194be4d847` passed GitHub Actions [run #1665](https://github.com/GoReallyBig-GRB/jobpunter-backend/actions/runs/39011811107) with the same counts; CircleCI backend verification passed on that exact parent. This audit is evidence of tested backend paths, not production-readiness certification. Live OCR credentials/provider behavior, live R2/Drive credentials and provider-level recovery, deployed worker operation, frontend integration, and end-to-end candidate acceptance remain unverified. This audit supplements, and does not replace, the PRD's Current-State Audit & Gap Closure Control Register and invariant list.

## Status meanings

- **Partial:** a scaffold or only part of the required path exists.
- **Not implemented:** no implementation was found in the audited backend tree.
- **Not verified:** code exists but persistence, security, integration, or runtime evidence is missing.
- **Verified:** the complete required path exists and relevant tests pass.

A model, service file, route, screen, or green unit-test run alone does not prove a capability is implemented. The matrix below describes the status of each broad PRD capability, which can remain Partial when its full product/runtime acceptance is open. It is not the completion status of a narrower, tested implementation task. PR #77's committed task scopes and their acceptance evidence are tracked separately in the completion ledger below.

## Executive finding

**Current implementation update (2026-10-16):** The cumulative branch now includes all twelve registered AI tasks with typed input and output contracts, governed execution/fallback provenance, application lifecycle/audit hardening, candidate-scoped immutable CV versions and approvals, CV document generation/download, package revision and approved-CV provenance checks, explicit per-conflict import decisions, and checksum-verified restore from a recorded Google Drive backup to private object storage. Exact-head verification is GitHub Actions run #1636 on `77b31bdd0cd0c8526da024f8a80fe8503d315228` (1,401 passed, 1 skipped, 1 warning). These results do not close the broader PRD end-to-end workflow, runtime, deployment, live credential, OCR, frontend integration, or production-recovery requirements.

The backend is an early foundation, not a complete MVP backend. The repository now has a linear Alembic history, ORM/database schema parity checks, candidate/identity/evidence persistence, a more reliable acquisition-persistence slice, and admin configuration lifecycle integration coverage. The latest completed CI run ([workflow run](https://github.com/GoReallyBig-GRB/jobpunter-backend/actions/runs/38067188966)) passed compilation, migration upgrade, ORM/schema parity, and the PostgreSQL 16 test suite: 456 tests passed, with one deprecation warning. Discovery screening now sends records missing employer or both location and work-mode context to NEEDS_REVIEW instead of allowing them into matching; these unknowns are not treated as automatic disqualifications. AI request validation now rejects whitespace-only prompts and schema descriptions and enforces the 100,000-byte limit against the actual request body even when Content-Length is absent; this does not replace ingress-level request-size limits. Greenhouse acquisition integration tests cover durable run/raw-record/outbox persistence, audit attribution, authorization, invalid board-token rejection, idempotent run-key replay without refetching, and sanitized upstream-failure persistence/audit. Firebase unit tests verify expected-project initialization, rejection of a mismatched default app, revocation checking, and fail-closed handling of malformed decoded claims. PostgreSQL failure-injection tests verify candidate profile/source/evidence creation rolls back atomically and candidate claim/link creation does not leave partial records. AI governance tests also verify that successive executions use and record the respective current published configuration versions. PostgreSQL regressions cover duplicate delivery through the production canonicalization outbox handler and deterministic rejection of a candidate claim whose asserted years conflict with approved evidence. Admin governance validation also rejects task configuration keys that do not match a registered AI task. This run includes centralized server-owned AI task policy metadata and registry coverage tests, strict JSON-mode parsing including rejection of non-standard JSON constants and scalar root values, published-task configuration/traceability including config changes between executions, persisted AI failure-category regressions, and semantic grounding of document/JD extraction plus evidence-matching requirement/evidence references; typed input contracts and semantic checks remain incomplete for other generation tasks. This run includes concurrent canonicalization, concurrent first-time Punt qualification, Firebase project-bound/revocation-checked verifier behavior, identity persistence/concurrency and invalid-token non-creation, concurrent acquisition run-key idempotency, acquisition connector/persistence failure handling, deterministic hard-blocker enforcement, application lifecycle rollback/revision/audit/concurrency regressions, and candidate claim approval tests for ownership, expired/future-effective evidence, and concurrent revision safety. CI validates the tested paths only; it does not establish full PRD parity or production readiness. See [foundation migration and audit PR](https://github.com/GoReallyBig-GRB/jobpunter-backend/pull/2) and [acquisition reliability PR](https://github.com/GoReallyBig-GRB/jobpunter-backend/pull/3).

The remaining implementation gap is still substantial. Candidate-scoped evaluation listing, Punt qualification/persistence, application lifecycle transitions, lifecycle events, and outbox dispatch/canonicalization handling now exist as foundations. One Greenhouse public-board connector and a protected synchronous acquisition API now exist, but live-board verification, connector editing and policy review, worker deployment, a complete evaluation-generation engine, scheduling, and integrations remain incomplete. Canonical provider-neutral AI orchestration now governs background job-requirement extraction through a registered task, typed input, server-owned output validation, source-excerpt grounding, published task configuration and system-owned AIExecution provenance. Worker deployment and recovery remain unverified. A protected admin API now provides grant-checked telemetry/audit reads, versioned configuration draft/publish/rollback-activation operations, and an audited administrator-grant lifecycle. AI task configuration keys are checked against the canonical task registry, so administrators cannot publish a configuration for an unregistered task ID. PostgreSQL lifecycle tests cover draft/version sequencing, publish/retire behavior, rollback activation, audit request IDs, grant creation/revocation/reactivation, and concurrent revocation safety. The first-admin bootstrap runbook exists but has not been exercised in staging; broader operational governance remains open.

## Requirements-to-implementation matrix

| PRD capability | Status | Evidence / gap |
|---|---|---|
| FastAPI shell, request IDs, error handling | Partial | Startup, middleware and error handler exist; deployed API not demonstrated. |
| Provider-neutral identity | Partial | Identity port and user/identity models exist. PostgreSQL integration tests cover durable Firebase UID-to-user mapping, repeat sign-in, concurrent first-sign-in convergence, rejection of disabled local accounts, and invalid-token failure without local user/identity creation using injected verified claims. Actual Firebase Admin token verification with production credentials and deployment configuration remain unverified. |
| Candidate ownership and authorization | Partial | Candidate opportunity queries are scoped through the authenticated principal's user ID and candidate profile. PostgreSQL integration tests cover cross-candidate evaluation/Punt isolation and application-transition IDOR. Trusted production token validation and broader private-resource coverage remain unverified. |
| Master Career Profile | Verified for structured profile fields and profile API | Candidate-owned GET/PATCH profile APIs cover identity/contact, eligibility, professional identity, career history, projects, achievements, challenges, leadership, skills/tools, education and certifications. PostgreSQL API tests round-trip the PRD field groups, preserve explicit unknown/unverified values, verify section-level clears, revision conflicts, provenance, and cross-candidate isolation. Preferences and approved career pathways are separate capabilities and are assessed separately; this status does not claim the full candidate journey is end-to-end verified. |
| Evidence Library and conflict resolution | Partial | Evidence/source and claim links exist. Claim approval requires linked, current APPROVED evidence owned by the same candidate, and a deterministic validator checks scalar facts against linked evidence. PDF/DOCX/TXT import supports candidate-scoped preview/confirmation and structured extraction through the server-owned `document_parsing` contract. Structured conflicts compare selected single-value facts (name, email, phone, date of birth, LinkedIn URL) against approved evidence. Confirmation now requires one explicit `KEEP_IMPORTED`, `KEEP_EXISTING`, or `KEEP_BOTH` decision per detected conflict; decisions persist on the import record, and imported evidence does not silently overwrite the Master Profile. Multi-valued work/education facts are not treated as conflicts. Authenticity verification, broad semantic entailment, and full end-to-end profile/evidence acceptance remain open. |
| PDF/DOCX/TXT import | Partial | Upload validates type/signature and limits files to 4 MiB; text extraction, private object storage, candidate-scoped preview, conflict review, explicit conflict decisions, confirmation and evidence-save flow exist. Structured AI extraction uses the server-owned `document_parsing` output contract; overlong text is rejected rather than silently truncated. Opt-in scanned-PDF OCR is implemented behind `DOCUMENT_OCR_ENABLED` and uses bounded Google Cloud Vision requests; it is disabled by default. Candidate-owned restore from a recorded Google Drive backup verifies backup identity metadata, SHA-256 and byte size before restoring to the original private object key. Failure states distinguish missing private objects, transient storage-read failures, parse failures, AI extraction failures and OCR failures. Live Google Vision credentials/provider behavior, live R2/Drive credentials, provider-level recovery validation, and end-to-end candidate acceptance remain open. |
| Master CV, variants and document generation | Partial | Candidate-scoped CV Studio stores append-only Master CV/variant versions with profile snapshots and hashes, source revision/master-version provenance, stale-write checks, explicit approval records, and history. Candidates can regenerate a Master CV from a revision-checked profile snapshot, create variants from an approved Master CV version, save a new version, approve a latest version, and generate/download DOCX or PDF documents through private storage. Application packages can link a candidate-owned approved CV version and retain its provenance; package revisions use expected-current-version checks. Still open: full end-to-end candidate workflow, frontend integration, proof that all CV editing/regeneration flows preserve profile facts, package editing/approval and rendered-artifact E2E acceptance, and candidate submission integration. |
| Source Registry and connector contract | Partial | Source/connector models and the acquisition Protocol exist. A Greenhouse Job Board connector reads public board metadata and job listings through fixed HTTPS GET endpoints, validates board-token syntax and response shape, hashes raw content deterministically, and sanitizes upstream failures. Protected admin endpoints create/list source registry entries, pause/disable/reactivate sources, run an audited health check, and execute acquisition. Health-check success/failure tests verify token non-disclosure, audit attribution, connector ACTIVE/DEGRADED state, and that a degraded connector cannot acquire jobs. Health checks now update `Source.last_observed_at` on both success and failure. Connector editing, live Greenhouse smoke testing, source-specific terms/rate-limit review, and scheduled execution remain open. See the [official Greenhouse Job Board API documentation](https://docs.greenhouse.io/job-board.html). |
| Acquisition runs and raw records | Partial | Run creation is durable before connector execution; idempotent run keys, safe failure status, raw-record deduplication, changed-content preservation, and transactional outbox events for new raw records are implemented. The API reports `acquired_count` (records fetched), `persisted_count` (new raw records stored), and `duplicate_count` (fetched records not newly stored because of deduplication). It also returns `downstream_processing` counts for durable outbox events associated with raw records newly persisted by that run: pending, processing, processed, failed/dead-letter, and other statuses. Duplicate records do not create a new event and are excluded from that run's downstream counts. A `processed` event means the canonicalization handler completed, not that the job was qualified, evaluated, or surfaced to a user. PostgreSQL tests verify duplicate accounting, processing-status aggregation, and the transactional outbox record. Protected admin GET endpoints now list source-specific acquisition history with bounded pagination and retrieve an individual run by source and run ID. History returns persisted run metrics and safe error summaries; detail retrieval also returns refreshed downstream counts. Tests cover admin-only access, source scoping, pagination/order, and status changes after the original POST response. Same-key replay does not refetch. Success and failure both update `Source.last_observed_at`. An acquisition run marked `SUCCEEDED` means fetching and raw-record persistence completed; it does not mean downstream canonicalization or evaluation completed. Live upstream verification, source-specific outcome monitoring, and scheduled end-to-end processing remain open. |
| Canonical Job Pool and identity resolution | Partial | Canonicalization uses source-scoped external IDs, normalized application URLs and exact normalized fingerprints; ambiguous candidates are routed to review. PostgreSQL integration tests cover replay idempotency, per-observation provenance, stable description versions, changed-description versioning, source-ID convergence, and concurrent processing of distinct raw records sharing an exact normalized identity signal. The concurrent test asserts one canonical job and CREATED/MATCHED outcomes. A recoverable outbox dispatcher can invoke canonicalization. Real-source ingestion, operational worker deployment, richer fuzzy matching, and the review API remain open; broader repository-wide transaction, rollback, and concurrency coverage is still incomplete. |
| URL, pasted JD, email, API/feed ingestion | Partial | Authenticated `POST /api/v1/candidate/jobs/manual-ingestion` supports manual URL retrieval, pasted job descriptions, and job-alert/recruiter email parsing through the shared acquisition/canonicalization path. PostgreSQL tests cover persistence, provenance, URL normalization, idempotent replay and retrieval failure fallback. General third-party API/feed adapters and complete end-to-end discovery acceptance remain unimplemented. |
| JD retrieval, completeness, closure and freshness | Partial | Acquisition normalizes description content and assigns a minimum-content status using 300 characters and 50 words; discovery screening prevents missing/incomplete descriptions, missing application URLs, missing employer metadata, and unknown location/work-mode context from entering matching as eligible. This is not semantic JD completeness, closure verification, or a full retrieval/freshness pipeline; those remain open. |
| Candidate-specific evaluation and explainable decisions | Partial | CandidateEvaluationService persists versioned evaluations, input snapshots, approved evidence/claim provenance, deterministic scores/outcomes, hard blockers and explanations. PostgreSQL tests cover candidate-specific evidence, source-version provenance, excluding future/expired evidence, closed-job WATCH outcomes, and an explicit excluded-industry blocker that persists as REJECT and prevents Punt creation. Unit tests additionally verify excluded job type, ineligible country, job-specific country restrictions, and remote-only mismatch all deterministically REJECT before incomplete-description or evidence scoring can soften the decision. Where candidate or job country constraints exist but the job country is missing, or a stated work-authorization requirement cannot be compared because the job country is missing, the policy now returns REVIEW with no score rather than silently treating the unknown as a match. The matcher remains a transparent lexical signal rather than full semantic requirement matching; broader hard-constraint coverage, stale invalidation, and end-to-end requirement extraction/evaluation remain incomplete. |
| Punt qualification | Partial | Domain qualification rules, candidate-scoped create/list API, evaluation associations, persistence and outbox event exist. PostgreSQL concurrency coverage verifies distinct evaluations qualifying the same candidate/job converge on one Punt, preserve both evaluation links, and emit one PUNT_QUALIFIED outbox event. Broader transaction and rollback tests remain open. |
| Application lifecycle and immutable events | Partial | Candidate-scoped create/list/transition APIs, domain transition rules, revision checks, ApplicationEvent records and outbox writes exist. PostgreSQL tests verify invalid transitions leave state/events/outbox unchanged, stale revisions are rejected without duplicate events, SUBMITTED records carry a CANDIDATE actor, and concurrent competing transitions against one revision yield exactly one winner and one revision conflict. Broader transition, rollback and concurrency coverage remains open. |
| Versioned application packages and claim provenance | Partial | Versioned package/artifact persistence, candidate-scoped create/list/read APIs, immutable source snapshots and content hashes, candidate-owned evidence references, AI execution references, semantic claim-audit reports, and fail-closed readiness/approval gates are implemented. A package item may link only to a candidate-owned approved CV version; source CV ID/version/content hash/profile revision/profile hash/approval time are captured in package provenance, and readiness rechecks ownership, approval, and source integrity. Package revision creation checks the expected current version to reject stale writes. PostgreSQL regressions cover stale artifact/evidence/source/CV snapshots, approval-time rechecks, concurrent writes/version allocation, and deterministic evidence-snapshot ordering. The semantic audit remains AI-assisted, not formal proof. Remaining: full candidate edit/approval workflow, candidate submission integration, live storage checks, rendered-artifact E2E coverage, and broader end-to-end API acceptance. |
| Provider-neutral AI orchestration | Partial | The canonical service routes through provider-neutral Google/OpenAI adapters; provider/model selection is server-controlled. All twelve registered tasks have server-owned typed input schemas and output contracts. The immutable code registry defines task instructions and policy versions; each AIExecution persists the task-policy, system-policy, input/output-contract versions, selected provider/model, configuration version/fingerprint, full effective contract/config snapshot, input/output fingerprints where available, validation outcome, fallback activity, latency, timestamps, and safe failure category. Database regressions enforce immutability of execution contract provenance and recorded output fingerprints. Published per-task configuration is versioned, fingerprinted, audited, and rollback creates a new immutable version. Configured fallback is attempted at most once, only when an explicit alternate provider/model is published; tests cover fallback success, fallback failure, and rejecting fallback output that violates the same server-owned contract. Grounding checks cover document/JD source excerpts, requirement/evidence references, and application-package claim validation. Remaining: broader semantic entailment and cross-record checks, provider credentials/live calls, and deployed worker recovery; passing tests do not prove live runtime behavior. |
| Work items and transactional outbox | Partial | Work-item/outbox models, append service, and a recoverable dispatcher with leases/retries/dead-letter handling exist. Production handlers cover raw-record canonicalization, Punt/application notifications, evaluation-attention and discovery-completion notifications, optional email delivery, and support-ticket lifecycle notifications. Support-ticket creation/status changes persist their outbox signals in the same transaction as ticket/message/audit state. Tests cover idempotent event replay, bounded retries, email failure handling, and partial support-notification fanout recovery. Worker deployment and other production event handlers remain open. |
| Server-side scheduling and notifications | Partial | Candidate-owned in-app notifications persist event type, message, resource reference, timestamp and read state. Punt, application-status, evaluation-attention, discovery-completion, and support-ticket create/resolve/close events are generated from persisted state through the outbox. Optional SMTP delivery honors per-user opt-in and verified-email requirements; delivery attempt/status/error categories persist, and bounded outbox retries are used. Support APIs now include an authorized admin inbox, candidate context, status/unread/date/assignee filters, assignment/reassignment, linked follow-up tickets, audit records, and a published API contract. PostgreSQL integration tests exercise candidate API ownership, ticket idempotency, admin assignment, lifecycle outbox events, notification fanout, and email work. Live SMTP delivery, frontend integration, and deployed scheduler/worker recovery remain unverified. |
| Google/external integrations | Not implemented | No consent/scope lifecycle, connection/revocation, adapter execution or observed-health implementation found. |
| Protected Admin Control Plane | Partial | Protected admin endpoints enforce active ADMIN grants for telemetry/audit/config reads, config draft/publish/rollback-activation operations, and audited admin-grant list/create/reactivate/revoke actions. PostgreSQL tests cover grant creation, duplicate grant rejection, inactive-target rejection, revocation, immediate loss of authorization, reactivation, audit attribution, self-elevation denial, prevention of revoking the final active admin, and concurrent revocations that leave one active administrator while rechecking the actor grant under lock. Initial first-admin bootstrap remains operator-managed. The [bootstrap/recovery runbook](ADMIN_GRANT_BOOTSTRAP_RUNBOOK.md) now documents identity preflight, transaction steps, external change record, post-change verification and recovery. It has not yet been exercised in staging, and the out-of-band bootstrap cannot appear as a truthful actor-attributed application audit event under the current non-null `actor_user_id` schema. |
| Durable audit/activity | Partial | AdminAuditEvent records governance draft, publish, and rollback-activation actions, and a protected audit-read endpoint exists. Broader domain activity coverage, tamper resistance/retention policy, and production operational review remain open. |
| Object storage abstraction | Partial | A provider-neutral object-storage port and factory exist; Cloudflare R2 is the default private object store, GCS is explicit migration-only configuration, signed URLs are short-lived, and object writes validate key/checksum/size. Optional personal Google Drive backup is disabled by default; a retention worker deletes only eligible stale unsaved imports and retains database metadata. A candidate-owned restore endpoint now requires a recorded backup and verifies Drive file identity, application metadata, SHA-256, and byte size before restoring the original private object key; repeated restore is idempotent when the stored object already matches. Live R2 credentials, actual Drive OAuth, scheduler execution, and production restore/recovery remain unverified. |
| PostgreSQL schema integrity | Verified for current migrations | CI asserts one Alembic head, applies migrations to a fresh PostgreSQL 16 database, and runs `alembic check`. This does not verify all application data paths. |
| Candidate-neutral behavior | Partial | Deterministic evaluation policy has regression coverage for profession-specific role/evidence inputs and empty profiles without inventing fit. The complete candidate journey across diverse occupations, profile states and real source data is not yet demonstrated end to end. |
| Real-source truthfulness | Partial | A Greenhouse public-board connector and source-state/health-check paths exist with mocked integration coverage; live upstream smoke testing, broader source types, source policy review, and scheduled execution remain unverified. |
| Deployment outside AI Studio | Architecture direction only | No deployment, secret handling, worker operation, backup/restore or portability proof found. |

## Priority implementation order

### P0: integrity and security

1. **Migration integrity is addressed for the current schema.** Keep the PostgreSQL migration and ORM-parity checks required on every schema change.
2. **Partially addressed.** Server-side ownership is enforced for implemented candidate APIs, with cross-candidate tests for evidence, evaluation, Punt and application resources. Firebase UID-to-account mapping and concurrent first sign-in have PostgreSQL coverage using injected verified claims; actual Firebase Admin token verification with production credentials and broader private-resource coverage remain open. Request-body user IDs must never establish ownership.
3. **Partially addressed.** Selected PostgreSQL tests now cover canonicalization/Punt convergence, identity creation races, acquisition rollback, invalid lifecycle transitions and concurrent application revision conflicts. Repository-wide transaction boundaries, rollback, uniqueness and idempotency coverage remain open.
4. **Implemented for the tested acquisition paths.** PostgreSQL tests verify connector failure persists a sanitized FAILED run and a mid-batch persistence failure rolls back raw records and outbox events while leaving the run FAILED. Real connectors, partial-source failures and worker recovery remain open.
5. **Partially addressed.** Deterministic hard-blocker enforcement is covered by PostgreSQL regression tests. Candidate claim APIs now require current approved evidence that matches each stated scalar claim fact before approval and reject cross-candidate evidence links. Admin grant lifecycle is now audited and self-elevation is blocked; initial first-admin bootstrap and its recovery procedure remain operator-managed and unverified. Broader hard-constraint coverage, semantic entailment beyond exact fact matching, and AI-generated claim validation remain open.
6. **Partially addressed for the implemented application lifecycle.** PostgreSQL tests verify invalid transitions do not mutate state or append events, stale revisions do not create duplicate events, concurrent transitions yield one winner, and SUBMITTED events carry a CANDIDATE actor. Broader event sources and verified integrations remain open.

### P1: complete the core workflow

1. A Greenhouse public-board connector and protected synchronous acquisition endpoint now connect a configured board to the durable run/raw-record/outbox pipeline. Protected source registry create/list/status endpoints exist with audit and token non-disclosure tests. Connector editing, live-board smoke testing and source-specific policy review remain open.
2. Normalize/split → retrieve/validate JD → completeness/freshness → resolve identity → canonical Job/version → provenance.
3. Connect the raw-record outbox event to a durable, idempotent worker with bounded retries and recovery.
4. Candidate profile/preferences/approved pathway/evidence → requirement matching → deterministic blockers → versioned explainable evaluation.
5. Evaluation → Punt qualification → candidate choice → application/package → candidate-recorded submission → auditable application lifecycle.
6. Provider-neutral AI task/version/configuration/execution records and evidence-grounded output validation.

### P2: remaining MVP capabilities and hardening

1. Document import/review and private artifact storage.
2. CV Studio, package editing/versioning and DOCX/PDF output.
3. Integration consent, scopes, connection health and revocation.
4. Protected admin configuration lifecycle, rollback and audit.
5. Notifications, operational logs/metrics, security regression tests and deployment/recovery runbooks.

## Verification gates

- [x] Exactly one Alembic head; clean upgrade from an empty PostgreSQL database.
- [x] ORM/database schema parity check passes.
- [ ] Unit tests cover all domain rules and critical state machines. Candidate evaluation unit tests now cover excluded industry/job type, candidate eligible-country mismatch, job country restrictions, remote-only mismatch, and missing-country uncertainty when eligibility constraints apply. Salary/relocation/fee semantics and broader eligibility rules still need an explicit PRD-to-policy mapping before implementation.
- [x] Selected PostgreSQL integration tests cover opportunity ownership, repeated-application paths, canonicalization provenance/versioning/source-ID matching and concurrent convergence, concurrent first-time Punt qualification, sanitized acquisition connector failure and partial-persistence rollback, candidate evidence/source/profile atomic rollback, candidate claim/link atomic rollback, deterministic hard-blocker enforcement, invalid application transition rollback, stale-revision rejection, candidate-recorded submission audit, and concurrent competing application transitions; repository-wide transaction, rollback and broader concurrency coverage remains open.
- [ ] API/adapter contract tests cover enabled identity, acquisition, AI, storage and integrations; Firebase identity mapping and concurrency have PostgreSQL coverage using injected verified claims, but actual Firebase Admin token verification and deployment credentials remain unverified. AI unit tests verify JSON mode parses valid JSON and rejects malformed JSON without a schema description, published task overrides govern adapter calls, execution records retain config-version references across configuration changes between executions, invalid config is rejected before provider invocation, candidate requests cannot select provider/model, provider/config/invalid-JSON failures persist stable error categories, blank prompts/schema descriptions are rejected, and actual-body size enforcement rejects oversized requests without Content-Length; server-owned output schemas and runtime validation cover all eleven registered AI tasks, including claim-to-evidence validation. Application Q&A contracts include confidence and unsupported-claim warnings. Versioned package persistence, candidate-scoped package APIs, semantic claim-audit reports, evidence/artifact snapshot checks, and review gates are implemented on the application-package branch and pass CI. The semantic audit is AI-assisted, not a formal proof of entailment; candidate review remains mandatory for incomplete or non-supported claims. Admin configuration lifecycle PostgreSQL coverage is present and passes in CI.
- [x] Cross-candidate evaluation/Punt access and application-transition IDOR are covered for opportunity resources; unauthorised admin reads/writes, nested secret-like config fields, and core config lifecycle behavior have regression coverage. Broader state-transition abuse remains open.
- [ ] End-to-end tests cover acquisition through canonical Job, evaluation, Punt, package and candidate-recorded application events.
- [x] Selected outbox tests cover bounded retry scheduling, terminal failure in the work-item dispatcher, safe error-category persistence, expired-lease recovery, concurrent PostgreSQL claiming, replay after simulated acknowledgement failure using an idempotent test handler, duplicate canonicalization delivery, SMTP failure persistence/retry, and partial support-notification fanout recovery without duplicate logical notifications; [ ] partial source failure and AI failure/fallback remain open.
- [ ] Demo/test data cannot be mistaken for production records.
- [ ] Deployment outside AI Studio and database backup/restore/recovery are demonstrated.
- [ ] The PRD's original INV-001–INV-016 wording, INV-017–INV-030 and individual gap-register entries are reconciled to executable tests or explicit accepted exceptions.

## Audit rules

- The PRD v1.4 and its existing Current-State Audit & Gap Closure Control Register remain authoritative.
- Do not mark a capability complete because a model, screen, route or stub exists.
- Every schema change needs a migration, ORM update, repository review, tests and invariant verification.
- Assess source access, terms, authentication, rate limits and retention before implementing each production connector.
- Do not make paid services or separate infrastructure architectural prerequisites without demonstrated need.
- Keep unresolved hosting, identity, object storage, AI provider and initial source choices open until decided.


## Security and deployment review checkpoint (2026-10-11)

This checkpoint is a code/configuration review, not a production certification.

| Control | Current evidence | Status / required follow-up |
|---|---|---|
| Candidate-scoped document import | Import routes require an authenticated principal and pass its user ID into the service; service queries constrain import and document to the candidate profile. PostgreSQL integration tests now cover upload, parse, extraction failure/retry, confirmation/save, storage-object recovery, metadata cleanup, and save rollback/retry. | Partial: live authentication-provider verification, live object-storage credentials, OCR for scanned PDFs, and frontend acceptance remain unverified. |
| Upload bounds and content validation | Request reads are bounded to 4 MiB + 1 byte; service checks extension, MIME, signatures/ZIP structure, PDF page count, and DOCX expansion. | Partial: production ingress/body limits and malicious-file scanning are not demonstrated. |
| Object-storage integrity and privacy | R2 adapter checks configured credentials and verifies key/checksum/size on upload; objects are retrieved through the server-side adapter. | Not verified: live R2 bucket privacy, credentials, permissions, lifecycle policy, and recovery must be tested in deployment. |
| Optional Drive backup | Backup is optional and disabled by default; backup metadata is recorded with the document. | Not verified: real OAuth consent, minimum scopes, failure behavior, deletion semantics, and backup restore. |
| Request correlation | Middleware returns an `X-Request-ID`; supplied values are length-bounded and allow-listed, with invalid values replaced by a generated ID. Backend verification run #756 passed after the current security middleware and CORS changes. | Partial: production proxy/header behavior and request-ID propagation across deployed services remain unverified. |
| Deployment and worker operations | Repository review found no committed deployment manifest or verified staging run for R2, Drive backup, retention scheduling, or worker recovery. | Open: choose deployment target, define secret injection/rotation, health/readiness checks, migrations, worker/scheduler execution, monitoring, restore drills, and rollback procedure. |
| Security headers, CORS, and runtime configuration | `SecurityHeadersMiddleware` adds `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, and restrictive `Permissions-Policy`, including early body-limit rejections. CORS uses `JOBPUNTER_CORS_ALLOWED_ORIGINS`, rejects wildcard/non-origin values, disables credentialed requests, and has allow/deny preflight tests. Run #756 passed with 794 tests passed, 1 skipped, and 1 warning. Runtime configuration also unifies `JOBPUNTER_ENVIRONMENT` with the settings object, rejects development identity when the environment is not development, and bounds signed-URL TTL and retention settings. These checks passed on latest-head run #38146896292 at commit `27f0b2da5e981e2f10999b149638f0053086e684`. | Partial: configure and verify the real frontend origin in deployment. TLS, HSTS, ingress-level headers/body limits, and deployed proxy behavior remain unverified. Runtime configuration hardening passes the current-head CI checks; deployed ingress, TLS/HSTS, and proxy behavior remain unverified. |

### Acceptance boundary

Green CI proves only the automated paths it executes. It does not prove live cloud permissions, real OAuth consent, scheduled retention, ingress limits, TLS, monitoring, restore capability, or deployment rollback. These remain release gates, not assumptions.


## Urgent invariant triage disposition (2026-10-16)

This is a repository-code disposition against the current `mains-shadow` baseline and the Batch 1 follow-up checklist. It is not a production-readiness certification. Existing code is marked covered only where a relevant test path was found; the exact PR head must pass CI before this disposition is considered verified.

| Urgent item | Disposition | Repository evidence |
|---|---|---|
| Authentication fails closed; `X-User-Id` is development-only | Covered; no code change needed | `app/api/deps.py`; `tests/unit/test_auth_dependencies.py`; `tests/unit/test_auth_fail_closed_cases.py` cover rejected credentials, production/test environments, explicit development opt-in, and private candidate routes. |
| Outbox registry for Punt/application events | No unsupported outbox event currently emitted; notification bridge remains open | `app/workers/outbox.py` registers `RAW_SOURCE_RECORD_ACQUIRED` and `RAW_SOURCE_RECORD_OBSERVED`; acquisition repositories emit those event types. `APPLICATION_CREATED` and application transitions are persisted as `ApplicationEvent`; Punt qualification history is persisted separately. The production outbox does not currently emit `PUNT_QUALIFIED`, `APPLICATION_CREATED`, or `APPLICATION_STATUS_CHANGED`, so these are not currently dead-lettering unsupported outbox types. Generating candidate notifications from persisted domain events remains Batch 8 work and must not be reported as implemented. |
| Requirement extraction scheduling, persistence, provenance and retry safety | Defect fixed in PR #77; regression coverage added | `app/workers/job_requirement_extraction_worker.py` now reclaims `IN_PROGRESS` rows whose start timestamp is null, as well as stale claims. `tests/integration/test_acquisition_to_canonical_job_postgres.py` covers stale, missing-timestamp and fresh leases; extraction-worker integration tests cover persisted, source-grounded `JobRequirement` rows and retry behavior. |
| Verification ordering and invalidation after verification changes | Regression coverage added | `app/application/jobs/discovery_evaluation.py` screens unverified jobs to review; `app/application/opportunities/evaluation_policy.py` requires a current URL-matched application-route verification. `OpportunityService` includes route verification in the evaluation snapshot and marks changed inputs stale. `tests/unit/test_discovery_evaluation.py` covers unverified jobs; PR #77 adds a PostgreSQL regression for evaluation staleness when route verification changes. |
| Application-package review imports and readiness | Covered; no code change needed | `tests/unit/test_application_package_review_imports.py` explicitly imports review ORM dependencies. `tests/integration/test_application_package_readiness_regressions.py` exercises evidence/artifact tampering, candidate ownership, approval rechecks and concurrency locks. |
| Acquisition failure and partial-outcome truthfulness | Covered by existing tests; no duplicate implementation added | `tests/integration/test_acquisition_to_canonical_job_postgres.py` covers persisted acquisition outcomes, source state and failed-attempt timestamps; `tests/unit/test_acquisition_failure_simulations.py` verifies connector failures are not reported as empty success. |
| Unchanged reacquisition refreshes observation/freshness without duplicate material | Covered by existing tests; no duplicate implementation added | `tests/integration/test_acquisition_to_canonical_job_postgres.py::test_unchanged_and_changed_observations_refresh_canonical_job_freshness` and `tests/integration/test_canonicalization_postgres.py::test_postgres_canonicalization_records_each_observation_without_duplicate_description_version`. |
| Race-safe canonical creation, URL normalization and collision review | Covered by existing tests; no duplicate implementation added | `tests/integration/test_canonicalization_postgres.py` covers concurrent convergence, conflicting-title URL collisions, source-ID convergence and duplicate delivery; `tests/integration/test_acquisition_to_canonical_job_postgres.py` covers tracking-parameter URL convergence. |
| Content-based completeness gates in discovery, evaluation and Punt | Code paths present; keep adversarial acceptance in the regression suite | Discovery uses `screen_job_for_discovery` and `assess_job_completeness`; evaluation policy rejects incomplete descriptions from qualification; `OpportunityService.create_punt` independently requires a complete, substantive latest description. Relevant discovery/policy tests cover incomplete and missing descriptions; PR #77 adds a PostgreSQL test proving the Punt gate rejects an incomplete latest description and creates no Punt. |
| `OPEN` versus `ACTIVE` lifecycle compatibility and matcher model fields | Lifecycle defect fixed in PR #77; matcher compatibility already covered | `OpportunityService.create_punt` now accepts both `OPEN` and `ACTIVE`, matching evaluation and application-approval policy. The PostgreSQL qualification test exercises both values. `tests/unit/test_requirement_matcher_job_contract.py::test_matcher_payload_does_not_read_nonexistent_job_description_attribute` covers the reported matcher model-attribute mismatch. |

### Batch 1 boundary

PR #77 contains the two confirmed code fixes and the verification-invalidation regression. The other listed items have explicit code/test dispositions rather than duplicated implementations. The persisted-event-to-notification flow was open at this historical triage checkpoint. It was subsequently implemented and tested in PR #77 Batches 8–11; see the completion ledger below. This does not establish live email delivery or deployed worker/runtime verification.


## Discovery and source-governance triage (2026-10-16)

This follow-up checks Batch 2 against the actual discovery, ingestion, source-governance and worker paths. It records repository evidence only; it does not certify live source availability or deployed scheduling.

| Area | Disposition | Repository evidence |
|---|---|---|
| Permission review before automated acquisition | Fixed in the current implementation branch | `RegisteredAcquisitionService.run` already blocks sources without an approved permission review. The discovery worker previously converted that policy block into a generic `FAILED` source run. It now persists a `RESTRICTED` source-run outcome and `PERMISSION_REVIEW_REQUIRED` source state/error without invoking the connector. `tests/integration/test_discovery_missions_postgres.py::test_discovery_permission_review_block_is_recorded_as_restricted_not_connector_failure` covers the outcome and absence of an acquisition run. |
| Candidate settings must not override connector configuration | Fixed in the current implementation branch | `validate_discovery_settings` no longer allows `acquisition_context` in candidate source filters. `policy_snapshot` removes that legacy field when snapshotting old settings, while the worker ignores it even if present in an already-persisted mission snapshot. `tests/unit/test_discovery_missions.py` covers rejection and legacy snapshot sanitization; `tests/integration/test_discovery_missions_postgres.py::test_legacy_candidate_context_cannot_override_connector_configuration` verifies the worker passes no candidate-supplied context to acquisition. Administrator-published source/connector configuration remains the authority. |
| Truthful source operational states and durable run history | Covered in code/tests | `app/application/jobs/source_governance.py` defines `AVAILABLE`, `DEGRADED`, `RESTRICTED`, `FAILED`, and `UNAVAILABLE`; acquisition and discovery persist terminal/partial outcomes. `tests/integration/test_discovery_missions_postgres.py` covers all-source failure, partial success, restricted/unavailable outcomes, and source-run history. |
| Manual URL, pasted JD and email intake through canonicalization | Covered in code/tests | `app/api/v1/manual_ingestion.py` routes `MANUAL_URL`, `MANUAL_JD`, and `MANUAL_EMAIL` through acquisition persistence and canonicalization with an idempotency key. The manual-email path expands complete jobs while retaining ambiguous chunks for review. Existing manual-ingestion and canonicalization tests remain the acceptance evidence. |
| Provenance, deduplication, observation freshness and material versions | Covered in code/tests | `RawSourceRecord` preserves external record identity and raw source content; `JobDiscoveryEvent` records observations; canonicalization maintains job-description versions. PostgreSQL acquisition/canonicalization tests cover idempotent replay, URL normalization, unchanged observations and concurrent convergence. |
| Candidate ownership and source-registry authorization | Covered in code/tests | `app/api/v1/discovery.py` derives the candidate profile from the authenticated principal for settings and missions. Admin source-registry routes enforce admin authorization; `tests/integration/test_source_registry_admin_postgres.py` covers non-admin denial and safe secret redaction. |
| Durable schedule, concurrency and worker recovery | Daily/weekly calendar scheduling added; deployment still unverified | Migration `0051_discovery_schedule_contract` adds a schedule type, local time, IANA timezone and weekly weekday while preserving legacy `INTERVAL` schedules. The API and `schedule_due_missions` calculate timezone-aware next occurrences and skip missed calendar slots rather than launching catch-up bursts. Unit tests cover daily/weekly time calculations and validation; PostgreSQL tests cover weekly advancement after downtime and concurrent scheduler workers. The scheduler process is implemented separately in `app/workers/discovery_scheduler.py`; actual deployment/continuous operation remains a release/runtime gate. |

### Batch 2 boundary

The confirmed permission-outcome and candidate-context override defects are fixed in the implementation branch with regression coverage. The other listed behaviors have existing route/service/persistence tests and were not rebuilt. No claim is made that third-party sources are live, that production schedules are running, or that deployment/recovery has been verified in the target environment.

## Cumulative follow-up: PR #77, Batch 3 and Batch 4 (2026-10-16)

This note supplements the older baseline above. It does not replace the invariant register or certify production readiness.

### Batch 3 — candidate profile and deterministic eligibility

- The evaluation service passes Master Career Profile data to the deterministic policy separately from candidate preferences.
- Preferences are not treated as proof of factual work authorization. Recorded factual country eligibility can produce a deterministic hard blocker; missing authorization remains a review gap.
- Regression coverage was added for preference/fact separation, factual authorization, and country eligibility.
- The implementation was committed on the existing `batch-1-invariant-triage` branch. Exact-head CI passed on commit `1a4b8a83f32cd26825c9088e0bf17d4dca801e74` (GitHub Actions run #1609 and CircleCI backend verification).

### Batch 4 — PRD-aligned evaluation and Punt

- The evaluation policy supports the PRD match-state vocabulary and produces explicit `APPLY`, `REVIEW`, `WATCH`, or `REJECT` outcomes with evidence summaries and explanations.
- Evaluation snapshots retain candidate/profile and preference revisions, approved evidence/claims, job and job-description versions, reviewed requirements, decision-rule configuration, and AI execution trace metadata.
- Punt qualification remains a separate gate requiring a current evaluation, `APPLY`, no hard blockers, a score, supported mandatory requirements, deterministic eligibility, verified application route, and current job freshness. The API also rechecks lifecycle, freshness, route verification, and substantive description completeness.
- A correctness gap was found: the service could invoke semantic matching when the deterministic policy had already returned a hard rejection or `WATCH` for a closed/stale or otherwise non-evaluable job. Semantic matching is now gated by the policy's explicit semantic-unavailable result, which is reached only after its deterministic eligibility and readiness checks.
- Evaluation freshness now also compares evidence-matching task-policy version, system-policy version, and the currently published task-configuration version, in addition to the output-contract version.
- Regression tests cover deterministic hard-blocker short-circuiting, closed/stale jobs short-circuiting, contradictory requirement evidence remaining `REVIEW`, and invalidation after a published AI task configuration changes.
- **Acceptance status:** implementation and regression tests are on the current branch; the latest-head CI run is required before Batch 4 can be marked verified. No merge or deployment has been performed.

### Verification update — Batch 4 accepted (2026-10-16)

The pending acceptance note above was resolved. GitHub Actions run #1620 and CircleCI backend verification both passed on exact commit `90b6cd57e4cd1714b221ab266f0de81ab960b360`. GitHub Actions completed compilation, single-Alembic-head validation, legacy migration-stamp compatibility, ORM/schema parity, and the full unit/PostgreSQL integration test step: **1,300 passed, 1 skipped, 1 warning**. Batch 4 is CI-verified on that commit. This verifies the tested backend paths only; it does not certify production deployment or live third-party integrations.

### Batch 5 — application lifecycle and append-only audit (2026-10-16)

- **Canonical lifecycle contract:** the API `ApplicationStatus` literal, domain `APPLICATION_TRANSITIONS` map, ORM status constraint, and migration `0034_application_lifecycle_audit` agree on `PLANNED`, `APPROVED`, `SUBMITTED`, `INTERVIEW`, `OFFER`, `ACCEPTED`, `REJECTED`, `WITHDRAWN`, and `CLOSED`. `APPLIED` is not a valid state.
- **Candidate-controlled milestones:** transitions require the authenticated user actor and a reason. Transition notes and rationale persist in `ApplicationEvent`; revision checks prevent stale writes. The state-machine gate does not allow system-originated transitions to fabricate submission, interview, offer, or other milestones.
- **Approval gate:** application approval rechecks job lifecycle/freshness, substantive current job-description completeness, active route verification, the latest approved package, and package readiness/source snapshot integrity. Submission requires the application to be approved and the latest package to remain approved and ready.
- **Audit integrity:** migration `0034_application_lifecycle_audit` creates the unified `audit_events` projection, mirrors application and admin audit records, and installs database triggers that reject UPDATE, DELETE, and TRUNCATE on unified audit history. PostgreSQL integration tests verify projection fields and immutability.
- **Batch 1 disposition retained:** `APPLICATION_CREATED` and `APPLICATION_STATUS_CHANGED` are persisted as application domain events and projected to audit history; they are not emitted as unsupported outbox types. Candidate notification generation remains Batch 8 work.
- **Additional regression coverage:** `tests/unit/test_application_lifecycle_contract.py` now checks API/domain status vocabulary parity, every declared transition, every undeclared transition including self-transitions, no mutation on rejected transitions, and rejection of system-originated real-world milestones.
- **Acceptance:** GitHub Actions run #1623 passed on exact code/test commit `fbb01eca0d0a05433a499ce94de1c12fb852c04d`: compilation, single Alembic head, migration compatibility, ORM/schema parity, and the full PostgreSQL 17 test step passed (**1,383 passed, 1 skipped, 1 warning**). Batch 5 lifecycle and audit behavior is CI-verified on this commit. Live deployment remains outside this evidence.

### Batch 6 — AI task registry and governed execution (2026-10-16)

- **Registry and contracts:** the immutable server-owned task registry covers all twelve registered task IDs. Each task has a typed input contract and an output contract; contract tests reject unknown fields, invalid inputs, malformed output, and task-specific provenance violations.
- **Server authority:** candidate requests cannot select the provider or model. Runtime selection uses the canonical service and the currently published per-task configuration; generic `system.*` drafts are rejected.
- **Execution trace:** each `AIExecution` records task-policy/system-policy and input/output-contract versions, selected provider/model, published config version where configured, config fingerprint, effective contract/config snapshot, validation result, fallback state, timing, output fingerprint where serializable, and a safe failure category. Database triggers protect contract provenance and recorded output fingerprints from mutation.
- **Fallback and failure:** an explicitly published alternate provider/model can be tried once after a supported primary provider/JSON failure. Tests cover successful fallback, failed fallback, and rejection of fallback output that fails the same server-owned output contract. Invalid output is never marked `SUCCEEDED`.
- **Evidence grounding:** document and JD extraction check source excerpts against supplied source text; evidence matching checks requirement coverage and supplied evidence references; claim validation checks supplied artifact types and approved evidence IDs. These are deterministic boundary checks plus AI-assisted semantic review, not formal proof of entailment.
- **Audit correction:** the matrix now records typed inputs for all twelve tasks and controlled fallback as implemented/tested rather than leaving the older five-task/“fallback open” statement. It also clarifies that Punt/application domain events are persisted and audited but are not unsupported outbox event types; notification generation remains Batch 8.
- **Acceptance:** existing tests were rechecked on GitHub Actions run #1623 at code/test commit `fbb01eca0d0a05433a499ce94de1c12fb852c04d` (**1,383 passed, 1 skipped, 1 warning**), including task registry, typed inputs, output enforcement, fallback success/failure, immutable execution snapshots, and PostgreSQL governance versioning. Run #1624 passed on the subsequent audit-documentation head `3e17a1a64e433b497ebc8d213e7005de129cfe63`. No additional code change was justified by this audit. Live provider credentials and deployed worker recovery remain unverified.


### Batch 7 — CV Studio, package provenance/revisions, conflict decisions, and document restore (2026-10-16)

- **Requirements basis:** PRD + Technical Spec v1.5, including the Master CV/variant acceptance criteria, Evidence Library conflict review, application-package source provenance/versioning, private document handling, and candidate isolation/data-integrity invariants.
- **CV Studio:** Candidate-scoped append-only CV versions preserve content hashes, profile snapshots/hashes, source profile revision, and source Master CV version. Profile-revision and latest-version checks reject stale writes. Approval is recorded separately so historical versions remain immutable. Master CV regeneration, variant creation from an approved Master CV, version history/save, approval, and private DOCX/PDF generation/download paths are implemented and regression-tested.
- **Package linkage:** An application package can reference only a CV version owned by the candidate and explicitly approved. The immutable source snapshot captures CV version/content/profile/approval provenance; readiness rechecks the linkage. Package revision creation rejects stale expected-version values.
- **Conflict decisions:** Import confirmation requires an explicit decision for every identified conflict (`KEEP_IMPORTED`, `KEEP_EXISTING`, or `KEEP_BOTH`); missing, duplicate, or unknown decisions are rejected. Decisions are persisted without silently changing the Master Profile.
- **Document restore:** Candidate-owned restore requires a recorded backup. The adapter verifies Drive file identity and application metadata, and both adapter and service validate checksum and size before restoring the private object. Existing valid objects return idempotently; corrupted objects are replaced only after backup bytes verify.
- **Acceptance evidence:** GitHub Actions [run #1636](https://github.com/GoReallyBig-GRB/jobpunter-backend/actions/runs/38974326568) passed on exact implementation head `77b31bdd0cd0c8526da024f8a80fe8503d315228`: **1,401 passed, 1 skipped, 1 warning**. Compilation, single Alembic head, legacy migration-stamp compatibility, ORM/schema parity, and PostgreSQL integration tests passed.
- **Limitations:** Local tests were not run because direct Git clone failed due unavailable network DNS. Live R2/Drive credentials and OAuth, frontend integration, scanned-PDF OCR, deployed worker/recovery behavior, and complete end-to-end candidate workflows remain unverified. Batch 7 is CI-verified for tested paths, not a declaration of full PRD or production readiness.


### Batch 8 — Candidate in-app notifications from persisted Punt/application events (2026-10-17)
- Added migration `0055_candidate_notifications` and a candidate-owned `Notification` model with a unique source-event key, resource reference, timestamp, and read state.
- A newly created Punt emits `PUNT_QUALIFIED` in the same transaction as the Punt and qualification history. Existing Punt retries do not emit a second creation signal.
- Successful application status transitions persist an `APPLICATION_STATUS_CHANGED` outbox event in the same transaction as the `ApplicationEvent`. The outbox aggregate is the owning `Application`; the payload points to the persisted event.
- Outbox handlers derive recipient and message details from persisted Punt/application/job records, not trusted payload fields. Unique event keys make replay idempotent.
- Added authenticated `GET /api/v1/candidate/notifications` with pagination and unread filtering, plus `POST /api/v1/candidate/notifications/{id}/read`. Cross-user access returns 404; repeated read requests preserve the original read timestamp.
- Regression coverage includes handler replay/idempotency, source-event provenance, candidate isolation, read/unread state, transactional event emission, concurrent Punt creation, and no duplicate signals after rejected or stale application transitions.
- **CI-PASS:** GitHub Actions [run #1642](https://github.com/GoReallyBig-GRB/jobpunter-backend/actions/runs/38980778358) passed on exact implementation head `4f38bfe842eff91715d46e56e8149d5709884a46`: **1,404 passed, 1 skipped, 1 warning**.
- **Historical status at the end of Batch 8:** evaluation-attention and discovery-completion notifications, optional email delivery tracking, and support-ticket notification triggers had not yet been implemented. These backend items were subsequently added and CI-verified in Batches 9–11 below. Live SMTP delivery, deployed scheduler/worker recovery, live integration credentials, and frontend integration remain unverified; therefore this historical Batch 8 section must not be read as the current status of those later-completed backend tasks.


### Batch 9 — Optional email delivery and failure tracking (2026-10-17)

- **Requirements basis:** PRD + Technical Spec v1.5, Lightweight Notification Specification: optional configurable email, durable delivery outcomes, failure isolation from in-app notifications, and reuse of the existing outbox worker.
- Added optional SMTP settings (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM_EMAIL`, `SMTP_USE_TLS`, `SMTP_TIMEOUT_SECONDS`); SMTP is not required for in-app notifications.
- Added candidate-owned email preference read/update endpoints. Email opt-in requires a verified email address; preferences default to disabled.
- Notification creation now persists in-app notification state and email-delivery state atomically. Opted-in notifications with a verified address enqueue a durable `NOTIFICATION_EMAIL_REQUESTED` outbox event.
- The existing outbox worker delivers email through SMTP. Attempt counts and sanitized failure categories are persisted; failures raise back into the outbox's bounded retry policy. Unconfigured providers are recorded as skipped rather than blocking in-app notification creation.
- Regression coverage includes successful delivery and replay after success, SMTP failure persistence and retry-visible state, and unconfigured-provider behavior.
- **CI-PASS:** GitHub Actions [run #1645](https://github.com/GoReallyBig-GRB/jobpunter-backend/actions/runs/38982794223) passed on exact implementation commit `df84daa8ddbd98bdf23c3104de0eddf002283f00`: **1,409 passed, 1 skipped, 1 warning**. Compilation, Alembic single-head validation, migration upgrade/legacy stamp compatibility, and ORM/schema parity passed. CircleCI also reported success for this commit.
- **Limitations:** No live SMTP provider or credentials were exercised. SMTP has an unavoidable uncertain-outcome window if a provider accepts a message but the worker loses the response before persisting `SENT`; the stable Message-ID is not proof of provider-side deduplication. Support-ticket notification triggers were subsequently implemented and tested in Batches 10–11. Live provider delivery, deployed worker recovery, and frontend integration remain unverified. This batch is not a production-readiness certification.


### Batch 10 — Support tickets, lifecycle notifications, and retry/idempotency (2026-10-17)

- **Requirements basis:** PRD + Technical Spec v1.5, Scope K — Customer Support tickets, messaging, and notifications; Lightweight Notification Specification; transactional outbox and bounded retry requirements.
- Added migration `0057_support_tickets`, `SupportTicket` and `SupportMessage` models, ticket/message timestamps, unread flags, status timestamps, candidate ownership, optional admin assignment reference, linked follow-up reference, and request idempotency constraints.
- Added candidate ticket create/list/detail/message/status endpoints and admin inbox/detail/reply/status endpoints. The admin inbox supports status/unread filters and searches ticket title, issue details, and candidate name/email. Admin endpoints use the existing trusted `AdminGrant` authorization check.
- Ticket creation persists the ticket, initial message, audit event, and `SUPPORT_TICKET_CREATED` outbox event in one transaction. Resolution/closure state changes persist audit and outbox events atomically. The worker creates in-app notifications for the candidate and active administrators; notification email work is queued only for recipients who opted in and have verified email addresses.
- Same-key ticket/message retries return the original record when the payload matches and reject key reuse with a different payload. A resolved/closed ticket creates a linked follow-up instead of being reopened. Closed tickets are terminal. Notification fanout uses stable per-user event keys so a partial failure and retry does not duplicate notifications.
- PostgreSQL integration tests cover creation/message idempotency, linked follow-up creation, lifecycle notification deduplication, partial fanout retry, and optional email outbox work. Admin route authorization regression coverage includes the support inbox.
- **CI-PASS for implementation commit:** GitHub Actions [run #1649](https://github.com/GoReallyBig-GRB/jobpunter-backend/actions/runs/38986276726) passed on exact commit `e117b73aebab4edba6a6325fb4cc8a257494bcce`: **1,414 passed, 1 skipped, 1 warning**. Compilation, single Alembic head, migration upgrade/legacy-stamp compatibility, ORM/schema parity, and PostgreSQL integration tests passed. CircleCI also reports success for that exact commit.
- **Limitations:** Live SMTP delivery was not exercised. Assignment is stored as a field but an assignment/reassignment endpoint is not included in this batch. Frontend message-state behavior, deployed worker recovery, and production runtime remain unverified. This is not a production-readiness certification.


### Batch 11 — Support inbox controls, assignment, and end-to-end API verification (2026-10-17)

- Added administrator ticket assignment/reassignment and explicit unassignment. Targets must be active users with an active `ADMIN` grant; assignment changes are persisted and audited.
- Extended the administrator inbox with creation-time bounds and assignee filtering, candidate name/email search, and candidate context in list results. Invalid reversed date bounds return 422.
- Published `docs/support-api.md` with candidate/admin routes, authentication and ownership rules, idempotency behavior, status lifecycle, filters, and notification delivery semantics.
- Added PostgreSQL-backed API integration coverage for ticket creation and same-key replay, cross-candidate access denial, admin inbox candidate search, assignment, assignee/status filtering, resolution, audit persistence, and dispatch of persisted lifecycle outbox events into idempotent candidate/admin notifications.
- Added authorization regression coverage ensuring non-admin users cannot use the assignment mutation endpoint.
- **CI-PASS:** GitHub Actions [run #1656](https://github.com/GoReallyBig-GRB/jobpunter-backend/actions/runs/38987093450) passed on exact implementation commit `6faadc5e48b9dce1fe060704183b46e2490c3a11`: **1,416 passed, 1 skipped, 1 warning**. Compilation, single Alembic head, migration upgrade/legacy-stamp compatibility, ORM/schema parity, and PostgreSQL integration tests passed. CircleCI also reports success for that exact commit.
- **Limitations:** Live SMTP delivery, frontend integration and message-state presentation, deployed worker recovery, and production runtime remain unverified. This is not a production-readiness certification.


### Batch 12 — Document import failure states and safe recovery (2026-10-17)

- Storage failures now have distinct, stable categories: a missing private object is `DOCUMENT_OBJECT_MISSING`; transient storage read failures are `DOCUMENT_STORAGE_READ_FAILED`; parser/integrity errors retain their own categories. A failed parse can be retried after the storage object is restored.
- AI extraction failures now persist the provider's validated error code (or a sanitized fallback) while keeping the import at its truthful `PARSED` state. The error category is visible in history/detail responses, revision tracking records the failure, and retry success clears the error.
- Invalid structured output and missing durable AI execution references are recorded as extraction failures rather than leaving an apparently clean import.
- Added regression coverage for storage write failure leaving no metadata, metadata commit failure cleaning up the uploaded object and rolling back rows, missing-object classification and recovery, AI-provider failure/retry, and save commit failure rolling back partial evidence/provenance before a successful retry.
- **CI-PASS:** GitHub Actions [run #1660](https://github.com/GoReallyBig-GRB/jobpunter-backend/actions/runs/38988039266) passed on exact implementation commit `0e6ee13c79a31cc52260de3fdde73fc080757496`: **1,421 passed, 1 skipped, 1 warning**. Compilation, single Alembic head, migration upgrade/legacy-stamp compatibility, ORM/schema parity, and PostgreSQL integration tests passed. CircleCI also reports success for that exact commit.
- **Iteration evidence:** The first test run identified an outdated assertion that classified a storage-read exception as a parser failure. The implementation now reports the storage-read category, and the assertion was corrected; the subsequent exact-head CI run passed.
- **Limitations:** Live OCR credentials/provider behavior, live R2/Drive credentials and provider-level failure behavior, deployed worker recovery, frontend integration, and production runtime remain open. This is not a production-readiness certification.


### Batch 13 — Opt-in scanned-PDF OCR integration (2026-10-17)

- Added an optional Google Cloud Vision OCR adapter for scanned PDF pages, rendering pages to bounded PNG images and batching requests within configured limits.
- OCR is disabled by default. Operators must explicitly set `DOCUMENT_OCR_ENABLED=true` and provide valid Google Cloud credentials before scanned-page OCR is used. Timeout and batch-size settings are bounded in configuration.
- PDF text extraction now preserves selectable text and sends only pages with no selectable text to OCR. It distinguishes disabled OCR, unavailable credentials/provider, provider failure, and pages with no recognized text; upstream provider details are not exposed in candidate-facing errors.
- Added tests for disabled-by-default behavior, mixed selectable/scanned PDFs, provider unavailability sanitization, successful page-indexed OCR and sanitized provider errors.
- **CI-PASS:** GitHub Actions [run #1662](https://github.com/GoReallyBig-GRB/jobpunter-backend/actions/runs/38988619432) passed on exact OCR implementation commit `2095f81756e3f1d2d1b625ec05ae1d3243161424`: **1,423 passed, 1 skipped, 1 warning**. CircleCI backend verification also reports success on the exact implementation commit.
- **Limitations:** Live Google Cloud credentials and real provider behavior have not been exercised. This does not establish production readiness. Live R2/Drive provider behavior, deployed worker recovery, frontend integration, and end-to-end candidate acceptance remain open.


## PR #77 committed-task completion ledger

**Scope rule:** This ledger evaluates the backend tasks actually implemented and committed on PR #77. A row is marked **COMPLETE (CI-PASS)** when its scoped behavior has implementation and relevant regression coverage passing in the cited CI run. The PRD matrix above remains **Partial** where the broader capability still lacks separate requirements, live integration evidence, frontend behavior, deployment, or end-to-end acceptance. Those broader gaps must not be used to downgrade a completed, narrower backend task; conversely, this ledger does not close those broader gaps.

| Batch | Committed task scope | Acceptance evidence | Task status |
|---|---|---|---|
| 1 | Urgent invariant triage: extraction lease recovery, lifecycle compatibility, completeness/Punt gate, verification-driven evaluation invalidation, and documented dispositions for the ten urgent items | GitHub Actions and CircleCI passed on `d28628f70d953c357860c1ea3d1e29c960929c96`; targeted PostgreSQL regressions and triage dispositions recorded in this audit | **COMPLETE (CI-PASS)** |
| 2 | Discovery-source permission gate, safe settings migration, daily/weekly schedule contract, missed-slot behavior | GitHub Actions and CircleCI passed on `1d74f6817fe157322e97485f4f9e29759e069002`; unit and PostgreSQL schedule/permission tests | **COMPLETE (CI-PASS)** |
| 3 | Separate factual profile/eligibility inputs from candidate preferences; deterministic authorization/country rules | GitHub Actions and CircleCI passed on `1a4b8a83f32cd26825c9088e0bf17d4dca801e74`; profile/preference/eligibility regression tests. Follow-up profile API round-trip and candidate-neutral/empty-profile coverage passed on `5ec242cf0481bea221a844ab93ac87161563d843` (run #1664) | **COMPLETE (CI-PASS)** |
| 4 | Evaluation/Punt hard-blocker short-circuiting, stale-input invalidation, decision explanations and qualification gates | GitHub Actions run #1620 and CircleCI passed on `90b6cd57e4cd1714b221ab266f0de81ab960b360`; later branch-head regressions retained | **COMPLETE (CI-PASS)** |
| 5 | Canonical application lifecycle, revision checks, candidate-controlled real-world milestones, package approval gate, append-only audit projection | GitHub Actions run #1623 passed on `fbb01eca0d0a05433a499ce94de1c12fb852c04d`; lifecycle contract and PostgreSQL audit tests | **COMPLETE (CI-PASS)** |
| 6 | Twelve registered AI task contracts, server-owned provider/model routing, execution provenance, bounded configured fallback | GitHub Actions run #1625 passed on `65a48afa24b8acf7f9edf51783a7da3eb943383c`; task-contract, governance and failure-path tests | **COMPLETE (CI-PASS)** |
| 7 | Candidate-scoped CV versions/approvals, document generation/download, package CV provenance/revisions, explicit import-conflict decisions, checksum-verified document restore | GitHub Actions run #1636 passed on `77b31bdd0cd0c8526da024f8a80fe8503d315228`; PostgreSQL and unit regression coverage | **COMPLETE (CI-PASS)** |
| 8 | Persisted-event-driven candidate notifications for Punt and application status; candidate isolation, read state and idempotent replay | GitHub Actions run #1642 passed on `4f38bfe842eff91715d46e56e8149d5709884a46` | **COMPLETE (CI-PASS)** |
| 9 | Optional SMTP notification delivery, verified-email opt-in, durable attempts/failures, bounded retries, unconfigured-provider behavior | GitHub Actions run #1645 passed on `df84daa8ddbd98bdf23c3104de0eddf002283f00`; follow-up config/audit commits also passed | **COMPLETE (CI-PASS)** |
| 10 | Candidate/admin support tickets and messages, idempotent writes, linked follow-up tickets, audit/outbox events, lifecycle notifications | GitHub Actions run #1649 and audit follow-up run #1650 passed; PostgreSQL support-ticket and notification integration tests | **COMPLETE (CI-PASS)** |
| 11 | Admin ticket assignment/unassignment, inbox search and filters, candidate context, API contract and PostgreSQL journey tests | GitHub Actions run #1657 passed on exact head `ca61a7faecf0d9f559dc5628bf2a084d4ae39a3b`; CircleCI success | **COMPLETE (CI-PASS)** |
| 12 | Truthful document-import failure categories, storage-object recovery, extraction failure persistence/retry, transaction cleanup and rollback | GitHub Actions run #1661 passed on exact audit head `9f8d3c2070f591217c5187a2af3580d3654dc24b`; PostgreSQL recovery/failure-injection tests | **COMPLETE (CI-PASS)** |
| 13 | Opt-in scanned-PDF OCR, bounded rendering/batching/configuration, selectable-text preservation, sanitized failure modes | GitHub Actions run #1662 passed on `2095f81756e3f1d2d1b625ec05ae1d3243161424`; CircleCI success and OCR unit tests | **COMPLETE (CI-PASS)** |

**Latest exact-head verification before this audit reconciliation:** GitHub Actions [run #1665](https://github.com/GoReallyBig-GRB/jobpunter-backend/actions/runs/39011811107) passed on parent head `10e232657950b4158282cb4cb6e474194be4d847`: **1,427 passed, 1 skipped, 1 warning**. CircleCI [backend verification](https://app.circleci.com/workflow/7bab93e1-eee4-46c8-b4ae-30da14cbacfd/job/d048a624-92a1-496d-a9e1-e830491dbf33) also passed on that exact parent. The CI status of this ledger update is determined only by checks run on the new commit, not inherited from its parent.

**Broader PRD/release acceptance remains separate:** live SMTP and Google Vision/R2/Drive credentials and provider behavior; deployed worker/scheduler recovery; frontend integration; external-integration consent/revocation lifecycle; broader semantic entailment; full end-to-end candidate/admin journeys; deployment/restore drills. These are not Batch 1–13 task failures; they remain explicitly open requirements for later integration/release acceptance.

## Resumption point — post-PR #77 merge

- **Merged PR:** [#77 — Close backend invariants and discovery reliability gaps](https://github.com/GoReallyBig-GRB/jobpunter-backend/pull/77) merged into `mains-shadow`.
- **Merge commit:** `1f71510f02ed999ff265fa2c53e26588a621c325`.
- **Last implementation/audit head before merge:** `a165d3f23491ed21c7969b7e154bc4b9ca115180`. This is the precise handoff point the continuation must preserve; PR #77's Batches 1–13 are complete within their recorded backend task scopes and passed exact-head CI before merge.
- **Continuation branch:** `post-pr-77-batch-14-continuation`, created from `mains-shadow` after the merge. Continue implementation here; do not reopen completed batch work unless new evidence demonstrates a regression.
- **Next-step gate:** Before coding, reconcile the approved PRD + Technical Spec v1.5 with `PRD_BACKEND_GAP_AUDIT.md` and the Backend Open Items register. Convert only still-open acceptance gaps into a dependency-ordered, bounded next batch; record explicit in-scope tasks, acceptance tests, and out-of-scope items before implementation.
- **Known open acceptance areas (not claims of completed work):** live SMTP/Google Vision/R2/Drive provider verification and recovery; deployed worker/scheduler operation and recovery; frontend integration; external-integration consent/revocation lifecycle; broader semantic entailment; full end-to-end candidate/admin journeys; and deployment/restore drills. Re-check each against the source PRD and open-items register before assigning it to the next batch.
- **Verification rule:** For each new task, plan and implement, run targeted tests plus the relevant full backend verification gates, fix failures, and update this ledger with exact commit and CI evidence. Do not mark a requirement complete from code presence alone.
- **Boundary:** Merge is complete; this branch is a continuation checkpoint only. No deployment is implied or authorized by this checkpoint.

## PR #42 Follow-up Batches — open-item crosswalk (reviewed 2026-10-17)

**Source reviewed:** `Backend PR #42 Follow-up Batches.docx` (11 October 2026), including its Batch 0 closeout, ten urgent Batch 1 checks, Batches 2–10, and acceptance criteria. This is a current-state crosswalk against the post-PR #77 backend audit, not a rewrite of the source document. “Closed” means the scoped backend task has evidence in the completion ledger; it does not imply production readiness. “Partial” means code/tests cover part of the item but the source document’s full acceptance remains open. “OPEN” means the audit does not establish completion.

### Batch 0 — PR #42 closeout

| Source item | Current disposition |
|---|---|
| PostgreSQL 17 CI, PR description and latest-head check | **CLOSED HISTORICALLY** — PR #42 is closed and merged; its recorded PostgreSQL 17 run #888 passed. This is separate from PR #77’s later merge into `mains-shadow`. |
| Independent maintainer review and pre-merge recheck | **CLOSED HISTORICALLY** — PR #42 is now merged. Do not treat this old gate as a release-readiness gate for the current continuation branch. |
| Keep unrelated PRD gaps out of the integration PR | **CLOSED AS A PROCESS RULE** — remaining work belongs in focused follow-up batches, not by reopening merged PR #42/#77. |

### Batch 1 — urgent invariant triage (10 items)

| # | Source item | Current disposition and remaining work |
|---:|---|---|
| 1 | Auth fail-closed for absent/malformed/expired/revoked tokens; reject production `X-User-Id` impersonation | **PARTIAL** — backend identity/revocation and fail-closed regression coverage exists. Production Firebase credentials/configuration and deployed-environment verification remain open. |
| 2 | Inventory emitted outbox events; handlers for `PUNT_QUALIFIED`, `APPLICATION_CREATED`, `APPLICATION_STATUS_CHANGED`, or stop unsupported emissions | **PARTIAL** — persisted event-driven notifications and handlers are implemented/tested for scoped events. Reconcile the complete emitted-event inventory against every registered handler and operational worker dispatch; deployed worker behavior remains open. |
| 3 | Every `JobDescriptionVersion` schedules extraction and persists versioned/provenanced `JobRequirement` rows; idempotent retries | **PARTIAL** — governed extraction, requirement persistence, and lease/recovery logic are covered in backend tests. Live/deployed worker scheduling, restart recovery, and end-to-end operation remain open. |
| 4 | Evaluation must not use unresolved/unverified facts as approved evidence; invalidate or re-run after verification changes | **CLOSED FOR BACKEND SCOPE (CI-PASS)** — evidence approval/effectiveness gates and verification-driven evaluation invalidation have regression coverage. Full candidate journey remains open. |
| 5 | Package-review import smoke test, module tests and package-readiness integration tests | **CLOSED FOR BACKEND SCOPE (CI-PASS)** — the merged branch’s suite passes; reopen only if a reproducible import/runtime regression appears. |
| 6 | Connector/persistence/all-source/partial-source failures must not appear as empty success | **PARTIAL** — connector/persistence failure paths and truthful error categories are tested. Full partial-source aggregation and deployed operational behavior still require explicit acceptance evidence. |
| 7 | Unchanged reacquisition updates last-seen/observation history without duplicate canonical jobs or material events | **OPEN** — source-level observation timestamps are updated, but the audit does not establish the complete unchanged-canonical-job observation/history contract. Add a targeted PostgreSQL regression and define which observation events should or should not be emitted. |
| 8 | Race-safe canonical creation plus URL normalization/collision safeguards; ambiguous identity must not merge unsafely | **PARTIAL** — concurrent canonicalization convergence is tested. Redirect/query/host collision cases and evidence that ambiguous URLs never cause unsafe merges remain to be closed. |
| 9 | Content-based JD completeness used by discovery, evaluation and Punt; reject title/link boilerplate and incomplete descriptions | **CLOSED FOR BACKEND SCOPE (CI-PASS)** — substantive completeness checks and the discovery/evaluation/Punt gate are covered. Keep broader semantic JD completeness as a separate open requirement. |
| 10 | Align `OPEN`/`ACTIVE` lifecycle states and matcher access to real ORM fields | **CLOSED FOR BACKEND SCOPE (CI-PASS)** — lifecycle compatibility and targeted regressions are recorded in the completion ledger. |

### Batch 2 — discovery controls, sources and durable acquisition

| # | Source item | Current disposition and remaining work |
|---:|---|---|
| 1 | Source terms/permission review; block acquisition without approval | **PARTIAL** — an acquisition permission gate exists. Source-specific terms, rate limits, legal/operational review and live approval evidence remain open. |
| 2 | Complete source states `AVAILABLE`, `DEGRADED`, `FAILED`, `UNAVAILABLE` and observed-outcome health contract | **PARTIAL** — source/connector health and ACTIVE/DEGRADED behavior exist; full state vocabulary and transition contract are not evidenced as complete. |
| 3 | Manual URL, pasted JD and email ingestion share normalization/canonicalization | **PARTIAL** — the manual-ingestion endpoint routes URL, pasted text and email parsing through shared foundations. Safe URL retrieval, multi-job email behavior and complete channel acceptance remain open. |
| 4 | Persist external ID, posting date, first/last seen, source provenance and material description versions | **PARTIAL** — external identity, provenance and description versioning are implemented; full field-by-field coverage for posting date and first/last-seen semantics needs explicit verification. |
| 5 | Authenticated/authorized APIs for source registry, jobs, discovery settings and missions | **PARTIAL** — source registry and discovery-settings/admin APIs exist. The audit does not establish complete candidate Job Pool and discovery-mission API coverage; verify endpoint-by-endpoint ownership and authorization. |
| 6 | Daily/weekly schedule by day/time and durable run history | **PARTIAL** — schedule contract, timezone/day/time validation and durable acquisition history exist. Deployed scheduler execution and operational recovery are open. |
| 7 | Recover acquisition runs stuck in `RUNNING`; leases, retries and terminal failure | **OPEN** — generic outbox/worker lease recovery is not proof that acquisition-run records themselves recover correctly after process death. Add crash/restart tests for run-state reconciliation. |
| 8 | PostgreSQL idempotency, concurrency, partial failure and restart-recovery tests | **PARTIAL** — idempotency/concurrency and connector/persistence failure tests exist; restart recovery for deployed acquisition/workers remains open. |
| 9 | Explicit real-source feasibility, terms, rate limits and credentials; no synthetic jobs as production discovery | **OPEN** — live-source permission/feasibility evidence and bounded live acquisition have not been established. |

### Batch 3 — candidate profile, preferences and deterministic eligibility

| # | Source item | Current disposition and remaining work |
|---:|---|---|
| 1 | Complete Master Career Profile fields and API | **CLOSED FOR STRUCTURED PROFILE SCOPE (CI-PASS)** — profile field groups, API round-trip, clear semantics, revision conflicts and ownership are tested. Career-pathway persistence/approval is a separate open capability. |
| 2 | Persist target roles, seniority, industries, locations, remote preferences and exclusions separately from facts | **PARTIAL** — factual eligibility/profile data and preferences are separated; verify the complete PRD preference field set, persistence, and recomputation/invalidation after changes. |
| 3 | Deterministic country/work authorization, remote, salary, relocation, fees and all PRD hard constraints | **PARTIAL** — country/work-authorization and several hard blockers are covered. The full PRD constraint inventory, especially salary, relocation and fees, is not evidenced as complete. |
| 4 | Distinguish missing, contradictory, requires-verification evidence and explicit candidate exclusions | **PARTIAL** — approved/effective evidence and deterministic blockers exist; full state-by-state behavior across all profile/evidence workflows needs acceptance mapping. |
| 5 | Require approved/effective evidence before evaluation | **CLOSED FOR BACKEND SCOPE (CI-PASS)** — ownership, approval, expiry/future-effective and claim-validation tests are recorded. |
| 6 | Candidate ownership, conflict review, versioning and transaction tests | **PARTIAL** — ownership, revisions, import conflict decisions and transaction tests exist. Candidate evidence list/detail/edit APIs and full evidence-health review remain open. |

### Batch 4 — evaluation and Punt

| # | Source item | Current disposition and remaining work |
|---:|---|---|
| 1 | PRD evaluation dimensions/states and job/profile/preferences/rules/task-version snapshots | **PARTIAL** — versioned evaluations and input provenance exist; complete PRD dimension coverage and every required snapshot/version remain to be reconciled. |
| 2 | All nine evidence match states (`DIRECT`, `STRONG_TRANSFERABLE`, `TRANSFERABLE`, `PARTIAL`, `WEAK_TRANSFERABLE`, `MISSING`, `CONTRADICTED`, `REQUIRES_VERIFICATION`, `DISQUALIFYING`) | **PARTIAL** — semantic match contracts and deterministic outcomes exist, but broad semantic entailment and full state coverage remain open. |
| 3 | Wait for verification or re-run after material verification/profile/job changes | **CLOSED FOR BACKEND INVALIDATION SCOPE (CI-PASS)** — invalidation and evidence-ordering regressions are recorded; end-to-end re-evaluation via deployed workers remains open. |
| 4 | Recheck job freshness, closure/lifecycle and description completeness at Punt creation | **PARTIAL** — hard blockers and completeness gates exist; prove the entire Punt-create path rechecks each changing job condition with API-to-PostgreSQL tests. |
| 5 | Deterministic blockers precede AI; AI cannot override constraints | **CLOSED FOR BACKEND SCOPE (CI-PASS)** — deterministic hard-blocker regressions are recorded. |
| 6 | APPLY/REVIEW/WATCH/REJECT explanations with evidence and uncertainty | **PARTIAL** — outcomes and explanations exist; broad semantic matching, provenance completeness and full candidate acceptance remain open. |
| 7 | Punt qualification separate from recommendation; candidate-specific state and idempotency | **CLOSED FOR BACKEND FOUNDATION (CI-PASS)** — qualification/persistence and concurrent idempotency coverage exist; full journey remains open. |
| 8 | API-to-PostgreSQL stale/missing/contradictory/closed/stale/repeat evaluation tests | **PARTIAL** — important regressions exist; the complete matrix and end-to-end acceptance must be reconciled against the PRD. |

### Batch 5 — application lifecycle and append-only audit

| # | Source item | Current disposition and remaining work |
|---:|---|---|
| 1 | Canonical lifecycle values aligned across enums, API schemas, transitions, migrations and UI | **PARTIAL** — backend lifecycle contract and migrations are tested; frontend/UI contract has not been integrated or accepted. |
| 2 | Reason and notes in transition requests and persisted events | **PARTIAL** — actor/reason audit is covered; verify every required transition supports and persists both reason and notes consistently. |
| 3 | Append-only audit service for application transitions, admin actions and security-sensitive changes | **PARTIAL** — application audit projection and selected admin audits exist; whole-API privileged/security event coverage and immutable history need further acceptance. |
| 4 | Recheck freshness/completeness at approval; require approved package | **PARTIAL** — package approval gate exists; recheck of every job freshness/completeness condition at the actual approval boundary needs explicit end-to-end evidence. |
| 5 | No fabricated APPLIED/interview/offer milestones | **CLOSED FOR BACKEND SCOPE (CI-PASS)** — real-world milestones are candidate-controlled or integration-verified; application submission automation remains out of scope. |
| 6 | Register/test `APPLICATION_CREATED` and `APPLICATION_STATUS_CHANGED` handlers | **PARTIAL** — persisted-event notification paths exist; reconcile the full event-handler registry and deployed dispatcher behavior. |
| 7 | Rollback, concurrency, invalid transitions, actor attribution and immutable audit tests | **CLOSED FOR BACKEND TESTED PATHS (CI-PASS)** — targeted PostgreSQL regressions are recorded; broader privileged-action audit coverage remains separate. |

### Batch 6 — AI task registry and governed execution

| # | Source item | Current disposition and remaining work |
|---:|---|---|
| 1 | Immutable task versions with persisted input/output contracts and execution records | **PARTIAL** — registered task contracts, published configuration versions and execution provenance exist; full persisted immutable task-definition/version administration remains open. |
| 2 | Server-controlled, provider-neutral routing | **CLOSED FOR BACKEND SCOPE (CI-PASS)** — routing is server-owned and provider-neutral in the tested paths. Production provider selection/credentials remain an environment decision. |
| 3 | Validate outputs before persistence; retain task/policy/config versions and safe failure categories | **CLOSED FOR TESTED TASKS (CI-PASS)** — typed contracts, validation and failure categories exist; reconcile every production task and persistence path. |
| 4 | Bounded retries/fallback only when configured; graceful degradation | **CLOSED FOR BACKEND CONFIGURED PATHS (CI-PASS)** — bounded configured fallback and failure-path tests exist; live provider outages remain unverified. |
| 5 | Evidence grounding and deterministic policy cannot be overridden by generated output | **PARTIAL** — source grounding and deterministic gates exist; broader semantic entailment and all-task grounding are still open. |
| 6 | Migrations/contracts/regressions for invalid schema, malformed output, provider outage and retry idempotency | **PARTIAL** — these are covered for registered/tested paths; complete per-task acceptance and live-provider verification remain open. |

### Batch 7 — remaining candidate capabilities

| # | Source item | Current disposition and remaining work |
|---:|---|---|
| 1 | PDF/DOCX/TXT validation, private storage, parsing, extracted-field preview, conflict detection and confirmation | **PARTIAL** — upload, parsing, preview/confirmation, conflict decisions and recovery exist; live R2/Drive/Vision behavior and complete candidate journey remain open. |
| 2 | Evidence-source attribution and candidate-reviewed conflict resolution | **CLOSED FOR IMPORT-CONFLICT SCOPE (CI-PASS)** — explicit per-conflict decisions and provenance are recorded; broader evidence read/edit/health management remains open. |
| 3 | Master CV and variants without mutating source profile | **CLOSED FOR BACKEND CV-VERSION SCOPE (CI-PASS)** — candidate-scoped versioning and approvals exist; full editing/regeneration workflow acceptance remains open. |
| 4 | Genuine DOCX/PDF CV, cover letter and application Q&A from approved evidence/job context | **PARTIAL** — CV DOCX/PDF generation is implemented; cover-letter/Q&A artifact coverage and full grounded rendering acceptance are not established as complete. |
| 5 | Immutable package versions, previews/editing, claim references and unsupported-claim warnings | **PARTIAL** — package version/provenance and semantic audit foundations exist; package editing/approval and rendered-artifact end-to-end acceptance remain open. |
| 6 | Explicit package approval before application approval; no automatic submission | **CLOSED FOR BACKEND GATE (CI-PASS)** — approval gate and candidate-controlled milestones are tested. |
| 7 | Ownership, signed-download expiry, 4 MiB limit, checksums, retention and artifact restore | **PARTIAL** — upload limit, private-storage paths, checksum-verified restore and recovery tests exist; signed URL expiry, retention lifecycle and live provider recovery need explicit acceptance. |

### Batch 8 — admin governance, integrations and notifications

| # | Source item | Current disposition and remaining work |
|---:|---|---|
| 1 | Protected admin config draft/validation/immutable publication/rollback/audit | **CLOSED FOR TESTED BACKEND SCOPE (CI-PASS)** — protected configuration lifecycle and audit tests are recorded; staging operation remains open. |
| 2 | Operational source health, run history and AI execution status without private candidate data | **PARTIAL** — protected telemetry/audit and source/run APIs exist; prove complete operational visibility and privacy-safe output across all components. |
| 3 | Integration consent, minimum scopes, health, revocation and secret rotation for Gmail/Drive/other integrations | **OPEN** — provider-neutral integration connection lifecycle, explicit user consent/revocation and rotation are not established as complete. Google sign-in is not Gmail/Drive consent. |
| 4 | Notifications from persisted domain events; retry, duplicate and failure tests | **PARTIAL** — in-app notification/event paths and optional SMTP retry are implemented/tested; live SMTP and full event coverage/worker operation remain open. |
| 5 | Demo/test-data isolation from production records and discovery feeds | **OPEN** — no adequate end-to-end environment/data-isolation evidence is recorded. |
| 6 | Operational alert ownership, safe logs and manual reprocessing | **OPEN** — deployment-grade alert ownership and audited manual reprocessing/replay workflow are not established. |

### Batch 9 — deployment portability and release acceptance

| # | Source item | Current disposition and remaining work |
|---:|---|---|
| 1 | Deploy API/workers outside AI Studio assumptions with explicit environment configuration | **OPEN** — deployment topology and worker/scheduler operation remain unverified. |
| 2 | TLS, ingress/body limits, CORS, headers, secrets, readiness/health and controlled migrations | **PARTIAL** — some app-level security controls and request limits exist; target-environment ingress/TLS, secrets, readiness and migration operation require deployment evidence. |
| 3 | PostgreSQL backup/restore and document-object recovery | **OPEN** — checksum-verified Drive-to-private-object restore code is not a demonstrated PostgreSQL backup/restore drill or full deployed recovery exercise. |
| 4 | End-to-end candidate journey through candidate-recorded application event | **OPEN** — no complete target-environment journey is evidenced. |
| 5 | End-to-end admin configuration/publish/rollback/audit journey | **OPEN** — unit/PostgreSQL tests exist; target-environment journey remains unverified. |
| 6 | Exact commit/environment/CI/evidence/exceptions/owners/expiry release record | **OPEN** — per-commit CI evidence exists, but a complete release evidence pack with owners and expiry dates is not recorded. |
| 7 | Reconcile all PRD invariant IDs and historical gap IDs to executable tests or accepted exceptions | **PARTIAL** — this audit and the PR #77 ledger improve traceability; complete invariant/gap-ID reconciliation is still required. |

### Batch 10 — cross-cutting engineering quality

| # | Source item | Current disposition and remaining work |
|---:|---|---|
| 1 | PostgreSQL 17, one Alembic head, clean migration, schema parity and full tests in CI | **CLOSED FOR CURRENT CI GATES (CI-PASS)** — exact-head GitHub Actions and CircleCI passed before merge. Continue enforcing them on future commits. |
| 2 | Add type checking/lint/format checks where practical | **OPEN** — no evidence in this review that a comprehensive type/lint/format gate is configured and enforced. Inspect current CI before adding tools. |
| 3 | Setup, API contracts, deployment topology, migrations and recovery/rollback documentation | **PARTIAL** — audit/runbook material exists; deployment topology and operational recovery docs need to match a validated deployable environment. |
| 4 | ADRs for PostgreSQL authority, identity, source access, AI adapters and storage | **OPEN** — the PRD leaves several provider/deployment choices unresolved; record explicit decisions before production deployment. |
| 5 | Keep `PRD_BACKEND_GAP_AUDIT.md` and `implementation-gap-audit.md` aligned | **PARTIAL** — this audit has been updated, but the historical second-register path was not found at the continuation branch root during this review. Verify its canonical location or explicitly retire/redirect it; do not silently assume both registers are synchronized. |

### Prioritized open-work shortlist from this crosswalk

1. **P0 verification gaps:** unchanged-job observation/last-seen semantics; acquisition-run crash recovery; complete outbox event-to-handler inventory; production auth configuration; URL collision safety and full source-failure truthfulness.
2. **P1 workflow gaps:** full source permission/health/mission APIs; complete preferences and eligibility constraints; full PRD evaluation dimensions/match states and Punt-time rechecks; evidence read/edit APIs; application approval-time freshness/audit coverage; all-task AI contract/grounding coverage; full package artifact/approval acceptance.
3. **P2 product/operations gaps:** career pathways and explicit approval; User Records and read-only impersonation; Gmail/Drive integration consent/revocation lifecycle; demo/test isolation; operational alert ownership/manual reprocessing.
4. **Release gates:** deployable API/worker/scheduler topology; live source/provider verification; TLS/ingress/secrets/readiness; PostgreSQL and document restore drills; full candidate/admin end-to-end journeys; complete invariant/gap-ID traceability; enforced static-quality gates and architecture decisions.

**Execution note:** This is a review/crosswalk, not a claim that each item was freshly re-tested. Before implementation, use the source PRD v1.5 and `JobPunter Backend Open Items Draft.docx` as required by the resumption marker, reconcile overlapping register items, and select the next bounded batch. No deployment or merge is authorized by this crosswalk.


## Canonical continuation register — PRD v1.5 + open-items + PR #42 crosswalk

**Reconciled on:** 2026-10-17  
**Applies to:** `post-pr-77-batch-14-continuation`  
**Purpose:** Make the existing audit the single execution index for the three current requirement sources. This section does not supersede the PRD, its Current-State Audit & Gap Closure Control Register, or the detailed PR #42 crosswalk above. It links their obligations and fixes the execution order so no item is lost between documents.

### Source authority and reconciliation rules

1. **Product requirements and architecture:** the uploaded *JobPunter PRD + Technical Spec v1.5* is the current product/architecture baseline. Preserve its complete inherited requirements and invariants; a newer architecture section does not silently delete unaffected earlier requirements.
2. **Detailed backend task intake:** *JobPunter Backend Open Items Draft.docx* supplies the stable task IDs S01–S13 and their task-level acceptance criteria. Its old branch, PR #54, commit, CI, and ahead/behind metadata are historical only and are not current branch evidence.
3. **PR #42 follow-up checklist:** the crosswalk above preserves the Batch 0 historical closeout and dispositions for Batches 1–10. PR #42 closeout is historical; it is not the next batch.
4. **Implementation evidence:** the existing audit matrix and PR #77 completion ledger are the starting evidence, not proof that every broader workflow is accepted. A scoped task may be closed only for the exact tested scope. CI pass, code presence, mocks, and connector configuration do not prove live-provider or target-environment acceptance.
5. **No silent deletion or redefinition:** keep all existing invariant IDs, gap IDs, lifecycle history, and task IDs. Where requirements conflict, record a decision and its approval before changing contracts. Do not solve conflicts by silently picking one wording.
6. **Single register:** update this file and its existing crosswalk/ledger as work progresses. Do not create another competing open-items register. Keep `implementation-gap-audit.md` synchronized if it exists at a canonical path; first verify that path rather than assuming it exists.
7. **Scope and controls:** backend repository only; no frontend integration until user workflows, Admin workflows, and backend release acceptance are complete. No new branch, merge, or deployment without explicit authorization.

### Unified task index and dependency order

The following is the canonical dependency sequence. Detailed task descriptions and acceptance criteria remain in the S01–S13 source register summarized below; task IDs must be retained in commits, tests, and this audit.

| Order | Source task IDs | Capability / required outcome | Dependency and exit gate |
|---|---|---|---|
| 0 | Existing resumption marker + PR #77 ledger | Preserve the exact post-PR-77 baseline and prior completed batch evidence. | Already recorded. Re-check current branch/head before each implementation batch. |
| 1 | Residual P0 items in PR #42 crosswalk; S01-B01-T01 only as a decision prerequisite | Re-verify the residual auth, event-handler, extraction, verification ordering, package import, source-failure truthfulness, unchanged-job observation, URL collision, completeness and lifecycle/schema invariants against exact current code. | Each item needs a targeted regression test or a documented evidence-backed closure. Do not re-implement items already covered. Resolve the identity/sign-in decision before changing provider architecture; it does not block unrelated invariant tests. |
| 2 | S01-B01-T01–T02; S01-B02-T01–T02 | Record identity/sign-in decision; make provider selection replaceable at the trusted boundary; complete account fields, lifecycle states and audit. | Decision recorded before provider architecture changes. Production auth remains fail-closed and server-selected. |
| 3 | S04-B01-T01; S04-B02-T01–T02; S04-B03-T01–T04; S05-B02-T01–T03; S10-B02-T01 | Safe URL retrieval; multi-job email ingestion/idempotency; common connector/source path; permission/health truthfulness; canonical fields, material JD versions, freshness/closure and crash recovery. | One truthful, permissioned, idempotent acquisition path. SSRF and collision tests precede enabling URL retrieval or new automated sources. |
| 4 | S05-B01-T01–T02; S06-B01-T01–T03; S10-B01-T01–T04; S10-B03-T01–T02 | Candidate-facing canonical Job Pool and identity review; recoverable requirement extraction; reconcile outbox/worker/mission state machines; durable work-item APIs and schedule/run history. | Every producer has a reachable consumer; no permanent unexplained RUNNING state; restart, retry, duplicate, partial-failure and concurrency behavior tested. |
| 5 | S02-B01-T01–T03; S02-B02-T01–T02; S03-B01-T01–T02; S03-B02-T01–T02 | Career pathways with candidate approval; field-by-field profile/preferences; evidence list/detail/edit/health; import history and recovery. | Only approved pathways drive matching; facts/preferences/import candidates remain distinct; ownership, provenance, revisions and invalidation are enforced. |
| 6 | S07-B01-T01–T02; S07-B02-T01–T02 | Versioned decision rules and side-effect-free preview; complete evaluation dimensions, nine evidence-match states, Review Queue and explainable APPLY/REVIEW/WATCH/REJECT; Punt-time rechecks. | Depends on stable canonical job/JD, candidate facts/preferences, evidence and extraction contracts. Deterministic blockers run before AI; snapshots and stale-input behavior are tested. |
| 7 | S08-B01-T01–T02; S08-B02-T01–T03; S09-B01-T01–T03 | Master CV/variants; editable/versioned application artifacts; grounded generation; canonical application lifecycle decision; candidate-controlled transitions, package approval and historical audit. | Depends on approved profile/evidence/pathway, current JD/evaluation and package contract. No automatic submission or fabricated real-world events. |
| 8 | S11-B01-T01–T03; S11-B02-T01–T02; S11-B03-T01–T02; S11-B04-T01–T02 | Admin control plane, task/rule governance, operational overview, User Records, server-enforced read-only impersonation, separately authorized interventions, and full customer-support ticket/inbox/reply/resolution workflow. | Execute after user workflows as directed. Every read and mutation has a distinct permission policy, actor/reason audit, privacy boundary and negative authorization tests. |
| 9 | S12-B01-T01–T03; S12-B02-T01–T03 | Provider-neutral integration connections, explicit consent/scopes/revocation, Gmail ingestion if approved, separate Drive/Sheets decision, persisted-event notifications and truthful optional email delivery. | Resolve MVP scope and provider decisions first. Google sign-in is not Gmail/Drive consent; in-app notifications survive email failure. |
| 10 | S13-B01-T01–T03; S13-B02-T01–T03; S13-B03-T01–T03; S10-B02-T02–T03 | Rate/resource/AI usage controls; health, metrics, alerting and safe replay; whole-API authorization/privacy/SSRF review; hosting/storage/scheduler/provider decisions; deployable API/worker/scheduler; data migration and backup/restore; documentation and full candidate/admin E2E acceptance. | Final backend gate. Exact commit/environment evidence, recovery drills, demo separation, CI, invariant mapping and accepted exceptions required before frontend integration. |
| Continuous | S10 + every feature batch; engineering-quality Batch 10 | PostgreSQL 17, single Alembic head, schema parity, migration tests, type/lint/format gates where approved, docs and ADRs. | Enforce per batch; do not postpone correctness or migration coverage to the end. |

### Open-item coverage from the detailed register

- **S01 — Authority, identity, account lifecycle:** identity provider/sign-in decision; trusted-boundary provider injection; account email/photo/onboarding/last-login rules; active/suspended/deactivated transitions and audit.
- **S02 — Profile, preferences, pathways:** persistent candidate-owned pathways, explicit approval/rejection/retirement, pathway-to-discovery wiring, complete PRD field-by-field profile and preference tests.
- **S03 — Evidence and imports:** paginated owner-scoped evidence list/detail, controlled revision-checked edits and evidence health, import history/detail, failure/retry/provenance/duplicate recovery.
- **S04 — Source registry and ingestion:** SSRF-safe URL-to-JD retrieval; multi-job email digests and stable message IDs; shared connector contract; source permissions, supported API/feed/RSS scope, admin configuration/health and live-source evidence.
- **S05 — Canonical Job Pool/JD:** candidate-facing list/detail/search; audited ambiguous identity review; complete canonical fields, retrieval state, material versions, freshness and closure acceptance.
- **S06 — Requirement extraction/review:** per-JD-version durable state, abandoned-run recovery, reviewer authorization, exact-version linkage and evaluation invalidation.
- **S07 — Evaluation/rules/Review Queue/Punt:** immutable decision-rule versions, validation/publish/rollback/audit, non-mutating preview, full dimensions/states, owner-scoped Review Queue, stale-input and Punt-time gates.
- **S08 — CV Studio/packages:** versioned Master CV and approved variants; candidate editing and regeneration; evidence/JD provenance, unsupported-claim warnings, actual DOCX/PDF artifact checks, ownership and download/restore acceptance.
- **S09 — Application lifecycle:** explicit resolution of conflicting lifecycle vocabularies; reason/notes/actor/source/before-after history; package approval, candidate-recorded submission and outcome; no fabricated milestones.
- **S10 — Work, outbox and scheduler:** one authoritative dispatch path; reachable event handlers and worker state transitions; WorkItem status/cancel/retry/detail; acquisition/AI/import recovery; immutable schedule snapshots and persisted run metrics.
- **S11 — Admin, User Records, impersonation, support:** protected health/config/task/rule operations; candidate directory and records; read-only impersonation enforced server-side; separate allowlisted interventions; persistent two-way tickets, inbox, replies, assignment where needed, resolution, unread/delivery state and audit.
- **S12 — Integrations/notifications:** per-user provider-neutral connection lifecycle and consent; separately scoped Gmail/Drive/Sheets decisions; owner-scoped in-app notifications; persisted-event handlers; bounded, deduplicated optional email delivery with truthful status.
- **S13 — Security/operations/release:** resource limits and observed AI usage; readiness/metrics/alerts/redacted logs; whole-API authorization, privacy and SSRF review; hosting/storage/scheduler decisions; deployable topology; migration/cutover; PostgreSQL and object restore drills; docs, release evidence and full candidate/admin E2E.

### Decisions that must be recorded, not guessed

1. Identity provider and supported sign-in methods.
2. Hosting for API, worker, scheduler and database; object-storage vendor; secret management and backup ownership.
3. Initial AI provider/model routing and which credentials/limits are available in the target environment.
4. First production sources and per-source permission/terms, access method, rate limits, retained fields and live verification.
5. Whether Drive and Sheets are in MVP; Gmail is a separate explicitly authorized integration.
6. Canonical application lifecycle states/events where PRD sections differ; preserve historical events during mapping.
7. Storage/data migration from AI Studio, Firestore or browser-held prototype state; data ownership, provenance, demo isolation, rollback.
8. Admin role/permission model for requirement review, User Records, impersonation, support and privileged interventions.
9. Static analysis gates and operational alert ownership.

A decision may use an explicit, documented MVP default where the PRD allows it. Do not build speculative infrastructure or enable an external source/integration before the decision and acceptance requirements are recorded.

### Next bounded batch selected

**Batch 14 — Residual P0 invariant evidence closure (backend only).** This is a verification-and-fix batch, not a broad feature batch. It begins with the PR #42 crosswalk's remaining P0 items: (1) unchanged reacquisition updates last-seen/observation without duplicate jobs/material events; (2) abandoned acquisition-run recovery and concurrent recovery safety; (3) complete emitted-event-to-handler inventory, including PUNT_QUALIFIED, APPLICATION_CREATED and APPLICATION_STATUS_CHANGED, with idempotent handlers or removal of unsupported emissions; (4) production authentication fail-closed and development identity override boundary; (5) URL normalization/collision and ambiguous identity safety; (6) truthful all-source failure/partial success; (7) substantive JD completeness enforced consistently through discovery/evaluation/Punt. Also verify exact-JD-version extraction scheduling and package import/readiness regression coverage.

**Batch 14 entry gate:** inspect current exact-head code/tests and existing CI evidence first; classify each item as already covered, defect confirmed, or runtime evidence unavailable. Do not duplicate code/tests simply to create activity. Any confirmed defect is fixed with a targeted regression test and the required migration only if schema changes. Do not change identity-provider architecture in this batch; the unresolved provider/sign-in decision is a recorded prerequisite for the later S01 architecture task.

**Batch 14 exit gate:** every item has a linked test/result or a named environment blocker; relevant PostgreSQL tests and CI pass on the exact head; this audit records commit SHA, test command/run, result, and any remaining live-runtime limitation. Batch 14 does not authorize merge or deployment.

### Frontend entry gate

Frontend work remains prohibited until: (a) candidate/user workflows pass end-to-end backend acceptance; (b) Admin workflows, including customer support, pass their own acceptance; (c) cross-cutting integrations/security/operations/deployment/restore gates are evidenced; (d) every PRD invariant and historical gap ID is mapped to a test or explicitly approved exception; and (e) API contracts and unresolved decisions are documented. A green CI run alone is insufficient.


### Batch 14 — initial code/test inspection notes (not a closure claim)

Reviewed the exact continuation-branch source and test files after selecting Batch 14. These are source-level findings; no tests were executed in this review, and the documentation-only commits do not create new CI evidence.

| P0 item | Evidence found | Disposition for Batch 14 |
|---|---|---|
| Unchanged reacquisition and last-seen | `app/infrastructure/db/repositories/acquisition.py` records a `JobDiscoveryEvent` for each observation and emits `RAW_SOURCE_RECORD_OBSERVED` for unchanged payloads; `app/infrastructure/db/repositories/canonicalization.py` attaches observations and advances `Job.last_observed_at`. `tests/integration/test_canonicalization_postgres.py` covers last-observed convergence and URL collision. | **Existing implementation/test coverage found.** Verify exact assertions and retain as closed only for covered cases; do not duplicate the feature. |
| Expired acquisition-run recovery | `SqlAlchemyAcquisitionRepository.recover_stale_runs` selects expired RUNNING leases with `FOR UPDATE SKIP LOCKED`; `AcquisitionService.run` calls recovery before replay/new run. `tests/integration/test_acquisition_postgres.py::test_postgres_recovery_marks_only_expired_acquisition_leases_failed` and `tests/unit/test_acquisition_service.py::test_acquisition_service_recovers_expired_runs_before_idempotent_replay` cover single-run recovery. | **Partial.** A concurrent recovery regression was not found in the inspected tests. Runtime recovery also depends on subsequent acquisition invocation; periodic/deployed recovery remains a release/operations gate. |
| Outbox event/handler alignment | `app/workers/outbox.py::build_dispatcher` registers handlers for RAW_SOURCE_RECORD_ACQUIRED, RAW_SOURCE_RECORD_OBSERVED, PUNT_QUALIFIED, APPLICATION_STATUS_CHANGED, EVALUATION_REQUIRES_ATTENTION, DISCOVERY_MISSION_COMPLETED, NOTIFICATION_EMAIL_REQUESTED, SUPPORT_TICKET_CREATED and SUPPORT_TICKET_STATUS_CHANGED. | **Mostly present; registry-to-emitter inventory still needs explicit acceptance.** Verify all persisted outbox event producers against this registry and their idempotency tests. |
| APPLICATION_CREATED handling | `OpportunityService.create_application` writes an `ApplicationEvent`/audit event named APPLICATION_CREATED, not an `OutboxEvent`. The inspected lifecycle integration test asserts APPLICATION_CREATED and APPLICATION_STATUS_CHANGED audit history, and only the status transition creates the APPLICATION_STATUS_CHANGED outbox event. The PRD notification specification requires tracked application status-change notifications, not a separate creation notification. | **No missing outbox handler established.** Do not add an unnecessary creation notification. Preserve the distinction between application/audit events and outbox events; document this as the accepted disposition unless a separate requirement is approved. |
| Authentication fail-closed | `app/api/deps.py` allows `X-User-Id` only with explicit development environment plus opt-in flag; otherwise it calls the configured identity provider and returns 401/503 on auth/configuration failures. `tests/unit/test_auth_fail_closed_cases.py` covers missing, empty, malformed, expired and revoked tokens. | **Existing code/test coverage found.** Still require target-environment configuration verification; do not change provider architecture before the identity decision. |
| URL normalization/collision | `tests/integration/test_canonicalization_postgres.py` includes conflicting-title URL collision routed to review; canonicalization and normalization code preserve ambiguity rather than merging solely on weak similarity. | **Existing targeted regression found.** Verify other normalization cases and keep live URL retrieval/SSRF protection as a separate open S04 task. |
| Substantive JD completeness | `app/domain/jobs/completeness.py` rejects short/boilerplate text lacking both responsibility and qualification signals; discovery screening checks completeness; Punt creation rechecks latest JD status and substantive content. Unit tests cover boilerplate and partial content. | **Core gates found.** Confirm API-to-PostgreSQL coverage for discovery, evaluation and Punt and verify all paths use the same semantics. |
| Extraction and package readiness | Existing worker/integration tests cover persisted requirements per JD version and recovery of stale/missing extraction leases; dedicated package import/readiness tests exist. | **Existing targeted coverage found.** Confirm exact-version provenance/idempotency and ensure all package readiness entry points exercise the same guard. |
| Truthful source failures/partial success | `AcquisitionService.run` distinguishes terminal connector outcomes, partial batches, connector exceptions and persistence failures; unit acquisition tests cover failure categories and retry metadata. | **Existing code/test coverage found.** Verify all-source failure and mixed-source partial-success accounting at the mission/API boundary, not only at connector-service level. |

**Batch 14 remaining work after this inspection:** (1) complete the outbox producer-to-handler inventory and verify it with a regression test or evidence-backed per-event disposition; (2) add/verify concurrent stale-acquisition recovery behavior; (3) close the remaining end-to-end/API-boundary checks for source partial failure, completeness, exact-version extraction and package readiness; (4) distinguish test-proven behavior from deployed scheduler/worker recovery, which cannot be closed by source inspection. No implementation task is marked complete solely from the code review above.


### Batch 14 — evidence update and code-level disposition (2026-10-17)

**Candidate code head:** `ee1317a7ee9735f75bcdc8ddf41abe45458d1e4f`  
**Draft PR:** [#79 — Batch 14 residual backend invariant evidence gaps](https://github.com/GoReallyBig-GRB/jobpunter-backend/pull/79)  
**Exact-head CI:** [GitHub Actions run #1669](https://github.com/GoReallyBig-GRB/jobpunter-backend/actions/runs/39039490274)  
**Result:** 1,431 passed, 1 skipped, 1 warning; Python 3.12 / PostgreSQL 17. Compileall, single Alembic head, legacy migration-stamp compatibility, ORM/schema parity, and the full unit + PostgreSQL integration suite passed. The warning is Starlette's deprecation notice for using `httpx` with `starlette.testclient`; it is not a test failure.

An earlier intermediate commit `d8a6af7433f7cde96930389f8e5776379433c72c` failed two new parameter cases because the test fixture supplied three-element tuples where the mission worker expects source/connector pairs. The fixture was corrected in `dec2de5bd245445088e39fb8546e2f59ec6d9707`; the full suite then passed (1,430 passed, 1 skipped, 1 warning). The final handler-registry test was added in `ee1317a7ee9735f75bcdc8ddf41abe45458d1e4f` and the exact final head passed (1,431 passed, 1 skipped, 1 warning). The failed intermediate result is retained here rather than hidden.

#### Event producer-to-handler inventory

The following inventory was traced from current production code. The regression test `tests/integration/test_outbox_dispatcher_postgres.py::test_production_dispatcher_registers_every_persisted_domain_event_handler` locks the production handler set to the nine domain-event types below.

| Persisted outbox event | Producer | Dispatch/consumer disposition |
|---|---|---|
| `RAW_SOURCE_RECORD_ACQUIRED` | `SqlAlchemyAcquisitionRepository` acquisition persistence | `canonicalization_event_handler`; creates/updates canonical job, description versions, and provenance. |
| `RAW_SOURCE_RECORD_OBSERVED` | `SqlAlchemyAcquisitionRepository` unchanged/previously known source observation | Same idempotent canonicalization handler; records observation/freshness without requiring a duplicate description version. |
| `PUNT_QUALIFIED` | `OpportunityService.create_punt`, only for a newly recorded qualification | `punt_notification_handler`; persisted Punt is the source for notification. |
| `APPLICATION_STATUS_CHANGED` | `OpportunityService.transition_application`, when a status-change ApplicationEvent is recorded | `application_status_notification_handler`; consumes the persisted ApplicationEvent. |
| `EVALUATION_REQUIRES_ATTENTION` | Candidate evaluation service when the persisted evaluation requires attention | `evaluation_attention_notification_handler`. |
| `DISCOVERY_MISSION_COMPLETED` | `DiscoveryMissionWorker` on terminal mission outcomes, idempotently by mission | `discovery_completion_notification_handler`. |
| `NOTIFICATION_EMAIL_REQUESTED` | Notification persistence when email is enabled and the account email is verified | `notification_email_delivery_handler`; delivery attempts and outcomes remain persisted. |
| `SUPPORT_TICKET_CREATED` | Support ticket creation | `support_ticket_notification_handler`, with `kind=CREATED`. |
| `SUPPORT_TICKET_STATUS_CHANGED` | Support ticket status transition | Same support handler with `kind=RESOLVED` or `kind=CLOSED`; these match the supported handler kinds. |

Special dispatch behavior:
- `WORK_REQUESTED` is emitted by `SqlAlchemyWorkRepository` and is handled by the dispatcher's built-in WorkItem path, not by the domain handler map. The production dispatcher explicitly delegates `DISCOVERY_MISSION` work to `DiscoveryMissionWorker`, so that work type must not also be executed by an outbox handler.
- `APPLICATION_CREATED` is an append-only `ApplicationEvent`/audit lifecycle record, **not an OutboxEvent**. The inspected PRD requires status-change notifications; no separate creation-notification event is added without a product requirement.
- Career-pathway and evidence event names found in their respective services are audit/lifecycle records, not outbox events. They are not missing entries in the production outbox handler map.
- Unsupported outbox event types deterministically become dead-lettered by the dispatcher. The registry regression test prevents the known production handler map from silently losing a required handler.

#### Acceptance evidence for the remaining P0 checks

| Invariant | Code/test evidence | Disposition |
|---|---|---|
| Concurrent stale acquisition recovery | New `test_postgres_concurrent_stale_run_recovery_skips_locked_run_and_recovers_it_later`: one transaction locks the earliest expired lease; another recovery skips it and recovers the next lease; after release, the locked lease is recovered. | **TESTED** on the final exact CI head. Recovery is safe at the repository/transaction level for the tested PostgreSQL scenario. |
| Truthful all-source failure and mixed outcomes at API boundary | New parametrized `test_mission_api_reports_non_successful_source_outcomes_without_empty_success`; existing `test_all_source_failures_remain_visible_and_do_not_report_empty_success` and `test_partial_source_success_preserves_both_success_and_failure_outcomes` in `test_discovery_missions_postgres.py`. | **TESTED**: all-failure is surfaced as retryable `PENDING`, mixed successful acquisition/failure as `WAITING_FOR_CANONICALIZATION`; neither is represented as successful empty completion. Per-source run-history API exposure remains part of the broader source/workflow backlog. |
| Completeness across acquisition, canonicalization, extraction, evaluation and Punt | `test_incomplete_acquired_description_stays_incomplete_through_canonicalization`; `test_incomplete_job_description_is_not_sent_for_requirement_extraction`; unit completeness/evaluation tests; `test_punt_rechecks_latest_job_description_completeness`. | **TESTED** for the covered paths; no duplicate guard implementation added. |
| Exact JD-version extraction and crash recovery | `test_acquired_job_reaches_canonical_storage_through_outbox`, exact-version requirements assertions, extraction failure/backoff tests, and stale/missing-lease recovery tests in `test_acquisition_to_canonical_job_postgres.py`. | **TESTED** for covered PostgreSQL paths; no duplicate worker behavior added. |
| Package import/readiness and approval recheck | `test_application_package_review_imports.py`, `test_application_package_readiness_regressions.py`, and `test_application_packages_postgres.py`, including tampered artifacts/snapshots, evidence revocation, approval-time rechecks, ownership, and submission readiness gates. | **TESTED** for the covered regression suite; no duplicate readiness gate added. |
| Authentication fail-closed / development override | `app/api/deps.py` and `tests/unit/test_auth_fail_closed_cases.py`. | **TESTED** by existing suite; target-environment identity configuration is still **NOT VERIFIED** and provider architecture remains unchanged. |
| Unchanged observation, URL collision and freshness | `test_postgres_canonicalization_records_each_observation_without_duplicate_description_version`, `test_postgres_url_collision_with_conflicting_title_requires_review`, and `test_unchanged_and_changed_observations_refresh_canonical_job_freshness`. | **TESTED** by existing suite; URL-fetch SSRF/redirect/content-size controls remain a separate S04 implementation requirement. |

#### Batch 14 status

**Code-level acceptance: COMPLETE for this bounded invariant-verification batch**, based on the final exact-head CI and the source/test dispositions above. This does not mean the whole backend or production runtime is complete.

**Named environment/release blocker — RUNTIME NOT VERIFIED:** the repository has a recoverable acquisition lease implementation and passing PostgreSQL regression coverage, but no target deployment was exercised. The deployed worker/scheduler must be shown to invoke recovery after crashes, and the production API/worker/scheduler topology, credentials, and source integrations remain release gates. Do not mark runtime recovery or production readiness complete from CI alone.

The branch remains unmerged and PR #79 remains draft. No merge or deployment is authorized.


### Batch 15 — identity decision and trusted-boundary verification (2026-10-17)

**Decision record:** Firebase Authentication is the MVP identity provider, and Google sign-in is the selected sign-in method. This carries forward the user's previously locked JobPunter architecture decision. PRD v1.5 still contains an unresolved identity-provider placeholder; this record makes the explicit project decision visible without silently rewriting the source PRD. Firebase remains replaceable behind the backend's server-owned `IdentityProvider` boundary.

**Security and account-mapping constraints:**
- The backend verifies Firebase ID tokens server-side, checks revocation, and requires `FIREBASE_PROJECT_ID` to match the configured Firebase Admin app.
- Only verified claims may establish a local identity. A Firebase UID maps to a durable `Identity(provider="FIREBASE", provider_subject=<uid>)`; do not merge accounts by email or silently link another provider.
- Required deployment configuration is `JOBPUNTER_IDENTITY_PROVIDER=firebase`, `FIREBASE_PROJECT_ID`, and valid server-side Firebase Admin credentials. Unknown provider names must fail closed; there is no fallback to development identity.
- Google sign-in establishes application identity only. Gmail, Drive, and Sheets access require separate product decisions and explicit consent/scopes.
- Adding or migrating an identity provider later requires an explicit identity-linking/migration plan and audit trail; do not change provider-subject semantics or infer identity from matching email addresses.

**Batch 15 implementation scope:** verify the trusted-boundary provider selector with targeted regression tests, including default/explicit Firebase selection and unknown-provider rejection in production even if the development identity flag is mistakenly enabled. Preserve existing fail-closed token tests. No provider architecture rewrite is needed if these tests pass; the provider port and Firebase adapter already exist.

**Acceptance gate:** exact-head GitHub Actions and CircleCI must pass after the test change. The codebase's server-side auth path may be verified by CI, but target-environment Firebase credentials, Google sign-in UX, and deployment configuration remain runtime items until exercised in the target environment. No merge or deployment is authorized.


### Batch 16 — safe URL redirect regression coverage (2026-10-17)

**Commit:** `60e50b551dd12d715ac2497ec15a650877016020`  
**GitHub Actions:** [run #1674](https://github.com/GoReallyBig-GRB/jobpunter-backend/actions/runs/39041331280) — **PASS**, 1,438 passed, 1 skipped, 1 warning.  
**CircleCI:** backend-verification passed on the same head.

Added focused regressions for S04 safe URL retrieval:
- Redirect targets are revalidated; redirecting to a private destination is blocked before a connection is attempted.
- Redirect responses without a Location header fail closed.
- Redirect count is bounded and fails with the expected safe error category.

This closes only the covered redirect cases. It does not establish live-source access feasibility, full source-permission acceptance, or deployed retrieval behavior.

### Batch 17 — candidate-facing canonical Job Pool and manual-submission isolation (backend only)

**Current code head:** `761ee5665dd8974840a572f9997046e9a85f372d`  
**Draft PR:** [#79](https://github.com/GoReallyBig-GRB/jobpunter-backend/pull/79) remains open, draft, and unmerged.  
**GitHub Actions:** [run #1685](https://github.com/GoReallyBig-GRB/jobpunter-backend/actions/runs/39043567801) — **PASS**, 1,443 passed, 1 skipped, 1 warning.  
**CircleCI:** backend-verification passed on the same head.

**Implemented and tested**
- Added authenticated candidate Job Pool list and detail endpoints at `GET /api/v1/candidate/jobs/pool` and `GET /api/v1/candidate/jobs/pool/{job_id}`, with pagination, text search, lifecycle/freshness filters, country and work-mode filters, and latest JD preview/detail.
- Shared visibility requires a non-manual source with active source status, explicit approved permission review and reference, and a review timestamp. Source operational state is exposed to candidates; it does not silently hide an already acquired job. Job freshness and closure remain separate states.
- Manual-only jobs are visible only to the authenticated submitter. Manual source provenance now records `submitted_by_user_id`; migration `0058_manual_job_provenance_owner` backfills historical ownership from user-scoped manual acquisition run keys.
- The same visibility policy now gates new candidate evaluations, Punt creation, and the candidate opportunity feed. Inaccessible jobs return the same `JOB_NOT_FOUND` response to avoid revealing private job existence.
- Candidate opportunity responses expose an allowlist of safe job metadata and only source provenance visible to that candidate.
- Unreviewed manual submissions cannot overwrite the description, shared metadata, or freshness timestamp of a job with non-manual source provenance. The same owner can version their own private manual-only JD when submitting updated content.
- Regression tests cover public/private list and detail access, an approved but operationally unavailable source remaining visible with its health state, pagination/search, direct evaluation of another candidate's private manual job, and protection of shared canonical content.

**Intermediate failures retained**
- Run #1675 failed one Job Pool test because the first implementation did not yet persist submitter ownership on canonical provenance. Ownership persistence and the corresponding migration were added.
- Runs #1676–#1679 failed the migration compatibility gate while the ownership backfill migration was malformed. The migration was rewritten and the corrected migration and schema-parity checks passed.
- Run #1680 failed eight tests and run #1681 failed seven. Follow-up fixes addressed the global FastAPI dependency-override test fixture, same-owner manual JD versioning, test cleanup of provenance rows, and source-provenance setup. Several outbox concurrency/replay assertions also failed during those overlapping workflow runs; the latest exact-head full suite passed those tests. No failed run has been re-labelled as a pass.

**Remaining acceptance gaps**
- Identity-resolution review still needs an authorized, auditable API workflow; ambiguous matches remain review-required and must not be surfaced as canonical jobs.
- The Job Pool filters and sort contract still need a complete crosswalk against every PRD filter/constraint and candidate preference.
- Source permission is enforced for shared visibility. Operational health is exposed separately and does not silently hide an already acquired job. Source run-history/partial-failure reporting and live-source acceptance remain open.
- This batch does not complete the full candidate workflow or authorize frontend integration, merge, or deployment.



### Canonical User/Admin gaps — U1.1 identity and candidate ownership checkpoint (2026-10-17)

**Working branch:** `user-admin-workflows-gaps-fix`  
**Verified code head before this ledger update:** `12f2f9abcdac7e69d2616f753b61d55b08de70cc`  
**CircleCI backend-verification:** PASS on that exact head; the configured job compiles application/migrations, checks a single Alembic head, migrates PostgreSQL 17, checks ORM/schema parity, and runs the full pytest suite.  
**Local execution:** not run in this environment.

**Changes in this checkpoint**
- `tests/unit/test_identity_email_no_merge.py` asserts first sign-in looks up identity by Firebase provider + subject and does not query an existing user by email.
- `tests/unit/test_admin_user_status.py` now explicitly asserts that a legacy unsupported current account status is rejected without mutation, audit creation, or commit.
- Existing account-status tests cover all six distinct transitions among ACTIVE, SUSPENDED and DEACTIVATED, no-op and stale-revision rejection, actor/reason audit fields, and rollback invocation on commit failure.

**Acceptance disposition**
- **S01-B01-T01 — PARTIAL:** Code-level fail-closed tests cover missing, malformed, expired and revoked credentials, unknown provider, and explicit development-only identity fallback. Firebase project/credentials and deployed Google sign-in remain runtime-unverified.
- **S01-B01-T02 — PARTIAL:** Provider-subject mapping, first/repeat/concurrent sign-in and account claim synchronization are present; the new email non-merge regression is included in the full exact-head CI. Target runtime/onboarding behavior remains unverified.
- **S01-B02-T01 — PARTIAL:** The supported three-state transition matrix, invalid current status, no-op/stale revision, and actor/reason audit behavior have code-level test coverage. A complete production read/write gate matrix and deployed behavior are not yet evidenced. The PRD's conceptual deletion lifecycle is not treated as part of the supported Admin status API without a separate product decision.
- **U1-T04 — PARTIAL:** Existing ownership tests were traced across profile/preferences, evidence/claims, imports, CV/artifacts, packages, Job Pool, evaluations/Punts/applications, notifications and support. A consolidated endpoint-to-owner matrix and full private-content/secret log-leak acceptance evidence remain open.

No runtime verification is inferred from CI. No branch, PR, merge, deployment or repository switch was created or performed by this checkpoint.


### Canonical User/Admin gaps — privacy regression evidence update (2026-10-17)

**Code head tested by CircleCI before this audit update:** `e6d7cfc0764b69ceab126460d85ca5827ee1bd39` — backend-verification **PASS**. The configured job runs compilation, Alembic head validation, PostgreSQL 17 migration, ORM/schema parity and the full pytest suite. Local execution was attempted but could not start because this environment cannot resolve `github.com`; no local pytest result is claimed.

**New regression coverage and correction**
- `tests/unit/test_validation_error_privacy.py` uses a synthetic private marker in both rejected input and exception context. The original validation handler serialized Pydantic error details, including rejected input/context; the handler now returns only `type`, `loc` and `msg`. The test asserts the marker and provider-style exception text are absent while useful diagnostics remain.
- `tests/unit/test_sensitive_data_not_logged.py` exercises a rejected synthetic bearer credential and captures application logs, asserting the secret marker is not recorded.
- These tests are included in the exact-head CircleCI suite for the code commit above. The follow-up audit-only commit must receive its own exact-head CI result before its documentation update is considered verified.

**Cross-resource ownership evidence matrix (existing PostgreSQL integration coverage)**

| Resource family / route surface | Ownership boundary under test | Evidence currently identified | Disposition |
| --- | --- | --- | --- |
| Candidate profile and preferences | Authenticated candidate can read/change only their own profile/preferences | `test_candidate_api_postgres.py`, `test_candidate_preferences_postgres.py` | Existing coverage; full route-by-route inventory still needed |
| Evidence and claims | Evidence/claim reads and state transitions are scoped to the owning candidate; foreign IDs are non-disclosing | `test_candidate_evidence_postgres.py`, `test_candidate_evidence_editing_postgres.py`, `test_candidate_evidence_library_postgres.py`, `test_candidate_claim_api_postgres.py`, `test_candidate_claims_postgres.py` | Existing coverage; verify every mutation/list/detail route against the PRD |
| Document uploads/imports/restores | Imported/private documents and derived evidence remain candidate-scoped | `test_document_imports_postgres.py`, `test_document_restore_postgres.py` | Existing coverage; provider/runtime storage ACLs not verified here |
| CV versions, approvals and generated artifacts | CV versions and download/generation operations require owner and applicable approval | `test_candidate_cv_studio_postgres.py` | Existing coverage; deployed object-storage permissions not verified |
| Application packages and package items | Package, artifact, claim-validation and submission operations cannot cross candidate ownership | `test_application_packages_postgres.py`, `test_application_package_readiness_regressions.py` | Existing coverage; verify every nested resource route |
| Candidate Job Pool / private manual jobs | Shared visibility rules apply; manual-only jobs remain submitter-private; inaccessible IDs do not reveal existence | `test_job_pool_postgres.py`, `test_opportunity_service_postgres.py` | Existing coverage; complete list/detail/filter crosswalk remains open |
| Evaluations, Punts and applications | Evaluations and resulting actions are scoped to the candidate; foreign applications/evaluations are denied | `test_candidate_evaluation_postgres.py`, `test_opportunity_service_postgres.py`, `test_application_lifecycle_contract.py` | Existing coverage; full route/action inventory remains open |
| Notifications | Candidate can list/read only their own notification rows | `test_notifications_postgres.py` | Existing coverage |
| Support tickets and related notifications | Candidate/admin views and notification recipients follow ticket ownership and explicit admin grants | `test_support_ticket_notifications_postgres.py`, `test_admin_route_authorization.py` | Existing coverage; complete Admin action matrix remains open |
| Admin governance, source registry and user status | Admin-only routes require grants; mutations are audited and state/revision guarded | `test_admin_governance_contract.py`, `test_admin_governance_postgres.py`, `test_source_registry_admin_postgres.py`, `test_admin_user_status.py` | Existing coverage; deployed authorization configuration remains unverified |

**Logging/privacy evidence limits**
- The middleware and error-handler paths reviewed for this checkpoint do not intentionally log request bodies. The new captured-log test covers rejected bearer-token handling only; it is not proof for every route, worker, provider SDK, audit payload, or production log sink.
- GitHub code-search queries for common logging symbols returned no indexed matches but reported incomplete results, so they are not treated as an exhaustive logging inventory.
- Remaining work for **U1-T04 remains PARTIAL**: finish a route-by-route owner/action inventory including nested-resource and collection endpoints; exercise cross-candidate reads/writes and response non-disclosure for every identified gap; inspect logging paths in workers/providers/audit and test synthetic private markers on both success and failure paths; then record exact-head CI evidence. Production sink/configuration and deployed storage ACLs require a target environment.
- Do not advance to U1.2 while U1.1 acceptance remains unresolved. No merge, deployment, branch creation, or repository switch is authorized by this update.

### Canonical User/Admin gaps — route inventory and Admin guard coverage (2026-10-17)

**Scope and method:** inspected the registered API router and route handlers on `user-admin-workflows-gaps-fix` at the pre-change head `ccbca1a6090a284817efa214a641c424b26e3d47`. This is a source/test audit, not a claim of live runtime verification.

**Route families inventoried**
- Candidate profile, profile provenance, preferences, evidence and claims: `/candidate/profile`, `/candidate/profile/provenance`, `/candidate/evidence` (collection, item, transitions), `/candidate/claims` (collection, item, transitions), and candidate preferences.
- Job discovery and review: discovery settings/missions, candidate Job Pool list/detail, manual job ingestion, authenticated email-alert parsing, and the separately allowlisted Admin job-requirement review queue/history/decision routes.
- Candidate opportunity lifecycle: opportunity feed, evaluations and overrides, Punts, applications and application events.
- Private documents and work products: document imports (upload, parse, extraction, preview, conflicts, confirm, save, history/list/detail), restore, CV list/master/variants/versions/approval/document generation/download, and application-package list/create/revision/detail/claim validation/review/documents/download.
- Candidate support and notifications: support ticket collection/detail/messages/status and notification collection/read/email preferences.
- Admin governance and operations: AI execution/audit reads, account status, configuration draft/publish/rollback, decision-rule preview, grants, source registry and connector configuration/health/acquisition, operations summary/work items/outbox, and dead-event retry/recovery; plus Admin support ticket collection/detail/messages/status/assignment.
- Health endpoint: `GET /api/v1/health`.

**Ownership and authorization findings**
- Candidate resource handlers reviewed pass the authenticated `principal.user_id` to their resource/service operations; the reviewed services scope candidate profile, CV, package, import, evaluation, Punt and application reads/writes to the owning candidate. Foreign IDs are expected to use the resource-specific not-found behavior where the service defines it.
- Existing integration tests already exercise cross-candidate denial/isolation for profile/evidence, evidence/claims, career pathways, imports/history, document restore, CV versions/downloads, application packages and nested artifacts, Job Pool/manual-only jobs, evaluations/overrides/Punts/applications, notifications, and support tickets. Relevant evidence is listed in the U1.1 ownership matrix above and in the named test functions.
- Admin governance handlers and Admin support handlers currently call `_require_admin`; requirement-review routes are intentionally separate and use the configured trusted-identity plus reviewer-allowlist guard. The inspected code did not establish a confirmed authorization bypass, so no application behavior was changed on speculation.
- A concrete **test coverage gap** was confirmed: the existing request-level Admin authorization tests exercised only a subset of the protected route surface. Added a static route-contract regression that enumerates every registered route in the Admin governance and Admin support modules and fails if a handler omits the shared `_require_admin` guard. Existing request-level tests continue to verify actual 403 behavior for representative reads and mutations.

**Limits and follow-up**
- The new route-contract test verifies guard presence, not every route's runtime denial response or every candidate resource's nested ownership semantics. Existing cross-candidate integration tests provide behavioral coverage for the named resource families; the remaining endpoint-by-endpoint gap review must add a targeted behavioral test whenever inspection identifies an untested ownership-sensitive action.
- No production credentials, Firebase configuration, object-storage ACLs, worker/provider logs, or deployed log sinks were inspected by this route-inventory task. Those remain separate U1.1 acceptance work.
- Full tests must run in exact-head CI. Local pytest remains unavailable in this environment due the previously observed GitHub DNS failure. Do not advance to U1.2 until the remaining U1.1 account lifecycle and private-data logging acceptance work is resolved. No branch creation, PR creation, merge, deployment, or repository switch was performed.

### Canonical User/Admin gaps — worker exception logging privacy audit (2026-10-17)

**Finding confirmed:** Python `logger.exception(...)` automatically attaches the active traceback. Even when the formatted message includes only `type(exc).__name__`, the traceback includes the exception string, which can contain private provider responses, submitted content, database values, or credentials. This is a concrete logging privacy risk, distinct from the earlier API validation/authentication checks.

**Correction**
- Replaced traceback-producing `logger.exception` calls with structured `logger.error` messages containing only the resource/mission identifier and exception class in:
  - `app/workers/discovery_mission_worker.py`
  - `app/workers/discovery_evaluation_worker.py`
  - `app/workers/job_verification_worker.py`
- Worker state/error classification remains based on the exception class. Full traceback text is deliberately not emitted from these catches because exception strings are not a safe logging boundary.
- Added a captured-log regression test that injects a synthetic private marker into a discovery-evaluation exception, exercises the worker failure path, verifies the worker records the error class, and asserts the marker is absent from application logs.

**Audit limits:** this covers the identified worker traceback paths, not every third-party SDK, connector, database driver, structured log sink, subprocess, or deployment logging configuration. Other worker outputs were inspected for the listed logging calls; normal cycle/batch logs contain counters/outcomes, and CLI JSON output is intended for operational summaries. Continue searching provider/audit paths and add marker-based tests where exception or payload content can reach logs. Exact-head CI must pass before treating this correction as verified; no local pytest result is claimed.

### Canonical User/Admin gaps — PRD account-field crosswalk (2026-10-17)

**Source:** PRD + Technical Specification v1.5, §39.2 account model (internal ID; external identity-provider reference; display name/email where supplied; photo URL where available; account status; onboarding status; created/updated timestamps; last-login information where available and appropriate). The PRD's User Records section also names email-verification status as an Admin directory filter.

| PRD account field / behavior | Current backend evidence | Test evidence / disposition |
| --- | --- | --- |
| Internal account ID | `User.id` UUID primary key | First-sign-in provisioning and identity mapping tests |
| External identity reference | `Identity(provider, provider_subject)`, unique on provider + subject; Firebase UID lookup is the identity boundary | `test_firebase_identity_provider.py`; `test_identity_email_no_merge.py`; first-sign-in race reload coverage |
| Display name, email, photo URL | Valid present Firebase claims synchronize to `User`; missing/invalid claims do not clear saved values; photo URL must be HTTPS | `test_firebase_identity_provider.py` claim-sync and missing-claims tests |
| Email verification | Persisted `User.email_verified` from a verified boolean claim | First-sign-in and claim-sync tests |
| Account status | Persisted supported states ACTIVE/SUSPENDED/DEACTIVATED; inactive users are rejected by the Firebase identity adapter; Admin status mutation is revision-checked and audited | `test_firebase_identity_provider.py`; `test_admin_user_status.py` transition, stale revision, invalid state, no-op, audit and rollback tests |
| Onboarding status | Persisted `User.onboarding_status`; new account starts at `NOT_STARTED`; Admin status response includes it | First-sign-in provisioning test. The PRD names the field but does not define a separate onboarding transition contract in §39.2; do not invent one here |
| Created/updated timestamps | Persisted on `User`; updated timestamp changes through ORM updates and explicit status mutation | Model/schema parity and status response tests; not a full runtime timestamp audit |
| Last login | `auth_time` from verified Firebase claims maps to UTC `last_login_at`; absent claim does not erase it; older auth times do not move it backwards | `test_firebase_identity_provider.py` tests first sign-in, sync, missing claims and monotonic last-login |
| Admin status change | Handler checks Admin grant, locks target, checks expected revision/current state, rejects no-op, updates status/revision and commits actor/reason/previous/new state audit together | `test_admin_user_status.py` and representative request-level denial tests |

**Disposition:** The model fields and core claim/status invariants above have existing code-level evidence; no new account-field implementation was justified by the PRD crosswalk. **S01-B01-T02 and S01-B02-T01 remain PARTIAL** because this does not prove deployed Firebase credentials/sign-in behavior, a complete production read/write gate matrix across every private resource, or the full Admin User Records capability set in PRD §40. The current Admin API has account-status mutation but not the complete directory/search/filter/sort/activity/overview/read-only impersonation surface specified in §40 and the related scopes. Those are Admin workflow implementation items, not a reason to invent account-field semantics. Exact-head CI result after the worker privacy fix: CircleCI `backend-verification` **PASS** on `5bb832674c7745a25c8aa77b217d7361529f95d6`; this confirms the configured suite at that commit, not target-environment runtime behavior.

### Canonical User/Admin gaps — private document cleanup logging follow-up (2026-10-17)

A broader pass over candidate document/CV handling found seven additional `logger.exception` calls in cleanup-failure branches: invalid stored CV/document/import/restore objects and orphaned object cleanup after metadata failure. These calls would attach the provider/storage traceback, potentially including private object references or backend exception content.

**Correction and regression coverage**
- Replaced those seven traceback-producing calls with error messages that contain only the cleanup operation and exception class in `app/application/cv_studio/documents.py`, `app/application/documents/imports.py`, `app/application/documents/restore.py`, and `app/application/documents/service.py`.
- Added a unit contract asserting that these private document/CV modules do not use `logger.exception`. The captured-log worker test above separately proves that a synthetic exception marker is not emitted from the worker failure path.
- User-facing error handling and cleanup/re-raise behavior are unchanged; only logging verbosity is reduced at these sensitive boundaries.

**Remaining limit:** this is a targeted correction of identified unsafe traceback sites, not a mathematical proof that no private content can reach any configured log sink. Continue the audit for other modules and provider SDKs; exact-head CI must pass. Local tests have not been run in this environment.

### Canonical User/Admin gaps — endpoint-pattern ownership evidence ledger (2026-10-17)

This ledger names the registered path patterns and the strongest existing test file found during this pass. A test file reference is evidence that the resource family is exercised; it does not imply every listed method has a dedicated cross-candidate HTTP test.

| Registered route pattern(s) | Boundary / actions | Existing evidence | Coverage disposition |
| --- | --- | --- | --- |
| `GET,PATCH /api/v1/candidate/profile`; `GET /api/v1/candidate/profile/provenance` | Candidate identity is derived from the verified principal; profile and provenance reads/writes use that account | `test_candidate_api_postgres.py` | Profile journey, provenance collection isolation (other candidate receives an empty list), and cross-user denial are explicitly covered |
| `GET,POST /api/v1/candidate/evidence`; `GET,PATCH /api/v1/candidate/evidence/{evidence_id}`; `POST /api/v1/candidate/evidence/{evidence_id}/{submit,approve,reject,archive}` | Owner-scoped collection, detail, edit and transitions | `test_candidate_api_postgres.py`, `test_candidate_evidence_postgres.py`, `test_candidate_evidence_editing_postgres.py`, `test_candidate_evidence_library_postgres.py` | Service and API journey coverage; transitions and foreign-owner behavior covered at resource/service boundaries |
| `GET,POST /api/v1/candidate/claims`; `PATCH /api/v1/candidate/claims/{claim_id}`; `POST /api/v1/candidate/claims/{claim_id}/{submit,approve,reject,archive}` | Candidate-owned claims and evidence links | `test_candidate_claim_api_postgres.py`, `test_candidate_claims_postgres.py` | Cross-candidate isolation and foreign evidence-link denial covered |
| `GET,PUT /api/v1/candidate/preferences` | Per-candidate preferences | `test_candidate_preferences_postgres.py` | Persistence, validation and candidate scoping covered; no separate write-permission bypass identified |
| `GET,POST /api/v1/candidate/career-pathways`; `POST /api/v1/candidate/career-pathways/suggestions`; `GET,PATCH /api/v1/candidate/career-pathways/{pathway_id}`; `POST .../{pathway_id}/{approve,reject,retire}`; `GET .../{pathway_id}/events` | Owner-scoped pathway CRUD, decisions, retirement and history | `test_career_pathways_postgres.py` | Added an explicit foreign-candidate history/events request asserting the same non-disclosing `404 CAREER_PATHWAY_NOT_FOUND` contract as detail; candidate isolation and foreign evidence reference remain covered |
| `GET,PUT /api/v1/candidate/discovery/settings`; `GET,POST /api/v1/candidate/discovery/missions` | Candidate settings and mission ownership | `test_discovery_api_postgres.py`, `test_discovery_missions_postgres.py` | Other-candidate mission list is empty, settings resolve to the authenticated candidate, and foreign/disabled settings cannot be scheduled; detail-action inventory is not applicable because no mission detail route is registered |
| `GET /api/v1/candidate/jobs/pool`; `GET /api/v1/candidate/jobs/pool/{job_id}`; `POST /api/v1/candidate/jobs/manual-ingestion` | Shared job visibility and private manual-submission ownership | `test_job_pool_postgres.py`, `test_manual_job_ingestion_postgres.py`, `test_opportunity_service_postgres.py` | Public/private visibility, manual submitter isolation and direct private-job access covered |
| `GET /api/v1/candidate/opportunities`; `POST,GET /api/v1/candidate/evaluations`; `POST,GET /api/v1/candidate/evaluations/{evaluation_id}/overrides`; `POST,GET /api/v1/candidate/punts`; `POST,GET /api/v1/candidate/applications`; `POST /api/v1/candidate/applications/{application_id}/transition`; `GET .../{application_id}/events` | Evaluation, override, Punt, application and event history scoped to candidate | `test_candidate_evaluation_postgres.py`, `test_opportunity_service_postgres.py`, `test_application_lifecycle_contract.py`, `test_opportunity_service_security.py` | Foreign evaluation/Punt/application access and override ownership covered across service/API tests; not every action has a separate route-level adversarial test |
| `POST /api/v1/candidate/document-imports`; `POST .../{import_id}/{parse,extract-fields,identify-conflicts,confirm,save}`; `GET .../{import_id}/preview`; `GET /api/v1/candidate/document-imports`; `GET .../{import_id}` | Candidate-owned uploads, previews, extraction, history and writes | `test_document_imports_postgres.py` | Explicit candidate-scope tests cover foreign parse/extract/detail and history; API upload/preview/confirm/save paths covered |
| `POST /api/v1/candidate/documents/{document_id}/restore` | Restore only a candidate-owned private document | `test_document_restore_postgres.py` | Service test verifies ownership and non-disclosing not-found; route delegates principal user ID to service |
| `GET /api/v1/candidate/cvs`; `POST /api/v1/candidate/cvs/master/regenerate`; `POST /api/v1/candidate/cvs/variants`; `GET /api/v1/candidate/cvs/{cv_key}`; `POST .../{cv_key}/versions`; `POST .../{cv_key}/versions/{version_number}/approve`; `POST .../documents`; `GET .../documents/{document_id}/download` | Candidate-owned CV versions, approvals, generated private artifacts and download URLs | `test_candidate_cv_studio_postgres.py` | Version ownership, approved-version constraints and scoped downloads covered |
| `POST,GET /api/v1/candidate/applications/{application_id}/packages`; `POST .../{package_id}/revisions`; `GET .../{package_id}`; `POST .../{package_id}/{validate-claims,prepare-review,review-decision,documents}`; `GET .../{package_id}/documents/{document_id}/download` | Nested package/item/artifact access and review mutations remain application/candidate-scoped | `test_application_packages_postgres.py`, `test_application_package_readiness_regressions.py` | Cross-candidate package list/detail/review/validation and document download denials covered |
| `GET /api/v1/candidate/notifications`; `POST /api/v1/candidate/notifications/{notification_id}/read`; `GET,PUT /api/v1/candidate/notifications/email-preferences` | Candidate-owned notification rows and email preferences | `test_notifications_postgres.py` | Cross-user notification read denial and own-row listing covered |
| `POST,GET /api/v1/candidate/support/tickets`; `GET /api/v1/candidate/support/tickets/{ticket_id}`; `POST .../{ticket_id}/messages`; `PATCH .../{ticket_id}/status` | Candidate-owned support tickets and messages | `test_support_ticket_notifications_postgres.py` | Cross-user ticket read denial, assignment, status and notifications covered |
| `GET /api/v1/admin/support/tickets`; `GET .../{ticket_id}`; `POST .../{ticket_id}/messages`; `PATCH .../{ticket_id}/{status,assignment}` | Admin grant required; Admin actor remains distinct from candidate | `test_support_ticket_notifications_postgres.py`, `test_admin_route_authorization.py` | Representative request-level denial plus new all-route guard-presence contract |
| `/api/v1/admin/{ai-executions,audit,users/{user_id}/status,configs,grants,sources,operations/...}` and nested source connector/configuration/acquisition/outbox actions | Admin grant required for governance, account status, source operations and work recovery; mutations should audit | `test_admin_governance_contract.py`, `test_admin_governance_postgres.py`, `test_source_registry_admin_postgres.py`, `test_admin_user_status.py`, `test_admin_route_authorization.py` | Shared guard presence is now contract-tested for every registered governance/support handler; request-level 403 behavior remains sampled, not repeated for every action |
| `/api/v1/admin/job-requirements`, `/{requirement_id}/history`, `/{requirement_id}/decision` | Separate trusted-identity plus explicit reviewer allowlist; not the generic Admin-grant route family | `test_requirement_review_route_authorization.py`, `test_requirement_review_auth_guard.py`, `test_requirement_review_policy.py` | Fail-closed reviewer authorization and decision policy covered |
| `POST /api/v1/email/parse-job-alert`; `POST /api/v1/ai/generate`; `GET /api/v1/health` | Authenticated parsing/generation and non-private health response | `test_email_parser_api.py`, AI task/provider tests, health route source | Parsing is stateless and does not persist candidate resources; AI route uses the principal; health is intentionally public |

**Inventory disposition:** route registration and the resource-to-test mapping are now explicit. This is not a claim that every row has independent adversarial HTTP coverage: the table calls out the remaining per-action testing gaps rather than treating family-level tests as proof. No untested cross-candidate exposure was confirmed in the inspected handlers/services during this pass. Any newly identified endpoint-level gap should be closed with a focused behavioral test before U1-T04 is marked complete.

**Exact-head evidence:** the code/test commit `7eb87de7a2247d691ec77cee2d95296d9983a823` received CircleCI `backend-verification` **PASS** (the configured job includes compilation, Alembic head validation, PostgreSQL migration, ORM/schema parity and the full pytest suite). The current documentation-only update must receive its own exact-head CI result before it is considered verified. Local pytest has not been run here.

### U1-T04 focused route regression — career pathway history ownership

**Change:** `tests/integration/test_career_pathways_postgres.py` now checks the registered `GET /api/v1/candidate/career-pathways/{pathway_id}/events` route using a second candidate after the owner creates and edits the pathway. The foreign request must return `404 CAREER_PATHWAY_NOT_FOUND`, matching detail access and avoiding history disclosure.

**CI evidence before this test addition:** CircleCI backend-verification passed on `ba066ddd7c1421ed6b4c93f86044835165110059`. The test addition requires a new exact-head CI pass; local pytest has not been run here.

### U1-T04 focused route regression — authentication dependency coverage

Added a route-registration contract to `tests/unit/test_admin_route_authorization.py`: every registered `/api/v1/candidate/*` and `/api/v1/admin/*` route must declare the shared `get_principal` dependency. This complements the Admin guard-presence contract and behavioral cross-candidate tests. It checks that private route authentication cannot be accidentally dropped during route edits; it does not replace the Firebase inactive-account/provider tests or per-resource ownership assertions.

**CI evidence before this addition:** CircleCI backend-verification passed on `ba066ddd7c1421ed6b4c93f86044835165110059`; subsequent pathway-history test commit `29033f2328c370bdb2a78400183760de3e3514e8` is awaiting exact-head CI at the time of this audit note. The new route dependency contract requires a new exact-head CI result. Local pytest has not been run here.

### Canonical User/Admin gaps — exact-head verification checkpoint (2026-10-17)

**Code/test head:** `381f7c64b77f0b61f05ea1271fe162c40bf5e3c0`  
**CircleCI `backend-verification`: PASS** on that exact head: https://app.circleci.com/workflow/4150bd8e-dfee-403b-93cb-532652e45170/job/46499811-5951-4a25-8c4c-13a5fc4ce6f0

This exact-head run includes the worker/document log-privacy corrections, synthetic-marker and private-document logging regressions, complete private-route authentication dependency contract, Admin guard-presence contract, and foreign-candidate career-pathway history denial. The configured workflow compiles application/migrations, checks the Alembic head, migrates PostgreSQL, checks ORM/schema parity and runs the full pytest suite. No local pytest result is claimed.

**Current disposition:** route inventory and evidence mapping are recorded; identified unsafe traceback logging sites were corrected; the account-field crosswalk is recorded. **U1.1 remains PARTIAL** because deployed Firebase/sign-in behavior, production log sinks/object-storage ACLs, and complete behavioral acceptance of every Admin/User Records capability are not established by repository CI. Do not advance to U1.2 until the remaining U1.1 acceptance work is resolved or a specific runtime blocker is recorded. No branch, PR, merge, deployment, or repository switch was created or performed.

### U1.1 PostgreSQL lifecycle integration — Admin suspension revokes candidate authentication

Added `test_admin_suspension_is_audited_and_blocks_subsequent_candidate_authentication` to `tests/integration/test_admin_governance_postgres.py`. With a real PostgreSQL-backed test session, it creates an Admin grant and Firebase UID mapping, suspends the candidate through the protected Admin HTTP endpoint, verifies persisted status/revision and the actor/reason/request-ID audit event, then confirms Firebase identity resolution refuses the now-suspended account.

This closes a previously indirect test seam between the Admin lifecycle write and candidate authentication read gate. It remains a repository integration test, not a deployed Firebase runtime test; the provider token verifier is injected, while local account mapping/status are exercised against PostgreSQL.

**Pre-change evidence:** exact-head CircleCI passed at `ff5ce29a2e2f62787a25be93bcd8f3693c83f083`. This new integration test requires its own exact-head CI result. Local pytest is not available from the current execution environment, so no local result is claimed.

### U1.1 HTTP authentication-gate regression — inactive candidate reads

Added a parameterized HTTP test in `tests/unit/test_sensitive_data_not_logged.py` for the candidate profile and notifications list routes. When the configured identity provider rejects the local account as inactive, both routes must return the same generic `401` authentication response before reaching their handlers. This complements the PostgreSQL lifecycle integration that proves Admin suspension is persisted/audited and subsequent Firebase identity mapping refuses the account, plus the route-registration contract requiring `get_principal` on every candidate/Admin route.

**Exact-head CI evidence before this addition:** `e9bcf202a089c8aa74125ce145067827ef11be92` — CircleCI backend-verification PASS. This HTTP regression requires a new exact-head run. Production Firebase credentials and live provider behavior remain runtime-unverified.

### U1.1 continued execution checkpoint — latest verified code/test head

**Code/test head:** `8f667ca12829bdd2e26d0b8bab59f513a90e5f0a`  
**CircleCI backend-verification:** PASS — https://app.circleci.com/workflow/e80f84a9-b892-4191-9c74-008496fbbaba/job/a2bb9055-f6ed-471b-837e-a6e84c663f1e

The exact-head run includes:
- The PostgreSQL integration that suspends a candidate through the Admin API, verifies the persisted state and actor/reason/request-ID audit record, and confirms subsequent Firebase UID mapping refuses the inactive account.
- HTTP-level inactive-account denial on candidate profile and notification reads.
- Previously added private-route authentication contracts, Admin guard contracts, pathway-history non-disclosure, worker/document log privacy tests and error-response sanitization.

**U1.1 acceptance remains PARTIAL.** These tests verify repository behavior, not deployed Firebase credentials/sign-in, production object-storage ACLs, external log sinks, or a complete runtime User Records capability. The PRD’s broader User Records directory/detail and impersonation requirements remain in the later Admin workflow sequence; they are not silently treated as implemented by the current account-status endpoint.

**Next eligible U1.1 work:** continue the remaining endpoint-by-endpoint ownership and private-data audit, add focused adversarial regressions where code inspection reveals a specific untested boundary, and document unavailable runtime gates with explicit owners/evidence needed. Do not begin U1.2 while U1.1 is partial. Local pytest was not run in this environment.


### U1.1 checkpoint — pathway mutation isolation and explicit runtime blockers (2026-10-17)

**Code/test head:** `b6bf211759371f25e1601bf6065fdcf307394b3a`  
**CircleCI backend-verification:** PASS — https://app.circleci.com/workflow/9e075c31-8233-4218-b236-116833577a28/job/46a6454c-db88-4653-8c86-72acbd3eae2a

Extended `test_pathway_crud_revision_audit_and_candidate_isolation` so a foreign candidate attempts PATCH, approve, reject and retire against another candidate's pathway. Every operation must return the same non-disclosing `404 CAREER_PATHWAY_NOT_FOUND`. The test then switches back to the owner and proves the pathway title/revision and `CREATED`/ `EDITED` event history remain unchanged. This checks both access denial and absence of mutation/audit side effects.

**U1.1 repository evidence status:** The account lifecycle, authentication-gate, route-guard, error/log privacy and cross-resource test inventory is recorded. Exact-head CI is green. The inventory is not misrepresented as an exhaustive independent adversarial HTTP test for every route/action.

**Explicit residual blockers (do not treat CI as production evidence):**
- **Firebase runtime owner / deployment operator:** supply the deployed project/configuration and verify real Google sign-in, valid token acceptance, and missing/malformed/expired/revoked token rejection against the deployed environment.
- **Storage/runtime owner:** provide deployed object-storage bucket policy/ACL evidence proving candidate-private documents and generated artifacts cannot be read cross-candidate or anonymously.
- **Operations/logging owner:** inspect configured production application, worker, provider and infrastructure log sinks using synthetic secret markers; prove private candidate content, credentials and tokens are not emitted.
- **Product/engineering owner:** resolve PRD-defined onboarding and last-login field semantics where the PRD is not specific enough to infer transitions; do not invent semantics.
- **Engineering verification:** continue closing the remaining route/action-specific ownership matrix gaps. The current route-to-test map distinguishes shared auth-guard contracts and representative behavioral tests from independent per-action isolation tests.

U1.1 remains **PARTIAL / runtime-gated**, not COMPLETE. The above blockers are now explicit and are outside what repository CI can establish. Per the canonical register, proceed with U1.2 while carrying these named residual gates forward; do not claim production identity, storage or logging verification. Local pytest was not run.


### U1.2 / PR42-B1-T4 — immediate evaluation invalidation on evidence and claim state changes

**Code/test head:** `ce78a09fae09fbf28b976ace65fa9edbb2a1c55a`  
**CircleCI backend-verification:** PASS — https://app.circleci.com/workflow/ee654197-fa5e-4b54-a324-ef50085d07c0/job/af0b16df-066d-4e1b-8dd2-19f1c514a400

**Change:** `CandidateService` now marks all CURRENT evaluations for the affected candidate STALE in the same database transaction whenever evidence or claim state transitions. This prevents an evaluation from continuing to be represented as current between a verification-state change and a later list/Punt request. Repeated transitions remain safe because the update targets CURRENT evaluations only.

**Regression:** renamed and strengthened the PostgreSQL evaluation test to archive previously approved evidence after evaluation and assert the persisted evaluation is immediately STALE without first calling the evaluation listing endpoint. The existing listing/Punt snapshot checks remain defense in depth.

**Scope status:** this closes the immediate invalidation seam for evidence/claim state transitions. The broader U1.2 acceptance remains PARTIAL: continue checking stale snapshots, expired/pending/rejected evidence, Punt qualification, all nine evidence-match states, deterministic blocker dimensions and Review Queue isolation. Local pytest was not run; exact-head CircleCI is the verification evidence.


### U1.2 / S07 — evidence validity regression expansion

**Code/test head:** `4d0c871a83d36857a1d243cc5b7fe039b6335750`  
**CircleCI backend-verification:** PASS — https://app.circleci.com/workflow/dd55ac84-4851-44e1-a9a9-05b3e626eb71/job/4d41dded-fb13-45e4-962a-8d8ed7f64e09

**Regression:** expanded `test_evaluation_excludes_future_and_expired_evidence_and_claims` to include evidence that is expired by `expires_at`, still `PENDING_REVIEW`, or `REJECTED`. The evaluation snapshot must exclude each of those records from `approved_evidence`; existing future-effective and expired-effective-to assertions remain.

**Scope status:** the latest exact-head backend-verification CI passed. Existing integration coverage also exercises evaluation staleness after input changes, Punt rechecks for job lifecycle/freshness, application-route verification and substantive JD completeness, plus candidate-scoped feed filters/pagination and stale-description handling. U1.2 remains **PARTIAL**: the full PRD decision-dimension crosswalk and Review Queue explainability/action acceptance still require a consolidated audit; no production runtime acceptance is implied. Local pytest was not run.


### U1.2 / S07-B02-T03 — candidate evaluation detail ownership

**Code/test head:** `794d7aba1f24b5df49c75f4c4ee5d48d8ce89727`  
**CircleCI backend-verification:** PASS — https://app.circleci.com/workflow/6f215491-a5e5-48a5-b373-93894febdbb4/job/22eb6637-7c82-48d8-bae5-cc2eb84180e6

**Change:** added `GET /api/v1/candidate/evaluations/{evaluation_id}`. The service scopes lookup to the authenticated candidate, returns the same non-disclosing 404 for absent/foreign evaluations, verifies that the candidate can still access the associated job, and marks a CURRENT evaluation STALE when its persisted inputs no longer match.

**Regression:** API integration coverage verifies the owner receives the structured evaluation including evidence summary, explanation and input snapshot, while another candidate's evaluation returns `404 EVALUATION_NOT_FOUND`.

**Scope status:** the candidate evaluation detail route is now present with ownership and stale-snapshot checks. U1.2 remains **PARTIAL** until the PRD dimension crosswalk and remaining Review Queue explainability/action acceptance are reconciled and verified. Local pytest was not run.


### U1.2 / S07-B02-T03 — stale evaluation detail regression

**Code/test head:** `2d80ebbdfae9595365ce7204e9fc4caccf712e1d`  
**CircleCI backend-verification:** PASS — https://app.circleci.com/workflow/b55ac101-2ab4-457e-b8c2-2858759a400c/job/a6a6c14c-c967-4e18-bc72-9e11e8b164ce

**Regression:** the evaluation-detail integration test now mutates canonical job facts without incrementing the revision and confirms a subsequent detail read returns `validity_status=STALE`. The same test checks foreign-candidate access remains a non-disclosing 404.

**Scope status:** detail lookup, candidate ownership, explainability payload and stale-snapshot handling are covered at the API/service boundary. Review Queue acceptance is still **PARTIAL** pending the remaining dimension/explanation crosswalk and confirmation that the filtered feed and override workflow satisfy the PRD’s human-review use cases. Local pytest was not run.


### U1.2 / S07-B02 — PRD dimension and explanation crosswalk

**Code/test head:** `5eb54f74f201dfdb538cd255a7d687d05925d685`  
**CircleCI backend-verification:** PASS — https://app.circleci.com/workflow/c6d3ba67-989f-4d63-9f69-5337b361e2e9/job/d5c98621-d337-4374-8a91-86c9db45dd70

**Regression:** the semantic-policy dimension test now explicitly asserts the presence of each PRD matching dimension: core-role fit, domain fit, technical fit, seniority fit, education fit, location/work authorization, preference fit, application accessibility, job freshness and evidence quality/completeness. The nine-state table-driven test now verifies that the supplied rationale is retained and that unsupported states appear in the correct unmet or verification explanation collections rather than being silently treated as supported.

**Scope status:** exact-head backend-verification CI passed. This strengthens the deterministic policy contract; it does not by itself prove every decision path, human Review Queue use case or production runtime gate. U1.2 remains **PARTIAL** pending the remaining Punt qualification and Review Queue acceptance crosswalk. Local pytest was not run.


### U1.2 / S07-B02-T04 — Punt denial after requirement-review changes

**Code/test head:** `1465370078272ea8ff3a65822659836447cdfa4f`  
**CircleCI backend-verification:** PASS — https://app.circleci.com/workflow/fb65b7e2-f6e5-40e3-a2d3-923b76aa9e11/job/3217436d-4961-4de4-8848-1d91913d8039

**Regression:** strengthened the versioned-requirement-review integration test to attempt Punt creation immediately after a requirement is reviewed/confirmed against a previously current evaluation. The Punt request must fail with `EVALUATION_STALE`, persist the evaluation as `STALE`, and create no Punt. The test still confirms the candidate evaluation listing exposes the stale state.

**Scope status:** exact-head CI passed for this gate. U1.2 remains **PARTIAL** until the remaining PRD-to-policy and Review Queue acceptance is fully reconciled. Local pytest was not run.


### U1.2 / S07-B02-T03 — opportunity feed filter contract

**Code/test head:** `280bef08458b3b06d93d442c7eb88b34fc3a6d8b`  
**CircleCI backend-verification:** PASS — https://app.circleci.com/workflow/d14e6c01-6846-4127-a7d2-0152280309b2/job/e7d39c53-975b-42b4-b69d-8f15a3ee057c

**Regression:** expanded the candidate feed integration test to verify `freshness_status`, `lifecycle_status`, `query`, and `validity_status` filters in addition to outcome filtering, pagination, totals, and candidate isolation. Each added filter is asserted against the expected candidate-owned evaluation.

**Scope status:** exact-head CI passed. The feed’s list/filter/pagination contract and the new detail route have coverage; remaining U1.2 work is a final acceptance reconciliation for all four decision outcomes, Punt qualification gates, and whether any PRD-required Review Queue behavior remains absent. Local pytest was not run.


### U1.2 — evaluation, verification, Review Queue and Punt acceptance closure

**Code/test head:** `57c52e6a5c123488e2e56e080788d9641bdc3de6`  
**CircleCI backend-verification:** PASS — https://app.circleci.com/workflow/91e32338-536f-4bf7-b057-92db76f15bc2/job/a2851381-c9a6-4a78-9c9d-87b3103fb9a1

**Acceptance reconciliation:**

- **Evidence verification and invalidation:** evaluation input construction excludes future-effective, expired, pending-review and rejected evidence. Expired `expires_at` is covered separately from `effective_to`. Evidence/claim state transitions invalidate CURRENT evaluations in the same transaction; currentness checks also compare evidence/claim snapshots and policy, job, description, requirement and route provenance.
- **Deterministic evaluation:** all nine PRD evidence-match states and an unknown state are table-driven. Rationale preservation, supported evidence references, unmet/verification explanations, separate PRD dimensions and deterministic blocker precedence are covered. Unsupported, contradicted or unversioned semantic results remain non-qualifying.
- **Review Queue candidate surface:** latest-evaluation feed tests cover outcome, validity, freshness, lifecycle and text filters, pagination, totals and candidate isolation. Evaluation detail now has an explicit candidate-owned route; it checks current job visibility and marks changed snapshots STALE. Tests cover explanation/evidence-summary payloads, non-disclosing foreign-evaluation denial, stale detail reads and append-only candidate overrides that cannot bypass hard blockers.
- **Punt qualification:** integration tests cover stale snapshots after job, preference and requirement-review changes; lifecycle/freshness; missing or changed application-route verification; incomplete/substantively invalid descriptions; hard blockers and contradicted mandatory requirements; plus concurrent qualification producing one Punt with evaluation associations preserved.

**Status: COMPLETE for the repository-verifiable U1.2 acceptance gates.** Exact-head backend-verification CI passed. Local pytest was not run. This closure does not upgrade U1.1’s separately recorded Firebase production sign-in, storage ACL, log-sink and account-semantics gates; those remain explicit external blockers. No live-provider or production-runtime acceptance is claimed.

**Next canonical work:** U2.1 — source permission review/enforcement and truthful source health/acquisition outcomes. Continue backend-only on this branch.


### U2.1 / S04-B03-T01–T02 — permission revocation and truthful source observation

**Code/test head:** `606e8df8bb087cfca0305611e82e7b24dfde1ec1`  
**CircleCI backend-verification:** PASS — https://app.circleci.com/workflow/a432c6b4-33e4-4a14-b1ef-1a6bd20c9c33/job/440541fd-5c4b-4458-8184-c0e7e29eb83b

**Regression:** source-governance integration coverage now revokes a previously approved source by changing permission review to `REJECTED`. The source becomes `RESTRICTED`, subsequent automated acquisition is blocked before a run is created, and the permission-change/block events are audited. Existing tests cover pending-review denial, explicit approval, health-check outcomes, all-source discovery failure, partial success, and restricted/unavailable outcomes at the discovery/API boundary.

**Correctness fix:** stale acquisition-lease recovery no longer overwrites `Source.last_observed_at` with the recovery timestamp. A worker-loss failure updates operational state/error but preserves the last successful source observation. The PostgreSQL regression asserts this invariant.

**Status:** repository-verifiable enforcement and failure-truthfulness checks are covered by exact-head CI. U2.1 remains **PARTIAL** for source-by-source verification of actual acquisition method, applicable terms/permissions and rate/scheduling limits, and deployed/live source health. The repository cannot establish those facts for external sources without the relevant approved source records and runtime checks; no such production verification is claimed.

**Next canonical work:** proceed to the next U2 acquisition/ingestion gap, keeping unresolved source-specific permission and live-runtime checks explicit.


### U2.1 / S10-B02-T01 — persisted acquisition run and downstream outcome contract

**Code/test head:** `01fc50512f994fbdb96ca8ddb1594c35ceb16f82`  
**CircleCI backend-verification:** PASS — https://app.circleci.com/workflow/93b7e8c3-1db8-4980-afa4-2e31827b2544/job/0ddfbd8d-5bc3-4191-9c30-e9ad0b8e74d5

**Change:** source acquisition-run list and detail responses now expose persisted `error_category`, `failed_item_count`, `failure_categories`, and `retry_after_seconds` separately from the safe run `error_summary`. The bounded list response also includes a per-run downstream-processing summary; detail retains the same counts.

**Regression:** the PostgreSQL admin integration test seeds a recovered failed run with distinct acquisition, duplicate, failure and retry metrics, associates a dead canonicalization outbox event, then asserts list/detail preserve each metric and downstream failed count without exposing raw payloads.

**Scope status:** S10-B02-T01’s repository-verifiable history/count contract is covered by exact-head CI. U2.1 remains **PARTIAL** for source-by-source permission/method/terms/rate-limit review and live/deployed health verification. Proceed to U2.2 without treating those external gates as passed.


### U2.2 / S05-B01-T01 — Job Pool filter and stable-sort contract

**Code/test head:** `143dcb83f7afd91f4f529f75a0d2288e63d8b8e8`  
**CircleCI backend-verification:** PASS — https://app.circleci.com/workflow/fd7f4e1d-70f0-4ad8-8d75-d2424160b518/job/20548670-767d-427f-818d-98f4818a3f2c

**Change:** the candidate Job Pool now supports explicit employment-type and seniority filters, plus salary bounds that require matching currency and period. Salary range inversion returns a deterministic 422; jobs with unknown salary bounds do not satisfy numeric salary filters. Existing location, work-mode, lifecycle/closure, freshness, text search, pagination and stable ordering remain in the same service contract.

**Regression:** PostgreSQL API tests cover each filter, lower/upper salary bounds, currency/period normalization, unknown salary/seniority, invalid salary inputs, pagination totals and stable ID tie-breaking.

**Scope status:** direct field filters and stable ordering are covered by exact-head CI. S05-B01-T01 remains **PARTIAL** for explicit application of saved candidate preferences: the PRD permits preferences to affect search but does not define which soft preferences should hide a job versus merely rank/recommend it. No implicit preference-based exclusion was invented. Continue with the authorized ambiguous-identity workflow.


### U2.2 / S05-B01-T02 — authorized ambiguous identity resolution

**Code/test head:** `4b42f903c4b64e82b6d102b3b8a4deafad5cd300`  
**CircleCI backend-verification:** PASS — https://app.circleci.com/workflow/43289d21-699d-490b-a592-4f867d6e316b/job/90fd63c3-a1db-4d96-818f-92f86eca8986

**Change:** added admin-only `GET /api/v1/admin/job-resolutions`, `GET /api/v1/admin/job-resolutions/{resolution_id}`, and `POST /api/v1/admin/job-resolutions/{resolution_id}/resolve`. Review can explicitly match one of the recorded candidate jobs or create a new canonical job; it cannot select a job outside the recorded candidate set. Both decisions require a reason, supersede rather than erase the unresolved record, link the resulting resolution to the prior one, preserve provenance, update discovery events, and write a before/after admin audit event. Detail redacts secret-like raw payload keys.

**Regression:** PostgreSQL API tests cover paginated listing/detail, secret redaction, match-existing resolution, create-new resolution with description and salary provenance, event transition, audit evidence, and non-admin denial.

**Status:** S05-B01-T02 is **COMPLETE for repository-verifiable acceptance**. Exact-head CI passed. Unresolved source permission review remains a prerequisite for shared-source resolution; saved-preference search semantics and canonical-field/freshness crosswalk remain open under their separate U2.2 items. No frontend integration or merge is authorized.


### U2.2 / S05-B02-T01 — canonical fields and route-change freshness

**Code/test head:** `da76bd1be8fef5652b309571ea055f6057c8412c`  
**CircleCI backend-verification:** PASS — https://app.circleci.com/workflow/bba60a6f-d8a5-4a90-a767-8217ca0a8f02/job/6f766c75-909a-4961-aef5-5b828a9ff6e3

**Change:** source normalization now retains seniority and canonicalizes supported salary currency/period values without guessing unsupported units. New canonical jobs persist supplied salary fields; authoritative same-source external-ID observations refresh known canonical fields, including salary and seniority. Weaker matches may fill unknown fields but do not overwrite known values. Blank source values do not erase a canonical title.

When an authoritative source changes the application URL, the canonical URL is updated and the previous route verification is invalidated: `last_verified_at` is cleared, lifecycle returns to `DISCOVERED`, and freshness becomes `UNKNOWN`. Material description versions remain keyed to exact source records; unchanged descriptions do not create new versions.

**Regression:** PostgreSQL coverage now verifies salary/seniority persistence, source-authoritative updates, preservation when later observations omit salary/title, material JD versioning, and application-route changes invalidating old active/fresh verification.

**Status:** S05-B02-T01 is **COMPLETE for repository-verifiable acceptance**. Exact-head CI passed. U2.2 remains open for saved-preference search semantics and the cross-entry-point shared/private visibility audit. No deployed-source freshness or frontend acceptance is implied.


### U2.2 / S05-B02-T02 — shared/private visibility preservation

**Code/test head:** `eb10c62f804685f7922827d91fe429959f17d8e9`  
**CircleCI backend-verification:** PASS — https://app.circleci.com/workflow/4bfd01dc-6753-43b6-b8a2-d64b07359dbb/job/d63bd751-a7da-4f3e-a0e4-37fefb275092

**Regression:** added an admin identity-review test where a manual submission is resolved to an existing shared canonical job. The source’s submitter ownership is preserved in provenance, but manual content cannot overwrite shared title/company, compensation, seniority, application route, lifecycle/freshness, last verification or JD versions. Existing canonicalization tests also cover private manual-only submissions and cross-owner non-merging; Job Pool, evaluation, Punt, feed and application tests enforce visibility at their respective entry points.

**Status:** S05-B02-T02 is **COMPLETE for repository-verifiable acceptance**. Exact-head CI passed. U2.2 remains **PARTIAL** only for saved-preference search semantics; preference data continue to inform evaluation, and no ambiguous soft-preference exclusion was introduced.

**Next canonical work:** U2.3 — discovery mission lifecycle, persisted schedules/timezones, and downstream-processing reconciliation.


### U2.3 / S10-B01-T01–T03 — mission outcomes, DST and downstream completion

**Code/test head:** `de584b62c006de0663f278c01d4111d6b504217c`  
**CircleCI backend-verification:** PASS — https://app.circleci.com/workflow/bc6ab493-4a75-4774-a01a-fa9d5360fbb8/job/e012d942-6937-4eac-8777-d501ccc783bd

**Change:** terminal discovery missions no longer collapse exhausted all-failure runs into an error-free `EXHAUSTED` result. No eligible sources and all-failed/restricted/unavailable source outcomes produce an explicit terminal `FAILED` mission/work item with actionable error codes. Mixed successful and failed sources preserve `PARTIAL_SOURCE_FAILURES` and distinct outcome counts/categories even when the mission otherwise exhausts or meets its target. The scheduler’s DST behavior is now explicitly tested for nonexistent spring-forward times and ambiguous fall-back times.

**Regression:** exact-head CI covers all-source failure through terminal completion, mixed-source outcomes through terminal completion, existing retry/stale-lease recovery, immutable schedule snapshots, missed slots, concurrent scheduler claims, and the new DST boundary cases.

**Status:** S10-B01-T01 and T03 are **COMPLETE for repository-verifiable acceptance**. S10-B01-T02 is **CI-PASS / runtime-partial**: timezone, DST, missed-slot and concurrency tests pass, but deployed continuous scheduler operation remains unverified. U2.3 overall remains **PARTIAL** only for that explicit runtime gate. Proceed to U3.1.

### U3.1 / S06-B01-T01 — extraction scheduling and exact JD version linkage

**Code/test head:** `8d66cb2f10ad0be287df2fe85b1aa98e60b29a86`  
**CircleCI backend-verification:** PASS — https://app.circleci.com/workflow/610be65b-b266-41bb-a720-31e2b4b87674/job/4f1f7f75-e3c2-4e41-aead-4a9cf5315e1a

**Regression:** canonicalization coverage verifies material description changes create distinct content hashes and leave each new version in `PENDING` extraction state. Worker integration coverage processes every `USABLE` version in a job independently, proves each persisted requirement links to the exact description-version row/content hash, skips `PARTIAL` descriptions, and confirms replay does not claim completed versions or create duplicate requirements.

**Status:** S06-B01-T01 is **COMPLETE for repository-verifiable acceptance**. This proves the persisted queue and version-linkage contract; it does not prove continuous deployed worker operation or crash recovery. S06-B01-T02 remains **PARTIAL** for retry/lease/idempotency acceptance and runtime recovery.

**Next canonical work:** S06-B01-T02 — complete extraction retry, leases and idempotency.

### U3.1 / S06-B01-T02 — extraction retry, leases and idempotency

**Code/test head:** `a280e0627a92868d80ac1c970a85d478ed98716e`  
**CircleCI backend-verification:** PASS — https://app.circleci.com/workflow/b37a8ffe-7bff-4344-a321-a3a6c984b53a/job/609296bb-7e3c-4fcd-93fe-0b413e19c40a

**Regression:** worker tests prove failed extraction persists a safe error category and retry deadline, attempts are not repeated during backoff, eligible retries can recover, and successful replay does not duplicate requirements. PostgreSQL integration coverage holds an expired `IN_PROGRESS` JD-version row under a competing lock, verifies `SKIP LOCKED` does not double-claim it, then releases the row and proves recovery increments the attempt count and persists one requirement.

**Status:** S06-B01-T02 is **COMPLETE for repository-verifiable acceptance**. Deployed worker restart/recovery remains **NOT VERIFIED at runtime** under S10-B02-T03; passing PostgreSQL lease tests do not close that operational gate.

**Next canonical work:** S06-B01-T03 — requirement review and evaluation invalidation.

### U3.1 / S06-B01-T03 — requirement review and evaluation invalidation

**Code/test head:** `e6622856ddce6055609aec4f5f1c9bb7485cab09`  
**CircleCI backend-verification:** PASS — https://app.circleci.com/workflow/4524fd2d-ca55-44b9-a15d-a0de6b47e064/job/910e9704-e85b-48f3-8ea2-839e9103a163

**Regression:** PostgreSQL integration coverage seeds requirements on two immutable JD versions, verifies evaluation snapshots include only requirements linked to the latest JD version, confirms reviewer decisions are recorded against the selected requirement, and verifies the evaluation becomes `STALE` when the reviewed requirement snapshot changes. Existing route-authorization tests cover Firebase trust requirements, reviewer allowlisting, and fail-closed behavior for untrusted identities. The review service locks the exact requirement row during a decision; the requirement and review history retain the JD-version linkage.

**Status:** S06-B01-T03 is **COMPLETE for repository-verifiable acceptance**. Runtime Firebase reviewer configuration and deployed worker behavior remain separate runtime gates; this test result does not close them.

**U3.1 status:** S06-B01-T01, T02 and T03 are **COMPLETE for repository-verifiable acceptance**. The extraction worker's deployed continuous operation/restart recovery remains **NOT VERIFIED at runtime** under S10-B02-T03.

**Next canonical work:** U3.2 — outbox, work-item contracts and recovery, beginning with S10-B01-T04 handler-inventory enforcement and then S10-B02-T02 work-item inspect/retry/cancel/replay.

### U3.2 / S10-B01-T04 — emitted-event handler inventory

**Code/test head:** `aa69ae982a0b3200a280b791b6378d149f16c181`  
**CircleCI backend-verification:** PASS — https://app.circleci.com/workflow/2b99b9ad-6669-4af1-a2a6-b968ac428aa7/job/b29665f2-9502-4b48-b229-948112065ff0

**Regression:** a source-level AST inventory scans application code for `OutboxEvent` and `append(event_type=...)` producers. It requires producer call sites to declare literal event types and verifies every emitted type has either a production dispatcher handler or the explicit built-in `WORK_REQUESTED` disposition. Existing production-map tests separately assert the registered handler set and dedicated-worker delegation for `DISCOVERY_MISSION`.

**Status:** S10-B01-T04 is **COMPLETE for repository-verifiable acceptance**. This inventory does not close the separate work-item inspect/retry/cancel/replay contract or deployed crash-recovery gates.

**Next canonical work:** S10-B02-T02 — complete WorkItem inspect/retry/cancel/replay contracts using the existing WorkItem/outbox/lease/retry/dead-letter system.

### U3.2 / S10-B02-T02 — WorkItem inspection, retry, replay and cancellation

**Code/test head:** `1569ae40f037136c2a52e1158d5fbd1004f07220`  
**CircleCI backend-verification:** PASS — https://app.circleci.com/workflow/d6e18f64-7da5-4838-8909-c3020f3334f4/job/a819d012-975b-4f76-960a-7e93172ebf69

**Implementation:** Added admin-only, reason-required actions for the existing `DISCOVERY_MISSION` WorkItem contract. Retry expedites only a future-scheduled `RETRY_WAIT` mission; it creates no new queue or work item. Replay restarts only an eligible terminally failed mission, preserves its WorkItem ID, idempotency key, attempt count and source-run history, assigns a new execution ID, and is capped at three successful administrative replays per item. Cancellation is limited to unclaimed pending work, locks the WorkItem and mission rows, rejects already-claimed work, updates both persisted states, and emits one idempotent completion event. The notification reports administrative cancellation truthfully.

**Regression:** PostgreSQL API tests cover trusted admin authorization, audited retry, replay state/idempotency/replay limit, cancellation-versus-claimed-work conflict, a single completion event and the resulting truthful notification. Existing list/detail endpoints remain the operational inspection contract; no second queue was introduced.

**Status:** S10-B02-T02 is **COMPLETE for repository-verifiable acceptance**. Staging restart/fault-injection and deployed worker recovery remain **NOT VERIFIED at runtime** under S10-B02-T03.

**Next canonical work:** U4.1 / S02-B01-T01 — profile/preferences field-by-field coverage, preserving explicit unknowns and candidate-fact/preference separation.

### U4.1 / S02-B01-T01 — profile and preference field coverage

**Code/test head:** `71ea1c421f4fed4be67583a6285775a4f5041709`  
**CircleCI backend-verification:** PASS — https://app.circleci.com/workflow/c77c67f2-6df9-471a-ab40-47ede53f34ea/job/7902068d-8f72-4619-b0fd-1394fc2e8f8a

**Regression:** Expanded the PostgreSQL Master Profile API round trip to exercise every declared field in identity, eligibility, professional identity, career history, projects, achievements, challenges, leadership, skills/tools, education and certifications. The test retains explicit unknown/unverified outcomes, null values and evidence representations, verifies section-clear semantics preserve unrelated sections, and checks revision conflicts. Preference round-trip coverage now includes every declared preference field, including application-fee currency, and confirms preference writes do not mutate factual profile data. Existing deterministic-policy tests cover factual work authorization separately from preferences, unknown eligibility, salary, fees, relocation, work mode and explicit exclusions.

**Status:** S02-B01-T01 is **COMPLETE for repository-verifiable acceptance**. This closes the field-level profile/preferences test gap; it does not close the separate pathway CRUD/approval or discovery-wiring tasks.

**Next canonical work:** S02-B01-T02 — complete career-pathway CRUD and approval lifecycle.

### U4.1 / S02-B01-T02 — pathway CRUD and approval lifecycle

**Code/test head:** `385f746ab2bf10922450719cd901bf0b6bcee195`  
**CircleCI backend-verification:** PASS — https://app.circleci.com/workflow/14bb8391-54cb-4162-b2ff-42e19c9f221d/job/d8a243c8-8d61-44f2-b2a2-39e4cefb6879

**Regression:** Added PostgreSQL API coverage for AI-suggested pathway ownership across read/approve/reject/retire, rejection followed by a prohibited approval, retirement with preserved source/approval provenance, duplicate-retirement rejection, revisioned append-only history, and list filtering by source and lifecycle state. Existing tests cover candidate-defined pathway CRUD, AI suggestions remaining pending until explicit candidate approval, rejected suggestions, retirement, evidence ownership, stale revisions, and immutable AI-generated fields.

**Status:** S02-B01-T02 is **COMPLETE for repository-verifiable acceptance**. The task closes candidate-owned pathway CRUD and lifecycle acceptance; it does not itself prove the separate discovery/matching integration task.

**Next canonical work:** U4.1 / S02-B01-T03 — wire only active approved pathways into discovery/matching while preserving deterministic explicit exclusions.

### U4.1 / S02-B01-T03 — approved pathways in discovery and matching

**Code/test head:** `a99fed5463c43d6fae40eab877a9abb4d3a58f1d`  
**CircleCI backend-verification:** PASS — https://app.circleci.com/workflow/715f4e02-5a83-4939-94da-adcf0ed8cbef/job/9a7885e4-189f-4f3d-a948-d5d421d034bd

**Implementation:** Discovery missions snapshot only candidate-owned `APPROVED` pathways and the worker derives role targets from that immutable snapshot. Candidate evaluations now incorporate the matching approved pathway's role family, target domains and seniority as soft matching preferences, and persist those effective preferences separately from the candidate's saved preferences for reproducibility. Explicit exclusions and eligibility constraints remain unchanged and retain deterministic precedence. Evaluation freshness compares the complete pathway snapshot, including domains and seniority, so a changed pathway invalidates the old evaluation.

**Regression:** PostgreSQL tests verify pending/rejected/retired pathways are excluded from discovery snapshots; discovery evaluates the approved pathway role target; pathway-derived matching fields are recorded; an excluded industry still yields a hard rejection and blocks Punt creation; and changing pathway domains invalidates evaluation before role-target changes invalidate subsequent evaluations. Existing tests cover cancellation of a mission when its approved-pathway snapshot changes.

**Status:** S02-B01-T03 is **COMPLETE for repository-verifiable acceptance**. Live acquisition, deployed worker operation and staging restart/fault-injection remain separate runtime gates; no live-source or deployment verification is claimed.

**Next canonical work:** U4.2 / S03-B01-T01 — complete the candidate-owned Evidence Library list/detail/edit/health workflow, including provenance, verification and conflict visibility.

### U4.2 / S03-B01-T01 — Evidence Library list/detail/edit/health

**Code/test head:** `c831fee55c24ba1942bb7893446188d7a412f03e`  
**CircleCI backend-verification:** PASS — https://app.circleci.com/workflow/bbdf5fd8-02d8-4834-b7e6-0837575de459/job/f2569c3b-7653-498a-a8b2-162e3d28574b

**Correction:** Evidence health now treats `effective_to` as an exclusive boundary, consistent with candidate evidence eligibility checks: evidence is out of range when `effective_to <= now`, not only after the boundary has passed.

**Regression:** Added a boundary unit case for `effective_to == now` and a PostgreSQL API test that verifies an approved evidence item is reported as VERIFIED/CURRENT, then VERIFIED/EXPIRED after its expiry timestamp passes. Existing tests cover candidate-scoped list/detail and pagination, source metadata, linked claims/profile facts, import conflict and missing-information signals, editable draft/pending evidence, stale revision rejection, immutable approved evidence, pending-claim invalidation, package-reference protection, date validation and ownership isolation.

**Status:** S03-B01-T01 is **COMPLETE for repository-verifiable acceptance**. Live OCR/storage-provider behavior and import recovery remain separate tasks/runtime gates.

**Next canonical work:** U4.2 / S03-B01-T02 — complete provenance and conflict handling, ensuring unresolved conflicts remain reviewable and no imported facts silently overwrite candidate facts.

### U4.2 / S03-B01-T02 — provenance and conflict resolution

**Code/test head:** `7f57f50d4ccbbe86fd4867ec6d7559ac20157ad0`  
**CircleCI backend-verification:** PASS — https://app.circleci.com/workflow/70f12194-1ba9-434a-b6ae-825831231336/job/dd3e66cd-24f6-4942-8bd4-471fa450bad0

**Regression:** Added PostgreSQL integration coverage for an imported identity value that conflicts with the saved Master Profile. A forged/stale conflict key is rejected without advancing the import state or revision. The valid `KEEP_EXISTING` decision is persisted through confirmation and save; the conflict remains visible in import history, the imported document is stored as DRAFT evidence with its extracted value and provenance, and the existing profile value/revision remain unchanged. This confirms that conflict decisions are review records, not permission to silently overwrite authoritative profile facts.

**Status:** S03-B01-T02 is **COMPLETE for repository-verifiable acceptance** alongside existing structured conflict tests for conflicting imported values, profile/evidence comparison, expiry filtering, explicit resolution of every conflict, stable resolution keys and source-excerpt provenance. No claim is made that AI extraction proves the truth of imported statements.

**Next canonical work:** U4.2 / S03-B02-T01 — complete import history/detail/recovery, including failed/interrupted/duplicate imports and idempotent retry.

### U4.2 / S03-B02-T01 — import history, detail and retry recovery

**Code/test head:** `399b222d13e101140bce2b8aecc864534b4e467f`  
**CircleCI backend-verification:** PASS — https://app.circleci.com/workflow/a9301863-697f-4e7e-96df-055b1f08ef09/job/123dd90a-d220-426a-81a2-a43324a36b78

**Regression:** Added an API/PostgreSQL test for a transient private-storage read failure. The failed import remains in candidate-owned history with a safe error code/category and source filename, but does not expose extracted document text. Retrying after storage recovery advances the same import record to `PARSED`, clears the error, exposes the preview only after parsing, and moves the record from the FAILED filter to the PARSED filter without creating a second import. Existing tests cover candidate-scoped history/detail, pagination, failed-parse retry, missing-object restore/retry, extraction retry, concurrent confirmation protection, idempotent save, and rollback after storage/metadata/evidence-save failures.

**Status:** S03-B02-T01 is **COMPLETE for repository-verifiable acceptance**. This does not verify live R2/Drive provider recovery, OCR-provider behavior, or deployed retry workers.

### U4.2 / S03-B02-T02 — document format boundaries, opt-in OCR and provider failure paths

**Code/test head:** `39dd2968ac73fd68f9233f365b9469637f598bd1`  
**CircleCI backend-verification:** PASS — https://app.circleci.com/workflow/a22ec5b2-bcd2-43da-a48c-40f4f6513ffa/job/e445c72a-671a-467f-b6c7-ca04185a2d0f

**Regression coverage added:** Unit tests verify that scanned PDF OCR is called only when explicitly enabled and receives the configured timeout and batch size; provider-unavailable and provider-failure exceptions map to safe, stable domain errors without leaking upstream details; blank OCR results are rejected; malformed DOCX ZIPs without required Word document parts are rejected; and PDFs exceeding the 200-page processing limit are rejected with a 413 error.

**Existing coverage reviewed:** Supported TXT/DOCX/PDF upload acceptance, extension/MIME/signature/UTF-8 checks, 4 MiB upload boundary, OCR-disabled behavior, and selectable-text extraction are covered by unit tests. PostgreSQL integration tests cover storage write/read failures, missing-object recovery, metadata rollback and object cleanup, parse retry, and AI extraction provider failure/retry. OCR client unit tests cover batched page mapping and sanitized provider responses.

**Status:** S03-B02-T02 is **COMPLETE for repository-verifiable acceptance**. CircleCI passed on the code/test head above. Real Google Vision credentials/provider behavior, live object-storage recovery, personal Drive backup behavior, and deployed smoke tests remain runtime gates; none is claimed as verified.

### U5.1 / S08-B01-T01 — Master CV/variant editing and regeneration

**Code/test head:** `806d8f51a3100227a1941c1866158ca971028bcb`  
**CircleCI backend-verification:** PASS — https://app.circleci.com/workflow/9d155d51-9bdf-42fe-93a0-18890ce3e3bc/job/24e96e83-4bab-4e9d-9093-44ae1b773e1f

**Regression coverage added:** PostgreSQL integration coverage verifies the candidate-facing CV history round trip across an edit: immutable version ordering, content hashes, source profile revision/snapshot/hash, source Master CV version linkage, and approval bound to version 1 rather than silently carried to the new version 2. It also verifies the old and edited content remain independently retrievable. Existing coverage already exercises Master CV regeneration from profile revisions, profile immutability, variant independence, optimistic revision conflicts, candidate ownership, append-only version rows, and approval idempotency.

**Status:** S08-B01-T01 is **COMPLETE for repository-verifiable acceptance**. The new regression test passed the branch's CircleCI backend-verification check. This is not a claim of frontend usability or live deployment verification.

### U5.1 / S08-B01-T02 — rendered CV artifacts and private document protections

**Code/test head:** `8eab73cf26a9d6d4134a61da3c3ad04427132bbf`  
**CircleCI backend-verification:** PASS — https://app.circleci.com/workflow/28710fc2-690c-4d51-84a5-61aacbe48a66/job/336d18bf-806b-4c86-89d7-bfd25b8f7c66

**Regression coverage added:** PostgreSQL integration coverage now opens generated DOCX bytes with python-docx and generated PDF bytes with PyMuPDF, then checks that the candidate's name, role, employer, achievement and skill are present in the extracted document text. It verifies MIME types, byte sizes, SHA-256 checksums and exact CV-version metadata. A tampered storage key outside the candidate/CV/version namespace is rejected, and cross-candidate download remains denied.

**Restore coverage reviewed:** Existing PostgreSQL restore tests verify restoration of a missing private object only from a recorded backup, skip backup retrieval when the private object already matches its checksum, replace corrupt stored bytes only after backup checksum validation, reject corrupt backups without writing bytes, and enforce candidate ownership. These are adapter-level tests; live Google Drive/R2 credentials and provider-level recovery remain unverified.

**Status:** S08-B01-T02 is **COMPLETE for repository-verifiable acceptance**. CircleCI passed on the code/test head above. Live storage/Drive behavior, deployed download-link expiry behavior, frontend rendering and end-to-end candidate acceptance remain runtime/integration gates.

### U5.1 / S08-B02-T01 — package revision and provenance chain

**Code/test head:** `f9a60a141022ff57a7872e82633cd69ed9d23a13`  
**CircleCI backend-verification:** PASS — https://app.circleci.com/workflow/d436b6d1-d159-41cf-8cd6-af19a2c6f3f5/job/566597a6-f28c-44d5-b030-8750f967aaa3

**Regression coverage added:** Package source snapshots now carry an explicit provenance schema version and bind each artifact's content hash, metadata hash, and linked AI execution provenance. The execution snapshot includes task and system policy versions, input/output contract versions and fingerprints, config fingerprint/version ID, provider/model, status, output fingerprint, and contract snapshot. The package source snapshot also includes evaluation validity and decision fields plus an input-snapshot hash, the exact linked job-description version, and per-evidence content hashes. Evidence referenced inside Q&A answer `evidence_basis` is now included in the locked and snapshotted evidence set, even if it is not duplicated in the artifact-level evidence list.

**Fail-closed enforcement:** Package reads, review readiness, and semantic claim validation compare persisted artifacts and linked AI execution records with the immutable revision snapshot. Missing provenance schema, changed artifact content/metadata, or changed AI execution provenance blocks the operation. Existing PostgreSQL regressions also cover expected-current-version conflicts, immutable historical revisions, concurrent sequential version allocation, application-row locking, and stale evidence snapshots.

**Status:** S08-B02-T01 is **COMPLETE for repository-verifiable acceptance**. CircleCI passed on the exact code/test head above. Live provider behavior, deployed end-to-end candidate workflows, frontend integration and production recovery remain unverified. This task does not claim semantic AI claim review is formal proof.

### U5.1 / S08-B02-T02 — unsupported-claim review and version-bound candidate approval

**Code/test head:** `de44274fd76381c41c947aeca8a96d3958460708`  
**CircleCI backend-verification:** PASS — https://app.circleci.com/workflow/cea98eed-92f2-4005-8b78-59382bd0b509/job/69ea8970-1d68-44f6-a71e-d335fd709151

**Regression coverage added:** Unsupported/unassessed claim warnings are verified in the persisted, hash-checked claim-validation report. A candidate-scoped `GET /candidate/applications/{application_id}/packages/{package_id}/claim-validation` endpoint now retrieves the latest persisted report for that exact package version, including its package version number, report hash, and AI execution reference. Retrieval verifies package ownership, artifact provenance, execution ownership/task/status and report integrity; cross-candidate access is denied. Candidate approval remains an explicit action after readiness succeeds. A regression test proves a revised package starts as DRAFT with no inherited review decision, while the prior package version retains its own approval and timestamp.

**Status:** S08-B02-T02 is **COMPLETE for repository-verifiable acceptance**. CircleCI passed on the exact code/test head. AI semantic claim auditing remains AI-assisted, not formal proof; production provider behavior and end-to-end frontend review remain unverified.

### U5.2 / S09-B01-T01 — application lifecycle vocabulary and transition contract

**Code/test head:** `aca6aed4e22f8df136bae8a3239ca07f75ea456b`  
**CircleCI backend-verification:** PASS — https://app.circleci.com/workflow/952aefed-d62a-41cb-87c3-eaf32d65e830/job/ba9b4cac-9119-4e26-ae7a-f7fc66789759

**Regression coverage added:** Tests lock the currently implemented lifecycle state vocabulary and transition table, exercise every declared valid edge, reject self-transitions without mutating the domain record, and verify a self-transition at the service boundary leaves application state, revision, lifecycle events, and outbox unchanged. Existing tests cover invalid-transition rollback, stale expected-revision conflicts, concurrent transitions with one winner, event history/audit projection, candidate-recorded submission, and submission/package gates.

**Vocabulary conflict retained explicitly:** PRD v1.5 section 22 names `APPROVED TO APPLY → APPLIED → SCREENING → INTERVIEW INVITED → INTERVIEW → OFFER → WON`, with `LOST` and `ARCHIVED` as alternatives. The current API/database contract uses `PLANNED`, `APPROVED`, `SUBMITTED`, `INTERVIEW`, `OFFER`, `ACCEPTED`, `REJECTED`, `WITHDRAWN`, and `CLOSED`. These are not silently renamed or migrated. Existing history remains intact pending an explicit product decision on canonical vocabulary and state mapping.

**Status:** **PARTIAL — transition contract verified; vocabulary reconciliation awaits explicit product decision.** No production state rename, historical-event rewrite, or migration was made.

### U5.2 / S09-B01-T02 — candidate-recorded application events

**Code/test head:** `5791f380b6dcb7973b403b89f887820645e6b472`  
**CircleCI backend-verification:** PASS — https://app.circleci.com/workflow/3ce54567-2e7f-487e-8183-81c16193b480/job/8ea8440b-a4f4-4cb5-a0c9-2ed7d45063cf

**Implementation and regression coverage:** Application creation now records `source=QUALIFIED_PUNT_WORKFLOW`; candidate-driven lifecycle changes persist a server-assigned `CANDIDATE_ACTION` source for approval and `CANDIDATE_RECORDED` for real-world/outcome milestones in the durable application-event metadata and audit projection. The API rejects client-supplied source/actor fields, so a caller cannot label an ordinary request as a verified integration event. Existing fields persist actor, reason, timestamp, and before/after status. Regression tests verify source attribution, reject source spoofing with no state/event mutation, and preserve existing audit/outbox behavior.

**Status:** S09-B01-T02 is **COMPLETE for repository-verifiable acceptance**. No verified external application-status integration is enabled or claimed; real-world milestones remain candidate-recorded.

### U5.2 / S09-B01-T03 — application approval/submission gates and audit

**Code/test head:** `af2e22751c6e3796fd2dfb8f62b7e4ccb66a7330`  
**CircleCI backend-verification:** PASS — https://app.circleci.com/workflow/cceca385-fde8-457e-9557-02fc7603ee79/job/4abbe615-7704-47db-92be-8ba82974ce71

**Implementation and regression coverage:** Submission now rechecks that the approved package still matches the current job and latest job-description version, not only when the application first moves to APPROVED. Submission fails closed if a newer package revision is still DRAFT, the JD changed after package approval, or referenced evidence changed after package creation. Package review and claim validation verify the exact evidence snapshot, including nested Q&A `evidence_basis`, before accepting review or invoking AI. Tests assert rejected submissions leave application state, revision, event history, and outbox unchanged; existing tests cover candidate application approval, approved-package readiness, evidence revocation, stale revision conflicts, concurrency, and notification/audit event consistency.

**Status:** S09-B01-T03 is **COMPLETE for repository-verifiable acceptance**. CircleCI passed on the exact code/test head. No automatic submission or external integration-generated real-world milestones are enabled or claimed. Live frontend and provider runtime behavior remain unverified.

### U6.1 / S11-B03-T01 — candidate support ticket/message state contract

**Code/test head:** `4b9ca5221eb5364faaa696e78148fd4aa0693387`  
**CircleCI backend-verification:** PASS — https://app.circleci.com/workflow/aab74acc-ba00-4828-b74b-a2ccff2bd971/job/2e9ab828-1f2c-4cc9-afb1-b34865e6b866

**Implementation and regression coverage:** Message retries now return the existing message before checking ticket closure, so a replay after a lost response cannot fail or duplicate an already-persisted message. New messages remain blocked after resolution/closure. Tickets can only transition OPEN → RESOLVED → CLOSED; direct OPEN → CLOSED is rejected without mutation, and repeated closure is idempotent. PostgreSQL tests cover ticket/message creation timestamps, sent responses, candidate/admin message exchange, unread flags and per-message `read_at`, idempotent retries, transient persistence failure followed by safe retry, linked follow-up tickets, terminal status behavior, and status timestamps/outbox events.

**State semantics:** The backend persists `SENT` only after the message is durably stored. `SENDING` and `FAILED` are request/transport states for the client to display while the request is pending or failed; retry uses the same idempotency key. Frontend state rendering remains deferred until backend acceptance is complete.

**Status:** S11-B03-T01 is **COMPLETE for repository-verifiable backend acceptance**. CircleCI passed on the exact code/test head. This does not claim a frontend implementation or live email delivery.

### U6.1 / S11-B03-T02 — support ticket notifications and truthful delivery

**Code/test head:** `c78fb432f6b8d57cb5931ed5363ea154b8af0846`  
**CircleCI backend-verification:** PASS — https://app.circleci.com/workflow/500b3510-9105-474f-a424-76c195237420/job/cca9b45c-e388-4a3f-9bca-1cbff90ab70c

**Implementation and regression coverage:** Support notification timestamps now come from the persisted event's corresponding ticket timestamp: `created_at` for creation, `resolved_at` for resolution, and `closed_at` for closure, with a safe fallback for older records. Fanout targets the candidate and active admins only; inactive admins are excluded. Event/recipient idempotency keys prevent duplicate in-app notifications and email work on replay. Tests cover creation/resolution/closure recipient sets and timestamps, opted-in verified email work, partial fanout retry, failed email delivery followed by a successful retry, and truthful `PENDING`/`FAILED`/`SENT` delivery records. Existing notification-email tests also cover unconfigured providers and already-sent idempotency.

**Status:** S11-B03-T02 is **COMPLETE for repository-verifiable acceptance**. CircleCI passed on the exact code/test head. Live SMTP provider behavior remains runtime-unverified; email remains governed by persisted user preference and verified-address checks.

### U6.2 / S12-B02-T01 — complete all candidate notification triggers

**Code/test head:** `c78fb432f6b8d57cb5931ed5363ea154b8af0846`  
**Exact audit-head CircleCI backend-verification:** PASS on `d9f10eb2c9d5e2f6ff1a1c044293c2ff789a95e0` — https://app.circleci.com/workflow/98fed275-01a2-4196-a44d-f8df85d1e9c5/job/2bb8b8d9-1358-4172-a4ba-1cba4300627a

**Implementation and regression evidence:** The production outbox dispatcher registers handlers for `PUNT_QUALIFIED`, `APPLICATION_STATUS_CHANGED`, `EVALUATION_REQUIRES_ATTENTION`, and `DISCOVERY_MISSION_COMPLETED`. Producers persist the corresponding outbox signals alongside the Punt qualification, application status event, REVIEW evaluation, and terminal discovery-mission update. Notification handlers resolve the persisted resource and candidate profile on the server, derive the recipient from the owning profile, and use stable event keys to make replay idempotent. Evaluation alerts are emitted only for `REVIEW` outcomes; discovery alerts require a persisted terminal mission. Candidate notification list/read endpoints are scoped to the authenticated user.

**Regression coverage reviewed:** PostgreSQL notification integration tests exercise all four handlers, duplicate replay, resource-specific content and stable event keys; the API test covers owner-only listing, read state, idempotent repeated reads, and 404 for another user's notification. Opportunity-service PostgreSQL tests verify Punt/application event creation and idempotency/concurrency; evaluation PostgreSQL tests verify the REVIEW outbox signal; discovery-worker PostgreSQL tests verify terminal completion signals and duplicate suppression. The outbox event inventory test requires every literal outbox emission to have a registered handler or explicit built-in disposition.

**Status:** **COMPLETE for repository-verifiable acceptance.** Exact audit-head CircleCI passed. This does not establish deployed worker operation, frontend rendering, or live email provider behavior.

### U6.2 / S12-B02-T02 — email delivery, bounded retries and provider verification

**Code/test head:** `20fdce044cdcb3a692a5b2aee955f9ce5385e5c8`  
**CircleCI backend-verification:** PASS — https://app.circleci.com/workflow/7fd00624-a2bd-46e1-8f1d-73e5b38aa74d/job/ba21d080-587c-4e59-a573-e5d04b2188ff

**Repository-verifiable implementation and regression coverage:** Email work is queued only for opted-in users with a verified email address; missing consent, unverified addresses and missing SMTP configuration do not attempt delivery. Delivery records persist attempt count and truthful `PENDING`/`SENDING`/`FAILED`/`SENT` or skip states; successful delivery uses a stable message ID, and retries of already-sent work are no-ops. Failures persist a safe exception category rather than provider response text. The notification-email integration tests cover success, no duplicate send after SENT, failure while preserving the in-app notification, recovery from failure, and unconfigured-provider behavior.

**New regression:** The production `OutboxDispatcher` is exercised with `NOTIFICATION_EMAIL_REQUESTED`: a simulated SMTP failure schedules a retry, the due retry reaches the configured attempt limit, the outbox event becomes `DEAD`, and both outbox and delivery records retain safe failure categories and the expected attempt counts. The test advances the retry due time directly rather than sleeping.

**Status:** **PARTIAL / RUNTIME NOT VERIFIED.** Repository tests verify bounded retry and persisted delivery semantics, but no live SMTP credentials or staging smoke-test evidence is available. Actual provider acceptance, delivery, and staging worker operation remain unverified.

### U6.2 / S12-B02-T03 — notification preference and privacy rules

**Code/test head:** `ddd445750f98173e0a114bf8f90fd216f7a57bef`  
**Exact-head CircleCI backend-verification:** PASS — https://app.circleci.com/workflow/2e9ad263-cbf9-4089-91cd-24cdfed7812d/job/f6f61da0-5933-4ef7-8385-6f78a305c0a5

**Regression evidence:** Candidate email preferences default to disabled; enabling email is rejected unless the authenticated candidate's address is verified; read/update operations act on the current candidate's preference. Delivery tests explicitly prove the SMTP client is not opened when consent is disabled or the address is unverified, and that the delivery record reflects the corresponding skip state without incrementing attempts or setting `sent_at`. Existing notification tests cover candidate-owned listing/read access and deny access to another candidate's notification.

**Status:** **COMPLETE for repository-verifiable acceptance.** No cross-candidate disclosure was observed in the tested notification list/read and preference paths. Broader cross-resource privacy matrix and live provider behavior remain separate release gates; no live SMTP/staging claim is made.

### U7.1 / S13-B03-T01 — backend end-to-end acceptance matrix

**Matrix commit:** `0dfa40bb6c3f753381e3bd95bb21719f37bdf768`  
**Exact-head CircleCI backend-verification:** PASS — https://app.circleci.com/workflow/ff197430-abe7-42a8-81d3-6e7537b7d28e/job/02f8edd3-4020-47f0-a9d0-9a28bbf4d4fe  
**Artifact:** [Backend end-to-end acceptance matrix](tests/acceptance/backend_e2e_acceptance_matrix.md)

**Result:** The matrix maps sign-in, candidate profile, preferences/pathway, evidence/import, discovery/Job Pool, evaluation/review, Punt, CV/package, application tracking, notifications/email, and support to their existing PostgreSQL-backed or runtime-oriented test evidence. It records cross-cutting ownership/privacy, durable side effects/replay, provenance/stale-input, and external-provider gates, with explicit exceptions for Firebase, SMTP, OCR/storage, live sources, and deployed workers.

**Status:** **COMPLETE as the acceptance-matrix deliverable.** The matrix explicitly does not claim a single orchestrated cross-domain candidate journey has run; the existing suite provides stage-level integration coverage. That full journey remains a separate acceptance gap.

### U7.1 / S13-B03-T02 — candidate API contracts and error/state semantics

**Code/test head:** `7af1fc637e4e4f3c2fc0ca589376e390e086e13f`  
**Exact-head CircleCI backend-verification:** PASS — https://app.circleci.com/workflow/eddf012f-30c7-426c-9daf-ded7f410bb7d/job/16fe95f9-b8e1-404b-b322-4cae34010f70  
**Artifacts:** [Candidate API contracts](docs/candidate-api-contracts.md); [OpenAPI contract tests](tests/unit/test_candidate_api_contract.py)

**Result:** Documented implemented candidate route families, pagination defaults/bounds, request idempotency, revision/state semantics, common domain-error and validation-error shapes, route-specific FastAPI error responses, request IDs/body-size limit, and known lifecycle/provider caveats. Added tests that inspect the app's generated OpenAPI for critical candidate paths/methods/create status codes, pagination bounds, and required idempotency/lifecycle request fields. Existing PostgreSQL integration tests remain the behavioral evidence for ownership, revision conflicts, idempotency, rollback, and lifecycle transitions.

**Status:** **COMPLETE for the documented route-contract deliverable and guarded OpenAPI surface.** This does not replace the remaining cross-domain end-to-end journey or provider runtime gates.

### U7.1 / S13-B03-T03 — provider, storage and scheduler runtime gates

**Status:** **PARTIAL / NOT VERIFIED / BLOCKED on target-environment evidence.** The repository's CircleCI job uses an ephemeral PostgreSQL container and runs compilation, migrations, schema parity, and unit/PostgreSQL integration tests. It does not execute live Firebase, SMTP, OCR, R2/Drive, approved live-source, deployed worker/scheduler, or restore drills.

**Evidence boundary:** Existing adapter/provider tests and the opt-in acquisition canary are not proof of actual staging behavior. No authorized target-environment run results, provider acceptance IDs, or worker/database/object-store restart/restore records were found in the reviewed repository evidence. No external provider was contacted as part of this task.

**Runbook added:** [Candidate runtime smoke-test and recovery checklist](docs/operations/candidate-runtime-smoke-checklist.md) — lists required checks, safety controls, evidence fields and exit criteria. The checklist is preparation, not a passing smoke test.

**Required to close:** Execute the checklist in an authorized staging environment using synthetic data and controlled mailboxes; record exact commit, timestamps, redacted logs/results, provider IDs and recovery outcomes. Until then, the backend release gate remains open.

### U1-T04 — cross-resource ownership and privacy matrix

**Code/test evidence baseline:** `7af1fc637e4e4f3c2fc0ca589376e390e086e13f` — CircleCI backend-verification PASS: https://app.circleci.com/workflow/eddf012f-30c7-426c-9daf-ded7f410bb7d/job/16fe95f9-b8e1-404b-b322-4cae34010f70  
**Artifact:** [Candidate cross-resource ownership/privacy matrix](tests/acceptance/candidate_ownership_privacy_matrix.md)

**Result:** Consolidated existing PostgreSQL-backed evidence for candidate profile/provenance, evidence and claims, document imports/history, CV versions/private artifacts, application packages/reports/downloads, public/private Job Pool behavior, evaluations/opportunity feed, Punts, applications/lifecycle events, notifications/email preferences, support tickets/messages, and sensitive logging/validation errors. The matrix records the specific tests and expected ownership/privacy behavior rather than claiming a new single end-to-end harness.

**Status:** **COMPLETE for repository-verifiable matrix acceptance.** Resource-specific tests cover the canonical list and the exact code/test baseline passed CI. This does not close the separately blocked target-environment provider/recovery gate, and the matrix itself is not a substitute for runtime checks.

**Release gate still open:** U7.1 / S13-B03-T03 remains **PARTIAL / NOT VERIFIED** pending authorized staging smoke tests and restart/restore evidence. Admin workflow work remains deferred until candidate workflow and backend release gates are satisfied.

### U1.1 / S01-B01-T02 — account fields and provider-subject mapping (focused regression update)

**Code/test head:** `dad69a093500b42ba45eac5cabb7dd1ad0476e54`  
**Exact-head CircleCI backend-verification:** PASS — https://app.circleci.com/workflow/de44a8f6-18f2-4f4a-9345-158521f507b2/job/1102e843-a300-47bb-bf0e-75120ffded45

**New regression coverage:** `tests/unit/test_firebase_identity_provider.py` now checks malformed/overlong optional account claims, invalid email/photo URL values, non-boolean email-verification claims, and unusable login timestamps. It also verifies an explicit `email_verified=false` claim updates an existing account rather than being ignored. `tests/integration/test_firebase_identity_postgres.py` now verifies two distinct verified Firebase UIDs sharing the same email create separate local users and identity records; email is not used as an account-merge key.

**Existing evidence retained:** First/repeat and concurrent first sign-in mapping, disabled-account denial, account-field synchronization, preservation of absent optional claims, onboarding default/preservation, monotonic last-login time, and invalid-token no-provisioning tests remain in place.

**Status:** **PARTIAL.** The added regression paths passed exact-head CircleCI. This narrows the account-mapping gap but does not close the full PRD field-by-field account/onboarding lifecycle crosswalk or the separate deployed Firebase credential/sign-in runtime gate. No live Firebase project was contacted. Local cloning was unavailable in this execution environment; remote CI is the test evidence for this commit.

### U1.1 / S01-B02-T01 — account lifecycle transition and audit safeguards

**Code/test head:** `dad69a093500b42ba45eac5cabb7dd1ad0476e54`  
**Exact-head CircleCI backend-verification:** PASS — https://app.circleci.com/workflow/de44a8f6-18f2-4f4a-9345-158521f507b2/job/1102e843-a300-47bb-bf0e-75120ffded45

**Regression coverage:** `tests/integration/test_admin_governance_postgres.py` now covers rejected same-state requests, stale expected revisions, blank reasons, and non-admin attempts. Each rejection asserts that account status/revision and `USER_STATUS_CHANGED` audit history remain unchanged. Existing coverage verifies an authorized ACTIVE → SUSPENDED change records actor, reason, before/after state and request ID, increments the revision, and causes subsequent Firebase authentication to fail closed. Existing Firebase identity integration tests also reject SUSPENDED and DEACTIVATED local accounts.

**Status:** **PARTIAL.** The tested mutation, authorization, stale-write and audit safeguards pass exact-head CircleCI. The PRD names ACTIVE, SUSPENDED and DEACTIVATED as supported states but does not define a complete permitted-transition graph. Do not infer whether DEACTIVATED may be reactivated or silently invent additional forbidden transitions; that policy must be explicitly resolved before the transition-matrix acceptance criterion can be closed. Live Firebase credentials/sign-in remain runtime-unverified.

### U1.1 / S01-B01-T01 — trusted identity-provider boundary

**Code/test head:** `dad69a093500b42ba45eac5cabb7dd1ad0476e54`  
**Exact-head CircleCI backend-verification:** PASS — https://app.circleci.com/workflow/de44a8f6-18f2-4f4a-9345-158521f507b2/job/1102e843-a300-47bb-bf0e-75120ffded45

**Regression coverage:** `tests/unit/test_firebase_identity_provider.py` covers malformed authorization headers, blank/malformed UID claims, non-mapping claims, expected Firebase project matching, revoked/invalid token rejection, unknown identity-provider configuration failing closed, and the requirement that spoofable `X-User-ID` identity is accepted only when both explicit development flags are set. Firebase verification requests revocation checking.

**Status:** **PARTIAL / RUNTIME NOT VERIFIED.** The repository-verifiable trusted-boundary checks pass exact-head CircleCI. Actual Firebase project credentials, deployed Google sign-in, and target-environment token verification remain unverified; no live Firebase project was contacted.

