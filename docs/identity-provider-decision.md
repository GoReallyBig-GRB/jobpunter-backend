# Identity Provider Decision Record

**Status:** MVP decision recorded in the JobPunter Admin Backend Implementation Task List; implementation and repository CI evidence verified at the checkpoint below.  
**Scope:** Backend authentication boundary for candidate and administrator APIs.  
**Implementation checkpoint:** `6b768c744dc87ee48484c6194eaf9c10cd6cd943`  
**CI evidence:** CircleCI `backend-verification` passed for the implementation checkpoint: https://app.circleci.com/workflow/33230685-c37b-4539-9278-1e8d7269cbb3/job/21dbd019-ec8b-4894-8fcd-d9643280c3e1

## Decision

Use **Firebase Authentication with Google sign-in for the MVP**. The backend owns the trust boundary through the `IdentityProvider` port; frontend-supplied identity or role claims are not authoritative.

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
- `app/api/deps.py`: server-owned provider selection and strict development-identity opt-in.
- `app/infrastructure/identity/firebase.py`: Firebase token verification, project check, revocation check, UID mapping and inactive-account denial.
- `tests/unit/test_auth_dependencies.py` and `tests/unit/test_auth_fail_closed_cases.py`: boundary and fail-closed behavior.
- `tests/unit/test_firebase_identity_provider.py`: token validation, account mapping, first sign-in and concurrency-race handling.
- `tests/integration/test_firebase_identity_postgres.py`: PostgreSQL identity lifecycle, concurrency and no-email-merge cases when the integration database is available.

## Verification boundary

Repository CI passed for the implementation checkpoint and exercises configured compilation, migrations/schema checks, and tests. This is not evidence of a live Firebase sign-in or deployed-provider verification. Those remain **NOT VERIFIED** until an authorized staging/runtime check is performed.
