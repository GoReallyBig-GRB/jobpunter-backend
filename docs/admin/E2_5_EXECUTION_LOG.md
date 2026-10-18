# E2.5 live-enforcement execution log

Updated: 2026-10-18  
Repository: `GoReallyBig-GRB/jobpunter-backend`  
Branch: `admin-backend-continuation-2026-10-18`

## Current checkpoint

Code-and-regression-test checkpoint `787bd902248f2c296d6a82061e23b36bf2907201` adds live revalidation of connector `capabilities` and `limits`, per-request callback enforcement in JSON API pagination, RSS/Atom retrieval, and Greenhouse board/jobs requests, plus regressions proving JSON API stops before a second page after a published configuration version changes and Greenhouse stops before its jobs request when the live guard blocks it. CircleCI `backend-verification` **passed** for this exact code checkpoint: https://app.circleci.com/workflow/e8cb697e-7140-42ca-8154-2f2b44ae5755/job/c95dc1bc-cbb9-404a-8ec2-d1b90dd7bad0.

Local tests and live/staging provider behavior remain NOT VERIFIED. No merge or deployment occurred.

## Resolution progress

- **E2.5-01 — RESOLVED (repository/CI scope):** Admin manual acquisition uses the shared live guard. A PostgreSQL/API regression test proves a forbidden rule activated after request one blocks request two and records the run as RESTRICTED.
- **E2.5-02 — RESOLVED (repository/CI scope):** Scrapy passes a non-secret policy snapshot to its worker, fails closed if live policy cannot load or the persisted snapshot differs, and checks requests through downloader middleware. It now also calls the live guard before enqueuing initial and pagination URLs, then checks again before network access. Tests cover worker guard initialization, snapshot mismatch, middleware calls across requests, and SQL-backed changes; the new pre-enqueue guard still needs its own focused regression test.
- **E2.5-03 — RESOLVED (repository/CI scope, fail-closed):** JobSpy returns RESTRICTED with `JOBSPY_LIVE_POLICY_UNSUPPORTED` whenever the guarded acquisition callback is supplied. This prevents unsupported third-party transport from bypassing fresh policy. Tradeoff: JobSpy is unavailable through guarded acquisition until a controllable transport is implemented.
- **E2.5-04 — OPEN (I):** Expanded regression cases cover source configuration/base URL/type changes, connector configuration/type/version changes, active configuration-version changes, source/connector deactivation, permission-review reference/time mutation, and rejection of POST/PUT/PATCH/DELETE/HEAD/OPTIONS/TRACE under the current GET-only guard. Approved-method policy confirmation, end-to-end configuration publication/rollback lifecycle evidence, and cross-entry-path acceptance remain outstanding. Revalidation now also compares connector capabilities and limits, not only connector configuration/version/type. JSON API checks every page URL; RSS/Atom checks its feed URL; Greenhouse checks both board and jobs URLs. The tests prove a persisted active-configuration-version change in the registered JSON API path blocks the next page request, and that Greenhouse does not issue its second request after the live guard blocks it. Equivalent persisted-policy mutation acceptance is still incomplete across every connector and entry path.

## Mandatory next queue

| Position | Item / classification | Required next action | Exit evidence |
|---:|---|---|---|
| 1 | **E2.5-04 — OPEN (I)** Method/configuration acceptance | Complete approved-method coverage across applicable paths; prove source configuration and active-version publication/rollback changes stop the next request through registered acquisition, and extend deterministic cross-entry-path acceptance. | Deterministic tests and passing exact-head CI; unresolved paths explicitly remain open. |
| 2 | **L1.5 — PARTIAL** URL acquisition and SSRF review | Focused regression tests now cover Scrapy discovered-link rejection before enqueue and Playwright live-policy rejection of browser subresources. Still verify DNS/private-address edge cases, concurrent policy changes, redirects and full cross-path acceptance. | Requirement-level security matrix and tests; claims limited to proven behavior. |

## Overall gates

**E2.5 remains OPEN (I)** until every supported acquisition path proves fresh enforcement before each applicable network request, or is deliberately disabled with tested fail-closed behavior. **L1.5 remains PARTIAL.** A0.2–A0.6 remain PARTIAL and the A0 exit gate remains NOT PASSED.
