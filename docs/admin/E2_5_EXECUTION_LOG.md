# E2.5 live-enforcement execution log

Updated: 2026-10-18  
Repository: `GoReallyBig-GRB/jobpunter-backend`  
Branch: `admin-backend-continuation-2026-10-18`

## Current checkpoint

Test checkpoint `6ee638b313868d7e7236d66a8ccc30a4fd8ffe74` adds a PostgreSQL discovery-mission integration regression: a persisted connector configuration change after the first Greenhouse request blocks the second request, and both the acquisition run and mission-source outcome are persisted as `RESTRICTED` with `ACQUISITION_CONFIGURATION_CHANGED`. CircleCI `backend-verification` **passed** for this exact checkpoint: https://app.circleci.com/workflow/4003d12c-0d47-4462-8132-f03ee1e80392/job/57979120-c6ba-4467-966f-c82cad5f9586.

Preceding test checkpoint `de0f5a2e129f3a03259c80ae9780fe1a6172cd36` adds adapter-level method enforcement evidence: Scrapy middleware forwards the actual request method to the live guard and blocks POST, and Playwright's browser route guard forwards and blocks a POST subrequest. The Playwright fixture also models live forbidden-path denial explicitly. CircleCI `backend-verification` **passed** for that checkpoint: https://app.circleci.com/workflow/3b37a37c-6bc3-42b3-884a-df075b914a21/job/e2176682-c1ee-48e0-9668-65c6362c01b2.

Preceding test checkpoint `eb079ff866956e7497b3fe03b8c6ca9a3032b6f0` expands end-to-end Admin manual-acquisition coverage to fourteen persisted-policy mutation cases. Each changes source/connector state after the first Greenhouse request, then verifies that the second request is never issued and the run is persisted as `RESTRICTED`. Cases cover source configuration/base URL/type, permission-review reference/timestamp/revocation, connector configuration/capabilities/limits/type/version/active configuration version, and source/connector deactivation. Test mutation values are deliberately distinct from initial values. CircleCI `backend-verification` **passed** for that checkpoint: https://app.circleci.com/workflow/5abdab18-1cdb-4e68-9c39-d832efa8843a/job/2eebec71-251e-40b8-b540-8f8e16bb90fb.

Preceding checkpoint `cd3f027ce8590ac82c6ecb6868de6b09f3441295` introduced the first six end-to-end Admin-path policy-mutation cases; its CI run also passed: https://app.circleci.com/workflow/e4a519e4-a959-4ac7-be81-407d9705264e/job/450369b5-5834-46e5-b8cb-9873cef44faf.

Preceding test checkpoint `74aec969131a4f0c54b66463b4764e32105b3be6` expands the SQL-backed live-policy guard regression from four to fifteen mutation cases. In addition to forbidden-rule changes and permission revocation, it verifies that changes to source configuration/base URL/type, permission-review reference/timestamp, connector configuration/capabilities/limits/type/version/active configuration version, and source/connector activation state are rejected before a second request. This is database-backed guard testing; it counts the first permitted request and asserts the second guard call raises, rather than claiming live provider execution. CircleCI `backend-verification` **passed** for that checkpoint: https://app.circleci.com/workflow/7b9c83d3-de49-44d5-adae-8919269de1a7/job/2d892cd6-bb21-4da2-9f6e-52f4b3f48435.

Code checkpoint `bfb44c1a14218f6765c4bb657d9510c44159dfcf` added the two deterministic adapter-level live-policy regressions. Greenhouse is checked before both board and jobs requests; denial on the second check prevents the second HTTP request. RSS/Atom invokes the live guard for the feed URL and a denial prevents the HTTP request entirely. Both use in-memory/no-network test clients. CircleCI `backend-verification` **passed** for that checkpoint: https://app.circleci.com/workflow/9548878c-254c-4275-af08-d2ff2d3b942c/job/5d3eb089-11a9-4c91-98a0-1472d96a2e39.

Prior checkpoint `0a4492a395f62ac23c1b565d8c4c545d556947de` removed duplicate live-policy checks in JSON API and RSS/Atom request setup and Greenhouse's per-request helper. It includes connector capability/limit revalidation and registered JSON API tests proving active-version publication (1→2) and rollback (2→1) block the next page. Its CircleCI run also passed: https://app.circleci.com/workflow/cdbee3f2-8f54-45dd-9999-30beaa0d8745/job/24ec014d-0718-4eed-83a0-8070e9d12894.

Local tests and live/staging provider behavior remain NOT VERIFIED. No merge or deployment occurred.

## Resolution progress

- **E2.5-01 — RESOLVED (repository/CI scope):** Admin manual acquisition uses the shared live guard. A PostgreSQL/API regression test proves a forbidden rule activated after request one blocks request two and records the run as RESTRICTED.
- **E2.5-02 — RESOLVED (repository/CI scope):** Scrapy passes a non-secret policy snapshot to its worker, fails closed if live policy cannot load or the persisted snapshot differs, and checks requests through downloader middleware. It calls the live guard before enqueuing initial and pagination URLs, then checks again before network access. Tests cover worker guard initialization, snapshot mismatch, middleware calls across requests, SQL-backed changes, and a discovered-link regression proving a forbidden pagination target is rejected before enqueue. Live/staging subprocess behavior remains NOT VERIFIED.
- **E2.5-03 — RESOLVED (repository/CI scope, fail-closed):** JobSpy returns RESTRICTED with `JOBSPY_LIVE_POLICY_UNSUPPORTED` whenever the guarded acquisition callback is supplied. This prevents unsupported third-party transport from bypassing fresh policy. Tradeoff: JobSpy is unavailable through guarded acquisition until a controllable transport is implemented.
- **E2.5-04 — OPEN (I):** Expanded regression cases cover source configuration/base URL/type changes, connector configuration/type/version changes, active configuration-version changes, source/connector deactivation, permission-review reference/time mutation, and rejection of POST/PUT/PATCH/DELETE/HEAD/OPTIONS/TRACE under the current GET-only guard. The governing task list requires methods to follow an approved policy but does not specify the allowed-method set; GET-only is an implementation restriction, not an established product decision. That method-policy decision remains unresolved and must not be inferred. Revalidation compares connector capabilities and limits as well as connector configuration/version/type. JSON API checks every page URL; RSS/Atom checks its feed URL; Greenhouse checks both board and jobs URLs. Registered JSON API tests prove active-version publication (1→2) and rollback (2→1) block the next page. New Greenhouse and RSS/Atom regressions prove live-guard denial prevents the relevant HTTP request. The SQL-backed mutation matrix now covers the source/connector fields listed above, but adapter-level acceptance across every supported connector and entry path remains incomplete.

## Adapter and entry-path acceptance crosswalk

The current CI-backed evidence is layered. It proves representative policy mutations at the shared guard, adapter request boundary, and two real application entry paths; it does **not** prove every mutation against every adapter.

| Path | Enforcement boundary | Current evidence | Remaining limitation |
|---|---|---|---|
| Admin manual acquisition | `RegisteredAcquisitionService` guard on each request | PostgreSQL/API test for a new forbidden rule; fourteen Greenhouse request-two mutation cases verify the request is blocked and the run is persisted as `RESTRICTED`. | Provider calls are mocked; this is repository/CI evidence, not live-source behavior. |
| Operator acquisition worker | `app/workers/acquisition.py::run_acquisition` delegates to `RegisteredAcquisitionService`. | Shared service's per-request guard and mutation tests. | Wrapper does not have a separate live-provider test; do not treat it as independent runtime proof. |
| Scheduled discovery mission | Mission worker delegates to `RegisteredAcquisitionService`. | PostgreSQL mission test changes connector configuration after Greenhouse request one and verifies request two is blocked and both acquisition and mission-source outcomes are persisted as restricted. | One representative mutation through the mission path, not the complete mutation matrix. |
| JSON API | Revalidates each page URL before `bounded_get`. | Registered connector test changes active configuration version during pagination and verifies no second HTTP request; publication and rollback (1→2, 2→1) are covered. | Not every source/connector mutation is repeated through the JSON API transport. |
| RSS/Atom | Revalidates the feed URL before `bounded_get`; redirects are disabled. | Denial-before-feed-request test verifies zero HTTP calls. | Single-feed request path; live provider behavior is not tested. |
| Greenhouse | Revalidates board and jobs URLs separately. | Adapter test and Admin/mission integration cases verify denial between requests blocks the second request. | Live provider behavior is not tested. |
| Scrapy | Worker policy snapshot plus downloader middleware before outbound requests. | Snapshot mismatch, per-request live guard, discovered-link pre-enqueue rejection, SQL-backed policy mutation, and actual POST-method forwarding tests. | Live subprocess/provider behavior is not verified. |
| Playwright | Browser route guard validates each navigation/subresource request, including actual method. | Live-policy subresource denial and POST-method rejection tests; redirect/host protections have separate tests. | Real browser/provider execution is not verified. |
| JobSpy | Guarded acquisition fails closed because the third-party transport cannot expose the required request hook. | Registered acquisition persists a restricted outcome with `JOBSPY_LIVE_POLICY_UNSUPPORTED`. | Deliberately unavailable on the guarded path until transport enforcement is controllable. |

**E2.5-04 exit blockers:** the method policy is now decided and enforced at the shared guard. The JSON API transport does not yet implement the explicitly approved read-only POST configuration, and the full mutation-by-adapter crosswalk remains incomplete. Keep E2.5 open until conditional POST has bounded transport and request-body validation, deterministic positive/negative tests, the cross-path acceptance matrix is reconciled, and exact-head GitHub Actions passes.

## Mandatory next queue

| Position | Item / classification | Required next action | Exit evidence |
|---:|---|---|---|
| 1 | **E2.5-04 — OPEN (I)** Method/configuration acceptance | Complete approved-method coverage across applicable paths; prove source configuration and active-version publication/rollback changes stop the next request through registered acquisition, and extend deterministic cross-entry-path acceptance. | Deterministic tests and passing exact-head CI; unresolved paths explicitly remain open. |
| 2 | **L1.5 — PARTIAL** URL acquisition and SSRF review | Focused regression tests now cover Scrapy discovered-link rejection before enqueue and Playwright live-policy rejection of browser subresources. Still verify DNS/private-address edge cases, concurrent policy changes, redirects and full cross-path acceptance. | Requirement-level security matrix and tests; claims limited to proven behavior. |

## Overall gates

**E2.5 remains OPEN (I)** until every supported acquisition path proves fresh enforcement before each applicable network request, or is deliberately disabled with tested fail-closed behavior. **L1.5 remains PARTIAL.** A0.2–A0.6 remain PARTIAL and the A0 exit gate remains NOT PASSED.


## Merge-readiness continuation — 2026-10-19

The merge-readiness branch adds a focused URL validation regression set in
`tests/unit/test_target_policy.py` for literal loopback, RFC1918 private,
link-local, IPv6 loopback, and IPv6 unique-local destinations, plus unsupported
schemes, non-standard ports, and reserved/local hostnames. These tests assert
that `normalize_public_target_url` rejects the target before a transport is
invoked. The new tests are authored but must not be counted as verified until
GitHub Actions passes on the exact resulting head.

The merge-readiness branch also fixes `.github/workflows/verify.yml` so pushes
to `admin-backend` run the same backend verification workflow as pushes to
`main`. GitHub Actions passed on the earlier workflow-only checkpoint
`028bb53e905d73adba663b7b67d305a1ba83a0e1`; that result does not validate the
subsequent URL-test commit.

### Remaining blockers (unchanged until evidence resolves them)

- **E2.5-04 remains OPEN:** the approved acquisition-method policy is now
  explicit: GET is the default; POST is allowed only when persisted connector
  configuration sets `request_method=POST` and a non-empty
  `read_only_post_action`, and persisted connector capabilities explicitly set
  `read_only_post=true`. Runtime context alone cannot grant this permission.
  PUT, PATCH, DELETE, TRACE, HEAD, and OPTIONS remain prohibited unless a separate
  reviewed exception is approved. The shared per-request guard now enforces this
  rule. The JSON API adapter still uses GET only; conditional POST transport,
  request-body validation, and adapter-level positive/negative tests remain to
  be implemented and verified before E2.5-04 can close.
- **L1.5 remains PARTIAL:** literal non-public IP and reserved-host validation
  is now covered by new deterministic regression cases, but DNS resolution and
  rebinding, redirect destination behavior across every applicable adapter,
  concurrent persisted-policy changes, and complete cross-path acceptance are
  not thereby proven.
- **A0.2–A0.6 remain PARTIAL; A0 exit gate NOT PASSED:** resource-level Admin
  authorization, unresolved account/role/session policy, material-write audit
  coverage and retention policy, canonical configuration-consumer tracing,
  discovery orchestration/quotas, and runtime/restore evidence still require
  their own requirement-to-test crosswalk and acceptance evidence.
- **Runtime/provider checks remain NOT VERIFIED:** repository CI cannot establish
  deployed Firebase/provider/SMTP/storage access, scheduler/worker operation,
  crash/restart behavior, or backup/restore readiness.

No policy has been inferred, no deployment has occurred, and no merge has been
performed.


## Acquisition HTTP-method policy decision — 2026-10-19

The product decision is recorded as follows:

- GET is the default method for external job acquisition.
- POST is permitted only for a documented read-only operation, explicitly
  identified by persisted connector configuration (`request_method=POST` and a
  non-empty `read_only_post_action`) and a persisted connector capability
  (`read_only_post=true`). Request-time context cannot grant this capability.
- PUT, PATCH, DELETE, TRACE, HEAD, and OPTIONS are denied by default. Any future
  exception requires a separately documented and reviewed use case.
- External API acquisition continues to require HTTPS and all existing URL,
  public-address, redirect, and per-request policy checks remain mandatory.

The shared registered-acquisition policy guard was updated to enforce the
conditional POST authorization. This does not mean POST transport is available
yet: the JSON API connector currently issues GET only. Conditional POST request
serialization, body limits/schema, adapter tests, and exact-head CI remain open.



## Connector diagnostics and bounded recovery — 2026-10-19

The JSON API connector now emits structured, sanitized diagnostic events for
request failures and successful recovery. Events include connector type,
attempt number, stable error category, retryability, and HTTP status where
available. They intentionally exclude request URLs, headers, credentials,
response bodies, and exception text so sensitive provider details are not
copied into logs.

For idempotent GET requests only, transient network/timeout and upstream
server failures are retried up to three total attempts with bounded exponential
backoff (0.25 seconds, then 0.5 seconds). Integer and HTTP-date `Retry-After` values are parsed; malformed values are
ignored. The connector honours a delay only up to a two-second wait bound. If
the provider requests a longer wait, it stops retrying and returns the existing
failure outcome rather than retrying early or tying up a worker. Persisted acquisition policy is
revalidated before every attempt. Policy restrictions, authorization/rate-limit
responses, invalid payloads, redirects, and oversized responses are not retried.
A failed request returns the existing sanitized acquisition outcome/category;
previously acquired records remain partial results when appropriate.

TRACE remains prohibited by the shared registered-acquisition method guard.
No connector uses TRACE for diagnosis. This implementation currently provides
diagnostics and bounded retry for the JSON API adapter only; equivalent
structured diagnostics/recovery have not been established for every other
adapter or entry path. Retry behavior and log sanitization require exact-head
GitHub Actions evidence before they are considered verified. Live provider
behavior remains unverified.
