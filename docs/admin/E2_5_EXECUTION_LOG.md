# E2.5 live-enforcement execution log

Updated: 2026-10-20  
Repository: `GoReallyBig-GRB/jobpunter-backend`  
Branch: `resume-admin-backend`

## Current checkpoint

Latest code/test checkpoint `f67c74651e62eba52b53f37d6e165232d6b06582` adds a safe audit event for failed manual URL retrievals. A PostgreSQL API test verifies a loopback redirect returns `NON_PUBLIC_ADDRESS`, never opens a socket, and persists only channel/status/category/fallback metadata without either URL. CircleCI `backend-verification` **passed** for this exact SHA: https://app.circleci.com/workflow/58493847-9ebc-478c-92fb-0f7e59712762/job/a763f4ca-3940-41db-9c10-12668109a949. The five-status redirect matrices, eight Playwright registered runtime scenarios, six Scrapy worker mutation cases, JobSpy fail-closed persistence, twelve RSS/Atom and Greenhouse policy cases, and fourteen-case JSON API GET/POST mutation matrix remain covered. Full mutation-by-adapter and cross-entry acceptance remains open.

Latest checkpoint `67d3692623fd4cd398d1ccdc9c8e64d41b111bbe` adds a PostgreSQL integration matrix through the JSON API connector. Six cases commit a policy mutation while the first page response is being served: source configuration change, source deactivation, connector configuration change, connector deactivation, permission-review revocation, and insertion of a forbidden URL rule. The live guard blocks page two in every case, preserves the first-page record, and reports `PARTIAL` with the expected category. CircleCI `backend-verification` **passed** for this exact commit: https://app.circleci.com/workflow/21364bd4-7895-4292-8e17-1df16b67c9bb/job/63e5189d-1a7e-494c-8d28-add1b2603c26. The HTTP transport is a deterministic fake; the database writes, policy revalidation, connector pagination boundary, and persisted deny-rule lookup are real.

Previous checkpoint `294633e66e1e1ef296b4a352e0b5cd0858591845` added the connector-level pagination regression for `ACQUISITION_CONFIGURATION_CHANGED`; the latest checkpoint expands that request-boundary proof to six actual PostgreSQL policy-mutation classes.

Previous checkpoint `216ee79464d26f70db0761e2a345ecaa77c8d592` hardens the real Chromium integration test so CI fails rather than silently skips if the browser binary is missing. The test strips the local fixture's ephemeral port only during connector configuration validation; Chromium still navigates to the unchanged fixture URL, while production nonstandard-port rejection remains covered by unit tests. The test passed in CircleCI alongside the actual main-frame and subresource interception assertions: https://app.circleci.com/workflow/8355ca00-40fe-41c2-8c29-e7b647df87a6/job/d6317dcf-46de-4257-b0d0-2462a6b971bc.

Previous checkpoint `9975cc56f9dfd1a43bccd5f48a265d0016db6773` adds connector-level JSON API evidence that a 302 response pointing to a private loopback URL is rejected without following the redirect or issuing a second request. CircleCI `backend-verification` passed for that exact commit: https://app.circleci.com/workflow/f480f606-6167-4233-94b0-c449e550299b/job/2b3d17ce-2cc7-4574-92d3-dbca59c33a74. This complements the shared GET/approved-POST redirect status matrix and the RSS/Atom and Greenhouse connector-level no-follow tests. The full cross-adapter/entry-path matrix and terminal outcome persistence checks remain open.

Latest checkpoint `580e6389bb8a6926062fafaad87223235d49b4fa` expands the real PostgreSQL-backed Scrapy worker subprocess test to five mid-run mutation cases. While the worker is running, a separate PostgreSQL session mutates (1) connector configuration, (2) source activation state, (3) connector activation state, (4) permission-review approval, or (5) active forbidden-target rules. The worker must block the discovered next-page URL and return the expected category before issuing that request. CircleCI `backend-verification` **passed** for this exact commit: https://app.circleci.com/workflow/ae62afbf-928c-4b10-a6df-af5b6c5782fa/job/c2cfe826-2e16-47c1-9c61-78856b5132a8. The fixture bypasses public-address enforcement only for loopback; source/connector snapshots, per-request PostgreSQL revalidation, and forbidden-rule matching for the non-loopback next-page host remain active. This is broader worker-runtime evidence, not full adapter/entry-path acceptance or live/staging provider verification.

Previous checkpoint `bd54ee9c021c5c5196cca2c94423ed867aaec475` established the four-case worker mutation matrix; the latest checkpoint above adds live forbidden-target-rule insertion as a fifth case.

Previous checkpoint `89264da6a1a3225499e61564f8d609467a8dd15a` introduced the first PostgreSQL-backed mutation case for connector configuration; the latest checkpoint above expands that same subprocess acceptance test to four persisted-policy mutation classes.

Latest checkpoint `49226c1dfacff4e8e119950d92ed2628f97fc727` expands the real Chromium integration test to cover both an off-domain main-frame redirect and an off-domain subresource. The main-frame case returns `RESTRICTED / REDIRECT_BLOCKED` with no records; the subresource case preserves the permitted page's record as `PARTIAL / REDIRECT_BLOCKED`. In both cases, the local fixture records no request to `blocked.invalid`. CircleCI `backend-verification` **passed** for this exact commit: https://app.circleci.com/workflow/156b0686-554c-4c10-89c1-25cc251afabe/job/f040e03d-4d5d-47f2-a1a0-5ca0b4646380. Chromium is installed by CI. The test isolates loopback/public-DNS and production-port checks, so it verifies actual browser request interception against local fixtures, not live-provider or deployment-egress behavior.

Latest checkpoint `c14ecf4601a2e8ae61264712aa8a4d3ed06d668a` adds a real Chromium integration test for an off-domain main-frame redirect. CI now installs Chromium and its system dependencies; the browser navigates to a local redirect fixture, the registered route handler aborts the redirect to `blocked.invalid`, and the connector returns `RESTRICTED / REDIRECT_BLOCKED` without extracting records or issuing a request to the blocked destination. CircleCI `backend-verification` **passed** for this exact checkpoint: https://app.circleci.com/workflow/13856917-db55-4ad4-b046-f79ceb4f0291/job/b7350a60-13c8-403b-8e4c-150685155982. The test isolates loopback/public-DNS and production-port checks to exercise Chromium locally; it is browser-runtime evidence for redirect handling, not staging/live-provider or deployment-egress evidence.

Latest checkpoint `0367b384984d88ef486925dcd63b67130691a8bb` adds a third actual one-shot Scrapy worker fixture test for a policy change affecting pagination: the worker acquires the first page, its live guard denies the discovered `/next` URL with `ACQUISITION_CONFIGURATION_CHANGED`, and the test asserts the second URL is never requested. CircleCI `backend-verification` **passed** for this exact commit: https://app.circleci.com/workflow/a94fb5af-5e9e-48f5-a1a9-0972435eb9c3/job/034c3633-1839-40c9-a37e-387e9c182a6d. Together, the three worker fixture tests cover redirect-loop bounds, forbidden redirect rejection before destination network access, and policy denial of a discovered pagination URL before request. The live guard is deterministic/test-injected in these subprocess tests; this does not prove a real PostgreSQL mutation during a running worker or live/staging provider behavior.

Latest checkpoint `791c063f23b4e85f7a17a6bebcdf199a215e7253` adds a second actual one-shot Scrapy worker subprocess regression. A local endpoint redirects to `https://blocked.example/private-jobs`; a deterministic live-policy guard denies that redirect target, and the real downloader middleware records `FORBIDDEN_TARGET_RULE_MATCHED` before the destination can be requested. CircleCI `backend-verification` **passed** for this exact commit: https://app.circleci.com/workflow/e25a7731-7639-4ef4-b341-887f008797ab/job/214326bb-e1a9-4f12-a42c-1196bcb2cb41. Together with the prior redirect-loop test, this verifies actual worker-process redirect-cap and redirect-destination rejection against local fixtures. It does not establish PostgreSQL policy mutation during the worker process, staging/live-provider behavior, real DNS/egress behavior, or production runtime readiness.

Latest checkpoint `d240295c2091b46a0c2c3a42d72266ead7264021` adds an integration test that launches the actual one-shot Scrapy worker against a local HTTP redirect-loop fixture. The test asserts that Scrapy's configured redirect cap bounds requests, returns no records, and records `SCRAPY_REDIRECT_LIMIT_REACHED`. CircleCI `backend-verification` **passed** for this exact commit: https://app.circleci.com/workflow/c6c82398-78c7-471d-a742-af5ab27f0411/job/77477ce5-170f-41b8-87ee-17cd526596d7. This is worker-process evidence against a local fixture, not staging or live-provider evidence; DNS and policy hooks are deliberately isolated for loopback in this test.


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
- **E2.5-02 — RESOLVED (repository/CI scope):** Scrapy passes a non-secret policy snapshot to its worker, fails closed if live policy cannot load or the persisted snapshot differs, and checks requests through downloader middleware. It calls the live guard before enqueuing initial and pagination URLs, then checks again before network access. Tests cover worker guard initialization, snapshot mismatch, middleware calls across requests, SQL-backed changes, and discovered-link rejection before enqueue. Actual worker subprocess tests now cover redirect-loop bounds, forbidden redirect rejection, and a PostgreSQL connector-configuration mutation between pages. Live/staging provider behavior remains NOT VERIFIED.
- **E2.5-03 — RESOLVED (repository/CI scope, fail-closed):** JobSpy returns RESTRICTED with `JOBSPY_LIVE_POLICY_UNSUPPORTED` whenever the guarded acquisition callback is supplied. This prevents unsupported third-party transport from bypassing fresh policy. Tradeoff: JobSpy is unavailable through guarded acquisition until a controllable transport is implemented.
- **E2.5-04 — OPEN (I):** Expanded regression cases cover source configuration/base URL/type changes, connector configuration/type/version changes, active configuration-version changes, source/connector deactivation, permission-review reference/time mutation, and rejection of unapproved POST plus PUT/PATCH/DELETE/HEAD/OPTIONS/TRACE. The method policy was approved on 2026-10-19: GET is default; POST is permitted only for a documented read-only action with persisted request_method=POST, a non-empty read_only_post_action, and persisted read_only_post=true capability. Other methods remain denied unless separately reviewed. HTTPS, redirect controls, and SSRF checks remain mandatory. Revalidation compares connector capabilities and limits as well as connector configuration/version/type. JSON API checks every page URL; RSS/Atom checks its feed URL; Greenhouse checks both board and jobs URLs. Registered JSON API tests prove active-version publication (1→2) and rollback (2→1) block the next page. New Greenhouse and RSS/Atom regressions prove live-guard denial prevents the relevant HTTP request. The SQL-backed mutation matrix now covers the source/connector fields listed above, but adapter-level acceptance across every supported connector and entry path remains incomplete.

## Adapter and entry-path acceptance crosswalk

The current CI-backed evidence is layered. It proves representative policy mutations at the shared guard, adapter request boundary, and two real application entry paths; it does **not** prove every mutation against every adapter.

| Path | Enforcement boundary | Current evidence | Remaining limitation |
|---|---|---|---|
| Admin manual acquisition | `RegisteredAcquisitionService` guard on each request | PostgreSQL/API test for a new forbidden rule; fourteen Greenhouse request-two mutation cases verify the request is blocked and the run is persisted as `RESTRICTED`. | Provider calls are mocked; this is repository/CI evidence, not live-source behavior. |
| Operator acquisition worker | `app/workers/acquisition.py::run_acquisition` delegates to `RegisteredAcquisitionService`. | Shared service's per-request guard and mutation tests. | Wrapper does not have a separate live-provider test; do not treat it as independent runtime proof. |
| Scheduled discovery mission | Mission worker delegates to `RegisteredAcquisitionService`. | `test_discovery_mission_blocks_second_request_after_live_policy_changes` is parameterized across 16 changes: source configuration/base URL/type, permission-review reference/time/status/evidence, source deactivation, connector configuration/capabilities/limits/active version/type/version/deactivation, and a newly added forbidden rule. It asserts the second HTTP request never occurs and both acquisition-run and mission-source outcomes persist as `RESTRICTED` with the expected category. | Database-backed mission execution with a mocked Greenhouse transport; no live provider call. |
| JSON API | Revalidates each page URL before `bounded_get`. | Registered connector test covers active configuration-version publication and rollback. Connector-level tests verify a 302 to loopback is not followed and page-two denial preserves page-one records as `PARTIAL`. New PostgreSQL integration matrix covers six mid-request mutations: source configuration/deactivation, connector configuration/deactivation, permission-review revocation, and forbidden-rule insertion. | Not every source/connector mutation is repeated through JSON API; complete terminal-outcome persistence coverage remains open. |
| RSS/Atom | Revalidates the feed URL before `bounded_get`; redirects are disabled. | Denial-before-feed-request test verifies zero HTTP calls. | Single-feed request path; live provider behavior is not tested. |
| Greenhouse | Revalidates board and jobs URLs separately. | Adapter test and Admin/mission integration cases verify denial between requests blocks the second request. | Live provider behavior is not tested. |
| Scrapy | Worker policy snapshot plus downloader middleware before outbound requests. | Snapshot mismatch, per-request live guard, discovered-link pre-enqueue rejection, SQL-backed policy mutation, actual POST-method forwarding, and real worker-subprocess tests. PostgreSQL mutation during worker execution covers connector configuration, source/connector deactivation, permission-review revocation, and insertion of a forbidden-target rule. | Staging/live provider behavior and mutation classes beyond those tested remain unverified. |
| Playwright | Browser route guard validates each navigation/subresource request, including actual method. | Unit tests cover live-policy subresource denial and POST-method rejection. Real Chromium tests exercise main-frame redirects and off-domain subresources against local fixtures. | Live-provider behavior, PostgreSQL policy mutation during browser runtime, and deployment egress are not verified. |
| JobSpy | Guarded acquisition fails closed because the third-party transport cannot expose the required request hook. | Registered acquisition persists a restricted outcome with `JOBSPY_LIVE_POLICY_UNSUPPORTED`. | Deliberately unavailable on the guarded path until transport enforcement is controllable. |

**E2.5-04 exit blockers:** the approved conditional read-only POST path has bounded HTTPS/JSON transport, persisted-configuration and capability authorization, request-body validation, redirect rejection, and positive/negative tests. The Admin manual path and scheduled discovery-mission path now have broad persisted-policy mutation matrices; JSON API version publication/rollback and representative RSS/Atom, Greenhouse, Scrapy, and Playwright request-boundary checks are also covered. E2.5 remains open because these are not yet a complete mutation-by-adapter/cross-entry-path matrix; scheduler-time blocked-scope visibility, deployment-level egress proof, and exact-head GitHub Actions evidence still require verification. The one-shot Scrapy worker has been executed in CI against local redirect, pagination, and PostgreSQL policy-mutation fixtures. These tests verify bounded redirects and selected live-policy changes in the worker process, not staging or live-provider behavior.

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

## Checkpoint — manual URL live-policy refresh (2026-10-20)

- Exact code/test SHA: `15a80ae63efd532c7c7f4ee90901a967fc512bfd`.
- CircleCI `backend-verification`: PASS on that exact SHA: https://app.circleci.com/workflow/4d1aa7f2-ab82-40ae-8717-78813c5ceab0/job/06d43ff5-e684-4087-9872-34e8aa1faeda.
- The manual URL endpoint now passes a repository-backed provider that reloads active global deny rules before every request and redirect, rather than using only the initial policy snapshot.
- Added deterministic regression: no rule at the first request, a global deny rule appears before redirect processing, and the redirected target is blocked without a fetch. The PostgreSQL API regression also proves an existing persisted global rule blocks the initial URL before network access.
- Manual URL global-rule enforcement is now covered for initial and redirect steps, including a mid-retrieval policy change. Broader cross-adapter and scheduler-level gates remain open.

## Checkpoint — connector-level RSS/Atom and Greenhouse redirects (2026-10-20)

- Exact code/test SHA: `8751fcf81eaa16556877215d99482f7d08c213ca`.
- CircleCI `backend-verification`: PASS on that exact SHA: https://app.circleci.com/workflow/f96a7df3-681e-47a8-bdc5-ad98edc3b384/job/684a9084-6608-4c94-bcd9-6e154adf8a18.
- Added actual connector-path regressions for RSS/Atom acquisition and Greenhouse health check. Each receives a 302 with a loopback Location and asserts a restricted/error outcome, one request only, and `follow_redirects=False`.
- The shared bounded GET/POST helpers separately cover 301, 302, 303, 307 and 308. Scrapy middleware and Playwright route interception have their own redirect/target policy tests.
- Still open: actual Scrapy worker redirect-loop/status matrix, live Chromium main-frame/subresource evidence, scheduler-time exclusion/visibility, and deployment-level egress proof. E2.5-04/L1.5 remain PARTIAL.


## Scrapy bounded redirect/status checkpoint — 2026-10-20

Checkpoint: `f5d9405cb2a2fa749f7bd97d955d255ed9e7f300`

- CircleCI `backend-verification`: **PASS** on the exact checkpoint SHA.
- Scrapy pins `REDIRECT_ENABLED=True` and `REDIRECT_MAX_TIMES=20`, retaining the bounded existing default.
- A deterministic 15-case worker errback matrix covers 401/403/407/429, 408/425, 5xx, 404, final 301/302/303/307/308 redirect outcomes, and transport timeout classification. Redirect-limit outcomes are non-retryable; transient upstream/timeout outcomes retain bounded retryability.
- This is worker classification/settings evidence, not a claim that an actual Scrapy subprocess completed a live redirect loop. Live subprocess/staging loop behavior remains **NOT VERIFIED**. E2.5-04/L1.5 remain **PARTIAL**.

## Checkpoint — PostgreSQL manual URL live-policy mutation (2026-10-20)

- Exact code/test SHA: `90a698edc745e31978b16e87818cce477e8fdecb`.
- CircleCI `backend-verification`: PASS on that exact SHA: https://app.circleci.com/workflow/1e9ba638-a567-4b36-8ded-b87862552250/job/e6ed2e27-c083-4540-ba6a-26383c9cf204.
- Added a PostgreSQL-backed integration regression through the authenticated manual-ingestion API. The first mocked fetch inserts and flushes a global forbidden-target rule, then returns a redirect. The repository-backed policy provider reloads the rule and rejects the redirect before a second fetch is attempted.
- At this checkpoint, the test verifies DB-backed policy refresh through the actual route within the integration test transaction. Separate-session visibility was not yet covered at that point; the subsequent checkpoint below adds that evidence. Neither checkpoint closes the broader adapter/scheduler/runtime acceptance gates.

## Checkpoint — manual URL policy committed from a concurrent PostgreSQL session (2026-10-20)

- Exact code/test SHA: `b9f9b6ff5601499dcc6206744cd485161cadc5b7`.
- CircleCI `backend-verification`: PASS on that exact SHA: https://app.circleci.com/workflow/55626fc2-fba0-4747-8fac-4d32f8bd0885/job/27cc74ef-2348-45be-972a-bb16b0346d23.
- Added a PostgreSQL integration regression through the authenticated manual-ingestion API. During the first mocked fetch, a separate database session commits a new global deny rule; the fetch then returns a redirect to the newly blocked host. The route's repository-backed provider observes the committed rule and rejects the redirect before a second fetch.
- The test uses independent database sessions for rule publication and policy refresh, with cleanup for committed fixtures. This closes the documented separate-transaction visibility gap for the manual URL redirect path. It does not close safe denial-audit/telemetry review, cross-adapter acceptance, scheduler eligibility/blocked-scope visibility, or live runtime/network-egress verification.



## Checkpoint — PostgreSQL registered JSON API POST terminal outcomes (2026-10-20)

- Exact code/test SHA: `fd61d2723a3cc5d03ed9f1cca76931ca3c2092e8`.
- CircleCI `backend-verification`: **PASS** on that exact SHA: https://app.circleci.com/workflow/d808d253-c382-4cb3-92d3-616a2a7feb3b/job/7369c57c-296f-4073-95b9-aeaaa7ffb7b7.
- Added a seven-case PostgreSQL-backed test through `RegisteredAcquisitionService` and `JsonApiConnector`. A separate session commits each mutation immediately before request-policy revalidation: read-only POST capability revocation, source configuration change, source deactivation, connector configuration change, connector deactivation, permission-review revocation, and forbidden-target rule insertion.
- Each case asserts zero HTTP requests and verifies the persisted `AcquisitionRun` is `RESTRICTED` with the expected category. This extends direct-connector GET pagination tests with registered-service POST terminal-outcome persistence.
- Still open: full per-adapter and alternate-entry acceptance, equivalent persisted terminal-outcome assertions across the complete GET/POST and connector matrix, scheduler-time blocked-scope visibility, deployment-level egress evidence, and staging/live-provider verification. E2.5-04 and L1.5 remain open; no merge or deployment occurred.


## Checkpoint — PostgreSQL registered JSON API GET/POST terminal outcomes (2026-10-20)

- Exact code/test SHA: `e1f906e1c34bead6b0457dc647f2a28d58acf17a`.
- CircleCI `backend-verification`: **PASS** on that exact SHA: https://app.circleci.com/workflow/d882bd7b-3dc8-4b11-98e1-d9779312bb7d/job/047f927a-79f9-456c-8c1f-e9671a4e1cee.
- Expanded the registered JSON API PostgreSQL test into a fourteen-case GET/approved-POST matrix. The test uses a separate session to change persisted policy immediately before the live source read: GET method changed to POST; POST capability revocation; source configuration change/deactivation; connector configuration change/deactivation; permission-review revocation; and forbidden-target insertion, with the common six mutation classes exercised through both methods.
- Each case asserts zero HTTP requests and verifies the persisted `AcquisitionRun` is `RESTRICTED` with the expected category. This is repository/CI evidence for the registered JSON API entry path, not live-provider or deployment-egress evidence.
- Still open: full mutation-by-adapter and alternate-entry acceptance, broader redirect/terminal-outcome persistence across connectors, scheduler-time blocked-scope visibility, deployment-level egress evidence, and staging/live-provider verification. E2.5-04 and L1.5 remain open; no merge or deployment occurred.


## Checkpoint — PostgreSQL registered JSON API redirect terminal outcomes (2026-10-20)

- Exact code/test SHA: `bd4e4079640f339e9ee06b86d822b55785c22c38`.
- CircleCI `backend-verification`: **PASS** on that exact SHA: https://app.circleci.com/workflow/f9fd2b51-7006-4e32-8f00-c67c259addab/job/4ab93b68-d32e-45a4-b7c2-e17339f249e4.
- JSON API now classifies HTTP redirect statuses 300, 301, 302, 303, 305, 307 and 308 as `RESTRICTED / REDIRECT_BLOCKED`, rather than the generic upstream-request-failure outcome. HTTP 304 is deliberately not treated as a redirect by this classifier.
- Added PostgreSQL-backed registered-service integration cases for GET and approved POST. Each receives a 302 with `Location: http://127.0.0.1/admin`, asserts the only request is the original endpoint with `follow_redirects=False`, and verifies the persisted `AcquisitionRun` records `RESTRICTED` with `REDIRECT_BLOCKED`.
- The fourteen-case persisted-policy mutation matrix remains covered on exact checkpoint `e1f906e1c34bead6b0457dc647f2a28d58acf17a`, which also passed CircleCI. Still open: full per-adapter/alternate-entry acceptance, broader redirect-status persistence across connectors, scheduler-time blocked-scope visibility, deployment-level egress evidence, and staging/live-provider verification. E2.5-04 and L1.5 remain open; no merge or deployment occurred.


## Checkpoint — shared redirect outcome and registered adapter persistence (2026-10-20)

- Exact code/test SHA: `047980f1dada954cc1e97a1779a40184a8d0aacb`.
- CircleCI `backend-verification`: **PASS** on that exact SHA: https://app.circleci.com/workflow/24f49583-3b0f-4efa-820c-c10632eef1ea/job/f9f7ceb7-d902-44e2-9b31-f195879adc3f.
- Shared HTTP error classification now maps redirect statuses 300, 301, 302, 303, 305, 307 and 308 to `RESTRICTED / REDIRECT_BLOCKED`; 304 is not classified as a redirect. JSON API, RSS/Atom and Greenhouse therefore report a consistent terminal policy outcome instead of a generic upstream failure for rejected redirects.
- PostgreSQL-backed `RegisteredAcquisitionService` tests now cover redirect responses for JSON API GET, approved JSON API POST, RSS/Atom GET, and Greenhouse GET. Each returns a 302 with a loopback `Location`, issues only the original request with `follow_redirects=False`, and persists `AcquisitionRun.status=RESTRICTED` with `error_category=REDIRECT_BLOCKED`.
- Still open: complete mutation-by-adapter and alternate-entry acceptance, Scrapy/Playwright redirect terminal-outcome persistence, scheduler-time blocked-scope visibility, deployment-level egress evidence, and staging/live-provider verification. E2.5-04 and L1.5 remain open; no merge or deployment occurred.


## Checkpoint — registered RSS/Atom and Greenhouse live-policy mutation matrix (2026-10-20)

- Exact code/test SHA: `77158a4e3b7cd16f04829eb95ee1a676b6a1022d`.
- CircleCI `backend-verification`: **PASS** on that exact SHA: https://app.circleci.com/workflow/c9a49824-6690-4c50-a759-19d2c784d842/job/f0706a01-3a84-4dba-b9cc-9edc7d11fc3d.
- Added six PostgreSQL-backed registered-service cases: RSS/Atom and Greenhouse each test source deactivation, permission-review revocation, and forbidden-target rule insertion committed from a separate session immediately before the live request guard.
- Each case asserts no HTTP request and verifies the persisted `AcquisitionRun` is `RESTRICTED` with the expected category. This extends the existing fourteen-case JSON API GET/approved-POST matrix to two additional registered adapters for selected policy mutations.
- Still open: full mutation-by-adapter coverage, Playwright/Scrapy persisted redirect outcomes, alternate-entry reconciliation, scheduler-time blocked-scope visibility, deployment-level egress proof, and staging/live-provider verification. E2.5-04 and L1.5 remain open; no merge or deployment occurred.


## Checkpoint — PostgreSQL registered JobSpy fail-closed persistence (2026-10-20)

- Exact code/test SHA: `84741bc9fe7931571ceece1475de5686c71bff07`.
- CircleCI `backend-verification`: **PASS** on that exact SHA: https://app.circleci.com/workflow/702c2ba8-d09b-408e-954d-0229359cceae/job/c4c7f808-f4cb-40c9-b36e-57c2c97d10b0.
- Added PostgreSQL-backed registered-service tests with and without an active forbidden-target rule. Both exercise the actual JobSpy connector, which rejects the injected per-request policy callback before loading/calling the third-party scraper, and assert the acquisition run persists as `RESTRICTED / JOBSPY_LIVE_POLICY_UNSUPPORTED` with zero acquired records.
- This documents deliberate fail-closed behavior; it does not make JobSpy a supported live-policy-enforced adapter. Keep it disabled until a reviewed per-request enforcement mechanism exists.
- Remaining E2.5-04 gaps include complete mutation-by-adapter/alternate-entry acceptance, Playwright/Scrapy persisted redirect outcomes, scheduler-time blocked-scope visibility, deployment-level egress evidence, and staging/live-provider verification. E2.5-04 and L1.5 remain open; no merge or deployment occurred.


## Checkpoint — PostgreSQL registered Playwright redirect persistence (2026-10-20)

- Exact code/test SHA: `006f2587d7d54de2f2e0ce0bd1fefecd174a7a33`.
- CircleCI `backend-verification`: **PASS** on that exact SHA: https://app.circleci.com/workflow/8746cbb0-85cc-4430-887b-09086a3619d7/job/6e09c177-3931-485c-a14f-1de5d60377a0.
- Added a PostgreSQL-backed registered-acquisition integration test that launches actual Chromium against a local loopback redirect fixture. The off-domain main-frame redirect to `blocked.invalid` is aborted; only the local `/redirect` request reaches the fixture.
- The test runs through `RegisteredAcquisitionService` and verifies `AcquisitionRun.status=RESTRICTED` and `error_category=REDIRECT_BLOCKED`. Loopback target-policy validation is isolated only at the test seam so Chromium's real route interception is exercised; this is not live-provider or deployment-egress proof.
- Still open: Playwright subresource partial-outcome persistence, Scrapy terminal-outcome persistence through the registered path, full mutation-by-adapter/alternate-entry acceptance, scheduler-time blocked-scope visibility, deployment egress evidence, and staging/live-provider verification. E2.5-04 and L1.5 remain open; no merge or deployment occurred.


## Checkpoint — PostgreSQL registered Playwright terminal outcomes (2026-10-20)

- Exact code/test SHA: `2e644c09f64649ded605b4fe1f71c993fc0a9cde`.
- CircleCI `backend-verification`: **PASS** on that exact SHA: https://app.circleci.com/workflow/37414fd4-c1ac-49a1-9b07-b26e6c427854/job/30d32d4a-cfc8-46fa-9c0f-af3a01ef05d0.
- Expanded the PostgreSQL-backed registered Playwright test to exercise real Chromium for both main-frame redirect and off-domain subresource denial against local fixtures.
- Main-frame redirect persists `RESTRICTED / REDIRECT_BLOCKED` with zero records. Subresource denial preserves the allowed-page record and persists `PARTIAL / REDIRECT_BLOCKED`; cleanup removes the related discovery event, raw record and outbox event after assertions.
- Loopback target-policy validation is isolated only at the test seam so Chromium route interception remains real. Live-provider behavior, PostgreSQL policy mutation during browser runtime, and deployment egress remain NOT VERIFIED.
- Remaining E2.5-04 gaps include Scrapy persisted terminal-outcome coverage through the registered path, complete mutation-by-adapter/alternate-entry acceptance, scheduler-time blocked-scope visibility, and staging/live-provider verification. E2.5-04 and L1.5 remain open; no merge or deployment occurred.


## Checkpoint — PostgreSQL registered Scrapy worker redirect outcome (2026-10-20)

- Exact code/test SHA: `edc55fb243856e45d7ca0097d74b496d32b6a80a`.
- CircleCI `backend-verification`: **PASS** on that exact SHA: https://app.circleci.com/workflow/86038ffb-7815-4dee-998b-496aedfbd94c/job/0fd8025a-1d49-43de-914e-afade4e89008.
- Added a registered-acquisition PostgreSQL integration test that invokes the actual Scrapy connector and one-shot worker against a local fixture. A separate persisted `HOST_EXACT` deny rule blocks the redirect destination before any request reaches that host.
- The test verifies no acquired records and a persisted `AcquisitionRun` with `RESTRICTED / FORBIDDEN_TARGET_RULE_MATCHED`. Test-only seams permit loopback fixture traffic while leaving the real persisted worker policy guard and active destination rule in force.
- Still open: complete mutation-by-adapter/alternate-entry acceptance, Playwright policy mutation during browser runtime, broader Scrapy worker mutation classes and terminal outcomes, scheduler-time blocked-scope visibility, deployment egress proof, and staging/live-provider verification. E2.5-04 and L1.5 remain open; no merge or deployment occurred.


## Checkpoint — PostgreSQL Playwright runtime policy mutation (2026-10-20)

- Exact code/test SHA: `a22fd0d4e6662e5ea669c6d7af46883135c8c0ae`.
- CircleCI `backend-verification`: **PASS** on that exact SHA: https://app.circleci.com/workflow/7dbbb992-060e-46db-8987-80b62ada046e/job/3f227b81-7b5f-400f-bc1f-936f28c331e5.
- Added a third real-Chromium PostgreSQL registered-service scenario. The local page handler commits a source-configuration mutation from a separate session after serving the page; when Chromium attempts an off-domain subresource, the live guard detects the changed persisted policy before transport.
- The test verifies the allowed-page record is retained and `AcquisitionRun` persists `PARTIAL / ACQUISITION_CONFIGURATION_CHANGED`. The other two scenarios continue to prove main-frame `RESTRICTED / REDIRECT_BLOCKED` and subresource `PARTIAL / REDIRECT_BLOCKED`.
- Remaining: complete adapter/alternate-entry mutation matrix, deployed runtime and egress evidence, and staging/live-provider verification. E2.5-04 and L1.5 remain open; no merge or deployment occurred.


## Checkpoint — PostgreSQL registered Scrapy live-policy mutation matrix (2026-10-20)

- Exact code/test SHA: `eb1f154ddfeb19f4322cbbb42f1b4f5bbf1531d7`.
- CircleCI `backend-verification`: **PASS** on that exact SHA: https://app.circleci.com/workflow/50088613-076f-4cf9-88fa-0151ab0df1be/job/21b3b73a-c88f-45a1-8d33-c045936ecd52.
- Expanded registered Scrapy worker PostgreSQL acceptance to six mid-run mutations: source configuration change, source deactivation, connector configuration change, connector deactivation, permission-review revocation, and forbidden-target rule insertion.
- The local fixture commits each mutation from a separate database session after the worker's `/robots.txt` request. The actual one-shot worker's persisted live-policy guard blocks the next start-URL request; the test asserts the only HTTP request reaching the fixture is `/robots.txt` and the persisted run is `RESTRICTED` with the expected category.
- Test-only seams permit loopback fixture traffic while retaining the real worker policy snapshot/revalidation and deny-rule matching. Live-provider and deployment-egress behavior remain NOT VERIFIED. E2.5-04 and L1.5 remain open; no merge or deployment occurred.


## Checkpoint — registered Playwright and Scrapy runtime mutation matrices (2026-10-20)

- Exact code/test SHA: `6f6c15f9f22cb961efdb7dae4faad4cfde074901`.
- CircleCI `backend-verification`: **PASS** on that exact SHA: https://app.circleci.com/workflow/c82dcfd6-68d2-49c8-9d2f-b357280b086d/job/e74a71a7-9897-4996-9473-d32220ee4aec.
- Playwright: eight PostgreSQL-backed registered-service cases use real Chromium. Two cover main-frame and subresource redirects; six mutate source/connector configuration or activation, revoke permission review, or add a forbidden-target rule after the local page response. The live guard blocks the off-domain subresource, retains the allowed-page record, and persists `PARTIAL` with the exact category.
- Scrapy: six PostgreSQL-backed registered-service cases use the actual one-shot worker. Each mutates source/connector configuration or activation, revokes permission review, or inserts a forbidden rule after `/robots.txt`; the worker blocks the next URL before request and persists `RESTRICTED` with the exact category.
- Test-only seams allow loopback fixture traffic but retain real browser/worker execution and database-backed policy revalidation. This is not live-provider or deployment-egress evidence. E2.5-04 and L1.5 remain open; no merge or deployment occurred.


## Checkpoint — redirect-status and manual URL SSRF acceptance (2026-10-20)

- Exact code/test SHA: `6447cdc795509687e7ae7365ef85cc1f54f3f74a`.
- CircleCI `backend-verification`: **PASS** on that exact SHA: https://app.circleci.com/workflow/eeb30c2a-bc12-4d77-9097-6eca0522a5ec/job/5cbd7835-dca1-491f-a546-d2cd223591ad.
- Registered JSON API GET/approved POST, RSS/Atom and Greenhouse PostgreSQL tests now exercise each redirect status 301, 302, 303, 307 and 308. Each asserts one request to the original endpoint, `follow_redirects=False`, and persisted `RESTRICTED / REDIRECT_BLOCKED` outcome.
- Manual URL ingestion now has a PostgreSQL API regression for a 302 to `127.0.0.1`. The retrieval's real address validation rejects the destination as `NON_PUBLIC_ADDRESS`; a test trap on `socket.create_connection` proves no socket connection is attempted. The manual redirect policy-mutation test also covers all five redirect statuses.
- Remaining acceptance is still broader than these tests: complete adapter/entry-path combinations, safe policy-rejection audit/telemetry, production egress proof, and staging/live-provider verification. E2.5-04 and L1.5 remain open; no merge or deployment occurred.


## Checkpoint — safe audit for manual URL retrieval failure (2026-10-20)

- Exact code/test SHA: `f67c74651e62eba52b53f37d6e165232d6b06582`.
- CircleCI `backend-verification`: **PASS** on that exact SHA: https://app.circleci.com/workflow/58493847-9ebc-478c-92fb-0f7e59712762/job/a763f4ca-3940-41db-9c10-12668109a949.
- Failed `MANUAL_URL` retrieval now writes an append-only `AuditEvent` with event type `MANUAL_URL_RETRIEVAL_FAILED`, actor, failure category, retrieval status, channel and fallback availability. The event deliberately omits the submitted URL, query string, redirect destination, response body and posting content.
- The PostgreSQL API regression follows a mocked 302 to loopback into the real address validator, traps `socket.create_connection`, verifies `NON_PUBLIC_ADDRESS`, and asserts the persisted audit details contain no source or redirect URL.
- This closes the previously documented safe-audit gap for URL retrieval failures. Full adapter/entry-path acceptance, deployment egress proof, and staging/live-provider verification remain open. E2.5-04 and L1.5 remain open; no merge or deployment occurred.
