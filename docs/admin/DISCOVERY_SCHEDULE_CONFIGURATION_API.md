# General discovery schedule configuration (M04 / E4.7)

All routes below are under `/api/v1/admin/discovery`. SUPER_ADMIN, ADMIN and OPERATIONS may use them; SUPPORT_AGENT may not. They delegate to the shared governed-configuration lifecycle under `discovery.schedule.<schedule_uuid>` and cannot operate on another schedule or configuration domain.

| Action | Route | Contract |
|---|---|---|
| Create schedule | POST `/schedules` | Full schedule payload plus reason; omit activate or use false. Returns PAUSED projection, `publication_required: true`, `draft_config_version_id`. activate=true is rejected (422). No work is enqueued. |
| Create replacement draft | POST `/schedules/{schedule_id}/versions` | Full replacement configuration plus reason; validates published strategy and source/target references. Does not modify effective settings. |
| List history | GET `/schedules/{schedule_id}/versions` | Newest first; limit 1–200, offset >=0, total count. |
| Publish draft | POST `/schedules/{schedule_id}/versions/{config_id}/publish` | Revalidates references; applies configuration, retires previous version and records audit atomically. Stale baseline or duplicate publish returns 409. |
| Roll back | POST `/schedules/{schedule_id}/versions/{config_id}/rollback` | Revalidates historical payload and creates a new published version; never rewrites history. |
| Pause/resume/retire | PATCH `/schedules/{schedule_id}/status` | Existing expected_revision and reason required. Resume requires published configuration and an eligible future occurrence. Publication/rollback preserves PAUSED state. |
| Manual run | POST `/schedules/{schedule_id}/runs` | Existing idempotency key and reason required; published manual permission, start/end and daily window are enforced. |

Schedule payload includes name/type, IANA timezone, frequency, strategy version, source/target/market scopes, priority, overlap/concurrency settings, execution limits, retry policy, manual permission and optional timezone-aware start/end. Optional window_definition is exactly `{ "start": "22:00", "end": "02:00" }` (local HH:MM; start inclusive, end exclusive; may cross midnight). An empty object removes the window. A delayed scheduler checks the actual execution time against the window/date bounds.

Every new run stores schedule_config_version_id, resolved settings, schedule revision and strategy snapshot in its database-protected immutable configuration_snapshot. Later publication and rollback do not rewrite admitted runs. Missing/unavailable effective configuration or strategy pauses the affected schedule with a system audit event, allowing other eligible schedules to continue.

Migration 0085 adds the configuration reference, pauses legacy ACTIVE schedules with a system cutover audit, and preserves settings and historical immutable runs. It invents no approvals or historical version IDs. Review settings, create/publish a genuine version, then explicitly resume. Downgrade refuses to remove existing general-schedule configuration history. This contract describes repository behavior; deployed acceptance remains unverified.
