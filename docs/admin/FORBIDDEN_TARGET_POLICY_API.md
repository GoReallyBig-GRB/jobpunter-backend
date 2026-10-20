# Forbidden target policy API and enforcement

## Purpose

The forbidden-target registry is a persisted Admin control for denying source and acquisition destinations. A saved rule is not merely advisory: the source configuration lifecycle and acquisition connectors check active rules before network requests, and connector execution rechecks rules for pagination, browser navigation and redirect-generated requests where those adapters support request interception.

## Rule types

- HOST_EXACT: blocks one normalized hostname.
- HOST_SUFFIX: blocks the hostname and all subdomains on a dot-boundary. It does not match lookalike suffixes such as notexample.com.
- URL_EXACT: blocks one normalized HTTP(S) URL. Query parameters are preserved because they may distinguish job records or pagination; fragments and default ports are normalized.
- URL_PATH_PREFIX: blocks a host/path subtree without matching a lookalike path prefix such as /jobs-archive when the rule is /jobs.

Rules have a connector scope (ALL or one supported connector type), a required reason, creator/update attribution, and an active flag. Rules are deactivated rather than deleted so audit history and prior rule identity remain available.

## Endpoints

- GET /api/v1/admin/target-policy/forbidden-rules — bounded pagination; optional active, rule_type and connector_scope filters.
- POST /api/v1/admin/target-policy/forbidden-rules — create and normalize a rule. Duplicate normalized rule identities return 409.
- PATCH /api/v1/admin/target-policy/forbidden-rules/{rule_id}/status — activate/deactivate with a required reason. The pattern and rule identity are immutable.

All endpoints require trusted Admin authorization. Creation and status changes are recorded in AdminAuditEvent with actor, target rule, previous/new status where applicable, reason and request correlation. Secret-bearing query keys are rejected for URL rules.

## Enforcement points

- Source registration rejects a configured target matching an active rule before saving the source.
- Draft creation, publication and activation re-evaluate the current active policy. A rule added after a draft was created therefore cannot be bypassed by publishing the old draft.
- Admin connector health checks pass the current rules to the connector and cannot probe a denied target.
- Registered acquisition loads active rules from PostgreSQL and passes them to the connector. Policy denials are represented as restricted acquisition outcomes rather than successful fetches.
- JSON API checks every page URL before HTTP; RSS/Atom checks the feed URL before HTTP.
- Playwright checks start URLs before browser launch and intercepts each browser request, including navigation, subresources and redirect destinations, before allowing it to continue. Discovered pagination links are checked before enqueueing.
- Scrapy checks start URLs before DNS validation, checks pagination links before enqueueing, and uses a downloader middleware to check all outgoing requests, including redirects, before network access. The public-address resolver remains a separate network-safety control.
- Greenhouse checks the actual board and jobs endpoint URLs before each request.
- JobSpy delegates requests to a third-party library without a supported per-request interception hook. It fails closed when an ALL- or JOBSPY-scoped rule is active rather than pretending to enforce destination-specific rules after fetching. This is intentionally conservative and can temporarily disable JobSpy for unrelated destinations; a safe per-request adapter is still needed to remove that limitation.

Network safety remains higher priority than forbidden rules. Forbidden rules cannot make private or otherwise restricted network destinations safe. Public-address validation, DNS pinning and redirect/browser controls remain in place.

## Remaining acceptance work

- The candidate-discovery scheduler still creates durable missions independently of the selected source's current target-rule outcome. Execution is protected, but scheduler-level exclusion/visibility for missions whose selected sources are all blocked needs an explicit acceptance test.
- Add adversarial tests for redirect and browser subresource denials, path canonicalization edge cases and concurrent rule changes while a run is queued.
- Verify the exact CI head and migration/schema parity after subsequent changes. Do not infer live source permission or provider health from rule enforcement tests.
