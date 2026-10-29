# Admin A0 closure status and evidence

**Status date:** 2026-10-29  
**Branch:** `admin-work`  
**Evidence head before this change:** `5244a15fa6e22e31a0a1cc7cc89041ac8257ff23`

This ledger separates code/test evidence from runtime evidence. A CI pass is not evidence of a staging deployment, provider connectivity, egress enforcement, or a successful backup restore.

## Closure matrix

| Item | Current disposition | Evidence and remaining acceptance |
|---|---|---|
| A0-CLOSE-01 Discovery truthfulness | Implemented; CI evidence must be checked on the final candidate | Discovery target resolution and distinct blocked/unavailable outcomes are covered in `app/workers/discovery_mission_worker.py` and `tests/integration/test_discovery_missions_postgres.py`. Runtime egress enforcement remains part of A0-CLOSE-07. |
| A0-CLOSE-02 Support lifecycle integrity | Implemented; final acceptance evidence remains | Locked ticket reload and notification retry/idempotency coverage exist in `app/application/support/service.py` and `tests/integration/test_support_ticket_notifications_postgres.py`. Final-head PostgreSQL CI and any required real concurrency test remain acceptance evidence. |
| A0-CLOSE-03 Identity portability | Partial; implementation work required | `IdentityProvider` protocol and Firebase startup adapter exist. PostgreSQL remains the application database. Extend provider resolution through an explicit provider registry/factory and keep internal user IDs/provider subject mapping independent of Firebase-specific identity. Firebase remains the configured startup provider. Acceptance: provider contract tests, unknown-provider fail-closed test, and identity mapping/lifecycle tests. A second production provider is not required for this startup-stage implementation unless the PRD decision is resolved to require one. |
| A0-CLOSE-04 Audit completeness | Partial; implementation and tests required | Admin audit records and selected route tests exist. Inventory every material Admin mutation and test success, authorization denial, validation failure, transaction rollback and audit-persistence failure. Assert actor, action, target, outcome, request correlation, safe reason and sensitive-data exclusion. |
| A0-CLOSE-05 Configuration authority | Partial; implementation and tests required | Versioned governance config draft/publish/rollback routes exist. Every runtime consumer must load the effective published version and expose version/fingerprint provenance. Add stale-write/concurrent-publish/rollback acceptance tests. SMTP provider settings must be editable in Admin without code changes; provider credentials must be stored only through a secure secret mechanism and never in generic config payloads/logs. |
| A0-CLOSE-06 Requirements/invariant ledger | Partial; source documents now accessible | This ledger must be reconciled against the three sanctioned Project sources only: Autonomous Development Protocol v3, PRD + Technical Spec v1.6, and Admin Backend Implementation Task List (+others), including its canonical gap register. Do not use older Library copies or create a parallel canonical register. Map each requirement/invariant to implementation, tests, evidence and disposition. |
| A0-CLOSE-07 Runtime/release gate | Deferred by user; release remains NOT PASSED | Documented as deferred, not closed. Requires authorized target environment, deployed API/worker/scheduler evidence, live provider connectivity, egress enforcement observation, and backup/restore exercise with integrity checks. CI alone is insufficient. |
| A0-CLOSE-08 Admin User Records | Partial; implementation work required | User detail already includes bounded document metadata, package summaries and targeted activity. Complete read-only summaries for saved jobs, evaluations, progress and applications where canonical records exist. Inspect all ORM/domain models and migrations before introducing any saved-job schema. Preserve ownership boundaries and document privacy. |
| A0-CLOSE-09 Notification/SMTP governance | Partial; repository implementation required; live acceptance deferred | Delivery/outbox retry exists, but the runtime still reads SMTP settings from environment configuration. Admin must be able to add/adopt/change providers such as ZeptoMail, Brevo or Foundermail by entering SMTP host, port, TLS mode, username, password/secret reference, sender identity and event/retry policy in Admin settings without code changes. Implement versioned provider/event/retry policy, secure credential storage, draft test-before-activation, audited publish/rollback, last-known-good fallback and effective-version diagnostics. Live SMTP acceptance and deployed secret-store integration are environment-dependent and must be documented until tested. |

# Admin A0 closure status and evidence

**Status date:** 2026-10-29  
**Branch:** `admin-work`  
**Evidence head before this change:** `5244a15fa6e22e31a0a1cc7cc89041ac8257ff23`

This ledger separates code/test evidence from runtime evidence. A CI pass is not evidence of a staging deployment, provider connectivity, egress enforcement, or a successful backup restore.

## Closure matrix

| Item | Current disposition | Evidence and remaining acceptance |
|---|---|---|
| A0-CLOSE-01 Discovery truthfulness | Implemented; CI evidence must be checked on the final candidate | Discovery target resolution and distinct blocked/unavailable outcomes are covered in `app/workers/discovery_mission_worker.py` and `tests/integration/test_discovery_missions_postgres.py`. Runtime egress enforcement remains part of A0-CLOSE-07. |
| A0-CLOSE-02 Support lifecycle integrity | Implemented; final acceptance evidence remains | Locked ticket reload and notification retry/idempotency coverage exist in `app/application/support/service.py` and `tests/integration/test_support_ticket_notifications_postgres.py`. Final-head PostgreSQL CI and any required real concurrency test remain acceptance evidence. |
| A0-CLOSE-03 Identity portability | Partial; implementation work required | `IdentityProvider` protocol and Firebase startup adapter exist. PostgreSQL remains the application database. Extend provider resolution through an explicit provider registry/factory and keep internal user IDs/provider subject mapping independent of Firebase-specific identity. Firebase remains the configured startup provider. Acceptance: provider contract tests, unknown-provider fail-closed test, and identity mapping/lifecycle tests. A second production provider is not required for this startup-stage implementation unless the PRD decision is resolved to require one. |
| A0-CLOSE-04 Audit completeness | Partial; implementation and tests required | Admin audit records and selected route tests exist. Inventory every material Admin mutation and test success, authorization denial, validation failure, transaction rollback and audit-persistence failure. Assert actor, action, target, outcome, request correlation, safe reason and sensitive-data exclusion. |
| A0-CLOSE-05 Configuration authority | Partial; implementation and tests required | Versioned governance config draft/publish/rollback routes exist. Every runtime consumer must load the effective published version and expose version/fingerprint provenance. Add stale-write/concurrent-publish/rollback acceptance tests. SMTP provider settings must be editable in Admin without code changes; provider credentials must be stored only through a secure secret mechanism and never in generic config payloads/logs. |
| A0-CLOSE-06 Requirements/invariant ledger | Partial; source documents now accessible | This ledger must be reconciled against the three sanctioned Project sources only: Autonomous Development Protocol v3, PRD + Technical Spec v1.6, and Admin Backend Implementation Task List (+others), including its canonical gap register. Do not use older Library copies or create a parallel canonical register. Map each requirement/invariant to implementation, tests, evidence and disposition. |
| A0-CLOSE-07 Runtime/release gate | Deferred by user; release remains NOT PASSED | Documented as deferred, not closed. Requires authorized target environment, deployed API/worker/scheduler evidence, live provider connectivity, egress enforcement observation, and backup/restore exercise with integrity checks. CI alone is insufficient. |
| A0-CLOSE-08 Admin User Records | Partial; implementation work required | User detail already includes bounded document metadata, package summaries and targeted activity. Complete read-only summaries for saved jobs, evaluations, progress and applications where canonical records exist. Inspect all ORM/domain models and migrations before introducing any saved-job schema. Preserve ownership boundaries and document privacy. |
| A0-CLOSE-09 Notification/SMTP governance | Partial; repository implementation required; live acceptance deferred | Delivery/outbox retry exists, but the runtime still reads SMTP settings from environment configuration. Admin must be able to add/adopt/change providers such as ZeptoMail, Brevo or Foundermail by entering SMTP host, port, TLS mode, username, password/secret reference, sender identity and event/retry policy in Admin settings without code changes. Implement versioned provider/event/retry policy, secure credential storage, draft test-before-activation, audited publish/rollback, last-known-good fallback and effective-version diagnostics. Live SMTP acceptance and deployed secret-store integration are environment-dependent and must be documented until tested. |

## Environment and access limitations

1. The three sanctioned Project documents are accessible in this session. Older Library copies and unsanctioned repository documents are not governing sources.
2. A local clone/test environment was unavailable during the earlier attempt because GitHub DNS resolution failed. Remote CI is evidence only for the exact SHA it reports; no local tests are claimed unless actually run.
3. Staging/production access and provider/runtime credentials have not been established. A0-CLOSE-07 is explicitly deferred by the user and remains NOT PASSED.
4. Protocol v3 sets a 130-commit hard limit per working branch/PR before merge, with an explicit checkpoint at commit 120 and no commit 121 before merge. It separately requires a user checkpoint after 3 consecutive batches or 15 tasks. These are different controls; 15 is not the commit cap.
5. Current observed `admin-work` comparison: 15 commits ahead of `main`, 0 behind. This is below the Protocol's 130-commit cap. No branch creation or merge is needed solely because of that count.
6. This ledger is being reconciled against the current sanctioned sources and the user's explicit decisions in this continuation. CI, runtime and implementation claims must remain tied to actual evidence.

## Required next actions

- Implement A0-CLOSE-03 through A0-CLOSE-06 and A0-CLOSE-08/A0-CLOSE-09 on the authorized `admin-work` branch, starting with outstanding B/C work in dependency order.
- Add targeted regression tests for identity provider resolution, material-write audit outcomes, effective configuration version use, saved-job/job-search records and SMTP draft/publish/rollback behavior.
- Keep A0-CLOSE-07 deferred and record the exact environment evidence required; do not mark release passed.
- Check remote CI against the exact resulting head after each coherent implementation batch. Do not claim local tests were run unless they were.
- Keep release status **NOT PASSED** until all required implementation and runtime evidence is present and reviewed.
