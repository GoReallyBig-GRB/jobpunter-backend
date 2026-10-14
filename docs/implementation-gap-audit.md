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

## Batch 3 progress (2026-10-14)

### Implemented

- Candidate-owned Master Career Profile sections persist separately from candidate preferences: identity, eligibility, professional identity, career history, projects, achievements, challenges, leadership, skills/tools, education and certifications.
- `GET /api/v1/candidate/profile` returns the profile. `PATCH /api/v1/candidate/profile` updates only supplied sections, uses `expected_revision`, rejects stale writes, and preserves other sections. Known identity, eligibility, professional-identity and repeatable-record fields are type-checked; unknown fields are preserved for forward compatibility. Empty/all-blank repeatable records are rejected, while an empty list clears a section and explicitly UNVERIFIED facts remain representable.
- Preferences remain a separate revisioned resource. Deterministic evaluation covers saved country/work-authorization constraints, remote-only rules, salary comparability/minimums, excluded industries and job types, allowed employment types, application-fee limits, and explicit relocation requirements. Missing or incomparable decisive facts produce REVIEW rather than being inferred. Canonicalization retains typed `application_fee_amount`, `application_fee_currency`, and `relocation_required` facts only when supplied explicitly; non-finite fee values and stringly-typed relocation flags are not accepted. Policy version is `candidate-evaluation-v4`.
- Evaluation only uses approved/effective evidence. Approved claims are excluded if their facts are no longer supported by current approved/effective evidence; excluded claim IDs and the reason `NO_CURRENT_APPROVED_SUPPORTING_EVIDENCE` are recorded in the immutable input snapshot.
- Claim approval distinguishes same-field scalar contradictions (`CONTRADICTED_CLAIM`) from missing/unsupported facts (`UNSUPPORTED_CLAIM`). A candidate can correct a DRAFT or PENDING_REVIEW claim through revision-checked `PATCH /api/v1/candidate/claims/{id}`; correction resets PENDING_REVIEW to DRAFT and requires resubmission. Approved claims remain immutable. Conflict checks are intentionally conservative and do not decide which source is true.
- Evidence cannot be archived while an approved claim depends on it. Claim approval locks supporting evidence rows. Regression coverage includes candidate isolation, stale revisions, transaction rollback, evidence validity, conflict correction/resubmission, and manual-ingestion persistence of explicit fee/relocation facts.

### Verification

- Executable-code checkpoint before this audit-only consolidation: `2dd54b8ac7ada2a9c43586401b814e048a28a9cc`. The current commit is documentation-only.
- CircleCI `ci/circleci: backend-verification` passed on that exact commit. The workflow compiles Python, upgrades a fresh PostgreSQL 17 database, checks Alembic schema parity, and runs unit plus PostgreSQL integration tests.
- GitHub Actions repeatedly failed before runner startup (empty steps, runner ID 0, no job steps or retrievable logs). Retrying the failed job did not change that infrastructure failure. This is not evidence of a code-test failure; CircleCI is the passing backend-verification check.

### Batch 3 follow-up closure (2026-10-14)

- **Section-specific profile schemas:** repeatable career-history, project, achievement, challenge, leadership, skills/tools, education, and certification records now have distinct typed patch schemas. Known fields are type-checked while unknown fields remain forward-compatible. The response payload retains its existing dictionary shape.
- **Richer conflict detection:** claim/evidence conflict checks compare semantic field aliases across differently named nested sections only when stable record anchors identify the same employment, education, project, or certification record. Different employers are not joined by role alone. Document-import review also compares extracted identity facts with saved profile identity and current approved evidence, and surfaces distinct contradictory values within the imported extraction. Conflicts remain review signals, not source-truth verdicts.
- **Fact provenance:** migration `0032_candidate_profile_fact_provenance` adds fact-level source records. Candidate profile PATCH records supplied facts, profile revision, actor and confirmation time. `fact_sources` can explicitly link a profile fact path to a field in that candidate’s saved, confirmed document import; the service checks ownership, confirmation, value equality, and that the source excerpt occurs in the parsed document before saving the link. Imported field records preserve the import, saved evidence, verifiable source excerpt when available, extraction confidence, candidate confirmation and profile revision. The candidate-scoped `GET /api/v1/candidate/profile/provenance` endpoint exposes this history; PostgreSQL integration tests cover successful links, mismatched-value rejection and ownership isolation.
- **Verification:** CircleCI `ci/circleci: backend-verification` passed on executable-code head `403909c5da53e62ed4ebb07828039b271e604b2b`. The suite includes Python compilation, fresh PostgreSQL 17 migration and schema-parity checks, unit tests, and PostgreSQL integration tests. Follow-up closure remains gated on the required CI result for the current cumulative PR head, not this historical code checkpoint alone.
- **Still outside this backend follow-up:** candidate-facing frontend integration. The API and provenance history are available for frontend consumption, but no frontend implementation is claimed here.

## Batch 4 completion (2026-10-14)

### Completed backend scope

- Evaluation snapshots preserve the structured candidate profile, approved evidence, eligible/excluded approved claims, candidate preferences and revision, requested target-role pathway, job facts and explicit eligibility data, current application-route verification, JD version/hash/completeness, requirement-extraction status/version and execution trace, persisted requirements with latest review events, deterministic policy version, and governed AI task/config/output-contract trace.
- Evaluation staleness checks compare the immutable input snapshot with current profile facts, preferences, approved/effective evidence, claim support, job fields, application-route verification, latest JD identity/hash/completeness, requirement-extraction state/version/execution, reviewed requirements and review decisions. Evaluations also stale when the deterministic policy version or a used AI output-contract version changes. These checks catch material changes even if a revision counter was not incremented.
- Semantic matching uses only persisted requirements tied to the exact JD version. Each used requirement must be confirmed or rejected, have a verifiable source excerpt and extraction version, and belong to a stable extraction state. The snapshot records the latest upstream extraction AIExecution/config trace by request ID. Unreviewed requirements, missing persisted requirements, missing approved candidate evidence, or missing provenance leave the evaluation in REVIEW; evaluation does not silently re-extract a new requirement set.
- Evidence-matching output contract v4 supports the PRD v1.5 states DIRECT, STRONG_TRANSFERABLE, TRANSFERABLE, PARTIAL, WEAK_TRANSFERABLE, MISSING, CONTRADICTED, REQUIRES_VERIFICATION and DISQUALIFYING. Positive, partial, contradictory and disqualifying states require supplied evidence references; MISSING must have none. Legacy lowercase states are normalized at the evaluation boundary. AI match states cannot create deterministic hard blockers.
- The deterministic evaluation policy is candidate-evaluation-v20. Separate assessments cover core-role fit, domain fit, technical fit, seniority fit, education fit, location/work authorization, preference fit, evidence quality/completeness, application accessibility, job lifecycle and job freshness. Requirement-level output identifies requirements not met and those requiring verification. No approved evidence produces REVIEW with a null score, not a zero-fit rejection.
- Candidate preferences influence soft role, industry, domain, seniority, location and work-mode fit. Transferability policy, evidence emphasis, terminology preferences and other search constraints are passed to the governed matcher without allowing them to rewrite candidate facts or override deterministic eligibility.
- Application accessibility requires a current successful, URL-matched JobVerificationAttempt; a syntactically valid URL alone is insufficient. Verification trust expires after the existing 24-hour reverification window. Successful verification sets lifecycle ACTIVE. Explicit route classifications DIRECT_EMPLOYER, FREE_ATS, FREE_AGGREGATOR, PAID, BLOCKED, PAID_OR_BLOCKED and UNKNOWN are accepted from per-job source metadata/manual intake or explicit source/connector configuration. Recognized per-job classification takes precedence; otherwise connector configuration takes precedence over source configuration. Invalid/missing values remain unknown, and URL/domain shape is never used to infer route type. Blocked/ambiguous routes cannot qualify, and paid routes require explicit fee facts plus a recorded maximum-fee preference.
- Punt qualification remains distinct from APPLY. It requires a current scored evaluation, no hard blockers, deterministic eligibility ELIGIBLE, mandatory requirements MET, a verified active route, current freshness, active lifecycle and a substantively complete latest JD. The service locks the candidate profile and rechecks current source inputs before idempotent candidate-specific Punt creation.
- PostgreSQL integration coverage now includes a successful persisted-requirement → governed-match → APPLY → Punt path, no-approved-evidence review behavior, repeated evaluation versions, stale requirement-review/extraction-trace/route-verification snapshots, lifecycle/freshness/JD completeness gates, and concurrent idempotent Punt qualification. Unit coverage exercises match states, provenance references, explicit route classification, paid/blocked routes, soft preference dimensions, and requirement-review gating.

### Verification

- Executable-code checkpoint: 6f4541929ce43e70eee710737691ee6d79148fbe.
- CircleCI ci/circleci: backend-verification passed on that exact checkpoint. The workflow compiles Python, migrates a fresh PostgreSQL 17 database, checks Alembic parity, and runs unit plus PostgreSQL integration tests.
- GitHub Actions continues to fail before runner allocation with no retrievable job steps. This is an infrastructure/check-run failure, not a code-test result. CircleCI is the passing backend verification evidence.
- This documentation-only consolidation creates a new cumulative commit. Batch 4 is closed for the backend acceptance scope above; the new cumulative head still requires its own CI confirmation before the next batch starts.

### Explicit follow-up gaps (not silently marked complete)

- Route classification can be supplied through the protected source registry API, explicit connector/source configuration, or per-job metadata; the API validates recognized classifications and exposes the configured value without exposing connector credentials. Per-job metadata takes precedence over connector configuration, which takes precedence over source configuration. This does not infer that a route is free, paid, or usable from provider/domain/URL. Any connector without an explicit recognized classification continues to produce an unknown route.
- The preference schema's evidenceExclusions field still lacks a documented, unambiguous selector contract; it must not be guessed as an ID, type, or keyword filter.
- Candidate evaluation override/correction history is not yet a complete candidate-facing workflow. This is the next planned backend batch, not part of the Batch 4 completion claim.
- Candidate-facing frontend integration and the broader P3 application-package/submission workflow remain outside this backend batch.

## Batch 5 plan (not started)

1. Inspect the PRD's candidate override, correction, and review requirements and the existing evaluation/Punt models before designing changes.
2. Define revision-checked candidate override actions separately from the deterministic evaluation outcome; preserve the original evaluation and its input snapshot.
3. Persist override actor, reason, previous decision, new candidate disposition, evaluation reference and timestamps with ownership/isolation guarantees.
4. Define how overrides affect Review Queue/Punt lifecycle without allowing an override to erase hard blockers or make stale evaluations current.
5. Add unit and PostgreSQL integration coverage for ownership, stale revisions, immutable original evaluations, audit history, and concurrent/idempotent updates.
6. Start only after this audit-only commit's CI is green on its exact head.

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
