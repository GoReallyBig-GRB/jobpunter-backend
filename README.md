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

Phase 1 foundation is established, with identity/candidate persistence and the transactional
outbox/work-item foundation. The next implementation slices are canonical jobs/acquisition,
evaluation, Punt qualification, applications, integrations and admin control-plane governance.


Verification workflow enabled.
