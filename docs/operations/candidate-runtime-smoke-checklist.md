# Candidate runtime smoke-test and recovery checklist

**Purpose:** Collect target-environment evidence for U7.1 / S13-B03-T03. Repository CI runs migrations and tests against an ephemeral PostgreSQL service; it does not supply live Firebase, SMTP, OCR, R2/Drive, job-source, or deployed worker credentials. Do not mark any provider/runtime gate complete until a real staging result is recorded.

## Run metadata (complete for each execution)

- Environment / deployment:
- Backend commit SHA:
- Operator and authorization:
- Run date/time (UTC):
- Test data identifiers (synthetic only):
- Provider/service and version:
- Command or controlled API procedure:
- Result: PASS / FAIL / NOT RUN
- Logs/run URL (secrets and personal data removed):
- Cleanup and recovery result:
- Follow-up issue / owner:

## Provider checks

### Firebase Authentication and local identity

- [ ] Use a dedicated staging identity and obtain a real Firebase-issued ID token through the configured sign-in flow.
- [ ] Call an authenticated candidate endpoint; confirm a durable local identity/profile is created and repeat sign-in maps to the same local user.
- [ ] Verify expired, malformed, revoked (where provider supports revocation detection), and unknown-provider credentials fail closed.
- [ ] Verify suspended/deactivated local accounts cannot use candidate APIs.
- [ ] Confirm no email-only account merge and no raw token in application logs.
- **Evidence:** redacted request IDs/statuses, local user IDs hashed or otherwise non-identifying, and identity-provider console result. Never attach the token.

### Private object storage (Cloudflare R2; authoritative document store)

- [ ] In staging, upload a synthetic DOCX/PDF through the actual application workflow.
- [ ] Verify object checksum/size and read-after-write bytes.
- [ ] Verify objects are private by default; only an authenticated owner flow can obtain a short-lived download URL.
- [ ] Verify the signed URL expires as configured and another candidate cannot request the artifact.
- [ ] Exercise cleanup on a failed DB commit and confirm no orphaned object remains.
- [ ] Delete the synthetic artifact and verify cleanup.
- **Evidence:** object key redacted or synthetic, checksum, size, TTL, API status, and cleanup result. Never publish signed URLs or storage credentials.

### OCR (Google Vision; only when explicitly enabled)

- [ ] Confirm the staging feature flag and credentials are intentionally enabled for this run.
- [ ] Submit a synthetic low-risk scan with known text and compare extracted output to expected text.
- [ ] Exercise provider timeout/error and verify failure is categorized, visible and retryable without creating authoritative evidence prematurely.
- [ ] Confirm OCR-disabled configuration does not call the provider.
- **Evidence:** fixture hash, expected-vs-actual extraction summary, provider status/category, and retry result. Do not use real candidate documents.

### Optional Google Drive backup/restore

- [ ] Confirm backup is explicitly enabled and target folder/permissions are approved.
- [ ] Back up a synthetic artifact and verify backup metadata/checksum.
- [ ] Remove or isolate the staging copy, then restore through the supported restore workflow.
- [ ] Verify restored bytes/checksum and ownership/access restrictions.
- [ ] Exercise missing/corrupt backup handling and record the safe error.
- **Evidence:** backup/restore IDs, checksums, result and cleanup. No OAuth tokens or shareable URLs in logs.

### SMTP email notifications

- [ ] Use a controlled test mailbox and explicit test approval; do not send to candidate addresses.
- [ ] Verify default-disabled preference produces no SMTP connection or message.
- [ ] Verify opted-in + verified address sends exactly one notification with the stable message ID.
- [ ] Verify unverified address and revoked consent at delivery time produce no send.
- [ ] Simulate provider failure; verify bounded retry, safe error category, attempt count and terminal dead-letter behavior.
- [ ] Restore provider connectivity and verify eligible pending work recovers without duplicate in-app notification.
- **Evidence:** provider message ID or redacted acceptance result, outbox/delivery status and attempts. Do not include message contents, mailbox secrets, or SMTP credentials.

### Approved external job source

- [ ] Obtain explicit source permission review for the exact connector/site and bounded query.
- [ ] Run the opt-in canary in an egress-restricted staging runner using the documented approval variables and a small result limit.
- [ ] Stop on access-denied, CAPTCHA, authentication barrier, or rate-limit responses; never bypass controls.
- [ ] Confirm records have valid source URLs, content hashes and non-empty titles.
- [ ] Follow the source run through persistence/canonicalization and candidate-safe downstream status; a connector result alone does not prove end-to-end acquisition.
- **Runbook:** docs/operations/acquisition-live-canaries.md.

## Worker, scheduler, and restore drills

- [ ] Enqueue a synthetic pending outbox event; restart the outbox worker before processing; verify the event is eventually processed exactly once at the business-effect level.
- [ ] Force one transient handler failure; verify scheduled retry, safe error category and eventual success or bounded DEAD state.
- [ ] Start a synthetic discovery mission, interrupt the worker at a safe test point, restart the worker/scheduler, and verify lease/recovery semantics resume or terminate truthfully without duplicate candidate notifications.
- [ ] Restart the scheduler and confirm due work is recovered from durable state rather than process memory alone.
- [ ] Perform a staging database backup and restore drill plus object-storage artifact restore. Validate migration/schema compatibility and checksums before declaring recovery successful.
- [ ] Confirm logs include request/event identifiers and state transitions without secrets or private document content.

## Exit criteria

The runtime gate is **PASS** only when each required provider and recovery check has an actual target-environment result, exact commit SHA, timestamp, and redacted evidence link. A skipped canary, mocked provider test, configured environment variable, green CI badge, or this checklist alone is **not** a pass.

Current known state before execution: CircleCI verifies code, migrations, schema parity, and unit/PostgreSQL integration tests against a CI PostgreSQL container. No target-environment provider smoke results or worker/database/object-store restart/restore evidence have been attached to this repository audit. Therefore the overall runtime gate remains **NOT VERIFIED / BLOCKED pending authorized staging access and evidence**.
