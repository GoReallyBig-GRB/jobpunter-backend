# E2.5 live-enforcement execution log

Updated: 2026-10-18  
Repository: `GoReallyBig-GRB/jobpunter-backend`  
Branch: `admin-backend-continuation-2026-10-18`

## Current checkpoint

Code checkpoint `6f882fe216a0217b92374bce3891fc93dd8ee67f` passed CircleCI `backend-verification`:
https://app.circleci.com/workflow/57477fa4-1c16-440a-92b1-7266f95c2a3d/job/d54011cf-30cf-4ff6-b92a-64c9c9762ee9

Local tests and live/staging provider behavior remain NOT VERIFIED. No merge or deployment occurred.

## Resolution progress

- **E2.5-01 — RESOLVED (repository/CI scope):** Admin manual acquisition uses the shared live guard. A PostgreSQL/API regression test proves a forbidden rule activated after request one blocks request two and records the run as RESTRICTED.
- **E2.5-02 — RESOLVED (repository/CI scope):** Scrapy passes a non-secret policy snapshot to its worker, fails closed if live policy cannot load or the persisted snapshot differs, and checks requests through downloader middleware. Tests cover worker guard initialization, snapshot mismatch, middleware calls across requests, and SQL-backed changes.
- **E2.5-03 — RESOLVED (repository/CI scope, fail-closed):** JobSpy returns RESTRICTED with `JOBSPY_LIVE_POLICY_UNSUPPORTED` whenever the guarded acquisition callback is supplied. This prevents unsupported third-party transport from bypassing fresh policy. Tradeoff: JobSpy is unavailable through guarded acquisition until a controllable transport is implemented.
- **E2.5-04 — OPEN (I):** Added tests for source configuration mutation, active configuration-version changes, connector deactivation, and unsupported methods. Remaining coverage must establish the complete approved method matrix and prove the relevant behavior across every supported acquisition entry path.

## Mandatory next queue

| Position | Item / classification | Required next action | Exit evidence |
|---:|---|---|---|
| 1 | **DOC-01 — OPEN (I)** Canonical gap-matrix reconciliation | Consolidate the recent E2.5-01/02/03 resolutions and fix duplicated table headers/queue numbering in `docs/admin/ADMIN_BACKEND_GAP_MATRIX.md`. Keep this log as a dated progress record, not a replacement for the canonical matrix. | Canonical matrix accurately reflects the current checkpoint, resolved items, and ordered outstanding queue. |
| 2 | **E2.5-04 — OPEN (I)** Method/configuration acceptance | Complete approved-method coverage across applicable paths; test source configuration, active-version publication/rollback, and connector/source status changes through the relevant entry points. | Deterministic tests and passing exact-head CI; unresolved paths explicitly remain open. |
| 3 | **L1.5 — PARTIAL** URL acquisition and SSRF review | Test forbidden redirects, discovered links before enqueue, browser subresources, DNS/private-address behavior, and concurrent policy changes across all acquisition paths. | Requirement-level security matrix and tests; claims limited to proven behavior. |

## Overall gates

**E2.5 remains OPEN (I)** until every supported acquisition path proves fresh enforcement before each applicable network request, or is deliberately disabled with tested fail-closed behavior. **L1.5 remains PARTIAL.** A0.2–A0.6 remain PARTIAL and the A0 exit gate remains NOT PASSED.
