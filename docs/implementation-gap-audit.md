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

### Resolved follow-ups (2026-10-14)

- evidenceExclusions contract: entries are exact UUIDs of Evidence Library records, canonicalized to lowercase UUID form. Duplicate IDs and free-text, type, keyword, or other ambiguous selectors are rejected. Excluded evidence remains stored in the Evidence Library but is omitted from evaluation evidence and claim-support inputs; snapshots record the applied IDs, and staleness comparison uses the same exclusion set. The preference does not delete evidence or rewrite the Master Career Profile.
- Candidate evaluation override/correction history: candidate-owned append-only events record the original evaluation decision, chosen disposition, actor, timestamp, reason, notes and per-evaluation revision. Overrides do not rewrite the immutable evaluation or its factual evidence snapshot. Stale evaluations cannot be overridden, optimistic revision conflicts are rejected, candidate ownership is enforced, and APPLY cannot bypass deterministic hard blockers. PostgreSQL migration adds an immutable-history trigger.

### Explicit follow-up gaps (not silently marked complete)

- Route classification can be supplied through the protected source registry API, explicit connector/source configuration, or per-job metadata; the API validates recognized classifications and exposes the configured value without exposing connector credentials. Per-job metadata takes precedence over connector configuration, which takes precedence over source configuration. This does not infer that a route is free, paid, or usable from provider/domain/URL. Any connector without an explicit recognized classification continues to produce an unknown route.
- Candidate-facing frontend integration and the broader P3 application-package/submission workflow remain outside this backend batch.

## Batch 5 completion (2026-10-14)

### Completed backend scope

- Canonical application states follow the explicit Technical Specification candidate-facing model: `PLANNED`, `APPROVED`, `SUBMITTED`, `INTERVIEW`, `OFFER`, `ACCEPTED`, `REJECTED`, `WITHDRAWN`, and `CLOSED`. The resolved mapping for higher-level PRD terms and allowed transitions is documented in `docs/application-lifecycle.md`.
- Transition requests now require a reason and accept optional notes. Both are persisted on application events. Application transition events use actor type `USER` and the authenticated candidate user ID; system-originated transitions are rejected by the domain transition function.
- Application approval now requires an active/current job, a complete substantive latest JD, a fresh successful URL-matched application-route verification, an approved latest package, passing package readiness, and a package snapshot matching the current job and JD version. Submission still requires the approved state and rechecks the latest package readiness.
- Added the unified `audit_events` projection and query service. Migration `0034_application_lifecycle_audit` backfills existing application/admin audit rows, mirrors subsequent application lifecycle and admin audit records, and installs a PostgreSQL trigger rejecting UPDATE/DELETE against the unified audit table.
- Added the candidate-owned application audit-history endpoint and integration coverage for reason/notes persistence, audit projection, admin-event projection, immutability, ownership isolation, approval gates, invalid transitions, and concurrency.

### Verification

- CircleCI `ci/circleci: backend-verification` passed on the exact cumulative acceptance head `dd938aa2f67cbd1bbc5d1449f24a16537a40ab91`. The workflow covers Python compilation, fresh PostgreSQL 17 migration, Alembic schema-parity checks, unit tests, and PostgreSQL integration tests.
- Added PostgreSQL regression coverage in commit `08693dbf230026777e44a00b4467f8ec5595cff2` asserting that application creation and a candidate-recorded withdrawal are projected into unified audit history with the expected actor, state transition, reason, and notes.
- GitHub Actions failed on the recent cumulative commits with no retrievable job steps/logs, so its failure cause remains **unknown**. CircleCI passed on the exact cumulative head and is the successful full backend verification evidence for this batch.
- PRD v1.5 lifecycle/audit acceptance was reconciled against `docs/application-lifecycle.md`, the lifecycle/audit services, migration `0034_application_lifecycle_audit`, and PostgreSQL integration coverage. This closes the **backend Batch 5 scope only**; it does not certify the wider product, frontend integration, deployment, or production readiness.
- No deployment or production-readiness claim.

## Batch 6 progress: AI task registry and governed execution (2026-10-14)

Batch 5 is closed for its defined backend acceptance scope. Batch 6 has started on the same cumulative draft PR and remains **in progress**.

### Implemented in this checkpoint

- AI task configuration payload fingerprints are recomputed and checked before provider execution. A mismatch fails closed before execution telemetry is created or a provider is called.
- Governance configuration versions now have a PostgreSQL guard that prevents deleting/truncating version history or changing version identity, authoring metadata, payload, or payload fingerprint. Publication lifecycle fields remain mutable so DRAFT → PUBLISHED → RETIRED continues to work.
- Published task configurations can declare a fallback provider/model pair. Publication validation requires both fields, valid provider-specific model naming, an explicit primary provider, and a distinct provider.
- Governed execution retries once through that configured alternate provider when the primary is unavailable, fails, or returns invalid structured JSON. Output validation still runs on the fallback result. The execution row records successful fallback use, and the response exposes the fallback flag. Failure telemetry remains privacy-minimal.
- Regression coverage added for fallback success/failure, fallback-route configuration validation, config-fingerprint mismatch, and database-level UPDATE/DELETE/TRUNCATE rejection for governance configuration history.
- Corrected the evidence-matching input contract to accept the exact production payload shape (job/JD-version context, persisted requirement provenance, evidence revisions, and bounded candidate preferences). The previous `extra="forbid"` schema rejected those fields, causing the semantic matching path to fail before provider execution. Bumped the input-contract trace to `v4` and added regression coverage for production-shape acceptance, unknown-field rejection, and duplicate IDs.
- Added a SHA-256 fingerprint of the validated task input to `ai_executions` and the protected admin execution response. This records a privacy-safe input trace without storing candidate prompts or evidence content. Migration `0039_ai_input_fingerprint` marks pre-existing rows as `legacy-unavailable`; current executions record the actual fingerprint.
- Added explicit output-validation outcomes (`PASSED`, `REJECTED`, or `NOT_RUN`) and safe categories to execution telemetry via migration `0040_ai_validation_result`. The admin execution response now serializes the required input-contract/fingerprint and fallback fields as well as validation results. Added a PostgreSQL regression test for the complete privacy-minimal execution trace; this also closes a response-schema/serializer mismatch that previously would have caused admin execution listing to fail response validation.
- Added an immutable per-execution contract snapshot through migration `0041_ai_contract_snapshot`. It retains the effective task/system instructions, policy versions, input/output schemas and versions, provider/model/fallback configuration, and published configuration identity without storing candidate prompt or output content. PostgreSQL rejects mutation of contract provenance while allowing execution status and final-route telemetry to update.
- Added a privacy-safe SHA-256 fingerprint of the parsed provider output via migration `0042_ai_output_fingerprint`. It is set once when a serializable output is received, remains available for rejected structured output, and cannot be changed or cleared after recording. Raw output content remains unpersisted.
- Added `document_imports.ai_execution_id` via migration `0043_document_ai_execution_ref`; a successful document extraction now persists the exact governed execution that produced its preview. The reference is checked for presence before the preview is applied, and PostgreSQL integration coverage asserts the persisted link.
- Added bounded, task-specific JSON input models for every registered AI task and removed the free-form prompt fallback from governed execution. The input contract is now `v4`; malformed JSON, missing required context/evidence, unknown fields, and unknown task IDs fail closed. This is an intentional API contract change: frontend callers must supply the new task-specific JSON payloads.
- Strengthened deterministic output provenance checks for profile extraction and requirement classification: source excerpts must exist in the supplied source, and classification must preserve every supplied requirement exactly once. Bumped those output contracts to `v2`.

### Still open before Batch 6 can close

- Validate that the frontend supplies the new per-task JSON input contract. The frontend repo has not been inspected in this backend-only batch, so cross-repo compatibility remains unverified.
- Audit downstream output-reference traceability across all production workflows. Document extraction now links to its AI execution; application-package and semantic-matching paths already carry execution IDs, but full end-to-end coverage is not yet established.
- Obtain final exact-head CircleCI verification after the latest migration and test changes. GitHub Actions failures still have no retrievable step logs, so their cause remains unknown.
- Provider/model availability and live external-service behavior remain unverified.
- Complete targeted regression coverage for invalid fallback output and fallback-attempt failure telemetry, then verify the complete migration chain and schema parity on the final cumulative head.
- Provider/model availability is not proven by syntactic config validation alone; no external-provider health or deployment claim is made here.

### Verification checkpoint

- CircleCI `ci/circleci: backend-verification` passed on executable head `407eead9def9e113c4db4c4102697dea33a0962b`. This is an interim checkpoint; the final head after the remaining Batch 6 work must pass again.
- GitHub Actions failed without retrievable job steps/logs. Cause remains **unknown**; this is not classified as a code failure or quota issue without evidence.
- No merge, deployment, or production-readiness claim.

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
