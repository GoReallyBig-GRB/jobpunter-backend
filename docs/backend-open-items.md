# JobPunter Backend Open Items

**Status:** Baseline audit compiled; implementation has not started from this document.  
**Scope:** `GoReallyBig-GRB/jobpunter-backend` only.  
**Governing specification:** JobPunter PRD + Technical Specification v1.5, including its appended notification, Admin Control Plane, domain-model, and implementation-control material.  
**Hierarchy:** **Section → Batch → Task**. There is no additional hierarchy level.

## 1. Purpose and execution rules

This document is the single execution plan for completing backend requirements that are missing, partial, blocked by a decision, or not yet adequately verified. It supersedes older task lists as the day-to-day source of open backend work; historical audit documents remain evidence, not competing plans.

For every task:
1. Inspect the current implementation and its callers before editing.
2. State the intended change and acceptance criteria.
3. Implement the task without broad unrelated refactoring.
4. Verify against its acceptance criteria using appropriate unit, API, PostgreSQL integration, security, and/or end-to-end tests.
5. Fix failures before closing the task.
6. Record commit/test/CI evidence and any remaining limitation in this document.
7. Do not mark a task complete because a route, class, model, or test file merely exists.

A batch closes only when all its tasks are complete or explicitly blocked with a documented reason. A section closes only when all its batches are closed. Do not start a later section because a task in the current section is inconvenient. If a genuine dependency forces a sequence change, record the reason and update this document before switching.

No merge or deployment is authorized by this document.

## 2. Status definitions

- **MISSING:** No adequate implementation was found in the inspected backend.
- **PARTIAL:** Some implementation exists, but a material part of the PRD requirement is absent.
- **VERIFY:** Code exists, but acceptance, live integration, operational behaviour, or cross-boundary evidence is not established.
- **DECISION:** The PRD leaves a decision open or contains a material conflict that must be resolved before implementation can be safely finalized.
- **BLOCKED:** A task depends on an unresolved decision or prerequisite. The dependency must be named.

These labels describe the current audit baseline, not task completion.

## 3. Baseline and evidence boundary

### Repository state inspected

- Repository: `GoReallyBig-GRB/jobpunter-backend`
- Authorized working branch: `sub-main-open-items`
- Starting baseline: `0fe7e101ebc616127600627ca9834cbeadd6a95d`, the current `main` merge commit for PR #54.
- PR #54 is merged. Its changes are part of the starting baseline; the former `work/post-42-follow-up-batches` branch is not the active work branch.
- Migration history is linear through `0044_user_account_fields`, which adds email, email verification, photo URL, onboarding status, and last-login fields to `users`.
- The original candidate-fact-provenance revision ID exceeded Alembic's default 32-character `alembic_version.version_num` width and failed fresh PostgreSQL migration. The canonical ID is now `0032_candidate_fact_provenance`; `migrations/env.py` normalizes a legacy overlength stamp to that ID before revision-graph resolution. GitHub Actions passed on commit `2a0b68b7d57bc99caa406a6d1e790289da9dbd8f` (run `38581774361`): 1,068 passed, 1 skipped, 1 warning; compilation, migration chain, and ORM/schema parity also passed.
- This workstream added identity-provider boundary regression tests in commit `83f87c39685a8ce2f074aba758ac81680f42aea8`. CircleCI passed on that exact earlier commit: https://app.circleci.com/workflow/3857f1ca-ae97-43f7-afb4-66e4e6ead78d/job/3e410b87-a66a-4410-a9d8-7b84a665376b. That result does not validate the current candidate.
- Local tests were not run from this tool session. The live Firebase check was performed on 2026-10-14 against project `jobpunter-252525` using test account `devvland@gmail.com`: email/password sign-in PASS; Firebase ID-token acquisition PASS; JobPunter Firebase Admin token verification PASS; token audience and issuer matched `jobpunter-252525` PASS. The protected JobPunter API runtime check is recorded as PASS in the PR #55 merge record. CircleCI reports success on PR #55 merge commit `af7ff74c8a5c185b4365f49727285b625ca954e9` at https://app.circleci.com/workflow/794ec32e-4399-4f9e-91ac-f5b2842a5391/job/a3d2cd81-4951-42a8-b758-d2829cd52e28. These are distinct evidence gates; live auth and protected API checks must not be reported as unverified.

The initial inspection covered the current identity dependency, provider adapter, identity/account ORM models, migration `0044`, relevant authentication tests, and the Section 1 requirements. This is not a claim that every external service was exercised live.

### Existing implementation to preserve and reuse

Do not rebuild these capabilities from scratch without evidence of a defect:

- Firebase ID-token verification and internal user/identity mapping; candidate-scoped profile, claim, evidence-write, preference, evaluation, Punt, application, package and document-import operations.
- Structured candidate profile sections, revision checks, profile-fact provenance, evidence/claim support checks and conflict signals.
- Manual JD ingestion without a URL requirement, shared canonicalization, JD versions, provenance and idempotent replay.
- Manual URL submission **only when title, company and pasted description are supplied**; the endpoint explicitly does not fetch the URL.
- Pasted email parsing and a one-job manual-email path; multi-job parsing exists, but the ingestion endpoint rejects anything other than exactly one processed job.
- Source/run/raw-record models, JobSpy/Scrapy/Playwright adapters, a separate public Greenhouse API adapter, source permission review, acquisition leases, canonical job resolution, provenance and the transactional raw-record-to-canonicalization path.
- Job-description versions, completeness/freshness checks, URL verification, persisted requirement extraction/review, candidate evaluation snapshots, deterministic eligibility checks, candidate overrides and Punt qualification gates.
- Versioned application packages, claim/evidence validation, readiness and approval gates, formatted document generation, scoped signed downloads, application lifecycle events and a database-protected unified audit projection.
- A static registry of 12 AI tasks, task-specific input contract v4, output contracts, provider adapters, configured fallback flags, execution validation telemetry, immutable contract snapshots and input/output fingerprints.
- PostgreSQL migrations and the current CircleCI verification workflow.

### Confirmed material gaps and inconsistencies

- No CareerPathway persistence/API or candidate-approval gate connects the existing pathway AI task to active matching targets.
- No Master CV/CVVariant persistence and editing lifecycle was found.
- Candidate evidence can be created and transitioned, but there is no evidence list/detail/edit API in the current route tree.
- No candidate-facing canonical Job Pool search/list/detail API was found; the current opportunity routes are evaluation/Punt/application routes.
- Manual URL intake does not retrieve a posting. The manual-email ingestion path accepts only one job and does not establish authorized Gmail mailbox ingestion.
- Greenhouse is implemented through a special admin path, is absent from the generic connector registry, and is not part of the official-feed discovery wave. The wave explicitly reserves API/feed types for future connector implementation.
- Ambiguous job-identity resolution records exist, but no resolution review/decision API was found.
- Deterministic evaluation exists, but versioned decision-rule governance and a non-mutating rule preview API were not found.
- Two outbox dispatcher implementations exist. The production outbox worker registers only `RAW_SOURCE_RECORD_ACQUIRED`; the older work dispatcher handles `WORK_REQUESTED`. Discovery missions directly insert and poll work items, while a separate discovery evaluation worker waits for `AWAITING_EVALUATION`/`EVALUATING` states that the current mission worker does not produce. These paths require explicit reconciliation, not more parallel patches.
- Source/configuration snapshots, complete work-item operations APIs, integration-connection records, notifications, user-record APIs, read-only impersonation, and support tickets/messages were not found.
- The User model now includes email, email verification, photo URL, onboarding status, and last-login fields through migration `0044_user_account_fields`; a candidate directory/account-management API was not found.
- The admin API covers grants, AI configuration/execution audit, and a limited source/Greenhouse workflow, but not the full PRD control plane.
- The backend has worker entry points but no backend deployment image/topology; Docker Compose contains PostgreSQL only. Production worker scheduling, health, backup/restore and external-service behaviour are not established.
- The README still points to the historical `PRD_BACKEND_GAP_AUDIT.md`; `docs/admin-governance-runbook.md` contains stale statements about controls that later migrations/tests implement. Documentation must not remain a competing source of truth.

## Section 01 — Architecture Authority, Identity and Account Lifecycle

### Batch 01 — Resolve authority decisions

#### Task S01-B01-T01 — Resolve identity-provider and sign-in decision [DECISION]
- **Current evidence:** Product-owner decision recorded 2026-10-14: Firebase Authentication is the MVP identity provider, with email/password and Google sign-in. The backend must map validated provider identities to a stable internal JobPunter account through a provider-neutral boundary. Google sign-in alone does not authorize Gmail, Drive, Sheets, Calendar or other Google-service access.
- **Required outcome:** Preserve the approved provider and sign-in methods in the product decision record; document account mapping, server-side configuration, provider replacement boundary, and separate consent for Google service integrations.
- **Acceptance:** Decision, rationale, environment requirements, account-mapping implications and migration implications are documented. The implementation and tests match the recorded decision.
- **Execution status (2026-10-14): DECISION RECORDED.** Firebase Authentication is approved for MVP with email/password and Google sign-in. Live project verification on 2026-10-14 passed email/password sign-in, ID-token acquisition, Firebase Admin verification, and audience/issuer checks for project `jobpunter-252525` using `devvland@gmail.com`. The protected JobPunter API runtime check is separately recorded as passed in the PR #55 merge record.

#### Task S01-B01-T02 — Make the selected identity provider replaceable at the trusted boundary [COMPLETE]
- **Current evidence:** `app/ports/identity/__init__.py` defines the `IdentityProvider` port. `app/api/deps.py:get_identity_provider` selects the adapter from server-side `JOBPUNTER_IDENTITY_PROVIDER`, defaults to Firebase, and fails closed for unsupported values. `app/infrastructure/identity/firebase.py` verifies revoked-token status, checks the configured Firebase project, maps only selected validated claims to internal account fields, and resolves the external UID through the unique `Identity` mapping.
- **Required outcome:** Inject the selected provider through a controlled server-side boundary; do not allow request data to choose or spoof the provider.
- **Acceptance:** Provider selection is server-controlled; invalid/expired/revoked credentials fail closed; production requests cannot fall back to `X-User-Id`; provider-specific claims do not leak into domain entities beyond the external identity mapping.
- **Execution evidence (2026-10-15): COMPLETE.** Unit regressions cover server-configured provider selection, unsupported-provider fail-closed behavior, invalid-token 401 handling, production rejection of spoofed `X-User-ID`, and the requirement for both explicit development flags before a development identity can be used. The Firebase adapter verifies revoked tokens against the configured project, resolves the Firebase UID through the unique provider-subject mapping, rejects non-active local accounts, and only maps validated account claims. Live Firebase sign-in, ID-token acquisition, Firebase Admin verification and audience/issuer checks passed on 2026-10-14 for project `jobpunter-252525`; the protected JobPunter API runtime check is recorded as passed in PR #55's merge record. GitHub Actions and CircleCI subsequently passed on exact PR #56 head `17a941baa2dda9bd3fbfc8eee44c3a75558eaa82`, including the unit and PostgreSQL integration suites. No deployment verification is claimed.

### Batch 02 — Complete account records and lifecycle

#### Task S01-B02-T01 — Complete account fields required by the chosen identity contract [COMPLETE]
- **Current evidence:** `app/infrastructure/db/models/identity.py` and migration `0044_user_account_fields` provide display name, email, email verification, photo URL, onboarding status, created/updated timestamps and last-login information. PRD v1.5 §39.2 requires internal account ID, external identity-provider reference, display name/email where supplied, photo URL where available, account status, onboarding status, created/updated timestamps and last-login information where available/appropriate. External provider identity is stored separately in `Identity` with a unique `(provider, provider_subject)` constraint. `FirebaseIdentityProvider._account_claims` accepts only validated provider claims, normalizes email, restricts photos to HTTPS, and derives last-login time from `auth_time`; `_sync_account_claims` does not clear optional stored values when claims are absent and does not regress last-login time. Existing PostgreSQL tests cover stable repeat sign-in, concurrent first sign-in and invalid-token no-account creation. A focused PostgreSQL regression for account-field updates/preservation was added in this batch (`test_firebase_account_claims_update_fields_and_preserve_absent_optional_values`).
- **Required outcome:** Preserve the PRD account-field/source rules and prove them through tests. Do not infer missing account data.
- **Acceptance:** Authenticated sign-in creates or updates one stable internal account; optional fields remain nullable; identity uniqueness and ownership constraints remain enforced; account changes are tested transactionally.
- **Execution evidence (2026-10-15): COMPLETE.** Added the account-claim regression described above and replaced the legacy `DISABLED` fixture with explicit `SUSPENDED`/`DEACTIVATED` authentication-denial cases. CircleCI `ci/circleci: backend-verification` passed on exact PR #56 code/test commit `8277562a436399e2fc44bd1558fe1cd44db45ad5`: https://app.circleci.com/workflow/172c2d0a-6436-4ba5-956b-7f5569e150bb/job/3929b9fe-7daf-480f-9d92-b2424f862bb1. The subsequent documentation update also passed CircleCI on exact current head `eddb19db678b635a679e3c0f5a5f81e40813918e`: https://app.circleci.com/workflow/533da8e6-af1d-41c1-af04-a5ba6606df84/job/31d438ce-b089-4ee0-bbe6-ce629e4cab62. No local run is claimed.

#### Task S01-B02-T02 — Implement supported account-status transitions and audit [COMPLETE]
- **Current evidence:** `PATCH /api/v1/admin/users/{user_id}/status` exists in `app/api/v1/admin.py`. It requires an active admin grant and active actor account, locks the target row, checks `expected_revision`, accepts only `ACTIVE`, `SUSPENDED` or `DEACTIVATED`, rejects no-op/unsupported states, increments revision, and writes `USER_STATUS_CHANGED` audit details (actor, previous/new state, reason, revision, request ID and timestamp) in the same database transaction. Firebase identity resolution rejects any local account whose status is not `ACTIVE`.
- **Recorded MVP transition policy (2026-10-15):** An authorized administrator may change an account from any one of the three supported states to either of the other two states. This explicitly includes reactivating a `SUSPENDED` or `DEACTIVATED` account to `ACTIVE`, and moving an `ACTIVE` account to either non-active state. A request that leaves the status unchanged is rejected. Every accepted transition requires the current revision and a nonblank reason and produces an audit event in the same transaction. Only `ACTIVE` accounts may authenticate. This records the behavior already implemented; it does not add an irreversible deactivation rule.
- **Requirement boundary:** PRD v1.5 §40 names active, suspended and deactivated as supported account-management states but does not define their transition matrix. The product decision recorded for this batch adopts the existing behavior as the MVP policy.
- **Acceptance:** Only explicitly authorized server-side operations can change status; all six distinct transitions among the three supported states are covered by tests; non-active accounts are denied authentication; each material change records actor, reason, time and before/after state; no-op changes are rejected.
- **Execution evidence (2026-10-15):** The implementation and existing authorization/revision/audit tests were inspected. Regression coverage for commit-failure rollback, supported/unsupported values and non-active authentication denial has been added. A parameterized unit test covers all six distinct status transitions. The explicit policy and test are in commit `85414290e05f20662025909fcda2e6e99b2611f0`. CircleCI `ci/circleci: backend-verification` passed on the latest observed head `f9ef53ba76cdd065556150a9274419a6a75e8215`: https://app.circleci.com/workflow/63424019-aae7-4cd4-b5be-94e551ba96a7/job/8251a6b7-4246-43fa-8f58-d3ddf304119e. GitHub Actions run `38696695648` passed on exact code/test head `6adb63e7d477701f8303dc692651c540eff731a3` on attempt 3 after repository visibility was changed to public: https://github.com/GoReallyBig-GRB/jobpunter-backend/actions/runs/38696695648. The subsequent documentation commit `1787e9922048a81ae1489085632179e2c28000b7` also passed GitHub Actions run `38705330172` and CircleCI: https://github.com/GoReallyBig-GRB/jobpunter-backend/actions/runs/38705330172. All workflow steps passed, including migration compatibility, ORM/schema parity, and the unit/PostgreSQL integration suite. This is consistent with the previously reported private-repository monthly Actions quota restriction; the earlier no-runner/no-steps attempts were not test assertion failures. Local tests were not run in this session; exact-head remote CI passed. Both Batch 02 tasks meet their recorded acceptance criteria; no merge is authorized.

## Section 02 — Candidate Profile, Preferences and Career Pathways

### Batch 01 — Complete pathway domain and candidate control

#### Task S02-B01-T01 — Persist career pathways [COMPLETE]
- **Current evidence:** `app/infrastructure/db/models/career_pathways.py` defines candidate-owned `CareerPathway` records and `CareerPathwayEvent` history. Migration `0045_career_pathways` persists title, role family, target roles/seniority, domains, evidence references, gaps, rationale, confidence, source type, approval status, approver, timestamps and revision. Database constraints restrict source/status values and confidence range; candidate/status and pathway-history indexes support scoped reads.
- **Required outcome:** Persist pathway role family, target roles/seniority, domains, supporting evidence references, gaps, rationale, confidence, source type (user-defined or AI-suggested), approval state and timestamps.
- **Acceptance:** Pathways survive sessions and profile revisions; candidate-owned pathways are isolated; source and approval history remain inspectable.
- **Execution evidence (2026-10-15): COMPLETE.** `tests/integration/test_career_pathways_postgres.py` verifies persistence across session expiration, candidate-profile ownership scope, source/approval state, and inspectable event history. PostgreSQL integration coverage also exercises API ownership boundaries and pathway revisions. The implementation/test head `6131ec7628b88c531ee0ba25d9e01f9141530878` passed the pathway-specific PostgreSQL and API regression coverage. The latest exact PR head `5a038027b2e0b99397845f6ceaf1d033698a78e9` additionally covers mission cancellation when an approved pathway changes. GitHub Actions run [38739145715](https://github.com/GoReallyBig-GRB/jobpunter-backend/actions/runs/38739145715) and CircleCI [backend verification](https://app.circleci.com/workflow/3137140d-cc92-4201-9e37-194d94fe378d/job/f1f5b200-a7e9-4b75-bfee-05e7894ef791) passed on that exact head. The remote suite reported 1,099 passed, 1 skipped, 1 warning. Local tests were not run.

#### Task S02-B01-T02 — Add pathway CRUD, review and approval APIs [COMPLETE]
- **Required outcome:** Allow candidates to create/edit user-defined pathways, inspect AI suggestions, approve/reject them, and retire outdated pathways.
- **Acceptance:** AI suggestions never become active matching targets before explicit candidate approval; user-defined and AI-suggested pathways remain distinguishable; cross-candidate IDs are denied.
- **Execution evidence (2026-10-15): COMPLETE.** `app/api/v1/career_pathways.py` exposes candidate-scoped list/detail/create/edit, AI suggestion generation, approve/reject/retire, and event-history endpoints. User-defined pathways are explicitly candidate-created and approved; AI suggestions are stored as `AI_SUGGESTED/PENDING_REVIEW` and cannot be edited or used as approved targets before candidate action. Mutations are revision-checked and evented. Evidence references must identify current approved evidence owned by the same candidate. The `career_pathway_discovery` output contract and task policy are versioned v2; output validation rejects invented evidence IDs and duplicate targets. PostgreSQL API tests cover create/edit, stale revisions, candidate isolation, foreign-evidence rejection, AI pending state, approve/reject, retirement and history. Exact-head CI evidence is recorded above.

#### Task S02-B01-T03 — Connect approved pathways to discovery and matching [COMPLETE]
- **Required outcome:** Use only approved active pathways as scheduled/quick-match targets, while keeping factual profile data separate from preferences/pathways.
- **Acceptance:** Unapproved suggestions cannot influence active target selection; pathway changes are versioned and invalidate affected evaluations/schedules as appropriate.
- **Execution evidence (2026-10-15): COMPLETE.** New discovery missions snapshot only approved pathways, including IDs, revisions and target fields. New missions use only these snapshot targets; an empty approved-pathway snapshot does not fall back to preferences. Missions without an approved-pathway snapshot do not fall back to preference targets; the worker has no target roles for such legacy missions. Candidate evaluations snapshot approved pathways matching the requested target role and become stale when that matching pathway set or revision changes. Tests cover pending suggestions excluded from mission snapshots, immutable mission snapshots, worker target gating, legacy fallback, and evaluation invalidation after pathway changes. Exact-head GitHub Actions run [38739145715](https://github.com/GoReallyBig-GRB/jobpunter-backend/actions/runs/38739145715) and CircleCI [backend verification](https://app.circleci.com/workflow/3137140d-cc92-4201-9e37-194d94fe378d/job/f1f5b200-a7e9-4b75-bfee-05e7894ef791) passed on current PR head `5a038027b2e0b99397845f6ceaf1d033698a78e9`; remote suite result: 1,099 passed, 1 skipped, 1 warning. Local tests were not run.

### Batch 02 — Verify profile and preference contract coverage

#### Task S02-B02-T01 — Complete PRD field-by-field profile acceptance [VERIFY]
- **Current evidence:** Typed structured sections and revision-checked profile updates exist.
- **Required outcome:** Reconcile every PRD §6 field against the current schema and prove editable repeatable records preserve delivery state, dates, responsibilities, stakeholders, tools, achievements, challenges, leadership/ownership and verified-vs-unverified outcomes.
- **Acceptance:** A field-level test matrix maps each required field to schema, persistence, read response and validation; no unverified metric is invented; clearing and revision conflicts behave explicitly.

#### Task S02-B02-T02 — Complete preference-to-discovery acceptance [VERIFY]
- **Current evidence:** Revisioned preference storage and deterministic eligibility checks exist.
- **Required outcome:** Verify every PRD §7 preference is consumed only for its intended purpose, stays separate from factual evidence, and distinguishes missing evidence from an explicit candidate exclusion.
- **Acceptance:** Regression cases cover all supported preference combinations and show that preference edits invalidate/recompute affected evaluations without rewriting profile facts.

## Section 03 — Evidence Library and Document Import

### Batch 01 — Complete candidate evidence management

#### Task S03-B01-T01 — Add candidate-owned evidence list and detail APIs [MISSING]
- **Current evidence:** `app/api/v1/candidates.py` exposes evidence creation and state-transition routes but no evidence GET/list route.
- **Required outcome:** Add paginated list and detail reads for the candidate's evidence, including source, classification/type, status, expiry/verification metadata, linked claims/profile facts and provenance.
- **Acceptance:** Only the owner can read an item; pagination/order are deterministic; expired, rejected, archived and unverified evidence is labelled truthfully.

#### Task S03-B01-T02 — Add controlled evidence editing and health review [PARTIAL]
- **Current evidence:** Evidence status transitions and supporting-evidence protections exist, but an evidence edit/read-management workflow is incomplete.
- **Required outcome:** Support allowed metadata/content corrections with revision checks, immutable audit history, source attribution, conflict indicators, missing-information review and evidence-health status.
- **Acceptance:** Edits cannot silently rewrite approved evidence used by claims/packages; dependent claims/evaluations/packages are invalidated or blocked according to policy; conflicts remain review signals rather than automatic truth adjudication.

### Batch 02 — Complete document-import visibility and recovery

#### Task S03-B02-T01 — Add import history/detail API [PARTIAL]
- **Current evidence:** Upload, parse, extract, preview, conflict, confirm and save routes exist, but no import list/history endpoint was found.
- **Required outcome:** Let candidates retrieve their prior imports and accurately inspect current status, failure category, preview availability and confirmation/save state.
- **Acceptance:** History is candidate-scoped; missing/expired objects are reported accurately; raw document content is not exposed through an unscoped endpoint.

#### Task S03-B02-T02 — Prove the full import-to-evidence lifecycle and recovery [VERIFY]
- **Current evidence:** PostgreSQL tests cover candidate confirmation, conflict review, ownership and concurrent extraction/confirmation.
- **Required outcome:** Verify failures during upload, storage, parsing, AI extraction, preview, confirmation and save leave recoverable, truthful states; connect recovery to the canonical worker plan in Section 10.
- **Acceptance:** No failed extraction becomes authoritative profile data; no saved evidence lacks source/document provenance; stuck imports can be safely retried without duplicate evidence.

## Section 04 — Source Registry and Ingestion Channels

### Batch 01 — Complete manual URL retrieval

#### Task S04-B01-T01 — Implement safe URL-to-JD retrieval with pasted-text fallback [MISSING]
- **Current evidence:** `MANUAL_URL` explicitly returns `MANUAL_DETAILS_REQUIRED` unless title, company and pasted description are supplied. No URL retriever is connected to this endpoint.
- **Required outcome:** Retrieve/extract a JD from a permitted user-supplied URL where feasible, then send it through the common raw-record/canonicalization pipeline. If retrieval fails or content is incomplete, preserve the failure and offer pasted-JD fallback.
- **Acceptance:** SSRF-safe URL validation, bounded redirects/timeouts/response size, public-address protections, safe content-type handling, truthful retrieval/completeness states, source URL provenance and idempotent replay are tested. Restricted/blocked pages are not bypassed.

### Batch 02 — Complete email-content ingestion

#### Task S04-B02-T01 — Ingest multi-job email digests through the shared pipeline [PARTIAL]
- **Current evidence:** The parser can split some multi-job digests, but `MANUAL_EMAIL` rejects any result not safely reduced to exactly one job.
- **Required outcome:** Persist each confidently extracted job as an individual raw candidate through the same canonical pipeline; send ambiguous/irrelevant chunks to review rather than dropping them or inventing fields.
- **Acceptance:** Single-job alerts, multi-job alerts, recruiter messages, irrelevant content, duplicates and changed alerts have explicit outcomes; each job retains email/source provenance; one malformed item does not erase other valid items.

#### Task S04-B02-T02 — Add stable email-message identity and idempotency [MISSING]
- **Current evidence:** The parser generates a random email ID per parse and does not establish durable message identity/deduplication.
- **Required outcome:** Use source message identifiers where available, retain processed state and content fingerprints, and safely distinguish duplicate messages from changed alerts.
- **Acceptance:** Reprocessing the same message does not create duplicate raw records/jobs/notifications; a materially changed alert is recorded as a new observation/version.

### Batch 03 — Unify connector and source-management paths

#### Task S04-B03-T01 — Integrate Greenhouse into the shared connector contract [PARTIAL]
- **Current evidence:** Greenhouse has a working adapter and admin acquisition route, but it is absent from `AcquisitionConnectorRegistry`; the discovery wave reserves official API/feed types and does not include `GREENHOUSE_JOB_BOARD`.
- **Required outcome:** Route Greenhouse through the shared registered-acquisition/discovery path, or document and test a single equivalent abstraction that preserves common permission, idempotency, provenance and downstream handling.
- **Acceptance:** Candidate discovery can invoke an approved Greenhouse source through the same pipeline; no duplicate source-run/canonicalization subsystem is introduced.

#### Task S04-B03-T02 — Implement or explicitly scope API/feed/RSS connectors [PARTIAL]
- **Current evidence:** Discovery wave constants mention API/FEED/RSS and Greenhouse/Lever API types, but the generic registry registers only JobSpy, Scrapy and Playwright.
- **Required outcome:** Implement supported permitted API/feed adapters, or mark each unsupported type explicitly and prevent it from appearing available. Do not treat reserved connector types as implemented.
- **Acceptance:** Each supported connector validates config, checks health, acquires bounded records, returns provenance and safe failure states, and passes contract/integration tests.

#### Task S04-B03-T03 — Provide admin configuration and health management for all supported connectors [PARTIAL]
- **Current evidence:** Admin source creation/health/acquisition is Greenhouse-specific; an operator JSON CLI can configure registered connectors, but it is not an equivalent admin management API and does not provide a full config-history workflow.
- **Required outcome:** Expose authorized create/update/enable/pause/permission-review/health operations for supported connector types, with safe secret handling and audit.
- **Acceptance:** Unsupported connector types are rejected; source and connector states are distinct; configured does not mean connected/healthy; configuration changes and permission reviews are auditable.

#### Task S04-B03-T04 — Verify permitted live acquisition for each enabled source [VERIFY]
- **Current evidence:** Connector tests primarily use controlled/fake clients; current CI does not prove live external source access.
- **Required outcome:** For each source selected for production, record permission/terms decision, access method, rate/pagination limits, fields retained, real health result and bounded live acquisition evidence.
- **Acceptance:** No source is enabled for automated acquisition without explicit permission review; failures/restrictions remain visible; untested sources are labelled unverified/unavailable rather than healthy.

## Section 05 — Canonical Job Pool and Job Description Lifecycle

### Batch 01 — Expose the shared canonical Job Pool

#### Task S05-B01-T01 — Add candidate-facing Job Pool search/list/detail APIs [MISSING]
- **Current evidence:** The route tree exposes evaluations, Punts and applications but no canonical job list/search/detail route.
- **Required outcome:** Provide candidate-safe access to canonical job summaries/details, latest JD version, source/provenance, external ID, discovery method, freshness, closure, verification and application-route state.
- **Acceptance:** Canonical jobs remain shared rather than duplicated per candidate; candidate-private evaluations and applications are not leaked; filters/pagination are bounded; unknown fields remain unknown.

#### Task S05-B01-T02 — Add an identity-resolution review API [PARTIAL]
- **Current evidence:** JobResolution and JobResolutionCandidate persistence and collision tests exist, but no review/decision route was found.
- **Required outcome:** Expose ambiguous identity cases to authorized review, record match/keep-separate decisions and preserve candidate resolution history.
- **Acceptance:** Weak similarity alone cannot merge conflicting jobs; review decisions are attributed and auditable; replays respect the recorded resolution.

### Batch 02 — Complete canonical fields, retrieval and freshness

#### Task S05-B02-T01 — Reconcile canonical Job/JD fields against PRD §14–15 [PARTIAL]
- **Current evidence:** Core job fields, JD versions, provenance and freshness exist; some source facts may only survive in raw/JSON metadata rather than a stable canonical contract.
- **Required outcome:** Map posting date, seniority, JD retrieval status, source timestamps, discovery method/message ID, content fingerprint, completeness, verification and closure fields to durable canonical fields or a documented versioned metadata contract.
- **Acceptance:** A field-by-field contract test proves that source facts survive normalization and versioning without invented values.

#### Task S05-B02-T02 — Add reusable JD retrieval for incomplete source records [MISSING]
- **Current evidence:** Connectors may return descriptions, and verification checks application URLs, but no generic worker retrieves the actual JD from a posting URL when source content is absent/incomplete.
- **Required outcome:** Add a permitted, bounded retriever that updates JD versions and completeness status, preserving the prior version and failure reason.
- **Acceptance:** Retrieval success/failure, incomplete content and blocked access are distinct; URL verification is not mistaken for JD retrieval; evaluation cannot treat an incomplete JD as complete.

#### Task S05-B02-T03 — Complete canonicalization and closure acceptance across real inputs [VERIFY]
- **Current evidence:** PostgreSQL tests cover idempotency, collision review, concurrent convergence, route classification and JD versioning; live-source and complete candidate-facing review remain unverified.
- **Required outcome:** Prove duplicate convergence, material-change versioning, additional provenance, last-seen/verification updates and closure/freshness gates with representative permitted source and manual inputs.
- **Acceptance:** Replays do not duplicate jobs/events; changed descriptions create versions; stale/closed jobs cannot be presented as fresh APPLY opportunities.

## Section 06 — Requirement Extraction and Review

### Batch 01 — Complete extraction lifecycle and reviewer authority

#### Task S06-B01-T01 — Make extraction retries and abandoned executions recoverable [PARTIAL]
- **Current evidence:** JD versions store extraction status, attempts, start/retry time and error category; a worker entry point exists.
- **Required outcome:** Define and test the complete pending/running/succeeded/no-requirements/retryable/permanent-failure lifecycle, including crash recovery and idempotent persistence per exact JD version.
- **Acceptance:** A crashed or stale extraction cannot remain silently running forever; retrying cannot duplicate requirements/review events; every outcome has safe status/error metadata.

#### Task S06-B01-T02 — Reconcile requirement-review authorization with the admin role model [VERIFY]
- **Current evidence:** `/admin/job-requirements` uses `JOBPUNTER_REQUIREMENT_REVIEWER_USER_IDS`, while the main admin control plane uses persisted AdminGrant records.
- **Required outcome:** Explicitly define whether requirement reviewers are a separate role or must use the shared grant model; enforce the chosen rule consistently.
- **Acceptance:** Unauthorized users cannot review; the configured reviewer authority is auditable and documented; production access does not depend on an undocumented environment-only allowlist.

#### Task S06-B01-T03 — Close extraction-to-evaluation version linkage acceptance [VERIFY]
- **Current evidence:** Persisted requirements, review events and execution-trace snapshot helpers exist.
- **Required outcome:** Verify that each evaluation consumes only requirements from the exact current JD version and a stable extraction/review state, with source excerpts and execution provenance.
- **Acceptance:** JD changes, extraction retries or review changes invalidate stale evaluations; unreviewed/unprovenanced requirements cannot silently qualify a candidate.

## Section 07 — Evaluation, Decision Rules, Review Queue and Punt

### Batch 01 — Govern deterministic decision rules

#### Task S07-B01-T01 — Persist versioned decision-rule configuration [MISSING]
- **Current evidence:** Deterministic rules exist in code, and evaluations retain a policy-version string; no RuleSet/RuleSetVersion persistence or admin rule lifecycle was found.
- **Required outcome:** Add draft, validation, publication, immutable version, rollback and audit support for supported decision rules without creating a second evaluation engine.
- **Acceptance:** Invalid rules cannot publish; published rules cannot be mutated; hard constraints cannot be overridden by configuration; every evaluation records the exact rule version used.

#### Task S07-B01-T02 — Add a non-mutating rule preview/evaluation API [MISSING]
- **Required outcome:** Allow authorized administrators to evaluate supplied test inputs against a selected rule version without changing candidate records or creating Punts/applications.
- **Acceptance:** Preview output explains decisions/blockers, records safe audit metadata, and cannot trigger external side effects.

### Batch 02 — Complete evaluation and Review Queue acceptance

#### Task S07-B02-T01 — Prove the PRD decision/dimension contract end to end [VERIFY]
- **Current evidence:** Deterministic eligibility rules, evidence matching, stale snapshots, candidate overrides and Punt gates have broad unit/PostgreSQL coverage.
- **Required outcome:** Reconcile every PRD §16–17 state and dimension against persisted response data: role/domain/technical/seniority/education, location/work authorization, preferences, accessibility, freshness, evidence quality, blockers, gaps, confidence and explanation.
- **Acceptance:** Missing evidence is distinct from evidence of absence; unknown facts do not become inferred facts; one strong dimension cannot conceal a hard blocker; candidate-neutral tests use empty and non-product-specific profiles.

#### Task S07-B02-T02 — Establish the candidate Review Queue contract [PARTIAL]
- **Current evidence:** Candidate evaluation listing and overrides exist, but no separate Review Queue contract was established in the inspected route tree.
- **Required outcome:** Ensure candidates can retrieve and filter evaluations needing judgment, including stale, incomplete, verification-required and evidence-gap cases, without turning REVIEW/WATCH into rejection.
- **Acceptance:** Queue counts and rows are derived from persisted current evaluations; stale items are labelled; candidate ownership is enforced.

## Section 08 — CV Studio and Application Packages

### Batch 01 — Implement Master CV and CV variants

#### Task S08-B01-T01 — Persist and manage the Master CV [MISSING]
- **Current evidence:** AI CV-generation and package/document generation exist, but no Master CV entity and edit/save/regenerate lifecycle was found.
- **Required outcome:** Generate the Master CV from the factual Master Career Profile and provide versioned view/edit/save/regenerate/download operations.
- **Acceptance:** Master CV changes never mutate the factual profile; edits and source revisions are traceable; generated DOCX/PDF files are real formatted documents.

#### Task S08-B01-T02 — Implement approved CV variants [MISSING]
- **Current evidence:** No CVVariant model/API was found.
- **Required outcome:** Persist variants tied to an approved pathway, role family/domain, evidence emphasis, terminology preferences and selected experiences/projects, with review/edit/approval state.
- **Acceptance:** Editing a variant does not modify the Master Profile or another variant; tailored packages reference the exact approved variant/version.

### Batch 02 — Complete candidate editing and package versioning

#### Task S08-B02-T01 — Add package artifact/item edit and save operations [PARTIAL]
- **Current evidence:** Packages are versioned and can be reviewed/approved, but the route tree has no package-item/artifact edit endpoint.
- **Required outcome:** Support candidate edits to tailored CVs, cover letters and application answers, preserving edited content, edit provenance, source snapshot and package version history.
- **Acceptance:** Every answer is editable; editing does not silently overwrite an approved version; regenerated packages create new versions and retain prior artifacts/approval history.

#### Task S08-B02-T02 — Enforce the complete tailored-CV source contract [PARTIAL]
- **Current evidence:** Package source snapshots and claim/evidence validation exist, but no approved CV variant path exists.
- **Required outcome:** Derive tailored CVs from Master Profile + approved CV variant + actual JD version + approved evidence, and preserve requirements addressed/unaddressed and unresolved warnings.
- **Acceptance:** Unsupported facts are blocked/flagged; the source variant remains unchanged; package provenance resolves every material claim to approved evidence.

#### Task S08-B02-T03 — Verify complete document workflow and artifact combinations [VERIFY]
- **Current evidence:** Package readiness, claim validation, document generation and scoped download tests exist.
- **Required outcome:** Test the complete GENERATE → FULL PREVIEW → EDIT → SAVE → DOWNLOAD → REGENERATE lifecycle for supported DOCX/PDF artifacts and all required package combinations.
- **Acceptance:** Content and hashes are revalidated before approval/download; failed generation is not reported as success; access to another candidate's artifact is denied.

## Section 09 — Application Lifecycle and Historical Integrity

### Batch 01 — Resolve the lifecycle contract and complete acceptance

#### Task S09-B01-T01 — Resolve the PRD lifecycle vocabulary conflict [DECISION]
- **Current evidence:** The product flow names SCREENING, INTERVIEW INVITED, WON, LOST and ARCHIVED, while the detailed technical state contract excludes some as canonical states. Current backend documentation chooses the detailed state model.
- **Required outcome:** Record the authoritative candidate-facing state/event mapping before adding states or migration logic.
- **Acceptance:** PRD, API contract, domain transition rules, audit projection and frontend-facing labels use one agreed vocabulary; historical states are mapped explicitly without rewriting history.

#### Task S09-B01-T02 — Complete end-to-end candidate-controlled application acceptance [VERIFY]
- **Current evidence:** Application transitions, revision checks, approval/submission gates and append-only unified audit have PostgreSQL tests.
- **Required outcome:** Prove the full journey from current evaluation/Punt through package readiness, candidate approval, candidate-recorded submission, subsequent outcome and audit history.
- **Acceptance:** No real-world event is fabricated; submission records the approved package version; invalid/stale transitions do not mutate state or append duplicate events.

#### Task S09-B01-T03 — Complete structured outcome and activity history [PARTIAL]
- **Current evidence:** ApplicationEvent captures state, actor, reason, notes and timestamp; no integration event path is established.
- **Required outcome:** Preserve required lost/won reasons and verified integration-originated events only if/when such an integration is approved; do not encode unsupported event types as if they occurred.
- **Acceptance:** Each event has explicit actor/source and before/after state; candidate, system and integration events remain distinguishable.

## Section 10 — Background Work, Scheduler and Operational Recovery

### Batch 01 — Consolidate work-item and outbox architecture

#### Task S10-B01-T01 — Establish one production outbox dispatch path [PARTIAL]
- **Current evidence:** `app/application/common/outbox_dispatcher.py` and `app/application/work/dispatcher.py` implement separate dispatchers. The production `app/workers/outbox.py` registers only `RAW_SOURCE_RECORD_ACQUIRED`; the other dispatcher handles `WORK_REQUESTED`.
- **Required outcome:** Select one authoritative dispatcher/repository/worker design and reconcile event producers, handlers, retry semantics and tests. Do not leave a second production-looking path unused.
- **Acceptance:** Every emitted event type has an explicit handler or an intentional documented terminal policy; no routine event is retried to DEAD because the production worker lacks its handler; idempotency and lease recovery pass PostgreSQL tests.

#### Task S10-B01-T02 — Reconcile discovery mission and discovery evaluation workers [PARTIAL]
- **Current evidence:** `DiscoveryMissionWorker` performs evaluation inline; `DiscoveryEvaluationWorker` polls for AWAITING_EVALUATION/EVALUATING, but the current mission worker does not produce those states.
- **Required outcome:** Choose and document one authoritative mission/evaluation lifecycle; wire state producers and consumers or retire the unreachable path after proving no required behavior is lost.
- **Acceptance:** Every mission state has a reachable producer and consumer; one mission cannot be evaluated twice concurrently; recovery and status transitions are integration-tested.

#### Task S10-B01-T03 — Consolidate discovery-wave decision logic [PARTIAL]
- **Current evidence:** `DiscoveryPolicy.decide_mission` defines a policy, while `DiscoveryMissionWorker` separately implements inline wave advancement.
- **Required outcome:** Use one authoritative policy path or remove the unused one; preserve target counts, partial failures, retry limits and canonicalization waits.
- **Acceptance:** Tests exercise the production policy path; no policy field (including wave retry limits) is silently ignored.

#### Task S10-B01-T04 — Complete WorkItem lifecycle and operational API [PARTIAL]
- **Current evidence:** WorkItem lacks a first-class next-attempt/availability field and does not represent all PRD lifecycle states; no admin work-item/outbox operations routes were found.
- **Required outcome:** Implement the required pending/queued/running/succeeded/partial/retry/failed/cancelled semantics as applicable, with durable retry timing, attempt count, entity references and safe error details. Expose paginated admin inspection and only safe retry/cancel operations.
- **Acceptance:** Retry/cancel respects leases, idempotency, terminal states and handler support; no endpoint merely re-enqueues work unsafely.

### Batch 02 — Make long-running operations recoverable

#### Task S10-B02-T01 — Add recovery for abandoned acquisition runs [PARTIAL]
- **Current evidence:** Acquisition leases and stale-run recovery exist, but recovery is invoked through acquisition activity rather than a demonstrated periodic operational sweep.
- **Required outcome:** Ensure expired RUNNING acquisition attempts are recovered without waiting indefinitely for a future run.
- **Acceptance:** Stale runs become safely recoverable/failed with bounded retries, source health updates and audit/metrics; concurrent recovery cannot double-process a run.

#### Task S10-B02-T02 — Add recovery for interrupted AI executions and document imports [MISSING]
- **Current evidence:** AI executions and imports can persist RUNNING/extraction states; no complete stale-execution/import recovery worker was established.
- **Required outcome:** Detect abandoned operations, classify safe retry vs terminal failure, and retain execution/import provenance. Do not duplicate artifacts or candidate evidence.
- **Acceptance:** Crash simulations prove no item remains silently RUNNING forever and retries do not create duplicate logical results.

#### Task S10-B02-T03 — Define durable execution for required background operations [PARTIAL]
- **Current evidence:** Worker entry points exist for discovery scheduling/missions, requirement extraction, job verification, outbox and document retention, but deployment and durable execution for JD retrieval, document parsing/generation, AI work and notifications are not established.
- **Required outcome:** Map each long-running PRD operation to a durable work request, worker, status and recovery policy; keep synchronous operations synchronous only where justified and bounded.
- **Acceptance:** Every asynchronous operation has observable status, retry limits, idempotency and a documented execution owner.

### Batch 03 — Complete scheduled matching and run history

#### Task S10-B03-T01 — Complete schedule configuration and immutable run snapshot [PARTIAL]
- **Current evidence:** Candidate discovery settings and a server-side scheduler exist, but schedule timezone/day/time, pathway selection and an explicit schedule/configuration version snapshot are not established.
- **Required outcome:** Support the agreed daily/weekly schedule contract and preserve the effective settings/source/pathway configuration for each run.
- **Acceptance:** Browser tabs are not required; editing settings does not rewrite an in-flight run's snapshot; disabled/cancelled schedules do not launch new runs.

#### Task S10-B03-T02 — Complete scheduled-run metrics and failure accounting [PARTIAL]
- **Current evidence:** Mission/source-run rows retain acquired/persisted/canonicalized/usable counts and source failures, but not the complete PRD metric set.
- **Required outcome:** Retain source attempts, raw/normalized/created/updated/merged/skipped/rejected counts where available, JD retrieval/completeness, evaluation outcomes, retries, terminal failures and notification outcome.
- **Acceptance:** Counts are derived from persisted records and are never inferred from queue submission alone; partial source failure is visible and never reported as successful empty discovery.

## Section 11 — Admin Control Plane, User Records, Impersonation and Support

### Batch 01 — Complete the admin API contract

#### Task S11-B01-T01 — Complete admin overview, health and integration-status endpoints [PARTIAL]
- **Current evidence:** Admin routes cover grants, AI config/executions/audit and limited source operations; the public health endpoint is a simple liveness response.
- **Required outcome:** Provide authorized overview/component health and integration-state APIs with configured/authorized/connected/available/degraded/restricted/failed/disconnected/unsupported distinctions where applicable.
- **Acceptance:** Health derives from actual checks/observations; credentials are never returned; unavailable capabilities are labelled unsupported/unknown rather than healthy.

#### Task S11-B01-T02 — Persist versioned AI task definitions and versions [PARTIAL]
- **Current evidence:** The task registry is code-defined; GovernanceConfigVersion persists task configuration, but no persisted AITask/AITaskVersion model or task-definition administration API was found.
- **Required outcome:** Implement the required task-definition/version lifecycle and stable references for purpose/ownership, system policy/task instructions, schemas, validation, provider/model routing and publication metadata without creating a second execution engine.
- **Acceptance:** Invalid definitions cannot publish; executions resolve the exact authoritative task/configuration/schema versions; published definitions are immutable and rollback preserves history.

#### Task S11-B01-T03 — Complete decision-rule and privileged-denial audit coverage [PARTIAL]
- **Current evidence:** Successful admin configuration and source actions are audited; the deterministic decision-rule lifecycle is missing, and denied admin access is not uniformly recorded.
- **Required outcome:** Align with Section 07 rule governance and define which denied privileged actions must be recorded without leaking sensitive data.
- **Acceptance:** Required denied actions produce safe audit events; audit records preserve actor/resource/request ID/outcome; audit immutability and pagination are tested.

### Batch 02 — Implement User Records

#### Task S11-B02-T01 — Add paginated candidate directory and search [MISSING]
- **Required outcome:** Authorized directory search by name/email/internal ID, filters for account status/registration/last activity/verification/support status, and sorting/pagination.
- **Acceptance:** Search uses persisted account fields; unsupported data is labelled unavailable rather than synthesized; candidate privacy and admin permissions are enforced per data category.

#### Task S11-B02-T02 — Add candidate record summaries and supported account management [MISSING]
- **Required outcome:** Expose profile/preferences, evaluations/progress, application tracking, documents/packages, support history and audit links where records exist; implement only explicitly supported account-status operations.
- **Acceptance:** A directory permission does not grant unrestricted document/evidence access; every mutation is separately authorized and audited.

### Batch 03 — Implement read-only impersonation and separate interventions

#### Task S11-B03-T01 — Implement server-enforced read-only impersonation sessions [MISSING]
- **Required outcome:** Persist session ID, administrator, target candidate, reason, start/end, expiry/termination and relevant access events. Return session context for a persistent UI banner, but enforce read-only permissions in the backend.
- **Acceptance:** Only authorized admins can start sessions; expired/terminated sessions cease to authorize access; candidate mutations, external side effects and application submission are blocked; admin remains the recorded actor.

#### Task S11-B03-T02 — Implement separately authorized admin interventions [MISSING]
- **Required outcome:** Define an allowlist of supported support/account interventions; each requires its own permission and reason and uses normal domain validation.
- **Acceptance:** Interventions are attributed to the administrator with target, action, outcome and before/after state; no unrestricted candidate-context writes; no application submission without explicit candidate approval.

### Batch 04 — Implement Customer Support

#### Task S11-B04-T01 — Implement persistent support tickets and messaging [MISSING]
- **Required outcome:** Candidate-created tickets with title/details, two-way text messages, timestamps, open/resolved/closed semantics, unread state, delivery state and linked follow-up tickets after closure.
- **Acceptance:** Candidate ownership is enforced; message failures can be retried without duplicate logical messages; historical threads are preserved; every status/message transition is auditable.

#### Task S11-B04-T02 — Implement authorized admin support inbox and workflow [MISSING]
- **Required outcome:** Paginated inbox, search/filters, candidate context, authorized replies, resolution/closure, and assignment/reassignment where multiple administrators require it.
- **Acceptance:** Admin permissions are checked on every read/mutation; ticket access and support actions are audited; links to User Records/impersonation preserve actor separation.

## Section 12 — Integrations and Notifications

### Batch 01 — Implement integration connection lifecycle

#### Task S12-B01-T01 — Add provider-neutral user integration records and status contracts [MISSING]
- **Current evidence:** No IntegrationConnection/UserIntegration model or common integration-status API was found. The optional Drive backup uses a system-configured refresh token and is not a complete per-user connection lifecycle.
- **Required outcome:** Persist provider, user, granted scopes, authorization/connection state, last observed success, safe latest error, expiry/revocation state and timestamps; keep credentials in designated server-side secret storage.
- **Acceptance:** Configured is not reported as connected; explicit consent, reauthorization, revocation and failure states are represented; signing in with Google never implicitly grants Gmail/Drive/Sheets access.

#### Task S12-B01-T02 — Implement authorized Gmail API ingestion and JobAlert labeling [MISSING]
- **Required outcome:** Implement the selected Gmail API flow with explicit user authorization/scopes, incremental retrieval, JobAlert label handling, stable message IDs, duplicate/changed-message detection and revocation.
- **Acceptance:** Gmail content enters the common ingestion pipeline; no parallel job model is created; message identifiers and source provenance persist; disconnected/expired authorization is visible; Apps Script is not a required dependency.

#### Task S12-B01-T03 — Resolve optional Drive/Sheets scope separately from Gmail [DECISION]
- **Current evidence:** A private, optional Drive backup adapter exists; Sheets export/sync and per-user Drive connection lifecycle were not established. PRD treats these services as optional.
- **Required outcome:** Document whether each optional service is in the agreed MVP. Keep R2/PostgreSQL authority unchanged; implement only selected capabilities with explicit consent and revocation.
- **Acceptance:** No optional integration is advertised as operational without real authorization/health evidence; disconnecting an external copy does not delete the authoritative R2 original.

### Batch 02 — Implement durable in-app and optional email notifications

#### Task S12-B02-T01 — Add persisted notification model and candidate APIs [MISSING]
- **Required outcome:** Persist recipient, event/type, message, timestamp, read/unread state, deduplication identity, source event and delivery metadata; provide candidate-scoped list/read/mark-read APIs.
- **Acceptance:** Notifications persist across sessions; candidates can access only their own records; duplicate event processing does not create duplicate logical notifications.

#### Task S12-B02-T02 — Add event-to-notification handlers [MISSING]
- **Required outcome:** Generate notifications from actual persisted results/events for discovery completion, new Punt, evaluation requiring attention, tracked application status changes and support-ticket events.
- **Acceptance:** Queueing is not reported as delivery; event handlers are idempotent; in-app notifications are created even when email is unconfigured or fails.

#### Task S12-B02-T03 — Add optional email delivery with truthful status and bounded retry [MISSING]
- **Required outcome:** Add a configurable email provider with optional delivery, queued/sent/failed/unknown outcomes, attempt history and safe errors, reusing the canonical outbox/worker path.
- **Acceptance:** Email is not required for in-app notifications; a message is marked sent only after provider confirmation; retries do not duplicate logical notifications; email content avoids unnecessary candidate/ticket PII.

## Section 13 — Security, Observability, Deployment and Final Acceptance

### Batch 01 — Complete security and operational controls

#### Task S13-B01-T01 — Add rate/resource controls and AI usage accounting [PARTIAL]
- **Current evidence:** Request body and task input/token bounds exist, but no complete per-user/global rate limiting, quota enforcement, token/cost accounting or runaway-task protection was established.
- **Required outcome:** Implement appropriate API/provider rate controls and record token/cost data where available; expose safe operational counters.
- **Acceptance:** Abuse/runaway cases are bounded; limits cannot be bypassed through candidate payloads; metrics are based on observed provider results, not estimates presented as actual usage.

#### Task S13-B01-T02 — Complete health, logs, metrics and alerting [PARTIAL]
- **Current evidence:** Request IDs, security headers, safe error handling and worker logs exist; public health is liveness-only and deployment-level worker/alert evidence is absent.
- **Required outcome:** Add readiness checks and operational visibility for database, workers, outbox/dead-letter counts, source acquisition, integrations, AI execution and retention cleanup.
- **Acceptance:** Liveness/readiness are distinct; logs carry correlation IDs and safe categories; alerts can detect stuck work, repeated failures, missing objects and cleanup failures without exposing candidate PII/secrets.

#### Task S13-B01-T03 — Complete whole-API authorization, privacy and SSRF review [VERIFY]
- **Current evidence:** Many candidate/admin ownership and fail-closed tests exist, but the whole API and live deployment configuration have not been accepted end to end.
- **Required outcome:** Review every route for authentication, owner/admin/reviewer permission, sensitive logging, upload/download access, SSRF/redirect/DNS behaviour, secret handling, retention and resource abuse.
- **Acceptance:** Negative cross-tenant and forged-identity tests cover every private resource class; URL fetchers cannot reach private/internal addresses; sensitive errors and logs are redacted; production configuration fails closed.

### Batch 02 — Resolve deployment and data-transition decisions

#### Task S13-B02-T01 — Record hosting, storage, scheduler and initial provider decisions [DECISION]
- **Current evidence:** PRD v1.5 leaves hosting topology, object-storage vendor, first worker deployment mechanism, initial AI providers/models and source feasibility open. Current backend defaults to R2 and Google Gemini while offering a GCS adapter and OpenAI adapter.
- **Required outcome:** Record which current defaults are accepted, which remain options, and which need replacement. Do not infer final approval from an environment default.
- **Acceptance:** Decision record includes costs/limits, secrets, portability, recovery, source permissions and operational ownership.

#### Task S13-B02-T02 — Create deployable backend and worker topology [MISSING]
- **Current evidence:** Repository root has no backend Dockerfile or deployment topology; Docker Compose defines PostgreSQL only. Worker scripts exist but production scheduling/deployment is unverified.
- **Required outcome:** Provide repeatable deployment configuration for API, selected workers/schedulers, PostgreSQL, object storage, secrets, migrations, logs and health checks outside Google AI Studio.
- **Acceptance:** Fresh environment can be deployed without editing application source; worker processes are supervised and restartable; migrations run in a controlled step; no production credentials are committed.

#### Task S13-B02-T03 — Define backup/restore and existing-data migration plan [MISSING]
- **Current evidence:** Alembic schema migrations exist; no source-to-target data migration/cutover script or verified backup/restore evidence was found.
- **Required outcome:** Audit any existing AI Studio/Firestore/browser-held records, define mappings and data-quality rules, preserve ownership/provenance/history, separate demo/test data, and document reconciliation/rollback.
- **Acceptance:** Migration is run against a representative dataset with count/relationship reconciliation; unresolved/duplicate/conflicting records are reported; backup restoration is tested before production cutover.

### Batch 03 — Finalize source-of-truth documentation and acceptance

#### Task S13-B03-T01 — Remove competing/stale backend status documentation [PARTIAL]
- **Current evidence:** README points to the historical `PRD_BACKEND_GAP_AUDIT.md`; `docs/admin-governance-runbook.md` still lists audit immutability and schema-parity checks as open despite later implementation/CI evidence.
- **Required outcome:** Update links/status claims to identify the PRD v1.5 and this Open Items Document as current sources; retain historical documents only with explicit historical labels.
- **Acceptance:** No stale document describes already-implemented controls as missing or old architecture as authoritative; implementation statuses match repository/CI evidence.

#### Task S13-B03-T02 — Run full end-to-end backend acceptance [VERIFY]
- **Required outcome:** Exercise the complete candidate journey using real user-supplied or permitted live job data: ingest → canonical Job/JD version → requirement extraction/review → evaluation → Punt → package generation/edit/approval → candidate-recorded submission/outcome → audit/notifications.
- **Acceptance:** Every stage has persisted records and provenance; failures/partial results remain visible; no fake job, unsupported claim, duplicate event or fabricated real-world action is accepted; all migrations and required integration tests pass.

#### Task S13-B03-T03 — Verify production/demo separation and release gates [VERIFY]
- **Required outcome:** Verify separate production/test databases and credentials, source permissions, provider authorization, private storage, retention, migration safety, monitoring, backup/restore and worker recovery in the intended deployment.
- **Acceptance:** Evidence is recorded for each gate; unknown or untested capabilities remain explicitly unverified; passing CI alone is not represented as production readiness.

## 4. External dependency explicitly outside this backend plan

The frontend repository has not been inspected in this pass. Backend AI input contract v4 is a deliberate contract change. Frontend caller reconciliation must happen in the separate frontend-only phase after the backend work is closed; do not weaken the backend contract merely to preserve legacy free-form prompts.

## 5. Specification decisions that must remain visible

1. **Identity provider:** Product-owner decision on 2026-10-14 approves Firebase Authentication with email/password and Google sign-in for MVP. Keep the backend identity port provider-neutral. Google sign-in does not grant Gmail/Drive/Sheets/Calendar access; each integration requires its own explicit authorization.
2. **Storage vendor:** PRD v1.5 leaves the vendor open; current backend defaults to R2 and has a GCS adapter. Confirm the accepted production choice.
3. **Application state vocabulary:** the high-level product pipeline and detailed technical lifecycle use different labels. Current backend follows the detailed state contract; confirm the canonical mapping before adding states.
4. **Optional integrations:** Drive/Sheets/Calendar are not automatically mandatory merely because they are named. Gmail-based automated alerts are a separate explicit product requirement and require user authorization.
5. **Live sources/providers/deployment:** adapter code and fake-client tests do not establish live availability or permission.

These are recorded decisions, not permission to invent a resolution.

## 6. Completion rule

Work begins at **Section 01, Batch 01, Task S01-B01-T01**. Complete tasks in order within each batch, close each batch before the next, and close the section before moving to Section 02. Update this file with evidence after every task. Do not create another parallel open-items list.
