# JobPunter Backend Implementation Gap Audit

**Specification basis:** JobPunter PRD + Technical Spec v1.5  
**Repository audited:** `GoReallyBig-GRB/jobpunter-backend`  
**Audit branch:** `audit/backend-gap-audit`  
**Status:** Partial implementation. Not production-ready.

This is a code-and-schema audit, not a claim that every requirement has been exhaustively tested. “Implemented” means code or schema was found in the current repository; it does not imply end-to-end acceptance.

## Executive finding

The repository is a backend foundation, not the complete JobPunter backend. It has a FastAPI shell, identity/candidate persistence, initial source/acquisition/job schemas, basic raw-record acquisition service, and CI for installation, compilation, and unit tests. The actual product workflow from source acquisition through canonical jobs, candidate evaluation, Punts, approved application packages, and candidate-controlled application tracking is not implemented end to end.

## Requirement status

| PRD / technical area | Repository evidence | Status | Remaining work |
|---|---|---|---|
| FastAPI API shell, request IDs, domain error handling | `app/main.py`, `app/api` | Partial | API-wide contract, validation, auth enforcement and endpoint coverage |
| Identity and accounts | User and external identity models, auth seam | Partial | Select and integrate identity provider; validate real credentials; account lifecycle and authorization tests |
| Candidate profile and evidence | Candidate-owned profile/claim/evidence/source models; structured profile sections; revision-checked profile PATCH and candidate API tests | Partial | Guided UI, typed domain validation within sections, pathways, evidence health, conflict resolution, import provenance and broader ownership tests |
| Document import | No complete parsing/review/save pipeline verified | Missing / unverified | PDF, DOCX, TXT validation, private storage, parsing, extraction, preview, conflict review and candidate confirmation |
| CV Studio and document generation | No complete generation pipeline verified | Missing | Editable master CV and variants, versioning, DOCX/PDF output, artifact storage and factual-claim validation |
| Source registry and connectors | Source and connector models, connector protocol | Partial | Source capability/permission registry, health checks, real permitted connectors, bounded retries and truthful source states |
| Acquisition runs and raw source records | Models, repository, service, tests | Partial | Crash recovery, robust retry policy, database-backed integration tests, raw snapshot history and complete run reporting |
| Canonical job pool | Job, resolution, provenance, JD version and requirement models; conservative pure identity resolver | Partial | Integrate resolution with persistence, canonical create/update, provenance and immutable material versions |
| JD retrieval and completeness | Source-field normalization and deterministic completeness assessment | Partial | URL retrieval adapters, manual URL/JD ingestion, freshness/closure verification, and integration with canonicalization |
| Requirement extraction and matching | Versioned JD requirement persistence and candidate evaluation service; governed semantic matching path and immutable evaluation snapshots | Partial | End-to-end extraction-to-evaluation proof, requirement/evidence links with source validation, dimension-specific scoring and complete task/model provenance |
| Decision engine and Punt | Deterministic APPLY/REVIEW/WATCH/REJECT policy, candidate evaluation versioning, Punt qualification history and hard-blocker handling | Partial | Complete PRD traceability, salary/eligibility regression coverage, candidate overrides, dimension-specific rationale, and end-to-end qualification lifecycle tests |
| Review Queue | No backend workflow verified | Missing | Candidate-specific query/view over canonical jobs and evaluations |
| Application packages | No complete package workflow verified | Missing | Versioned tailored CV, cover letter, application Q&A, evidence references, approvals and generated artifacts |
| Application lifecycle and audit | No complete application/event service verified | Missing | Validated state transitions, candidate-recorded submission, outcomes, actor/reason history and immutable audit events |
| AI task/provider management | No complete provider-neutral execution system verified | Missing | Provider/model/task registry, routing/fallback, output validation, execution logs, cost/availability metadata and secure credentials |
| Work items and outbox | ORM mappings, atomic idempotent enqueue, leased event claims, acknowledgement, bounded retry and dispatcher tests | Partial | Register production handlers, add a runnable worker process, define handler-specific idempotency and operational monitoring |
| Scheduling and notifications | No working scheduler/notification path verified | Missing | Daily/weekly schedules, durable run history, partial-failure visibility, preferences and idempotent dispatch |
| Admin/system control plane | No protected backend control plane verified | Missing | Separate authorization boundary, versioned configuration, validation/publish/rollback, audit and operational health |
| Object storage and integrations | No complete provider adapter verified | Missing | Private object storage, expiring downloads, explicit integration authorization, secret handling and revocation |
| Security and tenant isolation | Ownership/auth seams exist | Partial | Systematic negative authorization tests, secret redaction, admin boundary, upload security and cross-candidate isolation |
| Deployment and operations | Local configuration and CI workflow | Partial | Reproducible staging deployment, migrations against PostgreSQL, worker process, health/readiness checks, logs and backup/restore plan |

## Concrete defects found during this pass

1. **Acquisition failures could leave runs in RUNNING.** The service did not record failure when a connector or persistence step raised. A failure path now records a safe error category and re-raises the original exception.
2. **Raw-record deduplication was too broad.** Deduplicating solely on source plus content hash could collapse distinct records that happened to share content, while the original schema prevented a source record ID from retaining changed-content snapshots. The acquisition identity now includes stable external ID or source URL plus content hash, with a fallback for records lacking both. Migration `0004` updates the database constraints and adds missing job salary fields.
3. **Candidate ORM/schema parity was incomplete.** The ORM had candidate claim tables that the initial migrations did not create. The candidate evidence migration already defines those tables; the fix is to chain that migration into the single upgrade path.
4. **Outbox/work-item execution was only schema-level.** ORM mappings and an atomic idempotent enqueue service now create a work item and its outbox event in one database transaction. A generic dispatcher now claims due events with expiring leases, acknowledges successes, and applies bounded backoff. Production handlers, a runnable worker process and handler-level idempotency remain outstanding.
5. **The job ORM had fields/constraints absent from migration `0003`.** Migration `0004` adds missing salary fields, allows unknown employer names, and aligns raw snapshot versioning. Migration `0005` aligns non-null job metadata and resolution timestamps.
7. **Validation is stronger but not end-to-end.** CI now installs the package, compiles code, upgrades a fresh PostgreSQL database, checks Alembic schema drift, and runs 30 unit tests. Acquisition-to-canonical-job persistence and production worker execution are still not covered end to end.
6. **Alembic initially had two independent migration heads.** The old duplicate identity/candidate branch was consolidated into one dependency chain. PostgreSQL CI exposed a second issue: the Alembic revision string exceeded the version column's 32-character limit; the revision ID was shortened. The updated chain still requires a fresh successful CI run.

## Batch 3 progress (2026-10-13)

- Candidate profile data is now persisted in structured, candidate-owned sections: identity, eligibility, professional identity, career history, projects, achievements, challenges, leadership, skills/tools, education and certifications.
- `GET /api/v1/candidate/profile` returns those sections. `PATCH /api/v1/candidate/profile` updates only supplied sections and uses `expected_revision` to reject stale writes; candidate preferences remain a separate resource.
- Candidate evaluation now checks salary eligibility only when candidate/job currency and salary period are comparable. A disclosed job salary ceiling below the candidate's minimum is a hard blocker; missing or incompatible data produces REVIEW. Policy version is `candidate-evaluation-v2`.
- Regression coverage was added for salary constraints, structured profile patch validation, section preservation and stale profile revisions. The current head's CircleCI backend-verification check passed. GitHub Actions reported a failed job with no steps and runner ID 0; treat that as an infrastructure/check-run failure unless further diagnostics establish a code failure.
- Evidence integrity now blocks archiving approved evidence while an approved candidate claim still depends on it; claim approval locks the supporting evidence rows to serialize that decision against concurrent evidence transitions. Added a PostgreSQL integration regression test. CircleCI passed for commit `f48b2478eb327829cfc208ccccfc9373b68810ac`; GitHub Actions again failed before executing any steps.
- Added a PostgreSQL integration regression test proving a second candidate cannot transition another candidate's evidence or claim; the test also checks that failed cross-candidate attempts leave both records unchanged. Verification for the latest test commit is pending.
- These are backend foundations, not a complete Master Career Profile workspace or full end-to-end acceptance. The profile remains partial until the UI, domain-specific field validation, provenance/conflict workflow and remaining PRD requirements are implemented and verified.

## Delivery order

### P0: Establish trustworthy persistence and execution
- Run the full Alembic chain against PostgreSQL from an empty database.
- Compare SQLAlchemy metadata against the migrated schema and resolve every mismatch.
- Add PostgreSQL integration tests for foreign keys, unique constraints, rollback, concurrent idempotency and ownership boundaries.
- Complete outbox/work-item state transitions and worker retry semantics.
- Ensure acquisition failures, retries, partial results and duplicate run keys remain truthful and observable.

### P1: Complete canonical acquisition
- Source health/configuration and permitted acquisition contracts.
- Real manual URL and manual JD ingestion first, followed by only source connectors confirmed feasible and permitted.
- Immutable raw snapshots, splitting, normalization, JD retrieval, completeness, freshness, identity resolution, canonical job writes and provenance.
- Idempotent downstream work creation in the same transaction as durable state changes.

### P2: Implement the product's core judgement workflow
- Candidate preferences and approved career pathways.
- Versioned JD requirements and candidate evidence snapshots.
- Candidate × job × target-role evaluation with separate fit dimensions, hard blockers, uncertainty, explanations and model/task provenance.
- APPLY / REVIEW / WATCH / REJECT decision logic, candidate overrides, and the distinct Punt qualification model.

### P3: Complete candidate-controlled application workflow
- Review Queue, versioned application packages, truthful DOCX/PDF artifacts, approvals, candidate-recorded submissions, valid lifecycle transitions and audit events.
- Never automate application submission in the MVP.

### P4: Operational control and release readiness
- Provider-neutral AI task configuration and secure credentials.
- Scheduling, notifications, protected admin controls, integration consent/revocation and source health visibility.
- End-to-end acceptance tests against the PRD, staging deployment and a real-candidate validation plan.

## Release gate

Do not label the backend package complete until the PRD acceptance criteria are mapped to code and tests, all required migrations execute against PostgreSQL, the core candidate workflow passes end-to-end tests, and source/AI/application states are based on observed outcomes rather than assumed success.
