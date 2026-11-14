# Staging runtime verification

This runbook collects bounded evidence from a deployed JobPunter backend. It does not provision an environment, create credentials, mutate candidate data, send email, run a source acquisition, or perform backup/restore. Those actions remain controlled manual drills in `candidate-runtime-smoke-checklist.md`.

## Deployment requirements

1. Deploy an exact commit that has passed the authoritative GitHub Actions backend-verification workflow.
2. Set `JOBPUNTER_ENVIRONMENT=staging` and inject the exact lowercase Git SHA as `JOBPUNTER_DEPLOYMENT_REVISION`.
3. Apply the Alembic migrations as a controlled release step.
4. Start the API and the workers required by the chosen staging topology.
5. Obtain a short-lived bearer token for a dedicated staging administrator. Keep it only in `JOBPUNTER_STAGING_ADMIN_TOKEN`; never put it on the command line or in the evidence file.

The public endpoints are intentionally bounded:

- `GET /api/v1/health` proves only process liveness and reports the injected revision.
- `GET /api/v1/readiness` checks database connectivity, compares the database Alembic revision with the code migration head, and requires a deployment revision outside development. It returns HTTP 503 on any failed check and never returns connection or filesystem errors.

## Evidence command

Run from the matching backend checkout. Repeat `--expected-worker` and `--required-integration` only for processes and providers that the staging topology is meant to operate.

```powershell
$env:JOBPUNTER_STAGING_ADMIN_TOKEN = '<short-lived-token>'
python -m scripts.verify_staging_runtime `
  --base-url https://staging-api.example.com `
  --expected-revision <full-git-sha> `
  --expected-worker outbox `
  --expected-worker discovery_scheduler `
  --expected-worker discovery_run_worker `
  --required-integration smtp_email `
  --integration-max-age-seconds 3600 `
  --timeout 15 `
  --output staging-runtime-evidence.json
Remove-Item Env:JOBPUNTER_STAGING_ADMIN_TOKEN
```

The verifier fails when:

- the deployed revision differs from the expected SHA;
- database connectivity or migration-head parity fails;
- an expected worker has no heartbeat or its latest heartbeat is not `RUNNING`/`IDLE`;
- the operations summary reports stale workers, expired processing outbox events, or stale running acquisitions;
- a required integration is not configured and backed by a persisted `HEALTHY` observation with a timezone-aware timestamp within the configured maximum age (default one hour); future, missing and malformed timestamps fail; or
- Admin evidence is requested without a bearer token.

The JSON report contains only safe statuses, revision identifiers, migration revision names, timestamps, worker names, and sanitized error categories already exposed by the Admin APIs. It never includes the bearer token, credentials, provider responses, message bodies, candidate data, or document identifiers.

## Interpretation

A passing report is staging evidence for the exact checks requested in that invocation. It is not evidence of live email delivery, real source acquisition, external object read/write, worker restart recovery, provider failover, database restore, or document-object restore. Complete and retain the manual checklist with redacted logs and recovery results before making a production-readiness claim.

Response bodies, including HTTP error responses, are limited to 1 MiB and closed after reading. `--timeout` must be finite, greater than zero and at most 60 seconds per socket operation; it is not a whole-report deadline. `--integration-max-age-seconds` must be 1–86400. Recent connectivity evidence does not prove delivery, nor does the observation carry an effective provider-configuration version. Verify those separately.
