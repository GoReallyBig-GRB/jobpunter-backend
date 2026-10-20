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
previously acquired records remain partial results when appropriate. The JSON API
connector also enforces a 120-second total acquisition deadline across pages,
attempts, and retry waits, passing the remaining budget into each request timeout
and stopping before a retry would exceed the deadline. Regression coverage verifies
that the duration limit preserves already acquired records and prevents additional
page requests. Other adapters do not yet have an established equivalent total
duration budget. Regression coverage also verifies that 400/401/403/429 responses
are not retried, 502/503 responses use the bounded 0.25/0.5-second backoff, and a
persisted-policy revalidation failure prevents the next network request.

TRACE remains prohibited by the shared registered-acquisition method guard.
No connector uses TRACE for diagnosis. This implementation currently provides
diagnostics and bounded retry for the JSON API adapter only; equivalent
structured diagnostics/recovery have not been established for every other
adapter or entry path. Retry behavior and log sanitization require exact-head
GitHub Actions evidence before they are considered verified. Live provider
behavior remains unverified.


## Conditional read-only POST transport — implementation checkpoint

A dedicated `app.infrastructure.acquisition.read_only_post` transport path has
been added separately from `JsonApiConnector`, which remains GET-only. The
conditional entry point requires all of the following before any network call:

- Persisted connector configuration is an object with `request_method=POST`
  and a non-empty `read_only_post_action`.
- Persisted connector capabilities explicitly set `read_only_post=true`.
- The requested action exactly matches the persisted action.
- A callable live policy revalidator is supplied and accepts the request URL,
  connector type, and actual `POST` method immediately before transport.

The transport accepts only a JSON object request body, rejects non-finite
numbers and invalid JSON values, enforces a configurable request-body byte cap
(default 64 KiB), requires streaming response support, rejects redirects and
non-JSON response content types, and bounds response bytes (default 2 MB).
The transport entry point itself rejects non-HTTPS URLs, credentials in the URL,
non-standard ports, local/reserved hostnames, and literal non-public IPs. It also
requires the existing `PublicAddressHTTPTransport` and rejects clients with
alternate transport mounts, so the actual request is DNS-validated and connected
to a validated public IP. The registered acquisition policy revalidator remains
responsible for persisted source/connector policy and forbidden-target rules. This
helper does not make POST available through `JsonApiConnector`.

Unit tests now cover the positive authorized path, persisted-policy denials,
action mismatch, missing revalidator, invalid body types, non-finite numbers,
request-body size, HTTPS and unsafe target rejection, pinned-client enforcement,
alternate transport mounts, redirects, response content type, and response-size
limits. These are authored tests only until CI passes on the exact current PR head.
The method remains a reusable guarded transport primitive, not a newly registered
provider connector; selecting a real provider/action and proving its semantics
are genuinely read-only remains a separate acceptance requirement. E2.5-04
therefore remains OPEN pending exact-head CI and the broader acceptance matrix.

### Redirect enforcement hardening — 2026-10-19

`bounded_get` now passes `follow_redirects=False` at the HTTPX request boundary, rather than relying only on `response.history` after a redirect may already have been followed. This prevents a client configured to follow redirects from issuing an unvalidated second request before the guard rejects the response. Deterministic transport tests assert that redirects are disabled at request time. This narrows the redirect gap for callers using `bounded_get`; it does not establish complete cross-adapter redirect acceptance or replace the public-address-pinned transport and per-request persisted-policy checks. Exact-head CI is required before counting this as verified.


## Conditional JSON API read-only POST integration — implementation checkpoint

The JSON API connector now has a conditional POST path driven by the persisted connector configuration and capability snapshot supplied by RegisteredAcquisitionService. The runtime context cannot replace those reserved policy values. GET remains the default. POST requires request_method=POST, read_only_post=true, a non-empty persisted read_only_post_action, a persisted JSON-object request_body, and fresh policy revalidation immediately before the network call.

POST uses the bounded HTTPS/public-address-pinned transport, validates the serialized JSON object and request-body byte limit before transport, rejects redirects and non-JSON responses, bounds response bytes, and is deliberately single-shot (no retries or pagination). Unsupported methods and missing/revoked policy fail closed. Adapter-level tests have been added for a successful POST, the exact body and method, policy revalidation, invalid persisted method/capability/action/body/pagination configuration, missing live guard, and runtime attempts to forge persisted settings.

This is an implementation checkpoint, not a completion claim. The tests have been authored but have not yet been verified on the exact current PR head. CI status is pending for the latest commit at the time of this update. E2.5-04 remains OPEN until exact-head CI passes and the adapter/entry-path acceptance matrix is reconciled. L1.5 remains PARTIAL; A0.2–A0.6 remain PARTIAL; runtime/provider and restore behavior remain NOT VERIFIED. No merge or deployment has occurred.

### DNS rebinding acceptance regression — 2026-10-20

Added `test_dns_rebinding_from_public_to_private_is_rejected_at_connection_time` in `tests/unit/test_public_http.py`. The test simulates a hostname resolving to a public address during early JSON API URL validation and then changing to loopback before the transport opens its TCP connection. It asserts the connection-time transport rejects the private answer and never passes an address to the TCP backend.

CircleCI `backend-verification` **passed** for exact test checkpoint `bae04d5fb353f079d7ae7c42bf1c3572d4656ba9`: https://app.circleci.com/workflow/b22bc6c5-79e6-4c93-a849-5e50a5c1e559/job/71b62e94-0a44-42a6-9c3b-0a26a5ad47c9.

This adds deterministic repository-level evidence for DNS changes between preliminary validation and socket connection. It does not establish deployed DNS behavior, external provider behavior, or complete L1.5 cross-adapter acceptance. L1.5 remains PARTIAL; E2.5-04 remains OPEN pending full cross-path reconciliation; A0.2–A0.6 remain PARTIAL. No merge or deployment occurred.

### Registered acquisition POST acceptance regression — 2026-10-20

Added `test_registered_service_executes_only_persisted_approved_json_api_post` in `tests/unit/test_registered_acquisition.py`. This exercises the real `RegisteredAcquisitionService` and `JsonApiConnector` path with a controlled, no-network test client. It proves the request uses POST and the exact body from persisted connector configuration, requires the persisted `read_only_post=true` capability, disables redirects, and ignores runtime attempts to replace reserved persisted-policy snapshots and request settings.

CircleCI `backend-verification` **passed** for exact test checkpoint `eb1732dd453744bd7d1f5d05756f2b312c896b5a`: https://app.circleci.com/workflow/451922ea-9936-4e6f-a45c-772bbe46703f/job/98e43fda-7382-4a26-b365-320c09ad8bde.

This closes one registered-service positive-path test gap only. It does not complete the full mutation-by-adapter crosswalk or prove live provider behavior. E2.5-04 remains OPEN; L1.5 remains PARTIAL; A0.2–A0.6 remain PARTIAL; runtime/provider and restore behavior remain NOT VERIFIED. No merge or deployment occurred.

### POST authorization restricted to the reviewed JSON API adapter — 2026-10-20

The shared per-request acquisition guard now authorizes conditional POST only when both the requested connector type and the currently persisted connector type are `JSON_API`, in addition to the persisted method, action, and capability checks. This prevents a different adapter from inheriting POST permission merely because its configuration contains the same fields. Added `test_registered_guard_rejects_post_for_connectors_without_reviewed_json_api_transport`, which verifies that a Greenhouse connector is restricted before network access even when its persisted configuration claims POST approval.

CircleCI `backend-verification` **passed** for exact code/test checkpoint `65fec3bde5cb46699bf72fc4d8a81b00f2b555c3`: https://app.circleci.com/workflow/a24308f1-24fa-4008-931c-4dffd73a4875/job/6aff634c-9b84-4386-b0c8-4ab43d3ed0bb.

This closes the unsupported-adapter POST authorization case at repository/CI scope. E2.5-04 remains OPEN pending the full adapter/entry-path acceptance matrix; L1.5 remains PARTIAL; A0.2–A0.6 remain PARTIAL; runtime/provider and restore behavior remain NOT VERIFIED. No merge or deployment occurred.

## Checkpoint — redirect and method cross-path regression (2026-10-20)

- Exact code/test SHA: `5d44a9af7b7ff9c68afbd3b8a3a11497c0af8192`.
- CircleCI `backend-verification`: PASS on the exact SHA: https://app.circleci.com/workflow/695a2a8f-a60b-479e-b58e-f72ecb748819/job/e1da4fdc-8c9e-40e3-b2d0-9cf77184e045.
- `tests/unit/test_public_http.py`: parameterized bounded-GET rejection for 301, 302, 303, 307 and 308, asserting `follow_redirects=False` and exactly one request.
- `tests/unit/test_read_only_post.py`: equivalent status matrix for bounded read-only POST, asserting redirects are not followed.
- `tests/unit/test_registered_acquisition.py`: requested/runtime connector type and persisted connector type mismatch cannot grant POST.
- Existing DNS-rebinding, live-policy revalidation, registered JSON API POST, and unsupported-adapter denial tests remain in the same acceptance scope.
- Remaining E2.5-04/L1.5 gaps: complete per-adapter redirect/cross-entry acceptance, PostgreSQL-backed concurrent policy-change coverage, scheduler blocked-scope eligibility/visibility, and full PostgreSQL end-to-end security acceptance. This checkpoint does not mark E2.5-04 or L1.5 complete.

## Checkpoint — PostgreSQL live policy revocation (2026-10-20)

- Exact code/test SHA: `a1e1fc70df712e06fd7576a9014ee41c87b17004`.
- CircleCI `backend-verification`: PASS on that exact SHA: https://app.circleci.com/workflow/94f45f0b-846d-41ab-897e-f0a42e0ab656/job/68f0981f-4bd8-4aa4-993b-406c88839866.
- Added a PostgreSQL integration regression using separate sessions: first request policy check succeeds; another session commits permission revocation; the next policy check must reject with `PERMISSION_REVIEW_REQUIRED` before the next request.
- This closes the database-backed mid-run source-permission revocation case. It does not close database-backed forbidden-rule mutation, full cross-adapter acceptance, scheduler eligibility visibility, or runtime/provider verification.

## Checkpoint — PostgreSQL forbidden-target rule refresh (2026-10-20)

- Exact code/test SHA: `e9f062433199bd345815a9b422d3a619ce83533b`.
- CircleCI `backend-verification`: PASS on that exact SHA: https://app.circleci.com/workflow/e61ac43e-003b-4c5c-bb2f-a63d41b2c947/job/f10af8d0-23bf-4198-9e85-b19da673b69b.
- Added a PostgreSQL integration regression using separate sessions: after the first request policy check succeeds, another session commits an active exact-host deny rule; the next guard invocation must fail with `FORBIDDEN_TARGET_RULE_MATCHED`.
- Together with the prior PostgreSQL permission-revocation test, this proves fresh database policy is checked before a subsequent request for those two change classes.
- Still open: complete per-adapter/alternate-entry acceptance, scheduler-level blocked-scope eligibility visibility, and runtime/provider verification. E2.5-04/L1.5 remain PARTIAL.

## Checkpoint — PostgreSQL persisted-policy mutation matrix (2026-10-20)

- Exact code/test SHA: `569310c2d72f613d1851fd5a90fe3a402268576d`.
- CircleCI `backend-verification`: PASS on that exact SHA: https://app.circleci.com/workflow/3e7d916e-dcde-451c-b3e8-2688807002e4/job/6a8c824a-dd6d-4694-9ff5-42761af03d91.
- Added a table-driven PostgreSQL regression that commits each change from a separate session after an initial allowed request: source configuration, connector configuration, source active state, connector active state and read-only POST capability.
- Expected fail-closed outcomes are asserted for each mutation. This complements the dedicated permission-review revocation and forbidden-rule insertion tests.
- The full per-adapter/alternate-entry matrix, scheduler eligibility/blocked-scope visibility, and complete end-to-end PostgreSQL acceptance remain open; E2.5-04/L1.5 remain PARTIAL.

## Checkpoint — persisted request-method mismatch fix (2026-10-20)

- Exact code/test SHA: `f02660318fde2eed084d24da106499d2e2c7d56a`.
- CircleCI `backend-verification`: PASS on that exact SHA: https://app.circleci.com/workflow/24174fed-1392-4686-ae8a-24e68ef22c16/job/772499bd-bf8d-4d1b-81ad-25d64cbb8354.
- Found and fixed a real guard gap: a GET could be allowed even when persisted connector configuration requested POST. The guard now enforces method/configuration agreement, not merely the method presented by the adapter.
- Regression coverage includes GET fallback denial for JSON_API and all supported non-JSON adapters, a registered-service persisted/requested adapter mismatch, and CONNECT denial. Previously added tests cover all redirect status codes and PostgreSQL-backed mid-run policy changes.
- Remaining: full adapter/alternate-entry end-to-end acceptance, scheduler-time eligibility and blocked-scope visibility, and runtime/provider checks. E2.5-04/L1.5 remain PARTIAL.

## Checkpoint — JobSpy registered-path fail-closed clarification (2026-10-20)

- Exact code/test SHA: `d05f4038294faead7c1367b768c88cb54d6caf0b`.
- CircleCI `backend-verification`: PASS on that exact SHA: https://app.circleci.com/workflow/fc20fab2-5d78-4b13-9eb4-e9098eafe7f8/job/b3e77a40-2503-44a6-b32e-46654a147c80.
- Confirmed that the registered acquisition service always injects `acquisition_policy_revalidator`; JobSpy rejects any invocation because its third-party transport cannot enforce policy before each outbound request. This blocks registered JobSpy runs even when no deny rules are active.
- Parameterized regression now covers both no-applicable-rule and active-rule cases, asserting the truthful restricted outcome `JOBSPY_LIVE_POLICY_UNSUPPORTED` and no persisted items.
- This is a deliberate fail-closed limitation, not a working adapter capability. Do not remove the guard without an independently reviewed per-request enforcement design. E2.5-04/L1.5 remain PARTIAL.

## Checkpoint — manual URL alternate-path deny enforcement (2026-10-20)

- Exact code/test SHA: `f21df0c6ca5a8c3f331fe2b7c845bfb581b78254`.
- CircleCI `backend-verification`: PASS on that exact SHA: https://app.circleci.com/workflow/c0fc2b1a-9184-43ea-a3d2-d7f48489e335/job/1bc2c5c0-04c0-4776-a834-5fa19f85472a.
- The authenticated candidate `MANUAL_URL` path now loads persisted active forbidden-target rules and supplies them to the bounded retrieval utility. The utility rechecks global `ALL` rules before the initial network request and every redirect destination.
- Unit tests prove the initial forbidden URL and a forbidden redirect destination are not fetched. A PostgreSQL-backed API test proves a persisted global rule yields `FORBIDDEN_TARGET_RULE_MATCHED` before network access.
- Scope is deliberate: `MANUAL_SUBMISSION` is not a connector scope, so global `ALL` rules apply to this path; connector-specific scopes remain connector-specific. Full adapter/scheduler/end-to-end acceptance remains open.
