# First administrator bootstrap record

Procedure followed: `ADMIN_GRANT_BOOTSTRAP_RUNBOOK.md` (first bootstrap, zero active admins).

| Item | Value |
|---|---|
| Date (UTC) | 2026-11-17 |
| Environment | Live: Render `jobpunter-api` and Neon project `jobpunter-dev` |
| Authorised by | The product owner (Ijeoma), who asked for the bootstrap on 2026-11-16/17 |
| Performed by | Claude (Claude Code session), by SQL against the live database |
| Firebase project | `gen-lang-client-0793401397` (project name `jobpunter`) |
| Firebase UID | `b8YKh5slaefJlrmg5M4lPfDFAWH3`, printed by the owner's own Firebase sign-in |
| Local user | `a92d9461-b786-400d-8bdf-f81e7c7fa1c5`, status ACTIVE, created by the owner's first authenticated API call |
| Grant created | `bf45c119-9a5a-4236-8673-14ea94360738`, role ADMIN, `granted_by_user_id` NULL (bootstrap), at 2026-11-17 11:43:54 UTC |

Preconditions checked in the same guarded statement (advisory lock 748235109): the user is ACTIVE, the
identity row maps FIREBASE to that UID, and the active ADMIN grant count was 0. Afterwards: 1 active ADMIN grant.

This is an out-of-band database operation. It is not an application audit event (`admin_audit_events`
requires a user actor, and no administrator existed). All later admin actions are audited normally.

No secrets were used or recorded. Revoked-token checks run with the Firebase service-account key held only in the
Render environment variable `FIREBASE_SERVICE_ACCOUNT_JSON`.
