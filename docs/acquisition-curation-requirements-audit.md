# Acquisition & Opportunity Curation — Requirements and Dependency Audit

**Status:** Batch 7 / Gate 0 baseline audit  
**Repository:** `GoReallyBig-GRB/jobpunter-backend`  
**Audit baseline:** `7eaf79e09c2f0e5b5ae666263c17fe711edcb5d4` (`work/acquisition-curation-architecture-batch-6`)  
**PR stack:** #62 → #63 → #64 → #65 → #66 → #67; all are draft and unmerged.  
**Scope:** Backend acquisition and candidate-specific opportunity curation, from candidate facts and approved pathways through acquisition, canonicalization, JD completeness, requirement evidence, evaluation, Punt and opportunity feed. This audit does not claim runtime or frontend acceptance.

## 1. Governing requirements

The PRD v1.5 is the product authority. Its relevant requirements include:

- Product principles: evidence over keywords; candidate facts are authoritative; deterministic hard constraints; candidate control; explainability; provenance; idempotency; candidate isolation; real production discovery.
- Product sections: §6 Master Career Profile; §7 Preferences and Constraints; §8 Career Pathways and candidate approval; §§12–15 Discovery, Source Registry, Canonical Job Pool, ingestion/normalization; §16 requirement extraction and evidence matching; §17 decisions and Punt qualification; §§18–19 application route and Review Queue.
- Technical sections: §40 relational ownership/integrity; §42 shared connector contract and permitted access; §50 durable background work, lifecycle, transactional outbox and recovery.
- Invariants: INV-017 Real Discovery; INV-018 Source Extensibility; INV-019 Source Truthfulness; INV-022 Acquisition Convergence; INV-023 Acquisition Idempotency; INV-026 Evidence Integrity; INV-027 Configuration Governance; INV-028 Candidate Isolation; INV-030 No Silent Data Loss.
- The PRD's existing Current-State Audit & Gap Closure Control Register must be reconciled, not discarded. Its prescribed evidence path is UI → authenticated request → authorization → service logic → persistence → validation → audit/provenance → correct downstream behavior.

The uploaded **Backend Open Items Draft** is a historical input, not a current truth without rechecking. Some findings are now stale: for example, this baseline contains career-pathway APIs and approved-pathway use in discovery/evaluation. Those items must be reconciled against current code and tests rather than copied forward as still missing.

## 2. Dependency graph — implementation and acceptance order

The dependency order is a gate, not just a preferred task list.

1. **Authority and candidate inputs**
   - Authenticated account and candidate ownership.
   - Master Career Profile and approved evidence; preferences/constraints remain separate from facts.
   - Career pathways have origin and approval state; only approved active pathways may drive matching.
2. **Discovery intent and source eligibility**
   - Candidate discovery settings / mission and approved-pathway snapshot.
   - Source/connector registry, operational state, permission/terms approval, bounded access and provenance.
3. **Acquisition contract and durable execution**
   - Typed connector outcome contract; idempotent acquisition-run creation; raw records and observation provenance.
   - Durable outbox dispatch, explicit supported work types, retries, dead-letter and stale-work recovery.
4. **Canonicalization and job truth**
   - Raw record → identity resolution → canonical Job; duplicate convergence and source provenance.
   - JD retrieval/versioning and deterministic completeness; incomplete/uncertain records remain visible with truthful states, not falsely evaluated as complete.
5. **Requirements and evaluation inputs**
   - Requirements extracted/reviewed against the exact JD version.
   - Candidate profile/evidence snapshot, preference/constraint snapshot, approved pathway/target role, current job/JD version, route/freshness data, rule version and AI task/configuration trace.
6. **Candidate-specific decision**
   - Deterministic hard eligibility/exclusions first; evidence-based dimensions and governed semantic assessment second.
   - Explainable APPLY / REVIEW / WATCH / REJECT; uncertainty and missing evidence remain distinct; a strong score cannot override a hard blocker.
7. **Punt and opportunity feed**
   - Punt qualification remains distinct from evaluation outcome and actual application.
   - Candidate-owned Punt/evaluation data is isolated; the feed returns current, traceable job + evaluation + JD/provenance data.
8. **Operational and product acceptance**
   - End-to-end PostgreSQL acceptance across the chain, failure/retry/idempotency tests, source-specific live canaries where permitted, worker/scheduler runtime evidence, then real frontend/browser contract acceptance.

**Blocking rule:** Do not build or declare end-to-end curation complete until Gates 1–6 have agreed contracts and executable acceptance criteria. If a prerequisite fails or remains unverified, suspend dependent work and record the blocker; do not silently carry it into later batches.

## 3. Baseline evidence inspected

At the audit SHA, these implementation paths were fetched and reviewed:

- `app/application/jobs/service.py` and `app/ports/acquisition.py`: Batch 7 adds `AcquisitionBatchResult` and explicit `SUCCEEDED`, `PARTIAL`, `RESTRICTED`, `UNAVAILABLE`, and `FAILED` outcomes, sanitized failure categories, and retry hints. The service preserves typed terminal outcomes and persists records from partial results. Legacy list-returning connectors are temporarily normalized to success for compatibility, so registered adapters still need to emit typed outcomes to report partial results themselves.
- `app/application/jobs/registered_acquisition.py`: checks source/connector state and permission approval, merges configuration, creates a registered connector and invokes the acquisition service.
- `app/application/common/outbox_dispatcher.py` and `app/workers/outbox.py`: Batch 7 separates event handlers from an explicit `work_type` handler registry. A `WORK_REQUESTED` item is marked RUNNING only after its persisted type resolves to a registered handler; trusted type/resource/idempotency fields come from the database, not event payload. Unsupported work is dead-lettered immediately and marked FAILED with a safe category instead of being retried as a transient error. The production worker currently registers no generic work-type handlers; `DISCOVERY_MISSION` remains owned by its dedicated worker.
- `app/workers/discovery_mission_worker.py` and `app/application/jobs/discovery_missions.py`: a separate worker claims and advances discovery missions; approved pathways are snapshotted and used in discovery/evaluation. This worker must not be duplicated by a generic dispatcher.
- `app/application/opportunities/evaluation_service.py`: candidate-specific evaluation loads the candidate profile, latest JD version, requirement snapshot, preferences, approved pathways, claims/evidence and governed semantic-matching trace.
- `app/application/opportunities/evaluation_policy.py`: deterministic policy includes hard blockers and explicit WATCH/REVIEW handling for closed/stale/inaccessible or incomplete records. Its policy version is currently a code constant; persisted, publishable, rollback-capable decision-rule configuration remains an audit item.
- `app/domain/jobs/completeness.py`: defines explicit JD states and a deterministic completeness assessment.
- `app/api/v1/candidates.py`, `app/api/v1/career_pathways.py`, `app/api/v1/discovery.py`: candidate profile/evidence and pathway/discovery APIs exist in the baseline.
- `app/api/v1/opportunities.py` and `app/application/opportunities/opportunity_feed_schemas.py`: authenticated candidate opportunity feed, evaluation, override, Punt and application endpoints/schemas exist. Their existence alone does not prove the full cross-component journey.
- `app/infrastructure/db/repositories/canonicalization.py`: canonicalization uses the originating run's immutable connector configuration snapshot where available, preserving configuration provenance for delayed processing.
- `tests/integration/test_acquisition_to_canonical_job_postgres.py`: PostgreSQL integration coverage exists for acquisition-to-canonicalization and includes the Batch 6 configuration-provenance regression. Test-file presence is not a test execution claim.

## 4. Requirement coverage register

Status meanings follow the Autonomous Development Protocol: **IMPLEMENTED** means code exists; **TESTED** requires a named test actually executed; **CI-PASS** requires remote CI on the exact SHA; **RUNTIME-VERIFIED** requires target-environment evidence; **COMPLETE** requires all applicable gates. For this audit, unproven acceptance remains **PARTIAL** or **NOT VERIFIED**.

| ID | Requirement / invariant | Baseline finding | Status | Dependency / acceptance still required |
|---|---|---|---|---|
| AOC-01 | Master Career Profile is authoritative; unknown facts stay unknown (PRD §6, INV-026) | Candidate profile and claim/evidence models and candidate APIs exist; field-by-field PRD coverage and effect on every evaluation path have not been proven by this audit. | PARTIAL | Map every profile field to schema, validation, read/write API and evaluation use; test unknown vs explicit negative, revision changes and candidate isolation. |
| AOC-02 | Preferences are distinct from facts and affect intended decisions only (PRD §7) | Candidate preferences are loaded by evaluation; complete field-to-policy mapping and invalidation behavior remain unproven. | PARTIAL | Trace each preference to discovery/evaluation; test missing data versus explicit exclusion and stale-evaluation invalidation. |
| AOC-03 | Candidate-defined and AI-suggested pathways; explicit approval required (PRD §8) | Pathway API/domain exists; discovery and evaluation query approved pathways. | IMPLEMENTED; end-to-end acceptance NOT VERIFIED | Test create/edit/suggest/approve/reject/retire, ownership, and that unapproved pathways never influence discovery or evaluation. |
| AOC-04 | Source governance and truthful permission/access state (PRD §§12–13, §42; INV-018/019) | Source/connector checks and explicit permission-review guard exist; configuration/probe state is not proof of permitted reliable live acquisition. | PARTIAL | Audit every registered connector's access method, terms, fields, rate limits, restrictions and evidence; run permitted bounded canaries. |
| AOC-05 | Typed connector outcome contract; no silent partial loss (PRD §42; INV-019/030) | Batch 7 adds a typed result contract, validates outcome invariants, preserves status/error metadata in AcquisitionRun and maps outcomes to source operational state. Existing adapters still return legacy lists and therefore can only report success or throw. | PARTIAL | Adapt registered connectors to emit typed outcomes where partial/restricted/unavailable states are observed; test real adapter pagination/network failures and retained partial records. |
| AOC-06 | Idempotent acquisition and durable raw-record provenance (PRD §15; INV-022/023) | Run-key convergence, raw persistence/observation and outbox path exist; acquisition-to-canonical PostgreSQL test exists. | PARTIAL | Execute exact tests and add duplicate/retry/concurrency/failure recovery assertions across registered connectors and raw records. |
| AOC-07 | Canonical job identity, provenance, freshness and safe ambiguity handling (PRD §§14–15; INV-022) | Canonicalization and identity-resolution persistence exist; reviewed evidence does not prove full field/version/closure acceptance across all inputs. | PARTIAL | Test same job across sources, changed posting, weak-similarity ambiguity, last-seen vs verified semantics, and provenance replay. |
| AOC-08 | Actual JD retrieval/versioning and deterministic completeness (PRD §15.2) | Completeness state/function exists and worker checks suitability; reusable URL retrieval and complete failure/visibility behavior are not demonstrated by the reviewed evidence. | PARTIAL | Trace URL and manual JD paths through retrieval, versioning, failure states and UI/API visibility; test incomplete JD cannot receive confident APPLY/Punt. |
| AOC-09 | Requirement extraction/review linked to exact JD version (PRD §16; technical spec §40) | Versioned JD requirement snapshots and extraction trace are used by evaluation. | PARTIAL | Test extraction retries/recovery, review-state handling, JD-version invalidation and stale evaluation prevention. |
| AOC-10 | Deterministic hard eligibility and explainable evidence-based dimensions (PRD §§16–17) | Deterministic evaluation policy, hard blockers, candidate evidence, preferences and governed semantic matching exist. | PARTIAL | Acceptance must cover all required dimensions, hard blockers that AI cannot override, contradictory/missing evidence, low-confidence semantic failure and explainability. |
| AOC-11 | Versioned decision rules and evaluation trace (technical spec; INV-027) | Evaluation policy uses a code-level policy version; a governed published rule-version lifecycle and non-mutating preview are not evidenced here. | PARTIAL / likely implementation gap | Inspect governance configuration models and all evaluation callers before implementation; then require immutable published versions, rollback, audit and exact version on each evaluation. |
| AOC-12 | Correct Punt qualification and separation from application state (PRD §17.2) | Punt endpoints and service exist; no complete acceptance trace is established from evaluation through candidate action and recorded application event. | PARTIAL | Test eligibility, repeat/idempotent Punt creation, candidate ownership, evaluation invalidation, and that a Punt never implies submission. |
| AOC-13 | Candidate opportunity feed is real, scoped, explainable and actionable | Authenticated feed route/schema exists for job, evaluation, JD and provenance. | IMPLEMENTED; acceptance NOT VERIFIED | Verify ownership/isolation, pagination/filter correctness, stale/incomplete items, current evaluation snapshots and empty/error behavior against real persisted data. |
| AOC-14 | Explicit production work handlers, idempotency and recovery (PRD §50; INV-023/030) | Batch 7 adds an explicit `work_type` handler registry and immediate terminal handling for unsupported types. Production currently registers no generic work handlers; discovery missions remain separately owned by `DiscoveryMissionWorker`. | PARTIAL / implementation gap | Inventory all producers and required operation types; register only handlers with real production implementations and idempotency/recovery tests. Verify no producer emits a generic request for work owned by the discovery-mission worker. |
| AOC-15 | Full end-to-end acquisition → curation acceptance | Individual paths and acquisition-to-canonical PostgreSQL integration test exist; no inspected evidence proves the complete journey through candidate evidence, JD completeness, requirements, evaluation, Punt and feed. | NOT VERIFIED | Add one controlled PostgreSQL end-to-end suite with seeded candidate evidence/pathway, real registered test connector, canonical job/JD/requirements and assertions on eligibility, explanation, Punt/feed and failure variants. |
| AOC-16 | Runtime worker/scheduler, external-source and frontend acceptance | No runtime evidence was available in this audit. | NOT VERIFIED | Verify deployed process health, crash/restart, monitoring, live permitted source canaries and browser/API integration. Do not infer these from CI. |
| AOC-17 | Connected Gmail authorization and lifecycle | Manual email content ingestion is distinct from a connected mailbox. Gmail OAuth/token/revocation is not evidenced in the reviewed code paths. | NOT VERIFIED / scope decision required | Compare automated email-alert requirement with implemented scope; implement OAuth and consent/token lifecycle if required, otherwise record an explicit accepted limitation. |

## 5. Immediate blockers and rules

1. **AOC-05 is a prerequisite for AOC-06 and AOC-15.** End-to-end tests cannot use an ambiguous acquisition status contract as their truth source.
2. **AOC-14 must be resolved before declaring scheduled acquisition reliable.** Discovery mission processing is a separate supported path; do not route it twice.
3. **AOC-08 and AOC-09 precede AOC-10.** Evaluation must use the exact JD and reviewed requirements version; incomplete or unavailable descriptions must not produce a falsely confident recommendation.
4. **AOC-01 through AOC-03 precede candidate-specific curation acceptance.** Candidate profile, approved evidence, preferences and approved pathways must be real inputs, not fixture-only assertions.
5. **AOC-10 precedes AOC-12 and AOC-13 acceptance.** Punt and feed correctness depend on current, explainable evaluation output.
6. Source-specific permission and live reliability are independent gates; neither can be inferred from connector registration or a successful health probe.
7. No production deployment, PR merge, or frontend acceptance is claimed.

## 6. Ordered implementation plan

### Batch 7 — Contract gates (this branch)
1. Reconcile the acquisition/curation requirements and dependency graph against the current code and the historical open-items draft.
2. Add the typed acquisition outcome contract and persist run/source states for success, partial success, restriction, unavailability and failure.
3. Add explicit work-type handler dispatch, keeping discovery-mission ownership separate and failing unsupported work immediately.
4. Add unit and PostgreSQL integration coverage for outcome semantics, handler selection, trusted work metadata, retries and unsupported work.
5. Update this register with actual implementation status and inspect exact-head CI.

**Batch exit gate:** The contract and unsupported-work behavior are explicit and covered. The batch does not claim that adapters already emit partial results or that any generic work type is production-supported.

### Batch 8 — Reliable execution and acquisition
1. Implement typed acquisition outcomes across connector adapters and repository run-state aggregation.
2. Wire only explicitly supported work types to real handlers; preserve mission-worker ownership and add idempotency/retry/recovery tests.
3. Verify source configuration snapshot provenance and all failure/empty/partial outcomes.
4. Require PostgreSQL integration and exact-head CI.

### Batch 9 — Canonical JD and requirements
1. Complete retrieval, versioning, completeness, and safe failure visibility.
2. Complete extraction/review recovery and exact JD-version linkage.
3. Test deduplication, provenance, freshness, closure and identity-resolution ambiguity.

### Batch 10 — Candidate-specific curation
1. Reconcile profile/evidence/preferences/pathway requirements field by field.
2. Close deterministic eligibility and evaluation trace/rule-version gaps.
3. Add end-to-end PostgreSQL acceptance through Punt and opportunity feed, including negative cases and candidate isolation.

### After backend acceptance — runtime and frontend handoff
Verify permitted live canaries, scheduler/worker deployment and recovery, monitoring, and then browser-based frontend integration. Gmail OAuth is a separately tracked scope/implementation gate, not implied by manual email parsing.

## 7. Evidence boundary at audit creation

- The exact baseline SHA is `7eaf79e09c2f0e5b5ae666263c17fe711edcb5d4`.
- The combined CircleCI status `ci/circleci: backend-verification` is **success** on each exact head for PRs #62–#67. This is inherited baseline evidence, not evidence that the new audit file or future code has passed CI.
- Local tests were not run in this environment.
- No live external-source, deployed worker/scheduler, Gmail OAuth, or browser frontend runtime verification is claimed.
- This document is a scoped requirements/dependency audit and is updated during Batch 7. Typed acquisition outcomes and explicit work-type dispatch are implemented in the working branch. Registered connector adapters remain success-only until they adopt the typed outcome contract. No generic work type is claimed as production-supported.
