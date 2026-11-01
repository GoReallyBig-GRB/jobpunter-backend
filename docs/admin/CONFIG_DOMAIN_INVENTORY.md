# Configuration domain inventory (A0R-08, C2.1)

Governed by `GovernanceConfigVersion` (immutable published versions, validate-before-publish, audited publish/rollback). Enforced by `tests/unit/test_config_domain_inventory.py`.

| Domain | Key / store | Consumer at runtime | Effective-version trace |
|---|---|---|---|
| AI task assignment | `ai.task.<task_id>` (governance config) | `app/application/ai/service.py` | `config_version_id` on the AI execution record (D5) |
| Candidate evaluation decision rules | `decision.rules.candidate_evaluation` | `app/application/opportunities/evaluation_service.py`, `service.py` | evaluation records name the rule version |
| Source connector configuration | `SourceConnectorConfigurationVersion` (own table) | registered acquisition guard | acquisition run snapshot |
| SMTP provider | `SMTPProviderConfiguration` (own table, encrypted secrets) | `app/application/notifications/service.py` | delivery rows record `smtp_config_version` |
| Forbidden-target policy | `ForbiddenTargetRule` (live registry, not versioned) | all adapters before each request | per-request revalidation |

Adding a governed key family requires updating the schema pattern, this table and `INVENTORY` in the guard test; the test fails otherwise.
