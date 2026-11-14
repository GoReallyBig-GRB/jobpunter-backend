# Configuration domain inventory (A0R-08, C2.1)

The generic key families use `GovernanceConfigVersion` (immutable published versions, validate-before-publish, audited publish/rollback), guarded by `tests/unit/test_config_domain_inventory.py`. Dedicated stores below have their own lifecycle and regression coverage; not every live control uses the generic publication model.

| Domain | Key / store | Consumer at runtime | Effective-version trace |
|---|---|---|---|
| Source refresh cadence | `discovery.source_refresh.<source_uuid>` (governance config) | `source_refresh_governance.py`, scheduler and refresh worker | Schedule points to published version; each work item pins its immutable version; source-step snapshots retain exact version and cadence |
| AI task assignment | `ai.task.<task_id>` (governance config) | `app/application/ai/service.py` | `config_version_id` on the AI execution record (D5) |
| Candidate evaluation decision rules | `decision.rules.candidate_evaluation` | `app/application/opportunities/evaluation_service.py`, `service.py` | evaluation records name the rule version |
| Source connector configuration | `SourceConnectorConfigurationVersion` (own table) | registered acquisition guard | acquisition run snapshot |
| SMTP provider | `SMTPProviderConfiguration` (own table, encrypted secrets) | `app/application/notifications/service.py` | delivery rows record `smtp_config_version` |
| Forbidden-target policy | `ForbiddenTargetRule` (live registry, not versioned) | all adapters before each request | per-request revalidation |

Adding a governed key family requires updating the schema pattern, this table and `INVENTORY` in the guard test; the test fails otherwise.

## Discovery configuration stores

| Domain | Store | Consumer | Historical context and lifecycle |
|---|---|---|---|
| Orchestration strategy | `DiscoveryStrategyVersion` | `app/application/jobs/discovery_run_execution.py` | Immutable strategy snapshot/version on `DiscoveryRun` and role executions; draft/validate/publish/rollback API |
| Global discovery schedule | `DiscoverySchedule` | `app/application/jobs/discovery_schedules.py`, scheduler worker | Run stores strategy reference and resolved configuration; status mutations use expected revision |
| Per-source refresh schedule | `SourceRefreshSchedule`, `SourceRefreshScheduleEvent` | `app/application/jobs/source_refresh.py`, source-refresh worker | PUT creates a validated governance draft; source-bound publish/rollback delegate to the shared lifecycle. Operational pause/resume remains separate. Queue occurrences retain version IDs; steps preserve that version even after later publication. Legacy schedules pause at migration cutover until a genuine publication and explicit resume. |
| Tool enablement and tool/global run budgets | `DiscoveryToolControl` | registered acquisition and source-scope execution | Current controls enforced before new work; per-step snapshots retain effective context |
| Source run limit | `DiscoverySourceLimit` | registered acquisition and source-scope execution | Atomic reservation and rolling run-count enforcement; per-step snapshot records applied limits |

Acceptance tests: `test_admin_discovery_strategies_postgres.py`, `test_admin_discovery_schedules_postgres.py`, `test_admin_source_refresh_schedules_postgres.py`, `test_discovery_source_limits_postgres.py`, `test_discovery_run_budgets_postgres.py`. Provider monetary usage remains unmeasured. This inventory records implementation paths; deployed acceptance remains separate.
