# JobPunter Backend

Backend service for JobPunter.

## Architecture

- FastAPI + Python
- PostgreSQL as authoritative persistence
- SQLAlchemy 2.x + Alembic
- Modular monolith
- Provider-neutral AI and acquisition ports
- PostgreSQL-backed work items initially
- Transactional outbox for reliable downstream processing
- Next.js frontend consumes the versioned API

Staging and production use the same architecture. Environment configuration, credentials,
data, infrastructure instances and scale may differ.

## Governing principle

AI interprets. Evidence supports. Rules decide. The candidate remains in control.

## Current implementation slice

The backend currently includes:

- Firebase ID-token verification and local user/identity provisioning, with development identity gated by two explicit environment flags.
- Candidate profile, preferences, evidence and claim persistence, plus candidate-scoped opportunity APIs.
- Acquisition-run/raw-record persistence, canonical job identity resolution and description versioning, provenance, and recoverable outbox dispatch foundations.
- Candidate-scoped evaluation listing, Punt qualification/persistence, and application lifecycle transitions with revision checks, events and outbox writes.
- AI generation through a Google adapter with execution telemetry and optional published task configuration overrides.
- Admin-grant-protected telemetry/audit reads and versioned configuration draft, publish and rollback-draft operations.
- PostgreSQL integration tests for selected ownership boundaries and the admin configuration lifecycle.

## Verification

The GitHub Actions workflow runs Python compilation, the single-Alembic-head check, Alembic migration upgrade, ORM/database schema parity checks, and the test suite against PostgreSQL 16. A green run verifies only the tested paths; it does not establish complete PRD parity or production readiness.

## Material gaps

The backend remains an early foundation, not a complete MVP. A Greenhouse public-board connector, governed AI task output contracts, candidate-scoped evaluation/Punt and application-package foundations, and private object-storage adapters exist, but the complete end-to-end candidate journey is not yet verified. Remaining gaps include full JD completeness/freshness handling, broader semantic requirement matching, worker deployment and recovery, scheduling/notifications, external integrations, and production deployment/backup/restore verification. See `PRD_BACKEND_GAP_AUDIT.md` for the requirements matrix and implementation priorities.

## Deployment readiness

Use [`DEPLOYMENT_SECURITY_CHECKLIST.md`](DEPLOYMENT_SECURITY_CHECKLIST.md) for staging and production release gates. It is a checklist, not evidence that a deployment has been configured or verified.

## Private document storage

Cloudflare R2 is the default authoritative object store for generated and uploaded candidate documents. PostgreSQL stores metadata and object references only. Objects remain private; downloads use short-lived signed URLs. Set these environment variables:

- `DOCUMENT_STORAGE_PROVIDER=r2`
- `R2_BUCKET`
- `R2_ENDPOINT_URL` (the account-specific R2 S3 endpoint)
- `R2_ACCESS_KEY_ID`
- `R2_SECRET_ACCESS_KEY`

For a controlled migration only, `DOCUMENT_STORAGE_PROVIDER=gcs` uses `GOOGLE_CLOUD_STORAGE_BUCKET`. Do not configure both providers expecting automatic replication. Document imports accept PDF, DOCX, and UTF-8 TXT files up to 4 MiB (4,194,304 bytes). Google Drive personal backup is optional and disabled by default. To enable it, set `GOOGLE_DRIVE_BACKUP_ENABLED=true`, `GOOGLE_DRIVE_CLIENT_ID`, `GOOGLE_DRIVE_CLIENT_SECRET`, and `GOOGLE_DRIVE_REFRESH_TOKEN`; optionally set `GOOGLE_DRIVE_FOLDER_ID` to the private backup folder. The refresh token must be granted the `drive.file` scope. The adapter creates or updates a private Drive copy keyed by the JobPunter document ID. Backup failures are recorded in document metadata and do not invalidate the R2 original. Retention defaults to 30 days (`DOCUMENT_RETENTION_DAYS=30`). The dependency-aware cleanup entrypoint is `python -m app.workers.document_retention_worker`; schedule it through the deployment's job scheduler. It expires only imports untouched for the retention window that have not been saved as candidate evidence. Application-package documents and evidence-backed documents are retained. Expired R2 originals keep their database metadata for audit; personal Drive backup copies are intentionally not deleted by this worker.
