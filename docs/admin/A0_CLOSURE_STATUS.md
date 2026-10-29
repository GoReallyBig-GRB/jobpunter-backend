# Admin A0 closure status and evidence

**Status date:** 2026-10-29  
**Branch:** `admin-work`  
**Evidence head before this change:** `5244a15fa6e22e31a0a1cc7cc89041ac8257ff23`

This ledger separates code/test evidence from runtime evidence. A CI pass is not evidence of a staging deployment, provider connectivity, egress enforcement, or a successful backup restore.

## Closure matrix

| Item | Current disposition | Code/test evidence | Remaining acceptance |
|---|---|---|---|
| A0-CLOSE-01 Discovery truthfulness | Implemented in code; regression coverage exists | `app/workers/discovery_mission_worker.py` resolves `DiscoveryTarget`; records blocked/unavailable scopes and distinct terminal source outcomes. `tests/integration/test_discovery_missions_postgres.py` covers paused targets without acquisition and restricted/unavailable runs not being reported as successful-empty. | Re-run the complete PostgreSQL suite on the final head; verify network egress controls in the actual runtime. |
| A0-CLOSE-02 Support lifecycle integrity | Implemented in code; integration coverage exists | `app/application/support/service.py` performs locked ticket reload; `tests/integration/test_support_ticket_notifications_postgres.py` includes stale-ticket reload and notification retry/idempotency cases. | Full final-head PostgreSQL run and a true multi-transaction concurrency/race result are still required if the governing task list requires them. |
| A0-CLOSE-03 Identity portability | Adapter seam and fake-provider contract covered; not full provider portability acceptance | `app/ports/identity/__init__.py`, `app/api/deps.py`, and `tests/unit/test_identity_provider_contract.py`. Unsupported provider selection fails closed. | No second production identity adapter or real provider-subject migration/continuity test was implemented. The exact v1.6 acceptance clause could not be checked because the source document was not accessible. |
| A0-CLOSE-04 Audit completeness | Partial | Admin writes/read paths use AdminAuditEvent; route guards and selected audit tests exist in `tests/integration/test_admin_governance_postgres.py`. | Full material-write × outcome matrix remains: success, denial, validation failure, rollback, audit persistence failure, actor/target/action/outcome and sensitive-data exclusion for each required operation. |
| A0-CLOSE-05 Configuration authority | Version lifecycle exists; runtime traceability partial | `GovernanceConfigVersion`; Admin draft/publish/rollback routes in `app/api/v1/admin.py`; PostgreSQL integration tests cover publishing, retiring, and rollback activation. | Not all runtime consumers prove which effective version they used. Add per-consumer effective-version/fingerprint evidence and concurrent publication/rollback tests where missing. |
| A0-CLOSE-06 Requirements/invariant ledger | This file is a provisional evidence ledger, not a complete source-verified requirements ledger | Maps the nine known A0 closure items to current paths and evidence. | The exact Admin Backend Implementation Task List, PRD + Technical Spec v1.6, Autonomous Development Protocol v3, Battering Ram Doctrine and canonical gap register were not all accessible from the connected Library/repository sources. This ledger cannot claim clause-by-clause completeness until those exact source artifacts are provided or made accessible. |
| A0-CLOSE-07 Release/runtime gate | Blocked by environment access | `DEPLOYMENT_SECURITY_CHECKLIST.md` and the repository CI configuration define some checks. Latest available CI evidence before this change was green for `5244a15fa6e22e31a0a1cc7cc89041ac8257ff23`. | No authorized staging/production runtime, provider credentials, scheduler/worker deployment evidence, egress observation, or backup/restore session was available. Do not mark release-ready from repository CI alone. |
| A0-CLOSE-08 Admin User Records | Code expanded; regression assertions added in this change | `GET /api/v1/admin/users/{user_id}` now returns bounded recent document metadata and application-package summaries, and activity includes events where the user/profile is the target. The integration test asserts non-empty package/item counts and omits filenames, object keys, and artifact body content. | Saved-job/bookmark model was not found in the canonical ORM model tree reviewed. Confirm the governing model/contract before creating a new schema or reporting saved jobs as supported. |
| A0-CLOSE-09 Notification/SMTP governance | Not closed | SMTP health/delivery inspection and retry exist; credentials remain environment-backed; generic versioned configuration rejects secrets. | Governed provider/event/retry policy must be versioned and consumed at runtime, with a secure secret reference/store, test-before-activation against the draft settings, last-known-good behavior, activation/rollback audit and effective-version diagnostics. A code-only change cannot verify real provider acceptance. |

## Environment and access limitations

1. The exact file `docs/admin/A0_V1_6_REQUIREMENT_CROSSWALK.md` and the named admin task list were not present on the authorized `admin-work` branch. Searches of the connected Library returned the v1.4 PRD/Technical Spec, not the requested v1.6 source. No clauses are invented to fill this gap.
2. A local clone/test environment was unavailable because GitHub DNS resolution failed during the earlier clone attempt. CI is the available automated test evidence; the status for this commit must be checked after it is created.
3. Staging/production access and provider/runtime credentials were not available. A0-CLOSE-07 cannot be closed by editing repository files.
4. The branch was already 13 commits ahead of `main` before this closure pass. The project instruction limits a branch to 15 commits. This pass uses one consolidated commit; further code work likely needs a separately authorized branch. No other branch is created here.

## Required next actions

- Retrieve the exact v1.6 PRD/Technical Spec, Admin Backend Implementation Task List, Autonomous Development Protocol v3, Battering Ram Doctrine and canonical gap register.
- Continue A0-CLOSE-04, A0-CLOSE-05 and A0-CLOSE-09 in an authorized follow-on branch with PostgreSQL tests and runtime consumer evidence.
- Run final-head CI and, in the authorized environment, complete staging smoke, worker/scheduler proof, provider/source/storage/email checks, egress verification and backup/restore.
- Keep release status **NOT PASSED** until the above evidence is attached and reviewed.
