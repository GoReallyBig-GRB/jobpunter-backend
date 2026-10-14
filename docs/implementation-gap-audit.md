# JobPunter Backend Implementation Gap Audit

**Specification baseline:** JobPunter PRD + Technical Spec v1.5  
**Repository:** `GoReallyBig-GRB/jobpunter-backend`  
**Cumulative branch:** `work/post-42-follow-up-batches`  
**Cumulative PR:** [#54 — Backend follow-up batches after PR #42](https://github.com/GoReallyBig-GRB/jobpunter-backend/pull/54)  
**Audit updated:** 2026-10-14  
**Disposition:** Backend foundation and several bounded workflow slices are implemented and CI-verified. The backend is **not complete against PRD v1.5 and is not production-ready**.

## 1. Audit method and status meanings

This audit summarizes the cumulative backend changes on the follow-up branch, including work from Batches 1–6 and earlier consolidated implementation. It is a code/schema/test evidence map, not a claim that every PRD requirement has been exhaustively tested end to end.

- **Implemented (bounded scope):** implementation and relevant regression evidence exist for the specific behavior described. This does not imply that the whole product area is complete.
- **Partial:** useful implementation exists, but one or more material acceptance, integration, operational, or end-to-end requirements remain.
- **Missing / not verified:** this branch does not provide sufficient evidence to claim the capability is complete.
- **Not verified** means no supporting evidence was established in this audit; it must not be interpreted as success or failure.

PRD v1.5 remains the product source of truth. The audit must not promote historical checkpoint CI results to current-head evidence or treat unavailable CI logs as proof of a particular failure cause.

## 2. Executive assessment

The cumulative backend has progressed beyond the original API/data-model foundation. It now includes candidate profile and evidence provenance, conservative job/evaluation decisions, persisted and review-gated requirements, candidate-specific Punt qualification, governed AI execution and task contracts, document-import execution traceability, application-package readiness gates, candidate-controlled application lifecycle transitions, and unified append-only audit history. It also has durable work-item/outbox primitives and source/acquisition controls.

Those are meaningful backend capabilities, but they are **not a complete end-to-end JobPunter product**. The principal unresolved gates are cross-repository frontend compatibility with AI input contract v4; end-to-end workflow acceptance across acquisition → canonical job → reviewed requirements → evaluation → Punt → approved package → candidate-recorded submission; real provider/source integrations and operational workers; and deployment/operations readiness.

### Current acceptance boundary

- **Batch 5:** backend application lifecycle and unified audit projection scope is closed, with PostgreSQL regression coverage.
- **Batch 6:** backend governed AI execution scope is closed for the defined batch, on the executable head identified in `10. Frontend compatibility and live-provider behavior remain outside that closure.
- **Cumulative PR:** remains open and draft. No merge or deployment is authorized or claimed.
- **Local tests:** no local test-suite execution is claimed. Verification evidence is remote CI only.

## 3. PRD v1.5 requirement map

| Area | Evidence in the cumulative backend | Status | Remaining acceptance work |
|---|---|---|---|
| API foundation and domain errors | FastAPI application, versioned routes, request/error handling, service/domain layers | Partial | Full endpoint-contract and authorization review; end-to-end acceptance |
| Identity, accounts and authorization | User/external-identity models and authentication seams; candidate-scoped ownership checks in implemented workflows | Partial | Verify production identity-provider configuration, account lifecycle, authorization boundaries and negative cross-tenant coverage across all endpoints |
| Candidate Master Career Profile | Candidate-owned structured profile sections; revision-checked partial PATCH; typed known fields; repeatable records; optimistic concurrency | Partial | Guided frontend workflow, comprehensive domain validation, broader profile lifecycle acceptance |
| Candidate evidence and claims | Candidate-owned evidence/claims, approval and correction/resubmission paths, approved-claim immutability, supporting-evidence protection | Partial | Complete candidate-facing evidence-management flow and end-to-end import-to-evidence acceptance |
| Profile fact provenance and conflict review | Fact-source records, profile revision/actor/time, verified links to confirmed document imports, conservative cross-section conflict signals and provenance endpoint | Partial | Frontend review UX and broader acceptance of all import/conflict scenarios; conflicts are signals, not automatic truth adjudication |
| Candidate preferences and evaluation policy | Revisioned preferences; deterministic checks for location/work authorization, remote-only, salary comparability/minimum, excluded industries/types, employment type, fees and relocation | Partial | Complete PRD traceability and end-to-end policy regression coverage, including all decisive-fact combinations |
| Source registry and source health | Source/connector controls, health observations, explicit route classifications, permitted-source configuration seams | Partial | Real permitted connector validation, credential/permission lifecycle, operational health monitoring |
| Acquisition runs and raw records | Acquisition services/models, truthful fetched/persisted/duplicate counts, safe failure observations, improved deduplication and snapshot identity | Partial | Crash recovery, production retries, integration through canonicalization, connector-specific idempotency and real-source validation |
| Work items and outbox | Atomic idempotent enqueue, transactional outbox event creation, leased dispatch, acknowledgement and bounded retry | Partial | Production handlers, runnable worker process, handler-level idempotency, monitoring and recovery acceptance |
| Canonical job pool and provenance | Job/JD-version/requirement/resolution/provenance models; conservative identity resolution; completeness and normalization helpers | Partial | Fully integrated canonical create/update flow from acquisition/manual ingestion; immutable snapshot and provenance acceptance across real inputs |
| Manual URL/JD ingestion and JD lifecycle | Manual intake seams, JD versions/hash/completeness, freshness/lifecycle gates and route verification records | Partial | Prove complete ingestion-to-canonical-job workflow; retrieval adapters, closure/freshness observation and external URL behavior |
| Requirement extraction and review | Versioned persisted requirements, extraction execution trace, review events, source-excerpt and extraction-version gates | Partial | End-to-end extraction, candidate/admin review UX, recovery and acceptance against representative JDs |
| Candidate × job evaluation | Immutable input snapshots, staleness checks, separate fit dimensions, evidence-matching contract v4, conservative REVIEW behavior and candidate override history | Partial | Full PRD acceptance across all evaluation dimensions and a tested end-to-end canonical-job workflow; frontend integration |
| APPLY / REVIEW / WATCH / REJECT and Punt | Deterministic decision policy, hard blockers, uncertainty-aware REVIEW, explicit eligibility/route/freshness/JD gates, idempotent candidate-specific Punt qualification | Partial | Full traceability against all PRD decision cases, candidate-facing override flow, wider lifecycle/concurrency acceptance |
| Review Queue | Evaluation/Punt data and candidate-scoped backend seams | Partial | A verified candidate-facing queue contract and integrated queue workflow; no claim of completed frontend |
| AI task registry and governed execution | Server-owned task/provider configuration, configuration fingerprint validation, explicit fallback routing, execution telemetry and output contracts | Implemented for Batch 6 backend scope | Live provider/model availability, operational metrics/cost controls and cross-repo integration remain unverified |
| AI input contracts | Bounded task-specific JSON schemas for all 12 registered tasks; v4; rejects malformed/free-form payloads, unknown fields/tasks and missing required context/evidence | Implemented for Batch 6 backend scope | Frontend callers must send the new domain-shaped JSON payloads; contract compatibility has not yet been verified in the frontend repository |
| AI output provenance and execution trace | Output validation; source-grounding checks for profile extraction and requirement classification; input/output fingerprints; immutable contract snapshots; safe validation outcomes | Implemented for Batch 6 backend scope | Broader task-specific adversarial/output-quality acceptance and live-provider behavior remain separate gates |
| AI downstream references | Document imports link the execution that generated a preview; application-package items, claim validation, semantic evaluation and requirement extraction retain execution provenance | Implemented for the covered persistence paths | Verify all production paths and retry/replay scenarios end to end |
| Document import and review | Private-storage abstractions and document import/preview/conflict-review work exist in earlier cumulative implementation; Batch 6 adds durable AI execution reference | Partial | Verify real storage configuration, file security, complete extraction/review/confirmation flow, OCR if required, and live integration behavior |
| CV/application artifact generation | Application-package and document-generation capabilities exist in earlier cumulative work; package items retain AI execution references | Partial | Validate actual DOCX/PDF artifacts, versioning, storage/download authorization, claim validation and full package readiness flow |
| Application packages and approval | Versioned package/readiness concepts and approval gates; current job/JD/package snapshot checks before approval/submission | Partial | Complete candidate-facing editing/review UX, all artifact combinations and end-to-end acceptance |
| Application lifecycle | Explicit states and transitions, required reason/optional notes, candidate actor identity, approval/submission gates, lifecycle event persistence | Implemented for Batch 5 backend scope | Full cross-feature end-to-end workflow and frontend compatibility |
| Unified audit history | Migration `0034_application_lifecycle_audit`, application/admin audit projection, candidate-owned history query and database-level UPDATE/DELETE protection | Implemented for Batch 5 backend scope | Verify operational retention/monitoring and any future event producers against the same invariant |
| Protected admin governance | Versioned AI configuration, publication validation, immutable configuration history, admin execution response/telemetry fields | Partial | Verify deployed authorization boundary, operational administration UX, rollback/rotation procedures and production configuration |
| Scheduling and notifications | No complete working product path established by this audit | Missing / not verified | Schedules, preferences, durable history, notification providers, idempotent delivery and partial-failure visibility |
| Object storage and external integrations | Storage/integration seams and prior adapter work exist | Partial | Prove live credentials, private access, expiring downloads, consent/revocation, failure behavior and production storage configuration |
| Security and privacy | Candidate ownership checks, safe error categories, no raw candidate prompt/output persistence in the governed AI trace, privacy-minimal fingerprints | Partial | Whole-API threat/authorization review, upload security, secret redaction, retention policy and deployed configuration review |
| Deployment and operations | CI verification, migration chain, schema parity checks and application configuration | Partial | Staging deployment, runnable workers, readiness/health checks, logs/alerts, backup/restore, real-provider/source validation and release runbook |

## 4. Cumulative implementation record

This section records the material changes carried forward on the cumulative branch. Batch labels identify delivery checkpoints; they are not separate PRs or a claim that every product-level requirement is complete.

### Earlier foundation and Batches 1–2

- Established and hardened the backend API/domain/persistence foundation and corrected core-flow invariants.
- Added or reconciled source/acquisition controls, source health observations, leases, manual ingestion and truthful acquisition-run reporting.
- Improved raw-record identity/deduplication so distinct records with shared content are not indiscriminately collapsed, while changed-content snapshots can be represented.
- Reconciled ORM and Alembic schema gaps, consolidated the migration path, corrected migration constraints, and added PostgreSQL-backed CI verification.
- Added durable work-item/outbox primitives: enqueue and outbox event are written transactionally; dispatch uses expiring leases, acknowledgement and bounded retry.
- **Still open:** production handlers and a runnable worker; robust operational recovery; full acquisition-to-canonical-job integration; permitted live connector validation.

### Batch 3: profile, evidence, preferences and provenance

- Added candidate-owned Master Career Profile sections and a candidate profile GET/PATCH API. PATCH updates only supplied sections, requires an expected revision, rejects stale writes and preserves untouched sections.
- Added distinct typed schemas for repeatable profile records while retaining forward-compatible unknown fields where specified; empty/all-blank repeatable records are rejected, while an empty list clears a section.
- Kept candidate preferences separate and revisioned. Deterministic evaluation applies explicit location/work-authorization, remote-only, salary, industry/job-type, employment-type, application-fee and relocation constraints. Missing or incomparable decisive facts produce REVIEW rather than inferred eligibility.
- Restricted evaluation to approved/effective evidence. Claims without current approved supporting evidence are excluded from the evaluation snapshot with a reason.
- Distinguished contradicted claims from unsupported claims; added revision-checked correction/resubmission for DRAFT/PENDING_REVIEW claims and kept approved claims immutable.
- Prevented archival of evidence that supports an approved claim; added transaction/ownership/revision/evidence regression coverage.
- Added conservative conflict signals across stable record anchors and profile identity, without pretending to decide which conflicting source is true.
- Added fact-level provenance and candidate-scoped profile provenance access. Links to confirmed imports validate candidate ownership, confirmation, value equality and source-excerpt presence in parsed content.

### Batch 4: evaluation, requirement provenance and Punt qualification

- Expanded immutable evaluation snapshots to capture profile/evidence/claims/preferences, job and JD identity/completeness, route verification, persisted requirements and review events, deterministic policy version, and relevant AI execution/config/output-contract provenance.
- Added staleness detection against material source changes, including reviewed requirements, JD identity/hash/completeness, route verification, evidence/claim support, preferences, policy version and used output-contract version.
- Restricted semantic matching to persisted requirements belonging to the exact JD version. Requirements must be reviewed, have verifiable source excerpts and extraction-version provenance, and belong to a stable extraction state; missing evidence/provenance leaves the evaluation in REVIEW rather than silently re-extracting.
- Added explicit evidence-matching states and provenance-reference rules. AI match states cannot create deterministic hard blockers.
- Added separate evaluation dimensions and conservative handling of missing approved evidence (REVIEW with a null score, not a zero-fit rejection).
- Required a current successful URL-matched application-route verification; URL syntax alone is insufficient. Explicit route classifications are accepted from known metadata/configuration sources; unrecognized or missing classifications remain unknown rather than inferred from a URL/domain.
- Kept Punt qualification distinct from APPLY. Qualification requires a current scored evaluation, no hard blockers, deterministic eligibility, mandatory requirements met, verified active route, current freshness, active lifecycle and a substantively complete latest JD. Creation is candidate-specific and idempotent, with source rechecks.
- Added append-only candidate evaluation override/correction events. Overrides preserve the immutable evaluation/factual snapshot, enforce ownership and revision checks, reject stale evaluations, and cannot bypass deterministic hard blockers.
- Added PostgreSQL regression coverage for persisted requirement → governed match → APPLY → Punt, review behavior, staleness, route/freshness/JD gates and concurrent idempotent qualification.

### Batch 5: application lifecycle and unified audit

- Standardized candidate-facing application states as `PLANNED`, `APPROVED`, `SUBMITTED`, `INTERVIEW`, `OFFER`, `ACCEPTED`, `REJECTED`, `WITHDRAWN` and `CLOSED`; transition mapping is documented in `docs/application-lifecycle.md`.
- Required a transition reason and supported optional notes. Events record authenticated candidate actor identity; system-originated transitions are rejected by the domain transition function.
- Gated approval on current/active job state, substantive latest JD completeness, fresh successful URL-matched route verification, latest package approval/readiness and matching job/JD snapshot. Submission rechecks readiness.
- Added migration `0034_application_lifecycle_audit`, unified application/admin audit projection, candidate-owned audit-history query, backfill, and PostgreSQL protection against UPDATE/DELETE of unified audit rows.
- Added PostgreSQL regression coverage for application creation and candidate-recorded withdrawal projection, actor, state transition, reason/notes, admin-event projection, immutability, ownership, invalid transitions, approval gates and concurrency.
- **Batch 5 disposition:** backend scope closed; this is not evidence that the complete candidate application workflow or frontend is finished.

### Batch 6: governed AI execution and task contracts

- Added payload-fingerprint verification for published AI task configuration; mismatches fail closed before provider execution.
- Added database protection against deleting/truncating governance configuration history or mutating version identity, authoring metadata, payload and payload fingerprint, while preserving allowed publication lifecycle transitions.
- Added validated fallback provider/model pairs. Execution can attempt the configured alternate provider when the primary fails or returns invalid structured output; fallback attempts/use are recorded, and fallback output must pass the same server-owned output contract.
- Added task-specific bounded JSON input schemas for all 12 registered tasks. Input contract version is `v4`. Governed execution no longer falls back to a free-form prompt; malformed JSON, missing required domain context/evidence, unknown fields and unknown task IDs fail closed.
- Updated evidence matching to accept the production payload shape, including JD-version context, persisted requirement provenance, evidence revisions and bounded candidate preferences. Regression tests cover acceptance, unknown fields and duplicate identifiers.
- Added privacy-minimal SHA-256 input and parsed-output fingerprints. Raw candidate prompt and output content are not persisted as part of this execution trace.
- Added explicit validation outcomes (`PASSED`, `REJECTED`, `NOT_RUN`) and safe categories; corrected admin execution serialization for contract version/fingerprint, fallback telemetry and validation results.
- Added immutable per-execution contract snapshots recording effective instructions, policy versions, input/output schemas and versions, provider/model/fallback configuration and published configuration identity. Legacy executions are marked unavailable rather than given invented provenance.
- Added output-provenance checks for profile extraction and requirement classification: excerpts must be present in supplied source text, and classification must preserve each supplied requirement exactly once. These output contracts are versioned `v2`.
- Added migration `0042_ai_output_fingerprint` for the output fingerprint and migration `0043_document_ai_execution_ref` for the durable link from a successful document import preview to the exact AI execution that produced it.
- Audited execution references across document import, application-package items, claim-validation reports, semantic evaluation snapshots and requirement-extraction snapshots.
- **Batch 6 disposition:** backend acceptance scope closed for these governed-execution behaviors. Frontend input-contract compatibility, live provider/model availability and end-to-end product workflow remain unverified.

## 5. Migration and persistence notes

The cumulative branch contains migrations covering the evolving schema, including (non-exhaustive examples relevant to the recorded work):

- `0004`: job salary fields, raw-record identity/versioning and related schema corrections.
- `0005`: job metadata/resolution timestamp alignment.
- `0032_candidate_profile_fact_provenance`: profile fact source/provenance records.
- `0034_application_lifecycle_audit`: unified application/admin audit history and immutability.
- `0039_ai_input_fingerprint`: privacy-minimal AI input fingerprint and legacy-unavailable handling.
- `0040_ai_validation_result`: AI validation outcome telemetry.
- `0041_ai_contract_snapshot`: immutable per-execution contract provenance.
- `0042_ai_output_fingerprint`: privacy-minimal parsed-output fingerprint.
- `0043_document_ai_execution_ref`: document-import-to-AI-execution reference.

The full cumulative migration chain and SQLAlchemy/Alembic schema parity were included in the passing backend-verification workflow described below. This does not by itself prove production migration safety, rollback strategy, live data migration behavior or deployed database configuration.

## 6. Verified behavior and regression evidence

The cumulative tests include unit and PostgreSQL integration coverage for:

- candidate ownership, stale revisions, profile PATCH behavior, evidence validity and conflict correction/resubmission;
- fact-provenance links, value/source validation and candidate isolation;
- deterministic evaluation policy, explicit route classifications, persisted requirement review, evidence provenance, stale snapshots, candidate overrides and Punt idempotency;
- application lifecycle transitions, reason/notes, approval gates, actor attribution, unified audit projection and audit immutability;
- AI task registry/input schemas, rejection of legacy free-form prompts, source grounding, output contracts, config fingerprint mismatch, fallback success/failure/invalid output, execution trace, fingerprints and contract snapshot immutability;
- durable document-import AI execution reference.

These are focused regressions, not a claim that every listed capability has been tested end to end through the deployed UI and real external services.

## 7. Current CI evidence and limitations

### Latest executable-code verification before this audit-only edit

- **Executable head:** `a87c127340e22d7f1c8edb56cec1fb902fe8c691`.
- **CircleCI:** `ci/circleci: backend-verification` passed on that exact head. The workflow covers Python compilation, a fresh PostgreSQL 17 migration chain, Alembic schema parity, unit tests and PostgreSQL integration tests.
- **GitHub Actions:** run `38515567717` failed. Its job record had no retrievable steps/logs. The cause is **unknown**; it is not attributed to quotas or a code defect without evidence.
- **Local test suite:** not run for this verification; no local pass is claimed.
- **Merge/deployment:** none claimed.

This document edit creates a new documentation commit after the executable head above. The CI result on `a87c127340e22d7f1c8edb56cec1fb902fe8c691` is evidence for the executable code at that head, not a claim that a workflow has already run on the new documentation commit.

## 8. Open integration and release gates

1. **Frontend contract integration:** inspect the frontend repository separately and update/verify all callers to send task-specific JSON payloads compatible with backend input contract v4. Do not weaken the backend contract simply to preserve legacy free-form prompts without a deliberate specification decision.
2. **End-to-end candidate workflow:** demonstrate the complete path from permitted/manual acquisition through canonical job and JD versions, reviewed requirements, evaluation, Punt/Review Queue, package creation and approval, and candidate-recorded application transition.
3. **Real integrations:** verify permitted source connectors, provider/model availability, object storage, external credentials, authorization, revocation and failure behavior. Configuration syntax and mocked tests do not prove live-service availability.
4. **Workers and scheduling:** implement/run production work-item handlers and worker processes; validate retries, leases, idempotency, crash recovery, scheduling and notification delivery.
5. **Security/privacy:** complete whole-API authorization and tenant-isolation review, upload threat controls, secret handling, retention policy and operational audit/alerting.
6. **Release operations:** staging deployment, migration/rollback plan, health/readiness checks, observability, backup/restore, runbook and real-candidate acceptance.

## 9. Recommended next sequence

- First, perform a frontend-only batch to inventory and reconcile every AI task caller with input contract v4; keep backend changes separate unless a concrete backend defect is found and a deliberate switch is made.
- Then run cross-repository integration tests for AI task requests and the candidate workflow.
- Continue remaining backend work from the open gates above in bounded, evidence-based batches. Keep PR #54 cumulative, draft and unmerged unless explicitly authorized otherwise.

## 10. Release gate

Do not label the backend complete until PRD v1.5 acceptance criteria are mapped to implementation and tests, the core candidate journey passes end-to-end acceptance, migrations are validated in the intended deployment environment, real integrations and worker operations are verified, and source/AI/application states reflect observed outcomes rather than assumed success.
