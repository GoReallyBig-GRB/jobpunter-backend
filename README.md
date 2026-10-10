# JobPunter Backend

Production backend for JobPunter.

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

The GitHub Actions workflow runs Python compilation, Alembic migration upgrade, ORM/database schema parity checks, and the test suite against PostgreSQL 16. A green run verifies only the tested paths; it does not establish complete PRD parity or production readiness.

## Material gaps

The backend remains an early foundation, not a complete MVP. Real job-source connectors, end-to-end acquisition workers, JD completeness/freshness processing, evaluation generation and deterministic hard-constraint enforcement, the canonical provider-neutral AI task contract and output validation, application packages/document generation, object storage, integrations, scheduling/notifications, and deployment/recovery operations remain incomplete. See `PRD_BACKEND_GAP_AUDIT.md` for the requirements matrix and implementation priorities.
