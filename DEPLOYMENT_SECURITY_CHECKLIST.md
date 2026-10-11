# Deployment Security and Operational Readiness Checklist

Status: release checklist, not evidence that any environment is configured or production-ready. No hosting provider or ingress implementation is assumed by this document.

Use this checklist for a staging deployment first. Record the environment, tested commit SHA, operator, date, evidence links, and exceptions for each gate. Do not mark a gate complete from configuration alone when runtime verification is required.

## 1. Release identity and configuration

- [ ] Record the exact application commit SHA and matching successful backend verification run.
- [ ] Confirm the target database and environment before running migrations.
- [ ] Inject secrets through the deployment's approved secret manager; do not commit secrets or place them in logs, tickets, or shell history.
- [ ] Set production environment flags explicitly. Confirm development identity is disabled: `JOBPUNTER_ENVIRONMENT=production` and `JOBPUNTER_ALLOW_DEV_IDENTITY=false`.
- [ ] Set `FIREBASE_PROJECT_ID` to the intended Firebase project and verify sign-in with a real ID token from that project.
- [ ] Set `JOBPUNTER_CORS_ALLOWED_ORIGINS` to the exact deployed frontend origin(s), separated by commas. Do not use wildcard origins. Verify an approved origin succeeds and an unapproved origin is rejected.
- [ ] Confirm AI credentials and model settings are server-side only and never exposed through frontend-prefixed environment variables.

## 2. TLS, browser security, and ingress limits

- [ ] Terminate TLS at the approved ingress/proxy and redirect public HTTP traffic to HTTPS.
- [ ] Verify certificate renewal and expiry monitoring.
- [ ] Verify ingress-level security headers and decide where HSTS is set. The application middleware currently sets `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, and `Permissions-Policy`; it does not establish TLS or set HSTS.
- [ ] Verify `X-Request-ID` is returned and propagated as intended. Do not trust arbitrary client-supplied request IDs.
- [ ] Set an ingress request-body limit that permits the intended multipart envelope while preventing oversized uploads. The application separately enforces its AI request limit and document-file limit; ingress protection must not be assumed from those checks.
- [ ] Exercise normal responses, validation errors, oversized AI requests, upload-limit rejections, CORS preflight, and unapproved-origin requests through the deployed ingress.

## 3. Identity, authorization, and secrets

- [ ] Verify Firebase Admin token verification uses the expected project and rejects invalid, expired, and revoked tokens.
- [ ] Confirm candidate-owned endpoints reject cross-candidate access and that request-body user IDs never establish ownership.
- [ ] Confirm admin endpoints require an active administrator grant; stage the first-admin bootstrap and recovery procedure under dual control where available.
- [ ] Confirm no public route permits unauthenticated first-admin creation.
- [ ] Verify secret rotation and revocation procedures for Firebase, AI provider, database, object storage, and any enabled integration credentials.

## 4. PostgreSQL and release migrations

- [ ] Take a recoverable database backup or snapshot before production migration.
- [ ] Apply Alembic migrations as a controlled release step, not independently from every API replica.
- [ ] Verify migration head and ORM/schema parity in CI before deployment.
- [ ] Verify the deployed schema version after migration and run a smoke test against candidate-scoped persistence.
- [ ] Document the rollback decision for application and schema changes. Do not assume a destructive migration can be reversed automatically.

## 5. Private document storage and retention

- [ ] Verify the R2 bucket is private and public access is disabled.
- [ ] Use least-privilege storage credentials and verify object write/read permissions from the deployed application.
- [ ] Verify signed download URLs expire at the configured TTL and do not expose long-lived credentials.
- [ ] Test upload validation for file type/signature, 4 MiB file limit, malformed PDF/DOCX/TXT, oversized document structures, and cross-candidate access.
- [ ] Decide and configure how the retention worker is scheduled. Confirm it actually runs, records outcomes, and deletes only eligible originals while retaining required metadata.
- [ ] If Google Drive backup is enabled, verify OAuth consent, minimum scopes, failure behavior, deletion semantics, and an actual restore. It is disabled by default and must not be treated as a verified backup merely because configuration exists.
- [ ] Perform and record a database restore and document-object restore drill before production launch.

## 6. Workers, schedules, and observability

- [ ] Confirm which workers and schedulers are deployed, how they start, and how they recover after interruption.
- [ ] Verify outbox/work-item retry, lease recovery, idempotency, and terminal-failure visibility for each enabled handler.
- [ ] Do not claim scheduled discovery, notifications, external integrations, or health checks are operational unless an actual execution and outcome are recorded.
- [ ] Verify request IDs, execution IDs, safe error categories, audit events, acquisition outcomes, and AI execution records are observable without logging secrets or private document contents.
- [ ] Configure alerting and an owner for API, database, storage, worker, and scheduled-task failures.

## 7. Candidate journey smoke tests

- [ ] Sign in with a real candidate account and verify candidate isolation.
- [ ] Acquire a real job from an approved source; verify provenance, normalization, deduplication, and failure handling.
- [ ] Verify unknown or incomplete job data is not silently treated as a match.
- [ ] Evaluate a candidate against a job and inspect hard blockers, evidence provenance, uncertainty, and the recommendation explanation.
- [ ] Import a document, review extracted fields and conflicts, confirm evidence, and verify ownership.
- [ ] Generate and review an application package; verify claims against approved evidence and ensure the candidate retains approval control.
- [ ] Record application submission only from a candidate-confirmed real-world event. The system must not fabricate submission or acceptance.
- [ ] Verify audit history and lifecycle transition restrictions.

## 8. Go/no-go record

Do not describe the backend as production-ready until the required gates above have evidence from the intended environment. Record each uncompleted gate as a release blocker or an explicitly accepted exception with an owner and expiry date.

- Environment:
- Tested commit SHA:
- Successful CI run:
- Verification date/operator:
- Blocking findings:
- Accepted exceptions and expiry:
- Go/no-go decision:
