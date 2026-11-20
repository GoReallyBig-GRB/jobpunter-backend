# Candidate UI backend contract handoff

This is the backend companion to the versioned candidate design and reconciliation package. It does not implement the outstanding contracts or certify current deployments.

## Authoritative package

- [Frontend handoff and runnable demo](https://github.com/GoReallyBig-GRB/jobpunter-frontend/tree/docs/candidate-ui-handoff-2026-11-19/docs/candidate-ui)
- [Frontend draft PR #8](https://github.com/GoReallyBig-GRB/jobpunter-frontend/pull/8)
- [Approved requirements](https://github.com/GoReallyBig-GRB/jobpunter-frontend/tree/docs/candidate-ui-handoff-2026-11-19/docs/candidate-ui/requirements.md)
- [Authoritative gap register](https://github.com/GoReallyBig-GRB/jobpunter-frontend/tree/docs/candidate-ui-handoff-2026-11-19/docs/candidate-ui/gap-register.json)
- [Workflow/API map](https://github.com/GoReallyBig-GRB/jobpunter-frontend/tree/docs/candidate-ui-handoff-2026-11-19/docs/candidate-ui/workflow-map.md)
- [Review evidence](https://github.com/GoReallyBig-GRB/jobpunter-frontend/tree/docs/candidate-ui-handoff-2026-11-19/docs/candidate-ui/review.md)
- [Reconciliation procedure](https://github.com/GoReallyBig-GRB/jobpunter-frontend/tree/docs/candidate-ui-handoff-2026-11-19/docs/candidate-ui/reconciliation.md)

Read the frontend package first. All nine gaps started Open. Do not duplicate their statuses here. Backend PRs must reference the same CUI IDs and supply evidence for the frontend register. Branch links identify the handoff before merge; maintainers should update navigation links to the merged destination when integrating it.

Original review baseline: `c984af5ee30348dcd3b64309eb9e79fa0a84279d`. This documentation branch starts from `6074b573d0487784229dba64228a78abfaedda01`. Recheck intervening code before implementing or closing a gap; the review is not proof that a previously observed gap still exists. Prior dirty worker changes in another checkout were not included.

## Governing decision

All candidate searches and scheduled recommendations must read processed central opportunities. The owner reports this implemented and deployed: backend acquisition runs approximately 20 times daily, and candidate daily updates draw from the central pool. Candidate requests must not invoke acquisition connectors or configure their credentials. URL/JD/email submissions remain explicit intake paths requiring processing and privacy controls. Do not expose this architecture, refresh cadence or connector administration in candidate UI copy.

Latest explicit product decisions outrank older PRD discovery proposals. Keep shared job facts separate from private candidate evidence, assessments, dispositions and applications. An approved package is not evidence of employer submission.

## Gap-to-contract mapping

| ID | Reviewed existing surface | Required reconciliation / acceptance |
|---|---|---|
| CUI-001 | discovery settings/missions; discovery_mission_worker and source acquisition | Separate candidate matching from central acquisition. Candidate search/schedule test must assert no connector dispatch. Locate and verify the completed deployed implementation (approximately 20 acquisition runs daily), deduplication and freshness handling; do not presume rebuilding is necessary. |
| CUI-002 | jobs/pool and candidate/opportunities projections | Define processed search versus personal viable pool admission. Add complete filter/count semantics and stable paging; group pathway assessments under canonical job identity. No CV means personal fit pending. |
| CUI-003 | ApplicationCreate requires punt_id; qualification gate | Candidate-directed application path for viable non-Punts without inventing a Punt. Preserve original assessment, explicit evidence risk and deterministic blocker policy. |
| CUI-004 | ApplicationStatus, transition rules and application audit | Agree invitation/screening/terminal mapping and correction rules. Keep per-application identities, expected revision, actor, reason and actual submitted event. Test independent applications. |
| CUI-005 | saved_jobs, punts, evaluations and notification producers | Persistent recommendation/disposition history, consistent daily/current counts and watched-Punt activity. One aggregate fresh-login reminder; suppress after 14 days inactivity without deleting saved records. |
| CUI-006 | pasted-email parser, manual intake and outbound notifications | Personal mailbox consent/revocation and reviewed event attribution are separate capabilities. Add durable interview sessions, attempts, interpretation, repeat/end and agreed two-reminder abandonment timing. |
| CUI-007 | CV Studio content, document renderer and package documents | Shared sanitized rich-content/A4 model; preserve preview formatting in DOCX/PDF and exact approved version. Current review found Letter-sized PDF and fixed formatting. |
| CUI-008 | profile/evidence/import/pathways/CV/package operations | Resumable orchestration. Approval re-evaluates retained search results, keeps selected opportunity and does not silently approve partial saves. Handle idempotency, stale revisions and dependency failures. |
| CUI-009 | saved-job save/list versus canonical visibility helper | Same access predicate on write and read, including revoked visibility. Test foreign manual jobs and inaccessible canonical records. |

Paths and line-level evidence are in the review. No proposed route name here should be interpreted as an implemented API. Preserve existing profile, evidence, CV versioning, package review/export, notifications and support services wherever they already satisfy the requirements.

## Evidence required from backend implementation

For each gap record the owner, current baseline, commit/PR, schema/route changes, compatibility or migration implications, expected errors, authorization cases and test outcomes. Give frontend agents exact request/response contracts and version/concurrency semantics. Do not give them only an endpoint name.

Mark Implemented only with working code references. Mark Verified only with evidence against recorded revisions, distinguishing unit, database integration, cross-repository journey and deployed checks. Close only after all shared acceptance criteria and reconciliation are recorded in the frontend register. If a dependency or product decision is unresolved, keep the gap Open with a blocker rather than simulate completion.

Implement one repository at a time and preserve unrelated work. This handoff authorizes documentation packaging, not automatic production migration, merge or deployment.

## Update — 20 November 2026

Backend deployed and central acquisition complete are owner-reported facts; verify the deployed revision before reconnecting or replacing contracts. CUI-001 now tracks verification/integration. The frontend handoff adds backend-connection-policy.md, connection-actions.json and cloudflare-pages-connection.md. Each unconnected action must show an explicit message and gap reference, with no fake saved state. Connected runtime errors must remain distinct from absent integration.
