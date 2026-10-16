# Acquisition & Opportunity Curation — Requirements and Dependency Audit

**Status:** Batch 9 JD/versioning acceptance in progress / Gate 0 requirements audit  
**Repository:** `GoReallyBig-GRB/jobpunter-backend`  
**Audit baseline:** `7eaf79e09c2f0e5b5ae666263c17fe711edcb5d4` (`work/acquisition-curation-architecture-batch-6`); Batch 8 changes are on `work/acquisition-adapter-outcomes-batch-8`.  
**PR history:** #62 → #63 → #64 → #65 → #66 → #67 → #68; these remain draft and unmerged. Per user direction, Batch 8 work continues on `work/acquisition-adapter-outcomes-batch-8`; no new PR is to be opened for this work.  
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

- `app/application/jobs/service.py`, `app/ports/acquisition.py`, and `app/infrastructure/acquisition/outcomes.py`: Batch 7 defines typed acquisition outcomes and Batch 8 adopts them across the six registered connectors. Empty successful results remain `SUCCEEDED`; expected HTTP restrictions, upstream outages, malformed responses and other terminal failures remain distinguishable; page/search/item caps and mid-run failures retain acquired records as `PARTIAL` rather than silently reporting full success. Legacy list normalization remains only for non-registry compatibility callers.
- `app/application/jobs/registered_acquisition.py`: checks source/connector state and permission approval, merges configuration, creates a registered connector and invokes the acquisition service.
- `app/application/common/outbox_dispatcher.py` and `app/workers/outbox.py`: the production dispatcher separates event handlers from an explicit `work_type` registry. `DISCOVERY_MISSION` is explicitly delegated to its durable polling owner and its redundant outbox signal is acknowledged without mutating the WorkItem. Generic registered handlers use persisted WorkItem metadata, row locking, active-duplicate suppression, retries and dead-letter state. Unsupported work is terminally failed instead of retried as transient. No generic WorkItem handler is registered; non-mission generic types remain unsupported until a real producer/handler pair is approved.
- `app/workers/discovery_mission_worker.py` and `app/application/jobs/discovery_missions.py`: a separate worker claims and advances discovery missions; approved pathways are snapshotted and used in discovery/evaluation. Batch 8 preserves the acquisition outcome on each source run and waits for canonicalization when any records were acquired, even if all raw records deduplicated. This worker must not be duplicated by a generic dispatcher.
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
| AOC-05 | Typed connector outcome contract; no silent partial loss (PRD §42; INV-019/030) | All six registered connectors return typed outcomes. JSON API, JobSpy, Playwright and Scrapy preserve partial records across page/search/item failures or configured caps; RSS/Atom and Greenhouse distinguish empty success, restriction, unavailability and invalid payloads. Admin API and worker output expose safe status/error/retry metadata. | PARTIAL | Exact-head CI must pass; live source canaries, provider-specific permission/terms/rate-limit evidence and runtime reliability remain separate gates. Any remaining list-returning caller outside the registry is not treated as typed-outcome coverage and remains subject to the producer/caller inventory. |
| AOC-06 | Idempotent acquisition and durable raw-record provenance (PRD §15; INV-022/023) | Run-key convergence, raw persistence/observation and outbox path exist; acquisition-to-canonical PostgreSQL test exists. | PARTIAL | Execute exact tests and add duplicate/retry/concurrency/failure recovery assertions across registered connectors and raw records. |
| AOC-07 | Canonical job identity, provenance, freshness and safe ambiguity handling (PRD §§14–15; INV-022) | Canonicalization and identity-resolution persistence exist; reviewed evidence does not prove full field/version/closure acceptance across all inputs. | PARTIAL | Test same job across sources, changed posting, weak-similarity ambiguity, last-seen vs verified semantics, and provenance replay. |
| AOC-08 | Actual JD retrieval/versioning and deterministic completeness (PRD §15.2) | Bounded SSRF-resistant public HTTP(S) retrieval exists for manual URL ingestion, with redirect/response bounds and explicit retrieval failure categories. URL-only failure returns a truthful `URL_RETRIEVAL_FAILED` response and indicates pasted-text fallback; manual posting URLs are kept separate from verified application destinations. Canonicalization creates a new JD version on material description change. | PARTIAL | PostgreSQL CI covers manual retrieval success/failure, pasted-text fallback, URL/apply-route separation, changed JD versioning and the incomplete-description extraction gate. Still verify failure visibility/recovery across automated source ingestion, persisted API/UI states, and deterministic completeness/hard-block behavior through evaluation and Punt. |
| AOC-09 | Requirement extraction/review linked to exact JD version (PRD §16; technical spec §40) | PostgreSQL acceptance covers extraction to `NEEDS_REVIEW`, durable failure/retry backoff, skipping incomplete descriptions, stale `IN_PROGRESS` claim recovery without stealing a fresh claim, and independent extraction for JD versions 1 and 2 with each requirement linked to its exact version. Candidate semantic matching already refuses to match `NEEDS_REVIEW` requirements and requires confirmed, source-grounded requirements. A new PostgreSQL regression proves an existing evaluation becomes `STALE` when a new JD version arrives. | PARTIAL | Exact-head backend CI passed for the stale-claim and JD-version invalidation tests. Remaining gates include proving the full user review action → confirmed requirements → evaluation journey end-to-end and deployed-worker restart recovery. |
| AOC-10 | Deterministic hard eligibility and explainable evidence-based dimensions (PRD §§16–17) | Deterministic evaluation policy, hard blockers, candidate evidence, preferences and governed semantic matching exist. | PARTIAL | Acceptance must cover all required dimensions, hard blockers that AI cannot override, contradictory/missing evidence, low-confidence semantic failure and explainability. |
| AOC-11 | Versioned decision rules and evaluation trace (technical spec; INV-027) | Evaluation policy uses a code-level policy version; a governed published rule-version lifecycle and non-mutating preview are not evidenced here. | PARTIAL / likely implementation gap | Inspect governance configuration models and all evaluation callers before implementation; then require immutable published versions, rollback, audit and exact version on each evaluation. |
| AOC-12 | Correct Punt qualification and separation from application state (PRD §17.2) | Punt endpoints and service exist; no complete acceptance trace is established from evaluation through candidate action and recorded application event. | PARTIAL | Test eligibility, repeat/idempotent Punt creation, candidate ownership, evaluation invalidation, and that a Punt never implies submission. |
| AOC-13 | Candidate opportunity feed is real, scoped, explainable and actionable | Authenticated feed route/schema exists for job, evaluation, JD and provenance. | IMPLEMENTED; acceptance NOT VERIFIED | Verify ownership/isolation, pagination/filter correctness, stale/incomplete items, current evaluation snapshots and empty/error behavior against real persisted data. |
| AOC-14 | Explicit production work handlers, idempotency and recovery (PRD §50; INV-023/030) | Batch 8 explicitly delegates `DISCOVERY_MISSION` to `DiscoveryMissionWorker`, prevents the outbox worker from racing that owner, suppresses active duplicate generic execution and dead-letters unsupported types. The production registry has no generic handler; a legacy repository-port dispatcher remains documented as non-production. | PARTIAL / implementation gap | Complete the producer-to-owner inventory; reconcile whether PRD §50 requires the currently direct-polling acquisition, JD extraction, verification, evaluation and notification paths to be migrated to WorkItems or remain separately governed. Add a production handler only when its real operation, lifecycle and recovery are defined. |
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

### Batch 7 — Contract gates (completed in PR #68)
1. Reconcile the acquisition/curation requirements and dependency graph against the current code and the historical open-items draft.
2. Add the typed acquisition outcome contract and persist run/source states for success, partial success, restriction, unavailability and failure.
3. Add explicit work-type handler dispatch, keeping discovery-mission ownership separate and failing unsupported work immediately.
4. Add unit and PostgreSQL integration coverage for outcome semantics, handler selection, trusted work metadata, retries and unsupported work.
5. Update this register with actual implementation status and inspect exact-head CI.

**Batch exit gate:** Passed in CI on PR #68 head `84454f37d74e78c57f66f61807fc4ec0551367c5`. The contract and unsupported-work behavior are explicit; Batch 7 did not claim that adapters already emitted partial results or that generic work types were production-supported.

### Batch 8 — Reliable execution and acquisition (implementation in this branch)
1. Adopt typed results across the six registered connectors; map restriction, unavailability and terminal failures safely.
2. Preserve partial records across paginated/search/page failures and make item/page caps visible as `PARTIAL` rather than silent success.
3. Propagate truthful outcomes through acquisition persistence, admin API, CLI output and discovery source-run state; preserve last successful observation timestamps.
4. Delegate `DISCOVERY_MISSION` to its dedicated worker; test unsupported types, duplicate execution suppression and source-run/canonicalization coordination.
5. Update this register and require PostgreSQL integration plus exact-final-head CI before declaring the batch complete.

**Batch 8 exit gate:** All registered adapters must return typed outcomes; tests must cover empty success, partial, restriction, unavailability, failure, bounded truncation and preservation of acquired records. This does not substitute for live source canaries or runtime verification.

### Batch 9 — Canonical JD and requirements (in progress)
1. Add PostgreSQL acceptance for exact-version requirement extraction, retry/backoff after extraction failure, and exclusion of incomplete descriptions from extraction.
2. Add a regression to the changed-observation path: extract requirements for JD version 1, canonicalize a materially changed JD version 2, then prove version 2 is extracted independently without moving or duplicating version 1 requirements.
3. Continue with failure visibility across automated-source ingestion, the end-to-end human review → confirmed requirement → evaluation journey, and deployed-worker restart evidence before claiming Batch 9 complete.

**Current evidence:** `ci/circleci: backend-verification = success` on `1c3586e24dc0c8ef8ad70a88a510182c6653bc9c`: https://app.circleci.com/workflow/93990911-fbe0-47d6-a4bb-1152c69c855f/job/1772daf5-671a-4d8f-8680-704720e377d1. This includes exact JD-version extraction, stale-claim recovery, and evaluation invalidation tests. It is backend PostgreSQL/unit CI only, not live-source, deployed-worker, or browser acceptance.

### Batch 10 — Candidate-specific curation
1. Reconcile profile/evidence/preferences/pathway requirements field by field.
2. Close deterministic eligibility and evaluation trace/rule-version gaps.
3. Add end-to-end PostgreSQL acceptance through Punt and opportunity feed, including negative cases and candidate isolation.

### After backend acceptance — runtime and frontend handoff
Verify permitted live canaries, scheduler/worker deployment and recovery, monitoring, and then browser-based frontend integration. Gmail OAuth is a separately tracked scope/implementation gate, not implied by manual email parsing.

## 7. Evidence boundary at audit creation

- The exact baseline SHA is `7eaf79e09c2f0e5b5ae666263c17fe711edcb5d4`; Batch 7 CI passed on `84454f37d74e78c57f66f61807fc4ec0551367c5`.
- Batch 8 implementation passed backend CI on `20241a47b6d31c81366284ac2c516cc643b965db`; Batch 9 JD/versioning, stale-claim recovery and evaluation invalidation tests passed backend CI on `1c3586e24dc0c8ef8ad70a88a510182c6653bc9c`: https://app.circleci.com/workflow/93990911-fbe0-47d6-a4bb-1152c69c855f/job/1772daf5-671a-4d8f-8680-704720e377d1. This audit update changes the branch head; its new exact SHA must be revalidated before the documentation commit is considered CI-verified.
- Local tests were not run in this environment.
- No live external-source, deployed worker/scheduler, Gmail OAuth, or browser frontend runtime verification is claimed.
- This register is a scoped requirements/dependency audit. Typed connector outcomes are implemented, but source governance, the full candidate-curation journey, generic work-item coverage and runtime/frontend acceptance remain partial or not verified until their gates pass.
