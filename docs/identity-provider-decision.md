# Identity Provider Decision Record

**Status:** Firebase is the current MVP authentication adapter, not a permanent provider lock. The backend identity boundary is provider-neutral; PostgreSQL is the authoritative application database. Implementation and repository CI evidence are recorded at the checkpoint below.  
**Scope:** Backend authentication boundary for candidate and administrator APIs.  
**Implementation checkpoint:** `6b768c744dc87ee48484c6194eaf9c10cd6cd943`  
**CI evidence:** CircleCI `backend-verification` passed for the implementation checkpoint: https://app.circleci.com/workflow/33230685-c37b-4539-9278-1e8d7269cbb3/job/21dbd019-ec8b-4894-8fcd-d9643280c3e1

## Decision

Use **Firebase Authentication with Google sign-in as the current MVP provider** behind the backend-owned `IdentityProvider` port. This is an implementation choice, not a lock-in commitment: the app must not depend on Firebase-specific identity semantics outside the adapter and provider/subject mapping boundary. PostgreSQL remains the authoritative store for internal users, provider identities, candidate data, configuration, and application records. Frontend-supplied identity or role claims are never authoritative.

## Portability contract

- Application authorization uses the internal user ID and server-owned roles/permissions, not Firebase UID or frontend claims.
- Provider-specific token verification and provider-subject interpretation stay inside the selected `IdentityProvider` adapter.
- The durable identity record stores provider + provider subject mapped to an internal PostgreSQL user; provider subjects must not become application-wide primary keys.
- Replacing Firebase requires implementing and configuring another adapter, migrating/linking provider identities safely, and validating token/session/revocation behavior. It must not require moving the application database away from PostgreSQL or rewriting candidate/job/application domain records.
- `get_identity_provider()` currently supports Firebase only and fails closed for unsupported provider names. Therefore, the architectural seam exists, but switching providers is not a zero-code toggle today; a new adapter and migration/verification work would be required.

## Required behavior

- Verify Firebase ID tokens on the server and check revocation.
- Require the configured Firebase project ID to match the initialized Firebase Admin app.
- Map the verified Firebase UID to a durable internal user through the unique provider/subject identity record.
- Never merge accounts solely because their email addresses match.
- Reject inactive internal accounts.
- Reject missing, malformed, invalid, expired, or revoked credentials. Do not fall back to `X-User-ID` after authentication failure.
- Permit development `X-User-ID` identity only when both `JOBPUNTER_ENVIRONMENT=development` and `JOBPUNTER_ALLOW_DEV_IDENTITY=true` are explicitly configured.
- Fail closed when `JOBPUNTER_IDENTITY_PROVIDER` names an unsupported provider. Do not silently substitute another provider.
- Return a safe authentication failure to clients; missing provider configuration is a service-configuration failure, not successful authentication.

## Repository evidence

- `app/ports/identity/__init__.py`: provider-neutral contract and authenticated principal.
- `app/api/deps.py`: selects the configured provider at the backend boundary; currently Firebase is the only implemented provider, and unknown provider names fail closed.
- `app/infrastructure/db/models/identity.py`: durable provider/subject-to-internal-user mapping stored in PostgreSQL.
- `app/api/deps.py`: server-owned provider selection and strict development-identity opt-in.
- `app/infrastructure/identity/firebase.py`: Firebase token verification, project check, revocation check, UID mapping and inactive-account denial.
- `tests/unit/test_auth_dependencies.py` and `tests/unit/test_auth_fail_closed_cases.py`: boundary and fail-closed behavior.
- `tests/unit/test_firebase_identity_provider.py`: token validation, account mapping, first sign-in and concurrency-race handling.
- `tests/integration/test_firebase_identity_postgres.py`: PostgreSQL identity lifecycle, concurrency and no-email-merge cases when the integration database is available.

## Verification boundary

The abstraction and PostgreSQL mapping reduce provider lock-in, but the repository currently implements only the Firebase adapter. A second-provider contract test or migration rehearsal has not been evidenced in this record, so portability is an architectural requirement with a concrete seam, not yet a verified provider-switch acceptance test.


Repository CI passed for the implementation checkpoint and exercises configured compilation, migrations/schema checks, and tests. This is not evidence of a live Firebase sign-in or deployed-provider verification. Those remain **NOT VERIFIED** until an authorized staging/runtime check is performed.
