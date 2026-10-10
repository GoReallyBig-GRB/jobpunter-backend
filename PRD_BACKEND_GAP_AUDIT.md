# JobPunter Backend PRD Gap Audit

**Baseline:** JobPunter Product Requirements Document (PRD) + Technical Spec v1.4  
**Repository:** GoReallyBig-GRB/jobpunter-backend  
**Audit status:** Updated against `integration/backend-consolidation` after backend foundation, acquisition-persistence, governance, and canonicalization work. This supplements, and does not replace, the PRD's existing Current-State Audit & Gap Closure Control Register and invariant list.

## Status meanings

- **Partial:** a scaffold or only part of the required path exists.
- **Not implemented:** no implementation was found in the audited backend tree.
- **Not verified:** code exists but persistence, security, integration, or runtime evidence is missing.
- **Verified:** the complete required path exists and relevant tests pass.

A model, service file, route, screen, or green unit-test run alone does not prove a capability is implemented.

## Executive finding

The backend is an early foundation, not a complete MVP backend. The repository now has a linear Alembic history, ORM/database schema parity checks, candidate/identity/evidence persistence, a more reliable acquisition-persistence slice, and admin configuration lifecycle integration coverage. The latest completed CI run ([workflow run](https://github.com/GoReallyBig-GRB/jobpunter-backend/actions/runs/38060054898)) passed compilation, migration upgrade, ORM/schema parity, and the PostgreSQL 16 test suite: 377 tests passed, with one deprecation warning. This run includes concurrent canonicalization, concurrent first-time Punt qualification, Firebase identity persistence/concurrency and invalid-token non-creation, concurrent acquisition run-key idempotency, acquisition connector/persistence failure handling, deterministic hard-blocker enforcement, application lifecycle rollback/revision/audit/concurrency regressions, and candidate claim approval tests for ownership, expired/future-effective evidence, and concurrent revision safety. CI validates the tested paths only; it does not establish full PRD parity or production readiness. See [foundation migration and audit PR](https://github.com/GoReallyBig-GRB/jobpunter-backend/pull/2) and [acquisition reliability PR](https://github.com/GoReallyBig-GRB/jobpunter-backend/pull/3).

The remaining implementation gap is still substantial. Candidate-scoped evaluation listing, Punt qualification/persistence, application lifecycle transitions, lifecycle events, and outbox dispatch/canonicalization handling now exist as foundations. Real job-source connectors, an evaluation-generation engine, end-to-end acquisition API/worker deployment, application packages, provider-neutral AI orchestration, scheduling, and integrations remain incomplete. A protected admin API now provides grant-checked telemetry/audit reads and versioned configuration draft, publish, and rollback-draft operations. PostgreSQL lifecycle tests cover draft/version sequencing, publish/retire behavior, rollback-draft creation, and audit request IDs; operator grant provisioning/recovery and broader operational governance remain open.

## Requirements-to-implementation matrix

| PRD capability | Status | Evidence / gap |
|---|---|---|
| FastAPI shell, request IDs, error handling | Partial | Startup, middleware and error handler exist; deployed API not demonstrated. |
| Provider-neutral identity | Partial | Identity port and user/identity models exist. PostgreSQL integration tests cover durable Firebase UID-to-user mapping, repeat sign-in, concurrent first-sign-in convergence, rejection of disabled local accounts, and invalid-token failure without local user/identity creation using injected verified claims. Actual Firebase Admin token verification with production credentials and deployment configuration remain unverified. |
| Candidate ownership and authorization | Partial | Candidate opportunity queries are scoped through the authenticated principal's user ID and candidate profile. PostgreSQL integration tests cover cross-candidate evaluation/Punt isolation and application-transition IDOR. Trusted production token validation and broader private-resource coverage remain unverified. |
| Master Career Profile | Partial | Candidate profile and candidate-claim create/list/review lifecycle APIs exist; PostgreSQL API tests verify evidence-backed approval gates and cross-candidate isolation. Claims can link only to owned evidence. Experience, education, projects, achievements, preferences, target roles/pathways and full-record APIs remain absent. |
| Evidence Library and conflict resolution | Partial | Evidence/source and claim links exist. Claim approval now requires at least one linked, current APPROVED evidence item owned by the same candidate; PostgreSQL tests cover unsupported-claim rejection, cross-candidate evidence-link denial, expired/future-effective evidence rejection, and concurrent approval revision safety. Import review, confirmation, conflict resolution, semantic claim/evidence consistency and complete provenance remain absent. |
| PDF/DOCX/TXT import | Not implemented | No backend import pipeline, parser adapters, artifact storage or review API found. |
| Master CV, variants and document generation | Not implemented | No CV variant, package, artifact storage, DOCX/PDF generation or download path found. |
| Source Registry and connector contract | Partial | Source/connector models and Protocol exist. No real connector, health-check, policy-validation or admin API found. |
| Acquisition runs and raw records | Partial | Run creation is durable before connector execution; idempotent run keys, safe failure status, raw-record deduplication, changed-content preservation, and transactional outbox events for new raw records are implemented and unit-tested. PostgreSQL integration tests verify concurrent duplicate calls for one source/run key converge on one durable run and invoke the connector once, connector failure persists a FAILED run with a sanitized error summary, and persistence failure rolls back partially written raw records and their outbox events while marking the run FAILED. Real connectors, complete source-level outcome reporting, and end-to-end processing remain absent. |
| Canonical Job Pool and identity resolution | Partial | Canonicalization uses source-scoped external IDs, normalized application URLs and exact normalized fingerprints; ambiguous candidates are routed to review. PostgreSQL integration tests cover replay idempotency, per-observation provenance, stable description versions, changed-description versioning, source-ID convergence, and concurrent processing of distinct raw records sharing an exact normalized identity signal. The concurrent test asserts one canonical job and CREATED/MATCHED outcomes. A recoverable outbox dispatcher can invoke canonicalization. Real-source ingestion, operational worker deployment, richer fuzzy matching, and the review API remain open; broader repository-wide transaction, rollback, and concurrency coverage is still incomplete. |
| URL, pasted JD, email, API/feed ingestion | Not implemented | No channel adapters or API endpoints found. |
| JD retrieval, completeness, closure and freshness | Not implemented | States/models are partial; no retrieval or deterministic completeness/freshness pipeline found. |
| Candidate-specific evaluation and explainable decisions | Partial | CandidateEvaluationService persists versioned evaluations, input snapshots, approved evidence/claim provenance, deterministic scores/outcomes, hard blockers and explanations. PostgreSQL tests cover candidate-specific evidence, source-version provenance, excluding future/expired evidence, closed-job WATCH outcomes, and an explicit excluded-industry blocker that persists as REJECT and prevents Punt creation. Unit tests additionally verify excluded job type, ineligible country, job-specific country restrictions, and remote-only mismatch all deterministically REJECT before incomplete-description or evidence scoring can soften the decision. Where candidate or job country constraints exist but the job country is missing, or a stated work-authorization requirement cannot be compared because the job country is missing, the policy now returns REVIEW with no score rather than silently treating the unknown as a match. The matcher remains a transparent lexical signal rather than full semantic requirement matching; broader hard-constraint coverage, stale invalidation, and end-to-end requirement extraction/evaluation remain incomplete. |
| Punt qualification | Partial | Domain qualification rules, candidate-scoped create/list API, evaluation associations, persistence and outbox event exist. PostgreSQL concurrency coverage verifies distinct evaluations qualifying the same candidate/job converge on one Punt, preserve both evaluation links, and emit one PUNT_QUALIFIED outbox event. Broader transaction and rollback tests remain open. |
| Application lifecycle and immutable events | Partial | Candidate-scoped create/list/transition APIs, domain transition rules, revision checks, ApplicationEvent records and outbox writes exist. PostgreSQL tests verify invalid transitions leave state/events/outbox unchanged, stale revisions are rejected without duplicate events, SUBMITTED records carry a CANDIDATE actor, and concurrent competing transitions against one revision yield exactly one winner and one revision conflict. Broader transition, rollback and concurrency coverage remains open. |
| Versioned application packages and claim provenance | Not implemented | No package/artifact models or evidence-grounded generated-claim validation found. |
| Provider-neutral AI orchestration | Partial | AI execution telemetry records exist and admin read access is protected; provider-neutral task registry, model routing, provider adapters, orchestration/fallback, and claim validation remain absent. |
| Work items and transactional outbox | Partial | Work-item/outbox models, append service, a recoverable dispatcher with leases/retries/dead-letter handling, and raw-record-to-canonicalization handler exist. Unit tests verify bounded retry scheduling, terminal failure at the attempt limit in the work-item dispatcher, safe error-category persistence, and expired-lease recovery. PostgreSQL tests for the canonicalization outbox dispatcher verify concurrent workers claim one live event once and replay after a simulated acknowledgement failure; the replay test uses an idempotent test handler, so idempotency of every production domain handler remains unverified. Punt/application writes also append outbox events transactionally. Worker deployment and handlers for all domain events remain open. |
| Server-side scheduling and notifications | Not implemented | No durable schedule execution, scheduler, notification service or delivery outcome tracking found. |
| Google/external integrations | Not implemented | No consent/scope lifecycle, connection/revocation, adapter execution or observed-health implementation found. |
| Protected Admin Control Plane | Partial | Protected admin endpoints enforce active ADMIN grants for telemetry/audit/config reads, config draft/publish/rollback-draft operations, and audited admin-grant list/create/reactivate/revoke actions. PostgreSQL tests cover grant creation, duplicate grant rejection, inactive-target rejection, revocation, immediate loss of authorization, reactivation, audit attribution, self-elevation denial, prevention of revoking the final active admin, and concurrent revocations that leave one active administrator while rechecking the actor grant under lock. Initial first-admin bootstrap remains operator-managed and its deployment runbook/recovery procedure is not yet verified. |
| Durable audit/activity | Partial | AdminAuditEvent records governance draft, publish, and rollback-draft actions, and a protected audit-read endpoint exists. Broader domain activity coverage, tamper resistance/retention policy, and production operational review remain open. |
| Object storage abstraction | Not implemented | No storage port/adapter or private artifact-access implementation found. |
| PostgreSQL schema integrity | Verified for current migrations | CI asserts one Alembic head, applies migrations to a fresh PostgreSQL 16 database, and runs `alembic check`. This does not verify all application data paths. |
| Candidate-neutral behavior | Not verified | Backend is too incomplete to demonstrate across professions and empty profiles. |
| Real-source truthfulness | Not verified | No real connector or source-state execution path exists yet. |
| Deployment outside AI Studio | Architecture direction only | No deployment, secret handling, worker operation, backup/restore or portability proof found. |

## Priority implementation order

### P0: integrity and security

1. **Migration integrity is addressed for the current schema.** Keep the PostgreSQL migration and ORM-parity checks required on every schema change.
2. **Partially addressed.** Server-side ownership is enforced for implemented candidate APIs, with cross-candidate tests for evidence, evaluation, Punt and application resources. Firebase UID-to-account mapping and concurrent first sign-in have PostgreSQL coverage using injected verified claims; actual Firebase Admin token verification with production credentials and broader private-resource coverage remain open. Request-body user IDs must never establish ownership.
3. **Partially addressed.** Selected PostgreSQL tests now cover canonicalization/Punt convergence, identity creation races, acquisition rollback, invalid lifecycle transitions and concurrent application revision conflicts. Repository-wide transaction boundaries, rollback, uniqueness and idempotency coverage remain open.
4. **Implemented for the tested acquisition paths.** PostgreSQL tests verify connector failure persists a sanitized FAILED run and a mid-batch persistence failure rolls back raw records and outbox events while leaving the run FAILED. Real connectors, partial-source failures and worker recovery remain open.
5. **Partially addressed.** Deterministic hard-blocker enforcement is covered by PostgreSQL regression tests. Candidate claim APIs now require current approved supporting evidence before approval and reject cross-candidate evidence links. Admin grant lifecycle is now audited and self-elevation is blocked; initial first-admin bootstrap and its recovery procedure remain operator-managed and unverified. Broader hard-constraint coverage, semantic claim/evidence consistency and AI-generated claim validation remain open.
6. **Partially addressed for the implemented application lifecycle.** PostgreSQL tests verify invalid transitions do not mutate state or append events, stale revisions do not create duplicate events, concurrent transitions yield one winner, and SUBMITTED events carry a CANDIDATE actor. Broader event sources and verified integrations remain open.

### P1: complete the core workflow

1. Source registry → real connector → acquisition run → immutable raw record.
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
- [x] Selected PostgreSQL integration tests cover opportunity ownership, repeated-application paths, canonicalization provenance/versioning/source-ID matching and concurrent convergence, concurrent first-time Punt qualification, sanitized acquisition connector failure and partial-persistence rollback, deterministic hard-blocker enforcement, invalid application transition rollback, stale-revision rejection, candidate-recorded submission audit, and concurrent competing application transitions; repository-wide transaction, rollback and broader concurrency coverage remains open.
- [ ] API/adapter contract tests cover enabled identity, acquisition, AI, storage and integrations; Firebase identity mapping and concurrency have PostgreSQL coverage using injected verified claims, but actual Firebase Admin token verification and deployment credentials remain unverified. Admin configuration lifecycle PostgreSQL coverage is present and passes in CI.
- [x] Cross-candidate evaluation/Punt access and application-transition IDOR are covered for opportunity resources; unauthorised admin reads/writes, nested secret-like config fields, and core config lifecycle behavior have regression coverage. Broader state-transition abuse remains open.
- [ ] End-to-end tests cover acquisition through canonical Job, evaluation, Punt, package and candidate-recorded application events.
- [x] Selected outbox tests cover bounded retry scheduling, terminal failure in the work-item dispatcher, safe error-category persistence, expired-lease recovery, concurrent PostgreSQL claiming, and replay after simulated acknowledgement failure using an idempotent test handler; [ ] production-handler idempotency under duplicate delivery, partial source failure, AI failure/fallback and notification failure remain open.
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
