# Admin frontend integration queue

M15 of the backend mop-up plan. This is a contract queue for a later frontend repository batch, not frontend implementation or release approval. Use the server role matrix in ADMIN_ROLE_PERMISSIONS.md and the API schema for request/response details. The backend remains authoritative.

| Task | Contract | Acceptance |
|---|---|---|
| Role-aware navigation | Approved SUPER_ADMIN, ADMIN, SUPPORT_AGENT and OPERATIONS permissions; direct API access still server checked | Hide unavailable controls, handle 403, refresh after access changes; never infer authority from browser state |
| Support user summary | GET /api/v1/admin/users/{user_id}/support-summary; identity/status fields only | Support staff do not request full profile/evidence/document endpoints; unknown users show 404 |
| Ticket handling | /api/v1/admin/support/tickets; reply/assignment/status; idempotency on replies | Replies remain staff-attributed; OPEN to RESOLVED to CLOSED; follow-up instead of reopening; show real persisted outcomes |
| SMTP settings | /api/v1/admin/smtp/configurations and test/publish/rollback actions (SUPER_ADMIN/ADMIN only) | Secrets are write-only; test before publish; rollback creates a draft requiring test/publication; connection success is not delivery success |
| Read-only inspection | SUPER_ADMIN/ADMIN-only start/workspace/end contracts in READ_ONLY_IMPERSONATION_API.md | Persistent banner, target/reason/time remaining, explicit exit; maximum 15 minutes; no candidate mutations; expiry/revocation close access |
| Operations | Existing discovery/source/work-item APIs and explicit permission matrix | Distinguish queued, running, skipped, partial, cancelled, failed and completed; accepted retry is not successful execution |
| Health and reporting | Persisted observations, timestamps, run counters and storage metadata basis | Unavailable/stale observations are explicit; run-count budgets are not monetary cost; no fabricated green statuses |

Backend acceptance dependencies: final exact-commit CI, role/security acceptance, target-runtime verification, and any unresolved source-refresh configuration lifecycle gap in A0_CLOSURE_STATUS.md. No frontend files were changed in this workstream.

Owner controls: grant management is SUPER_ADMIN-only; no owner suspension/revocation control may be offered. Server/database enforcement remains authoritative. Do not infer ownership from an email address stored in the browser.
