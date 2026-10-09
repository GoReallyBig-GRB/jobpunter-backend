# Legacy Next.js Repository Backend Parity Audit

**Audit date:** 2026-10-09  
**Legacy source:** `GoReallyBig-GRB/jobpunter-frontend`, commit `702d929024b8baaac50cf03249efe88e2c3c426b` (the commit that introduced `backend/`)  
**Target:** `GoReallyBig-GRB/jobpunter-backend`, branch `audit/backend-functional-parity`  
**Scope:** All backend files introduced by the legacy `backend/` directory, plus adjacent frontend engines that still contain business/AI behavior and therefore must not be mistaken for migrated server functionality.

## Executive result

The legacy `backend/` directory was a **small foundation slice**, not a complete implementation of JobPunter. It implemented a FastAPI shell and a first candidate/evidence workflow. The current separate backend contains corresponding candidate/evidence code plus substantial additional backend domains (opportunities, acquisition, job verification, requirement review, AI task execution, work/outbox). The presence of corresponding code is not, by itself, proof of full behavior parity or production readiness.

The original development identity seam accepted a caller-supplied user ID header. That behavior must **not** be restored. The separate backend's verified-token identity boundary is the intended replacement. Ownership and state-transition semantics should be retained, with stronger route-level and PostgreSQL-backed tests. The audit found two concrete defects: (1) the evidence model/database default was `PENDING_REVIEW` while the transition graph requires new evidence to start at `DRAFT`; (2) the profile endpoint could create a profile with only `flush()`, then return it without committing, so the request-scoped SQLAlchemy session could roll it back on close. The audit branch changes new evidence defaults to `DRAFT` with a forward-only migration that does not rewrite existing rows, and commits a newly created profile in the profile endpoint while preserving a single transaction for profile-plus-evidence creation.

## File-by-file legacy backend inventory

| Legacy path at source commit | Behavior present in legacy code | Target evidence | Audit status / required proof |
|---|---|---|---|
| `backend/app/main.py` | Creates FastAPI app, registers request-ID middleware, domain-error handler, and v1 router | `app/main.py`, `app/api/middleware.py`, `app/api/errors.py`, `app/api/v1/router.py` | **Captured.** Test health/error/middleware behavior and deployment readiness separately. |
| `backend/app/api/middleware.py` | Adds or preserves request ID on the response | `app/api/middleware.py` | **Captured.** Add/retain route-level assertion for returned request ID. |
| `backend/app/api/errors.py` | Structured domain error with code, message, status, optional details | `app/api/errors.py` | **Captured.** Verify common errors retain stable public response contracts. |
| `backend/app/api/deps.py` | Development-only `X-User-ID` principal seam | `app/api/deps.py`, `app/infrastructure/identity/firebase.py` | **Intentionally replaced for security.** Never accept spoofable IDs in production. Verify invalid/missing token and dev-mode gating. |
| `backend/app/api/v1/router.py` | `GET /api/v1/health`; mounts candidate routes | `app/api/v1/router.py` | **Captured and expanded.** Test route registration and auth requirements. |
| `backend/app/api/v1/candidates.py` | Get/create candidate profile; create evidence; submit, approve, reject, archive evidence | `app/api/v1/candidates.py` | **Captured.** Route-level auth, ownership, request validation, response schema, and real PostgreSQL behavior need coverage. |
| `backend/app/application/candidates/schemas.py` | Profile, evidence-create, evidence-response and expected-revision request contracts | `app/application/candidates/schemas.py` | **Captured.** Add invalid/oversized input and alias/contract tests where clients depend on them. |
| `backend/app/application/candidates/service.py` | Profile creation; evidence-source/evidence persistence; ownership-checked transitions; stale-revision conflict | `app/application/candidates/service.py` | **Captured with implementation differences.** Verify transaction atomicity, concurrent revision conflicts, source/evidence FK integrity, cross-user isolation, and rollback. |
| `backend/app/domain/candidate/errors.py` | Invalid evidence transition domain error | `app/domain/candidate/errors.py` | **Captured.** Ensure mapped to stable HTTP conflict response. |
| `backend/app/domain/candidate/service.py` | Validates evidence state transition, increments revision, stamps verification/update times | `app/domain/candidate/service.py` | **Captured.** Unit tests exist; route/database tests must validate the complete persisted lifecycle. |
| `backend/app/domain/candidate/states.py` | Allowed state graph: DRAFT, PENDING_REVIEW, APPROVED, REJECTED, ARCHIVED | `app/domain/candidate/states.py` | **Captured.** Add transition-table tests for every allowed and forbidden edge. |
| `backend/app/infrastructure/db/models/base.py` | SQLAlchemy declarative base, UUID IDs, revision fields | `app/infrastructure/db/models/base.py` | **Captured.** Alembic schema drift check passes in CI; migration upgrade/downgrade and data-preservation tests remain useful. |
| `backend/app/infrastructure/db/models/identity.py` | User and external identity mapping | `app/infrastructure/db/models/identity.py` | **Captured and expanded.** Verify unique identity, account linking, race handling, and token-to-user mapping. |
| `backend/app/infrastructure/db/models/candidate.py` | Candidate profile, claims, evidence, sources, claim-evidence links | `app/infrastructure/db/models/candidate.py` | **Captured and expanded.** Validate constraints, cascades/restrictions, source provenance and tenant isolation. |
| `backend/app/infrastructure/db/session.py` | SQLAlchemy engine/session and DB dependency | `app/infrastructure/db/session.py` | **Captured.** CI migrates a real PostgreSQL service; production pool sizing, TLS, backup/restore and readiness remain deployment concerns. |
| `backend/app/config/settings.py` | Environment-based app/database settings | `app/config/settings.py` | **Captured.** Production secret/config validation and safe startup failure require deployment checks. |
| `backend/app/ports/identity.py` | Authenticated principal / identity-provider seam | `app/ports/identity.py` | **Captured.** Keep provider-independent domain identity and test principal mapping. |
| `backend/app/security/authorization.py` | Pure ownership predicate and admin context | `app/security/authorization.py` | **Captured but insufficient alone.** A helper test is not proof that every route invokes ownership checks. |
| `backend/migrations/versions/0001_identity_candidate.py` | Identity and candidate schema migration | `migrations/versions/` | **Captured in migration history.** CI upgrades schema; production data migration/rollback plan must be reviewed before deployment. |
| `backend/migrations/versions/0002_candidate_evidence.py` | Evidence/source/claim schema migration | `migrations/versions/` | **Captured in migration history.** Validate migration against representative existing data before production. |
| `backend/migrations/env.py`, `backend/alembic.ini` | Alembic configuration and model metadata registration | `migrations/env.py`, `alembic.ini` | **Captured.** Current CI runs `alembic upgrade head` and `alembic check`. |
| `backend/pyproject.toml` | Python dependencies and test/lint configuration | `pyproject.toml` | **Captured and expanded.** Dependency audit, lock/reproducibility and vulnerability scanning are still needed. |
| `backend/.env.example` | Local app/database configuration example | `.env.example` | **Captured in principle.** Must never contain real secrets; deployment variables must be documented separately. |
| `backend/tests/unit/test_authorization.py` | Pure ownership predicate tests | `tests/unit/test_authorization.py` | **Captured but shallow.** Add route-level cross-user denial tests. |
| `backend/tests/unit/test_evidence_states.py` | Basic allowed/forbidden state transition tests | `tests/unit/test_evidence_states.py` | **Captured but incomplete.** Full transition matrix and API/database tests required. |
| `infrastructure/docker/docker-compose.yml` | Local PostgreSQL service for development | Backend infrastructure config | **Functionally represented; verify exact current setup.** Local compose is not a production deployment definition. |
| `docs/architecture/foundation-01.md` | Documents the foundation slice and its limits | Backend docs | **Historical document.** This audit supersedes it for parity status. |

## Adjacent frontend behavior: not equivalent to the legacy backend directory

The same source commit contains TypeScript engines under `src/engine/`. They are application/client code, not files from the legacy `backend/` directory, but they can carry important product behavior:

- `src/engine/aiProvider.ts`: candidate profile extraction, CV conflict detection, deterministic fallback behavior, requirement evaluation helpers and other domain workflows.
- `src/engine/evaluator.ts`: requirement/evidence scoring and decision logic.
- `src/engine/emailParser.ts`: email/job information parsing.
- `src/engine/providers/*`: client-side provider/model registry and provider implementations at the legacy source commit.
- `src/services/*`: Google Workspace integration clients.

The backend must not be described as parity-complete until each of these client-side domain behaviors has been classified as (a) presentation-only and intentionally retained, (b) migrated to a server-owned implementation with equivalent or improved tests, or (c) explicitly deferred. Generic AI task execution is not a substitute for the evaluator, parser, evidence model, deterministic eligibility rules, or full candidate workflow. Workspace integration clients also require their own per-integration authorization and error-path tests; they are not candidate-domain persistence.

### Function-level disposition inventory from the legacy TypeScript code

| Legacy module / functions | Behavior to preserve | Current FastAPI parity status |
|---|---|---|
| `aiProvider.ts: parseCandidateProfile` | Extract candidate profile from supplied text; preserve source material; fallback behavior | **Partial / not certified.** Server task `profile_extraction` exists, but full return model, source-document metadata, fallback semantics and persisted profile update are not equivalent yet. |
| `aiProvider.ts: detectCVConflicts` | Compare imported CV claims with candidate-profile boundaries and documented certifications | **Not verified as a backend workflow.** Requires deterministic server implementation and tests. |
| `aiProvider.ts: extractJobRequirements` | Extract structured job requirements and classifications | **Partial.** Backend requirement extraction and AI task exist, but no proof of parity for the full TypeScript return contract or downstream linkage. |
| `aiProvider.ts: structureMasterProfile` | Convert raw profile text to structured profile sections | **Partial / not certified.** No full Master Career Profile CRUD contract or schema parity. |
| `aiProvider.ts: rankCVVariants` | Rank candidate CV variants against a job | **Not verified as a complete backend endpoint.** |
| `aiProvider.ts: generateApplicationMaterials` | Generate opportunity-specific application package | **Partial.** Generic cover-letter and suggested-answer tasks exist; package orchestration, versioning, provenance and candidate approval are not certified. |
| `aiProvider.ts: generateMasterCV` | Generate master CV from approved evidence | **Partial / not certified.** Server task exists, but evidence selection, factual claim validation and artifact lifecycle are not established end to end. |
| `aiProvider.ts: generateCVVariantFromPathway` | Generate CV variant from a selected career pathway | **Not verified as a complete backend workflow.** |
| `aiProvider.ts: generateJobSpecificCV` | Generate tailored CV for a specific job | **Partial / not certified.** Server task exists, but task-specific schema, evidence traceability, document artifact output and persisted variant management remain gaps. |
| `evaluator.ts: extractExternalJobId`, `deriveProvenanceLabel`, `sanitizeJobUrl` | Stable external identifiers, source/provenance labelling, safe URL normalization | **Partially ported and regression-tested.** URL normalization, provenance, and known ATS IDs have tests; keep the domain guard and unknown-domain cases in regression coverage. Exact TypeScript/Python corpus equivalence is still not established. |
| `evaluator.ts: evaluateParserConfidence`, `evaluateLocationAndWorkAuth`, `evaluateApplicationRoute`, `evaluateFreshness`, `evaluateSeniority`, `determineJDRetrievalStatus` | Parser confidence, location/work authorization eligibility, application route, freshness, seniority and JD retrieval readiness | **Helper-level ports and Python tests exist** for parser confidence, location/work authorization, route/fee checks, freshness, seniority and JD retrieval status. Full parity remains unproven until shared TypeScript/Python fixtures cover boundary cases and the full job-evaluation path. |
| `evaluator.ts: evaluateJobCandidateAgnostic`, `candidateExcludesSales`, `applicationMaterials`, `extractedRequirements`, `requirementEvaluations`, `evaluateJob` | Candidate-agnostic job assessment, candidate exclusions, requirement-level evidence matching and job decision | **NOT MIGRATED AS A COMPLETE PIPELINE.** Candidate exclusion helper is ported, but full candidate-agnostic evaluation orchestration, deterministic requirement extraction/mapping, all ten apply gates, score/output assembly and job decision persistence are not certified as equivalent. |
| `evaluator.ts: detectDuplicates` | Duplicate detection among jobs | **Partially ported and regression-tested.** External-ID, normalized URL, then company/title match logic exists; full parity against a shared fixture corpus and concurrency-safe persistence are still open. |
| `emailParser.ts: detectEmailSource`, `isJobAlertEmail`, `parseJobAlertEmail`, `hasMultipleHeadings`, `extractSingleJobFromText` | Identify job-alert email source, classify alerts, split multi-job alerts and extract individual job details | **Parser endpoint exists and is unit/API-tested.** It accepts submitted content and returns parsed jobs. It does not fetch Gmail, persist the source email or parsed jobs, enqueue acquisition, or complete the frontend ingestion journey. |
| `providers/providerManager.ts: initProviders`, `loadState`, `applyCredentials`, `refreshModelAvailability`, `getModels`, `addCustomModel`, `updateModel`, `getCredentials`, `setCredentials`, `getRoutes`, `setRoute`, `getRouteForTask` | Provider/model registry, credential setup, model availability and task routing | **Intentionally not copied as candidate-controlled frontend configuration.** Backend currently uses server-owned provider/model environment settings and fixed task policies. A protected, persisted, versioned Admin Control Plane is still missing; candidate-facing model selection is not an acceptable substitute. |
| `providers/googleProvider.ts: isConfigured`, `setCredentials`, `generateText` and sibling provider adapters | Call model providers, handle provider credentials, generate text | **Partially consolidated into server-owned Google provider adapter.** Other providers, explicit routing/fallback, rate/cost limits, structured output contracts and live-provider tests are not certified. |

This is an explicit disposition list, not a claim that the functions are already equivalent.

## Current backend capability map (code existence, not production certification)

- **Candidate identity/profile/evidence:** implemented foundation; route/database/security proof remains incomplete.
- **AI execution:** authenticated route and ten canonical task IDs; provider is mocked in unit tests; formal task-specific output schema validation, durable task audit, quotas/cost controls and live-provider tests remain gaps.
- **Opportunity/application workflow:** evaluation/Punt/application records and state transitions exist; not equivalent to the full frontend Job and application history model without end-to-end contract tests.
- **Acquisition/job verification/requirement extraction:** connector, worker and persistence modules plus unit/integration tests exist; live source permissions, rate limits, freshness, operational scheduling and full user journey need separate verification.
- **Requirement review:** route and reviewer authorization exist; production role provisioning and negative access tests must be demonstrated.
- **Work/outbox:** persistence and dispatcher logic exist; deployed workers, operational monitoring and handler idempotency need validation.
- **Full product workflow:** not yet certified end to end. The current repository gap audit remains applicable.

## Acceptance criteria for this audit

Do not mark parity complete until:

1. Each legacy backend behavior above has a test linked to its current FastAPI equivalent.
2. Every client-side domain function listed above has a disposition and mapped server endpoint/service or explicit deferral.
3. FastAPI route tests cover authentication, authorization, request/response contracts, and safe error handling.
4. PostgreSQL integration tests cover profile/evidence creation, the draft-to-review-to-approved lifecycle through FastAPI, stale revisions, cross-user isolation, and persisted state. Extend coverage to every remaining state-graph edge, transaction rollback and persistence across a separate session.
5. AI tests cover all task IDs, provider failures/timeouts, malformed JSON, schema mismatch, request limits, server policy registry completeness, and frontend response compatibility.
6. The frontend-to-backend journey is tested with a test identity and staging configuration; mocked unit tests alone are insufficient.
7. CI evidence, test outputs, unresolved gaps, and environment-dependent checks are recorded in the PR.
8. No branch is merged into `main` without explicit user authorization.


## Additional audit finding: request-size enforcement

A second pass identified that the AI route's Content-Length check alone did not bound the actual bytes consumed: clients can omit or lie about that header, and request parsing occurs before the route handler. The audit branch now adds an ASGI request-body limit at the application boundary (100,000 bytes), rejects malformed/oversized declared lengths, counts actual received bytes, and returns the same structured 413 REQUEST_TOO_LARGE response before the route parses the body. Regression tests cover both an oversized declared length and an oversized actual body with a deliberately false declared length.

**Verification:** backend CI run [#37954711385](https://github.com/GoReallyBig-GRB/jobpunter-backend/actions/runs/37954711385) passed on code/test head `c97530b4f8036a3d61839b2595fe69edf6624e39`: **237 passed, 1 warning**. The run compiled Python, upgraded PostgreSQL migrations, passed `alembic check`, and passed pytest. The new tests cover oversized declared length, a false declared length, and chunked body bytes without a Content-Length header. The body cap is scoped to `POST /api/v1/ai/generate`, so it does not impose the AI request limit on document-upload routes.


## Additional route-registration finding

Inspection of `app/api/v1/router.py` on the audit branch confirms that the mounted API currently includes health, candidate, opportunity, requirement-review, and AI routers. No acquisition/job-ingestion router is mounted there. `AcquisitionService` exists under `app/application/jobs/service.py`, but service existence alone does not expose an HTTP workflow. Treat user-facing acquisition/ingestion as **not verified and not available through a mounted v1 acquisition route** until an explicit route/worker entrypoint and end-to-end test are demonstrated.


## Deterministic evaluator parity work added in this audit

The following legacy helpers now have Python equivalents in `app/application/jobs/normalization.py` with focused regression tests:

| Legacy function | FastAPI-side implementation | Test coverage | Status |
|---|---|---|---|
| `sanitizeJobUrl` | `normalize_url` | Tracking parameter removal, canonical host/query handling, identity parameter preservation | Partial equivalent; backend normalization is stricter and also removes fragments/normalizes host. |
| `extractExternalJobId` | `extract_external_job_id` | Lever UUID, Greenhouse `gh_jid`/path, LinkedIn ID, Indeed `jk`, invalid/unknown URL | Implemented for known patterns; path-only ATS inference is restricted by host to avoid false positives. |
| `deriveProvenanceLabel` | `derive_provenance_label` | Scheduled, automated alert, manual email/URL and default precedence | Implemented. |
| `evaluateParserConfidence` | `evaluate_parser_confidence` | Required fields, short description and confidence thresholds | Implemented for documented field rules. |
| `determineJDRetrievalStatus` | `determine_jd_retrieval_status` | Explicit status, closed text, missing URL and description length thresholds | Implemented for documented status rules. |
| `evaluateFreshness` | `evaluate_freshness` | Closed, missing/invalid dates, recent/current/stale thresholds, timezone-aware dates | Implemented with injected clock for deterministic tests; invalid dates safely return `AGE_UNCERTAIN`. |
| `evaluateApplicationRoute` | `evaluate_application_route` | Paid application gate, free ATS, free job board, employer careers page, missing/unknown destination, nonzero fee preference | Implemented for documented route rules. |

Test files: `tests/unit/test_job_normalization_parity.py`, `tests/unit/test_legacy_job_normalization_helpers.py`, `tests/unit/test_legacy_job_evaluator_rules.py`, `tests/unit/test_legacy_freshness_parity.py`, and `tests/unit/test_legacy_application_route_parity.py`.

**Validation:** backend CI run [#37955854671](https://github.com/GoReallyBig-GRB/jobpunter-backend/actions/runs/37955854671) passed on code/test head `a3684a6e08434bf067f4908a3568d3c92544bfcf`: **251 passed, 1 warning**. The CI suite compiled Python, upgraded PostgreSQL migrations, passed `alembic check`, and passed pytest. An earlier test run caught a false ATS classification on unrelated domains; the implementation was tightened and the final run passed.

**Still not parity-complete:** `evaluateLocationAndWorkAuth`, `evaluateSeniority`, the full candidate-agnostic `evaluateJob`/`evaluateJobCandidateAgnostic` pipelines, requirement-evaluation orchestration, duplicate detection, all `aiProvider.ts` workflows, and the `emailParser.ts` workflow remain unmigrated or only partially equivalent. Do not remove the frontend engines or claim product parity until those are mapped, integrated, and tested end to end.


## Further parity work: eligibility, duplicates and email parsing

Additional legacy evaluator helpers now have Python equivalents in `app/application/jobs/normalization.py`:

- `evaluate_location_and_work_auth` preserves explicit authorization hard-rejects, country matches, worldwide-remote handling, generic-remote uncertainty, and foreign on-site relocation constraints.
- `evaluate_seniority` preserves the legacy executive-title/scope rules and candidate target-seniority comparison.
- `candidate_excludes_sales` preserves explicit sales/SDR/BDR/cold-call/commission exclusions.
- `detect_duplicates` preserves external-ID, canonical-URL, then normalized company/title matching order.

`src/engine/emailParser.ts` also has a Python parser in `app/application/jobs/email_parser.py`, exposed through authenticated `POST /api/v1/email/parse-job-alert`. Tests cover source detection, alert classification, explicit-field extraction, delimited multi-job parsing, manual-review fallback, and the FastAPI request/response contract.

**Important boundary:** the email endpoint parses and returns a structured result only. It does not persist the source email or extracted jobs, schedule ingestion, or connect the frontend workflow end to end. The TypeScript source remains in the frontend until that complete journey is migrated and verified. The full candidate-agnostic evaluation orchestration and AI-provider functions also remain open.

**Validation:** backend CI run [#37956663329](https://github.com/GoReallyBig-GRB/jobpunter-backend/actions/runs/37956663329) passed on code/test head `7300d31f9a652521d3e9c6d7cd8415a2b5aa20a2`: **268 passed, 1 warning**. Python compilation, PostgreSQL migration upgrade, Alembic schema check, and pytest passed.


## Current verified status (authoritative for this audit branch)

- Backend PR #40 targets `feature/backend-ai-task-execution`; it is a draft and has not been merged. No authorization to merge to `main` has been given.
- The latest previously verified code/test head `4379579c9ec11b9bde746eed019a0a436af064ad` passed [backend CI #37956884027](https://github.com/GoReallyBig-GRB/jobpunter-backend/actions/runs/37956884027): **268 passed, 1 warning**. Python compilation, PostgreSQL migration upgrade, `alembic check`, and pytest passed.
- External job ID extraction was hardened so ATS-specific query keys/path patterns do not classify arbitrary domains as Greenhouse, LinkedIn, Indeed or Lever. This is an intentional security/correctness improvement over the legacy parser's domain-agnostic pattern matching, not byte-for-byte behavior parity. A failed run on an intermediate head caught an outdated Lever test fixture and that fixture was corrected to use `jobs.lever.co`.
- Code/test head `178f6f4d7cf1c3f2a9a8123269920023402ba264` added profile-driven deterministic requirement extraction/evidence matching; [CI #37958781982](https://github.com/GoReallyBig-GRB/jobpunter-backend/actions/runs/37958781982) passed: **273 passed, 1 warning**. This remains a pure helper, not a user-facing evaluator.
- Latest code/test head `cf7833c514905a2f3f8ee1d3b7e8efaa719e24ac` adds the versioned candidate-preferences model, migration `0013_candidate_preferences`, strict request/response schemas, authenticated `GET/PUT /api/v1/candidate/preferences`, revision conflict handling, salary range validation, and PostgreSQL API tests for persistence, updates, stale revisions, and candidate isolation. [CI #37959979414](https://github.com/GoReallyBig-GRB/jobpunter-backend/actions/runs/37959979414) passed: **275 passed, 1 warning**. Python compilation, PostgreSQL migration upgrade, `alembic check`, and pytest passed.
- Preferences are now persisted but are **not yet read by the evaluation service**. Full `evaluateJob` orchestration and persisted evaluation; integration of approved persisted claims/evidence and preferences; Gmail ingestion and persistence; remaining `aiProvider.ts` workflows; task-specific server-owned structured-output schemas; live-provider and staging E2E verification; operational rate/cost controls, durable AI audit, and production deployment/security checks remain blockers.
- The exact current product-level blockers remain: full `evaluateJob` orchestration and persisted evaluation; integration of approved persisted claims/evidence and preferences; Gmail ingestion and persistence; all remaining `aiProvider.ts` workflows; task-specific server-owned structured-output schemas; live-provider and staging E2E verification; operational rate/cost controls, durable AI audit, and production deployment/security checks.


## Critical legacy design defect: do not port candidate-specific fallback data as global logic

Review of `src/engine/aiProvider.ts` shows the deterministic requirement mapper embeds specific project names and domain assumptions (including MarketGrowthOS, Zenith Property Repairs, UrCalls and NexaLife Care), a fixed Product Management profile, and hardcoded exclusions such as sales/SDR and unsupported specialist domains. This is not a reusable multi-user matching algorithm. Copying it literally into the server would cause other candidates to be evaluated against the wrong person's experience and preferences.

**Required replacement behavior:** all evidence and exclusions must be derived from the authenticated candidate's persisted profile, claims/evidence, target-role preferences and explicit boundaries. No project name, career history, or exclusion belonging to one candidate may be a global fallback. AI output must not override deterministic candidate-specific hard constraints. A parity test must run the same job against two different candidate profiles and prove the evidence match and decision differ only where their persisted evidence/preferences differ.

This is a deliberate correction to the legacy implementation, not a parity regression. Until the candidate-specific evaluator is implemented and those cross-profile tests pass, the full `evaluateJobCandidateAgnostic` / `evaluateJob` workflow remains a release blocker.


## Profile-driven requirement matching helper (not end-to-end parity)

The audit branch now has `extract_requirements_deterministically` and `map_requirements_to_evidence` in `app/application/jobs/requirement_evaluation.py`. Unlike the legacy fallback, these helpers accept a supplied candidate profile and derive evidence items and exclusions from that profile rather than global project-name/domain constants. Deterministic text overlap is explicitly labelled as a heuristic: a match is not proof of competence, and no match is not proof the candidate lacks a skill.

Tests in `tests/unit/test_requirement_evaluation.py` prove two profiles can produce different outcomes for the same requirement and that exclusions apply only when stored in that profile's preferences. Remaining work: map approved persisted claims/evidence into the input shape, load preferences from authoritative storage, expose an authenticated FastAPI evaluation route/service, persist evaluation snapshots and requirement-level provenance, enforce hard gates before AI, and add PostgreSQL/API cross-candidate isolation tests. Until that integration exists, this helper must not be represented as a working user-facing evaluator.


## Test traceability matrix

| Behavior area | Automated proof on this branch | What the tests establish | What they do not establish |
|---|---|---|---|
| Candidate identity and authorization | `tests/unit/test_identity.py`, `test_firebase_identity_provider.py`, `test_authorization.py`; `tests/integration/test_candidate_api_postgres.py` | Identity mapping and authenticated API ownership paths under test identities | Production identity-provider configuration, real token lifecycle and deployment security |
| Candidate profile/evidence lifecycle | `tests/unit/test_evidence_transition_matrix.py`; `tests/integration/test_candidate_evidence_postgres.py`, `test_candidate_api_postgres.py` | Allowed/forbidden transitions, persisted lifecycle, stale revision and cross-user denial | Production concurrency/load behavior, rollback drills and data migration against production-like records |
| AI route and policy | `tests/unit/test_ai_route_contract.py`, `test_ai_task_execution.py`, `test_ai_task_policy_registry.py` | Response contract, task policy coverage, empty provider output and policy instruction formatting | Live Google credentials/provider quality, structured task schema enforcement, latency/cost under load |
| AI request size | `tests/unit/test_request_body_limit.py`, `tests/unit/test_ai_route_contract.py` | Declared, false/missing Content-Length and streamed-body limits | CDN/reverse-proxy limits and deployment ingress configuration |
| URL identity and normalization | `tests/unit/test_job_normalization_parity.py`, `test_legacy_job_normalization_helpers.py`, `test_legacy_duplicate_detection_parity.py` | URL canonicalization, known ATS IDs, provenance and duplicate-helper cases | Complete parity over a shared real-world TS/Python fixture corpus and race-safe production deduplication |
| Eligibility helpers | `tests/unit/test_legacy_job_evaluator_rules.py`, `test_legacy_location_eligibility_parity.py`, `test_legacy_seniority_parity.py`, `test_legacy_application_route_parity.py`, `test_legacy_freshness_parity.py`, `test_legacy_sales_exclusion_parity.py` | Deterministic helper cases for parser confidence, location/work authorization, seniority, fee gate, freshness and exclusions | Full `evaluateJobCandidateAgnostic` orchestration and persisted decision parity |
| Email parser | `tests/unit/test_legacy_email_parser_parity.py`, `test_email_parser_api.py` | Parsing/classification behavior and authenticated HTTP contract | Gmail API fetch, source-email storage, extracted-job persistence, scheduling and frontend end-to-end ingestion |
| Requirement helper | `tests/unit/test_requirement_evaluation.py` | Profile-specific text matching, requirement classification, and preferences-based exclusion in a pure helper | Loading approved evidence/preferences from PostgreSQL, HTTP authorization, persisted evaluations or production scoring quality |
| Acquisition/connectors | `tests/unit/test_acquisition_service.py`, `test_registered_acquisition.py`, `test_acquisition_worker.py`, connector tests; `tests/integration/test_job_requirement_extraction_worker_integration.py`, `test_scrapy_worker_runtime.py` | Idempotency, connector and worker behavior in test environments | A mounted user-facing acquisition endpoint, live source terms/permissions, deployed scheduling and operational monitoring |
| Opportunity/application records | `tests/unit/test_opportunity_workflows.py`, `test_opportunity_service_security.py`; `tests/integration/test_opportunity_service_postgres.py` | Candidate-scoped records, qualification, transitions and database workflow behavior | Equivalence to the full legacy `Job` object and complete CV/application-materials workflow |
| Requirement review and outbox | `tests/unit/test_requirement_review_auth_guard.py`, `test_requirement_review_policy.py`, `test_outbox_dispatcher.py`, `test_outbox_worker.py`; `tests/integration/test_outbox_dispatcher_postgres.py` | Authorization policy, dispatch mechanics and persistence cases | Production reviewer provisioning, deployed worker operations and external handler idempotency under failure |
| Schema/migration integrity | CI: Python compilation, PostgreSQL `alembic upgrade head`, `alembic check`, pytest | Code compiles, migrations apply to clean CI PostgreSQL and metadata matches schema | Production upgrade safety, downgrade/rollback drills, backup/restore or existing-data migration validation |

This matrix intentionally separates **tested components** from **validated product journeys**. A passing row does not close any gap in its final column.


## PRD v1.4 acceptance-gap cross-check

The audit also checked the backend against the product's stated candidate-agnostic/evidence-first architecture. These are **observed implementation gaps**, not inferred feature requests:

| PRD contract | Current backend evidence | Outcome |
|---|---|---|
| Structured Master Career Profile | `CandidateProfile` is primarily a status/identity root; candidate claims and evidence are stored, but the full structured Experience, Education, Project, Achievement, Skill and Certification profile contract is not exposed as a complete CRUD API. | **Incomplete** |
| Approved evidence is authoritative | Evidence has a lifecycle, source linkage and claim links; transition and PostgreSQL lifecycle tests exist. | **Foundation implemented; end-to-end use by evaluation not implemented** |
| Candidate preferences are separate from facts | New `CandidatePreferences` JSON model, versioned GET/PUT API, strict schema, optimistic revision checks and PostgreSQL isolation tests now exist. The preference object is separate from profile/evidence. | **Persistence/API implemented; not yet consumed by the evaluation pipeline** |
| Candidate-approved pathways/TargetRole | Evaluations store a `target_role_key`; complete pathway CRUD, AI-suggested versus user-defined distinction, approval state and versioned target configuration are not verified. | **Incomplete** |
| Evaluation snapshot provenance | `CandidateEvaluation` stores outcome, score, blockers, summaries, explanation, policy version and timestamp. It does not by itself prove references to the exact JD version, evidence IDs/revisions, preferences snapshot, rule-set version and AI task version used. | **Snapshot contract incomplete** |
| Requirement extraction and traceability | `JobDescriptionVersion` and `JobRequirement` store content/version, extraction state, source excerpt, confidence and extractor version. | **Partially implemented; candidate-specific evaluation linkage is missing** |
| AI task registry contract | Ten task IDs and server-owned task instructions/temperatures exist. Per-task input/output schemas, schema/semantic validation before domain persistence, published task versions, multi-provider routing/fallback and durable execution audit are not complete. | **Incomplete** |
| CV Studio and application packages | Generic AI tasks exist, but full master-CV/variant CRUD, source evidence references, edit/approval/version lifecycle and real DOCX/PDF artifact generation are not verified. | **Incomplete** |
| Gmail/source-email ingestion | Parser endpoint accepts submitted text and returns parsed records. Gmail authorization/fetch, source email persistence, extracted job persistence and queued downstream work are not connected end to end. | **Incomplete** |
| Explainable APPLY/REVIEW/WATCH/REJECT evaluation | Some deterministic eligibility helpers and `CandidateEvaluation` persistence exist, but the complete decision pipeline and all required dimensions/output fields are not connected and regression-tested through FastAPI. | **Incomplete** |
| Application audit lifecycle | Application/Punt state models, transitions, events and outbox exist with unit/PostgreSQL tests. Full PRD lifecycle equivalence and user-journey validation remain open. | **Partially implemented** |
| Real, scheduled discovery | Connectors, acquisition/discovery workers and durable mission models/tests exist. The audited v1 router does not expose an acquisition/job-ingestion route, and production schedules/operations are not validated. | **Backend components exist; user-facing/deployed journey incomplete** |

**Release rule:** the backend is not MVP-complete merely because tables, helpers, workers or AI task IDs exist. The PRD's end-to-end invariants require the authenticated candidate's persisted facts/preferences, a real canonical job and JD version, deterministic hard constraints, validated AI output, a traceable evaluation snapshot, and an auditable application workflow to work together through tested API journeys.


## Candidate preferences API added and validated

The audit branch now persists candidate preferences separately from factual profile/evidence in `candidate_preferences` (migration `0013_candidate_preferences`). The authenticated endpoints are:

- `GET /api/v1/candidate/preferences`: returns defaults at revision 0 if none are saved.
- `PUT /api/v1/candidate/preferences`: creates at expected revision 0, then requires the current revision for each replacement update; stale writes return `REVISION_CONFLICT`.

The schema covers target roles/seniority/industries, exclusions, locations, remote preference, eligible/work-authorization countries, salary bounds/currency/period, relocation, employment types, maximum application fee, transferability policy, preferred/avoided terminology, evidence emphasis and other constraints. Tests in `tests/integration/test_candidate_preferences_postgres.py` validate initial defaults, persistence, revision increments, stale-write conflicts, invalid values and cross-candidate isolation.

**Boundary:** preferences are now stored and protected, but the candidate-evaluation service does not yet consume them. Do not treat this as a completed preference-aware recommendation workflow.
