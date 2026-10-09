# JobPunter Discovery Wave Policy

## Product rule

JobPunter deploys eligible discovery mechanisms in configurable waves. A later
wave is triggered when the current wave has not satisfied the mission target,
unless processing is inconclusive or a recoverable failure should be addressed
first. A mission ends as target-met or exhausted, with an explicit shortfall.

## What counts toward the target

Only distinct, canonicalized, candidate-relevant opportunities that meet the
configured verification standard count. Raw scraper records, duplicates,
confirmed-closed jobs, hard-constraint failures, and unresolved review items do
not count as fully usable opportunities. The discovery layer must not infer
that a vacancy is verified merely because a connector returned it.

## Initial recommended defaults

These defaults are starting product recommendations and must remain configurable:

- Target: 20 usable opportunities per discovery mission.
- Maximum vacancy age: 30 days.
- Verification: required before a vacancy counts as usable.
- Evaluation depth: preliminary candidate-specific evaluation.
- Wave order:
  1. Official APIs and feeds where available.
  2. JobSpy across enabled supported sources.
  3. Targeted Scrapy connectors for coverage gaps.
  4. Playwright for JavaScript-heavy sources where permitted and justified.
- Email intake, user-submitted URLs, manual imports, and user-provided lists are
  parallel intake paths, not scraper waves that must wait for earlier waves.
- Wave enablement, priority, attempt limits, result target, vacancy age, and
  evaluation depth are configuration, not values to scatter through connector code.

## Mission decisions

- TARGET_MET: cumulative usable opportunities meet or exceed the target.
- ADVANCE: current wave was insufficient, and another eligible wave remains.
- RECOVER: a wave is blocked by a retryable failure; recover or switch strategy
  rather than repeatedly executing the same failed mechanism.
- WAIT_FOR_PROCESSING: canonicalization, verification, or other processing is
  incomplete, so current counts cannot support a trustworthy decision.
- EXHAUSTED: no eligible waves remain and the target is still unmet. Report
  the actual count, target, waves attempted, blocked sources, and unresolved work.

A connector execution can succeed while the overall mission remains insufficient.
Connector health and discovery yield must be reported separately.

## Implementation boundary

app/application/jobs/discovery_waves.py currently provides deterministic policy
objects and a pure decision function. It is not yet the durable wave scheduler,
connector registry, settings API, or end-to-end discovery pipeline. Those require
explicit wiring to source/connector records, acquisition runs, canonicalization,
verification/evaluation, mission persistence, and user-facing status reporting.
