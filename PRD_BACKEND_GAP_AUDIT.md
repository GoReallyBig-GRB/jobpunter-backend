# JobPunter Backend PRD Gap Audit

**Baseline:** JobPunter Product Requirements Document (PRD) + Technical Spec v1.4  
**Repository:** GoReallyBig-GRB/jobpunter-backend  
**Audit status:** Updated after the foundation and acquisition-persistence fixes merged to `main`. This supplements, and does not replace, the PRD's existing Current-State Audit & Gap Closure Control Register and invariant list.

## Status meanings

- **Partial:** a scaffold or only part of the required path exists.
- **Not implemented:** no implementation was found in the audited backend tree.
- **Not verified:** code exists but persistence, security, integration, or runtime evidence is missing.
- **Verified:** the complete required path exists and relevant tests pass.

A model, service file, route, screen, or green unit-test run alone does not prove a capability is implemented.

## Executive finding

The backend is an early foundation, not a complete MVP backend. The repository now has a linear Alembic history, ORM/database schema parity checks, candidate/identity/evidence persistence, and a more reliable acquisition-persistence slice. The latest recorded CI run passed **14 unit tests**, applied all migrations to a fresh PostgreSQL 17 database, and reported no ORM/schema drift. See [foundation migration and audit PR](https://github.com/GoReallyBig-GRB/jobpunter-backend/pull/2) and [acquisition reliability PR](https://github.com/GoReallyBig-GRB/jobpunter-backend/pull/3).

The remaining implementation gap is still substantial. Candidate-scoped evaluation listing, Punt qualification/persistence, application lifecycle transitions, lifecycle events, and outbox dispatch/canonicalization handling now exist as foundations. Real job-source connectors, an evaluation-generation engine, end-to-end acquisition API/worker deployment, application packages, provider-neutral AI orchestration, scheduling, and integrations remain incomplete. A protected admin API now provides grant-checked telemetry/audit reads and versioned configuration draft, publish, and rollback-draft operations; PostgreSQL lifecycle verification and broader operational governance remain in progress.

## Requirements-to-implementation matrix

| PRD capability | Status | Evidence / gap |
|---|---|---|
| FastAPI shell, request IDs, error handling | Partial | Startup, middleware and error handler exist; deployed API not demonstrated. |
| Provider-neutral identity | Partial | Identity port and user/identity models exist. Provider is intentionally open; trusted token validation is not verified end to end. |
| Candidate ownership and authorization | Partial | Candidate opportunity queries are scoped through the authenticated principal's user ID and candidate profile. PostgreSQL integration tests cover cross-candidate evaluation/Punt isolation and application-transition IDOR. Trusted production token validation and broader private-resource coverage remain unverified. |
| Master Career Profile | Partial | Candidate profile, claims and evidence tables exist. Experience, education, projects, achievements, preferences, target roles/pathways and full-record APIs are absent. |
| Evidence Library and conflict resolution | Partial | Evidence/source and claim links exist. Import review, confirmation, conflict resolution and complete provenance are absent. |
| PDF/DOCX/TXT import | Not implemented | No backend import pipeline, parser adapters, artifact storage or review API found. |
| Master CV, variants and document generation | Not implemented | No CV variant, package, artifact storage, DOCX/PDF generation or download path found. |
| Source Registry and connector contract | Partial | Source/connector models and Protocol exist. No real connector, health-check, policy-validation or admin API found. |
| Acquisition runs and raw records | Partial | Run creation is durable before connector execution; idempotent run keys, safe failure status, raw-record deduplication, changed-content preservation, and transactional outbox events for new raw records are implemented and unit-tested. Real connectors, complete source-level outcome reporting, and end-to-end processing remain absent. |
| Canonical Job Pool and identity resolution | Partial | Canonicalization persistence and identity resolution exist, including replay handling, description versioning, provenance and ambiguous candidate links; a recoverable outbox dispatcher can invoke canonicalization. Real-source ingestion, operational worker deployment, richer fuzzy matching and review API remain absent. |
| URL, pasted JD, email, API/feed ingestion | Not implemented | No channel adapters or API endpoints found. |
| JD retrieval, completeness, closure and freshness | Not implemented | States/models are partial; no retrieval or deterministic completeness/freshness pipeline found. |
| Candidate-specific evaluation and explainable decisions | Partial | Versioned evaluation persistence model and candidate-scoped listing API exist. Evaluation generation, evidence matching, deterministic hard-constraint engine, explainability population and stale invalidation are not implemented. |
| Punt qualification | Partial | Domain qualification rules, candidate-scoped create/list API, evaluation associations, persistence and outbox event exist. Concurrent first-time creation conflict recovery and broader transaction tests need verification. |
| Application lifecycle and immutable events | Partial | Candidate-scoped create/list/transition APIs, domain transition rules, revision checks, ApplicationEvent records and outbox writes exist. Submission recording represents candidate action only. Broader transition, rollback and concurrency coverage remains open. |
| Versioned application packages and claim provenance | Not implemented | No package/artifact models or evidence-grounded generated-claim validation found. |
| Provider-neutral AI orchestration | Partial | AI execution telemetry records exist and admin read access is protected; provider-neutral task registry, model routing, provider adapters, orchestration/fallback, and claim validation remain absent. |
| Work items and transactional outbox | Partial | Work-item/outbox models, append service, a recoverable dispatcher with leases/retries/dead-letter handling, and raw-record-to-canonicalization handler exist. Punt/application writes also append outbox events transactionally. Worker deployment and handlers for all domain events remain open. |
| Server-side scheduling and notifications | Not implemented | No durable schedule execution, scheduler, notification service or delivery outcome tracking found. |
| Google/external integrations | Not implemented | No consent/scope lifecycle, connection/revocation, adapter execution or observed-health implementation found. |
| Protected Admin Control Plane | Partial | Protected admin endpoints enforce active ADMIN grants for telemetry/audit/config reads and config draft/publish/rollback-draft operations. Payload validation and audit writes exist; lifecycle PostgreSQL tests and operational grant provisioning/recovery remain to be verified. |
| Durable audit/activity | Partial | AdminAuditEvent records governance draft, publish, and rollback-draft actions, and a protected audit-read endpoint exists. Broader domain activity coverage, tamper resistance/retention policy, and production operational review remain open. |
| Object storage abstraction | Not implemented | No storage port/adapter or private artifact-access implementation found. |
| PostgreSQL schema integrity | Verified for current migrations | CI asserts one Alembic head, applies migrations to a fresh PostgreSQL 17 database, and runs `alembic check`. This does not verify all application data paths. |
| Candidate-neutral behavior | Not verified | Backend is too incomplete to demonstrate across professions and empty profiles. |
| Real-source truthfulness | Not verified | No real connector or source-state execution path exists yet. |
| Deployment outside AI Studio | Architecture direction only | No deployment, secret handling, worker operation, backup/restore or portability proof found. |

## Priority implementation order

### P0: integrity and security

1. **Migration integrity is addressed for the current schema.** Keep the PostgreSQL migration and ORM-parity checks required on every schema change.
2. Verify trusted authentication context and server-side ownership. Request-body user IDs must never establish ownership.
3. Add PostgreSQL integration tests for repositories, transaction boundaries, unique constraints, idempotency and rollback.
4. Extend acquisition failure tests to the real repository/database and prove that a connector or persistence failure cannot leave a durable run silently marked RUNNING.
5. Enforce hard constraints outside AI output and prevent unsupported candidate claims from becoming approved facts.
6. Never record real-world application events without candidate action or a verified integration.

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
- [ ] Unit tests cover all domain rules and critical state machines.
- [x] Selected PostgreSQL integration tests cover opportunity ownership and repeated-application paths; repository-wide transaction, rollback and concurrency coverage remains open.
- [ ] API/adapter contract tests cover enabled identity, acquisition, AI, storage and integrations; admin configuration lifecycle PostgreSQL tests are being added.
- [x] Cross-candidate evaluation/Punt access and application-transition IDOR are covered for opportunity resources; unauthorised admin reads/writes and nested secret-like config fields have regression coverage. Full config lifecycle PostgreSQL verification and broader state-transition abuse remain open.
- [ ] End-to-end tests cover acquisition through canonical Job, evaluation, Punt, package and candidate-recorded application events.
- [ ] Failure tests cover partial source failure, retry exhaustion, duplicate work, worker crash/recovery, AI failure/fallback and notification failure.
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
