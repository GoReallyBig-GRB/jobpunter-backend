# Legacy Next.js Repository Backend Parity Audit

**Audit date:** 2026-10-09  
**Legacy source:** `GoReallyBig-GRB/jobpunter-frontend`, commit `702d929024b8baaac50cf03249efe88e2c3c426b` (the commit that introduced `backend/`)  
**Target:** `GoReallyBig-GRB/jobpunter-backend`, branch `audit/backend-functional-parity`  
**Scope:** All backend files introduced by the legacy `backend/` directory, plus adjacent frontend engines that still contain business/AI behavior and therefore must not be mistaken for migrated server functionality.

## Executive result

The legacy `backend/` directory was a **small foundation slice**, not a complete implementation of JobPunter. It implemented a FastAPI shell and a first candidate/evidence workflow. The current separate backend contains corresponding candidate/evidence code plus substantial additional backend domains (opportunities, acquisition, job verification, requirement review, AI task execution, work/outbox). The presence of corresponding code is not, by itself, proof of full behavior parity or production readiness.

The original development identity seam accepted a caller-supplied user ID header. That behavior must **not** be restored. The separate backend's verified-token identity boundary is the intended replacement. Ownership and state-transition semantics should be retained, with stronger route-level and PostgreSQL-backed tests.

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
4. PostgreSQL integration tests cover profile/evidence creation, every state transition, stale revisions, cross-user isolation, transaction rollback, and persistence after a new session.
5. AI tests cover all task IDs, provider failures/timeouts, malformed JSON, schema mismatch, request limits, and frontend response compatibility.
6. The frontend-to-backend journey is tested with a test identity and staging configuration; mocked unit tests alone are insufficient.
7. CI evidence, test outputs, unresolved gaps, and environment-dependent checks are recorded in the PR.
8. No branch is merged into `main` without explicit user authorization.
