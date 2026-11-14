# Source refresh configuration lifecycle

Source refresh cadence uses the existing immutable configuration lifecycle under `discovery.source_refresh.<source UUID>`. SUPER_ADMIN, ADMIN and OPERATIONS may use the source-scoped endpoints. OPERATIONS cannot use generic configuration endpoints or substitute an AI/task or another source's version ID through these wrappers.

| Endpoint | Behaviour |
|---|---|
| PUT /api/v1/admin/discovery/sources/{source_id}/schedule | Creates a validated DRAFT; does not create/change active execution. Body: interval_minutes (15–10080), reason (8–1000 trimmed characters), optional window_start/window_end/window_timezone together |
| GET .../schedule | Current operational projection, config_version_id and publication_required, next/last run timestamps; no fabricated active state for a draft |
| GET .../schedule/versions | Immutable version history with bounded limit/offset pagination |
| POST .../schedule/versions/{config_id}/publish | Revalidates and publishes a draft in the same transaction as its schedule projection, history and audit. Rejects a stale publication baseline with 409 |
| POST .../schedule/versions/{config_id}/rollback | Validates a prior PUBLISHED/RETIRED payload and activates a new immutable version; does not rewrite history |
| PATCH .../schedule/status | Operational pause/resume remains immediate and audited. An unversioned legacy schedule cannot resume until publication |

A publication changes cadence but preserves an existing operational pause. A newly configured source becomes active only after publication. A draft is validated at creation and again before publication; schema and window constraints cannot be bypassed through the generic configuration API. Invalid payloads are 422; missing/wrong-source versions are 404; already-published or stale drafts are 409. Source publication locks serialize concurrent changes, including two requests publishing the same draft.

Each queued occurrence pins its governance version. Later cadence publication does not change the explanation of queued or completed work. Execution records preserve version ID/number and cadence; current source permission, URL exclusions, tool pause and run budgets are still rechecked before acquisition. A retired version remains usable only as an already-pinned historical context, not as the current scheduling authority. Missing or mismatched configuration fails before network access.

## Upgrade and rollback

Migration 0084 requires SOURCE_REFRESH processing work to be drained/recovered first. Stop scheduling/refresh workers during the controlled schema cutover. Existing active legacy schedules are paused with an append-only MIGRATED event (system actor, not an invented administrator), preserving cadence/window settings. Pending unversioned occurrences are cancelled with GOVERNANCE_CUTOVER. Publish the preserved settings and explicitly resume schedules after upgrade; the migration does not fabricate prior approvals or historical version IDs.

Downgrade refuses to remove governance reference columns once source-refresh configuration history exists. Do not delete immutable history to force downgrade. No deployment or live-source acceptance is implied by repository tests.

Regression evidence: source refresh configuration, queued-version, concurrent publication, migration drain/cutover, immutable history, rollback, role-denial, audit and leak-sweep tests. This API replaces the previous immediate PUT cadence behaviour; frontend callers must offer an explicit Publish action and report published-but-paused state accurately.
