# A0 v1.6 Requirement Crosswalk — Backend Acceptance Gate

**Status:** IN PROGRESS — A0 exit gate NOT PASSED  
**Base checkpoint:** 2d10e0fdad29f28b4450cdbeeddc9fa988d84f61 (PR #89 merged)  
**Governing specification:** JobPunter PRD + Technical Spec v1.6  
**Purpose:** Reconcile the existing Admin/backend gap audit, originally drafted against v1.5, to v1.6 without treating implementation presence or green CI as end-to-end acceptance.

## Decision rules

- **Verified (repository)** requires the relevant implementation and regression coverage to be inspected at an exact code checkpoint.
- **Partial** means some implementation exists but the requirement-level acceptance path is incomplete.
- **Not verified** means code/CI does not establish deployed, provider, security-boundary, or operational behavior.
- **Blocked / policy open** means the PRD leaves a product/security decision unresolved; implementation must not invent the policy.
- A0 remains open until every P0 requirement is verified or explicitly accepted with documented risk, and every remaining requirement has an owner and closure plan.
- No row below is marked complete solely because a route, model, migration, or test exists.

## v1.6 crosswalk

| v1.6 requirement area | Existing repository evidence identified in the current gap matrix | Current disposition | Required closure evidence |
|---|---|---|---|
| Identity, authentication boundary, and server-side authorization | Shared Admin guard and active ADMIN-grant checks; route-authorization tests; PostgreSQL denial/audit tests | **PARTIAL** | Reconcile identity-provider decision and session lifecycle against v1.6; verify candidate isolation and every privileged Admin operation. Fine-grained permission model, session expiry/revocation, and step-up rules remain policy-dependent where not approved. |
| Admin control plane and configuration governance | Protected source/configuration APIs; versioned configuration foundation; publish/rollback routes; audit history | **PARTIAL** | Demonstrate approved configuration families are persisted, validated, versioned, immutable after publication, consumed by the correct services, and audited. Invalid versions must never become active; rollback must preserve history. Resolve open publication-approval policy rather than infer it. |
| Canonical PostgreSQL persistence and migrations | SQLAlchemy/Alembic foundation, PostgreSQL integration tests, schema/migration CI, legacy stamp compatibility test | **PARTIAL** | Verify all v1.6 authoritative records use PostgreSQL; exact-head migration upgrade/downgrade and ORM/schema parity remain required. Production migration and restore rehearsal are separate operational gates and are **NOT VERIFIED**. |
| Candidate-private data isolation and minimization | Admin user detail returns minimized summaries/counts rather than document bodies/storage keys; shared authorization boundary | **PARTIAL** | Requirement-level checks for candidate ownership/isolation across profile, evidence, documents, evaluations, applications, support, notifications, and Admin access. Confirm logs and audit metadata do not leak sensitive content. |
| Canonical Job Pool, source observations, provenance, freshness, and resolution | Shared acquisition/canonicalization foundations; source-run and resolution models; provenance and mission regression coverage noted in the gap matrix | **PARTIAL** | Test representative successful and failed records from each supported route through canonical persistence; duplicate convergence, material JD versioning, freshness/closure, concurrency, partial failure, and worker-restart recovery must be evidenced. |
| Source registry, permission governance, and truthful health | Source/connector registry; permission-review lifecycle; structured health observations; PR #88 source-state normalization and migration 0070; PR #89 unsafe-target classification | **PARTIAL** | Complete the v1.6 requirement-to-adapter matrix; verify access method, terms, rate limits, credential handling, safe error categories, and truthful observed state. Live provider behavior is **NOT VERIFIED**. |
| SSRF and forbidden-target enforcement | Shared URL normalization and deny rules; manual URL redirect checks; JSON API, RSS/Atom, Greenhouse, Scrapy, and Playwright request-boundary checks; JobSpy registered path fails closed; PR #89 denial precedence regression | **PARTIAL** | Close the full alternate-entry/adapter/conflicting-policy matrix with deterministic tests. Prove deployment egress restrictions independently. Scheduler-time target exclusion and truthful blocked-scope visibility are not established by request-time checks. |
| Active discovery targets and acquisition orchestration | Persisted DiscoveryTarget lifecycle and immutable DiscoveryTargetEvent history; supported-target-kind checks | **PARTIAL** | Active targets must be consumed by scheduled acquisition. Prove paused, retired, invalid, unsupported, permission-revoked, and policy-blocked targets are excluded before network activity and are visible as blocked/excluded rather than false empty success. |
| Durable scheduling, workers, and recovery | Work items, outbox, scheduler/worker heartbeat instrumentation, operational inspection and selected retry/replay/cancel paths | **PARTIAL** | Demonstrate independent global and per-source schedules; idempotent scheduling; strategy/role/fallback resolution; budgets, quotas and cooldowns; durable step history; safe retries and crash/restart recovery. Deployed worker/scheduler behavior is **NOT VERIFIED**. |
| AI task governance, evidence grounding, and output validation | Task registry, AI execution records, configuration versions, decision-rule preview/validation foundations | **PARTIAL** | Verify each execution records the exact task/configuration/model/provider version; enforce schema and semantic validation, evidence traceability, candidate-claim controls, and governed fallback behavior. Do not infer quality or safety from registry presence. |
| Candidate-specific eligibility and explainable recommendations | Evaluation/rule foundations and persisted jobs/evidence-related models | **PARTIAL** | Feed actual persisted jobs through hard eligibility gates, approved-evidence matching, contradiction/missing-evidence handling, freshness/closure checks, and explainable outcomes. Demonstrate APPLY/REVIEW/WATCH/REJECT behavior without unsupported confidence. |
| Application/job separation and versioned application packages | Existing application and document/application foundations identified by the v1.6 architecture | **PARTIAL / NOT VERIFIED** | Prove package versioning, claim provenance, candidate approval boundaries, lifecycle audit, and no automatic submission. End-to-end acceptance is not demonstrated by the acquisition work. |
| Auditability and operational observability | Append-only Admin audit tables and triggers; request-correlated denial auditing; worker heartbeats and execution identifiers | **PARTIAL** | Complete material-write audit coverage, cross-domain outcomes, retry/recovery traces, retention policy, and sensitive-log checks. Persistence-failure audit limitations must remain explicit. |
| Notifications and external integrations | In-app notifications, email-delivery attempts, SMTP health observation/check, integration inventory | **PARTIAL / NOT VERIFIED** | Verify outbox idempotency, retry outcomes, truthful delivery state, least-privilege scopes, revocation, and live provider behavior. Never report a connection check as proof of message delivery. |
| Deployment portability, production/demo separation, and operational readiness | CI, migration checks, worker instrumentation, repository runbooks | **NOT VERIFIED** | Authorized staging smoke; deployed API/worker/scheduler proof; production network-egress evidence; backup and restore drill; proof demo/test records cannot be mistaken for production; deployment outside the authoring environment. |

## Open decisions that must not be silently resolved in code

- Final identity provider and session expiry/revocation/step-up rules where still undecided.
- Admin role/permission catalogue, privileged-grant approval, break-glass access, and last-admin/self-revocation policy.
- Which configuration families require separate publication approval.
- Initial production sources and permitted acquisition methods, including source-specific access/terms/rate limits.
- Scheduler/deployment mechanism and production worker topology.
- AI provider/model and credential configuration.
- Data export scope, approval, retention, and audit policy.
- Audit retention/review/export policy and operational alert thresholds/owners.

These decisions are blockers only for the behavior they govern; they do not justify inventing defaults or representing an unresolved capability as implemented.

## Batch sequencing and acceptance gates

1. **A0.2–A0.6:** complete the requirement-level crosswalk against v1.6, identify source/version conflicts, map each requirement to implementation and tests, and assign a closure action or explicit policy/runtime blocker. **A0 exit remains NOT PASSED until reviewed against repository evidence.**
2. **E2.5-04 / L1.5:** finish the deterministic adapter/alternate-entry policy matrix, then keep deployed egress and staging/provider evidence as separate release gates.
3. **E3–E5:** only after the preceding audit determines the actual code gaps, implement active-target consumption, blocked-scope visibility, independent schedules, strategy/budget enforcement, and durable execution history in bounded slices.
4. **Runtime gate:** separately record exact environment, commit, commands, results, and exceptions for staging/live providers, deployed worker/scheduler, network egress, and backup/restore.

## Evidence limits at branch creation

- PR #89 is merged at 2d10e0fdad29f28b4450cdbeeddc9fa988d84f61; CircleCI post-merge backend-verification is **PASS**.
- The GitHub workflow-run lookup returned no PR-triggered workflow run for the merge SHA. This is not evidence of a post-merge GitHub Actions pass; inspect the repository's actual workflow/check policy before making that claim.
- No local working-tree/test execution, live-provider call, deployed egress test, staging smoke, or backup/restore drill was performed by the connected GitHub operations.
- This crosswalk is a reconciliation artifact, not a declaration that any partial row has passed acceptance.


## A0 batch task status

| Task | Evidence available | Status | Remaining work / blocker |
|---|---|---|---|
| A0.1 — Confirm repository and working state | Repository identity and default branch were verified through GitHub metadata; PR #89 merge SHA and branch comparison are available. | **PARTIAL / NOT VERIFIED** | Connected GitHub operations do not expose a local working tree, tracked/untracked files, or local remotes. Do not claim these were inspected. Capture them in an authorized local checkout before declaring A0.1 complete. |
| A0.2 — Build requirement-to-code traceability | This v1.6 crosswalk and the existing Admin gap matrix cover major requirement domains and known gaps. | **PARTIAL** | Convert grouped domains into requirement-level rows for the Admin specification, discovery appendix, and applicable v1.6 clauses. Each row needs exact model/migration/route/service/worker/config/test evidence, state, next task, dependencies, acceptance criteria, and open decisions. |
| A0.3 — Audit administrative foundations | The current gap matrix records Admin grants, route guards, audit records, configuration versions, AI task governance, source/run operations, health checks, and work-item processing. | **PARTIAL** | Inspect complete execution paths and tests, not just the matrix summaries. Verify canonical-service use, duplicated/competing authorization paths, and bypasses. Local tests cannot be run from the connected GitHub operations used for this checkpoint. |
| A0.4 — Audit data and lifecycle boundaries | The matrix identifies candidate, job, source, support, AI, integration, notification, and work-item domains and names several existing lifecycle tests. | **PARTIAL** | Produce an explicit ownership/FK/uniqueness/transaction map for each material domain and verify candidate isolation, state transitions, concurrency behavior, and that Admin operations do not duplicate canonical records or bypass domain invariants. |
| A0.5 — Audit migrations and runtime configuration | The repository CI history includes Alembic/schema checks and legacy-stamp compatibility coverage; worker/scheduler heartbeat instrumentation is recorded. | **PARTIAL / NOT VERIFIED** | Inspect current migration ordering and ORM/schema parity; inspect test DB setup, secrets/configuration, worker startup, and scheduler wiring. Separate CI/local-fixture proof from deployed services, provider credentials, and production runtime. Backup/restore remains unverified. |
| A0.6 — Publish gap and dependency matrix | Existing gap matrix plus this v1.6 crosswalk preserve known partial, unverified, and policy-open areas. | **PARTIAL** | Link every requirement to a concrete implementation task, dependency, acceptance test, and unresolved decision. Keep runtime and policy blockers explicit; update dispositions only when appropriate evidence exists. |

**A0 exit decision: NOT PASSED.** A0.1 has an environment/tooling evidence limitation, A0.2 is only a first-pass grouped crosswalk, and A0.3–A0.6 still need requirement-level repository evidence and dependency mapping. The next feature batch must not be treated as authorized by this partial audit artifact.


## Code-level evidence inspected for A0.3 (2026-10-28)

This is a bounded source inspection, not a complete audit of all 208 KB of the Admin router or every domain lifecycle.

| Concern | Inspected implementation | Inspected tests / evidence | Finding and remaining gap |
|---|---|---|---|
| Authenticated principal | app/api/deps.py — get_principal | tests/unit/test_admin_route_authorization.py — authenticated-principal route coverage | Authentication dependency maps missing/invalid tokens to 401 and unconfigured identity service to 503. This does not establish production identity-provider readiness or full session lifecycle policy. |
| Admin guard and route inventory | app/api/v1/admin.py — _require_admin; tests/unit/test_admin_route_authorization.py — test_governance_read_routes_deny_authenticated_users_without_admin_grants, test_governance_mutation_routes_deny_users_without_admin_grants, test_every_admin_governance_and_support_route_calls_the_admin_guard, test_every_candidate_and_admin_route_requires_authenticated_principal | Unit source-inspection tests plus route-denial tests | A shared guard and broad route-inventory regression exist. They do not by themselves establish resource-level permissions, approved support/read-only roles, or every material-write audit/rollback invariant. |
| Admin audit and AI execution inspection | app/api/v1/admin.py — _audit, list_admin_audit_events, list_ai_executions | tests/integration/test_admin_governance_postgres.py | Audit and execution-inspection paths exist. The full requirement-level audit matrix, retention policy, and all cross-domain write outcomes remain open. |
| Versioned configuration | app/api/v1/admin.py — _validate_config_payload, list_config_versions, create_config_draft, publish_config_draft, rollback_config_version | Existing configuration lifecycle and PostgreSQL tests noted in the gap matrix | Draft/publish/rollback foundations exist. The v1.6 audit still needs to prove all approved configuration consumers use the canonical active version and that publication approval requirements are resolved. |
| Source governance and acquisition | app/api/v1/admin.py — _validate_connector_configuration, _enforce_source_permission_review, list_source_registry, create_source_registry_entry, activate_source_connector_configuration_version, update_source_permission_review, check_source_connector_health, run_source_acquisition | Source governance, acquisition and PostgreSQL mutation tests recorded in the gap matrix | The router exposes the governance path. Full adapter/alternate-entry policy acceptance, scheduler-time exclusions, and live-provider behavior remain separate gaps. |
| Discovery target lifecycle | app/api/v1/admin.py — list_discovery_targets, get_discovery_target, create_discovery_target, change_discovery_target_status | Discovery-target lifecycle and immutability tests recorded in the gap matrix | Registry/lifecycle exists. Active-target consumption by scheduled acquisition is not established; target state alone is not evidence that scheduled runs use the target. |
| Forbidden-target classification | app/application/jobs/target_policy.py — matching_forbidden_rule, classify_target_policy_error, assert_target_not_forbidden, assert_current_target_not_forbidden | PR #89 exact candidate CI evidence and registered-acquisition tests | Explicit deny-rule matches and unsafe URL classifications are distinct in code. This does not close E2.5-04/L1.5 or prove deployed network-egress controls. |
| Migration compatibility | app/infrastructure/db/migration_compat.py — ensure_alembic_version_column_capacity, normalize_legacy_alembic_stamps; tests/unit/test_migration_compat.py | Existing CI migration compatibility gate | Legacy stamp normalization has deterministic unit coverage. It does not prove production upgrade/rollback or backup/restore readiness. |

### A0.3 disposition

**PARTIAL.** The inspected files support the existing matrix's broad claims, but this was not a complete function-by-function audit of all Admin handlers, domain models, repositories, worker paths, transaction boundaries, and lifecycle tests. Continue the audit against each requirement row before assigning completion.

### Tool and test boundary

A local clone attempt was blocked because the execution environment could not resolve github.com. GitHub-backed file inspection and CI status queries remain available, but local commands, working-tree state, and test execution cannot be claimed. CircleCI backend-verification passed for commit 406501d0fb3e2d9752c37eae43e65e261f9f51c1; no GitHub Actions run was returned for that branch commit because the workflow lookup exposes PR-triggered runs only.
