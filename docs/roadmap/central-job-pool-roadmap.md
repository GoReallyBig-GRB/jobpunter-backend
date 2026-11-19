# Central Job Pool Roadmap (owner-approved direction, 2026-11-19)

Principle: the system fetches, validates and refreshes; users read the pool. AI interprets, evidence supports, rules decide.

## Approved decisions
- Source class approved by owner: "public company job boards on Greenhouse, Lever and Ashby, read through their official public APIs". The system may register discovered companies under this class (class-level permission review, audited, with rate limits).
- Tavily: system-initiated only, about 20 searches/day (about 600 credits/month, about 400 reserve), 90% hard stop, basic depth only. Two layers: company discovery (include_domains restricted to the three ATS hosts) and role-keyword searches.
- Guest first search (name + email) reads the pool. No personal data to search/AI providers. Free Gemini tier only with non-identifying career details.
- User live-search requests are queued to the system, served from the reserve, never direct.
- Requirement auto-accept: verbatim-grounded + high confidence, with random spot-check sampling.

## Live baseline (2026-11-19)
87 jobs, 1 source (Lever - Spotify), 0 refresh schedules, 0 discovery schedules, all freshness UNKNOWN, verifier 0 conclusive of 609, no disappearance-based closure.
Connector facts: Greenhouse has a dedicated connector; Lever and Ashby run through the generic JSON_API connector (no dedicated, tested connector yet).

## Phases (each batch max 5 tasks; evidence required per task)
### P1 Freshness and scheduling (Batch 1)
1. Listing-based freshness: still listed -> FRESH + last_confirmed_at; absent after N consecutive full refreshes -> CLOSED (guard: partial/failed fetch never closes anything).
2. Source-refresh schedule for Lever-Spotify; verify cycle live.
3. Replace page-check dependence: verifier result UNKNOWN must not override listing-based freshness.
4. Expose last_confirmed_at and freshness in pool API.
5. Live evidence: freshness counts, closure events, alert on 72h stale source.
### P2 Trusted ATS connectors and class approval (Batch 2)
1. Dedicated tested Lever and Ashby connectors (or hardened JSON_API configs) + Greenhouse live smoke test.
2. Class-level permission review record (owner approval 2026-11-19, official API terms/rate limits cited with evidence) and policy that auto-registered ATS sources inherit it.
3. Validation gate: employer, title, location/work mode, apply URL, usable description; incomplete -> quarantine, not pool.
4. Per-provider rate limiting and failure backoff; one failing company never blocks others.
5. Live evidence: pool size per source and per provider.
### P3 Company discovery loop (Batch 3)
1. Tavily connector (system-side) with credit ledger: per-call cost logged, monthly total, 90% stop, truthful budget status.
2. Token extraction from result URLs (greenhouse/lever/ashby patterns), normalization, dedupe, token validity probe against the official API before registering.
3. Auto-register discovered companies as sources under the class approval; schedule refresh by activity tier (busy 6h, quiet 24h).
4. Discovery query plan: sector x country x seniority matrix, rotated; yield tracking per query (new companies per credit), retire low-yield queries.
5. Live evidence: companies registered/day, credits used, yield.
### P4 Role/keyword searches into the pool (Batch 4)
1. Keyword/role search templates driven by demand (popular user preferences, non-identifying) and a fixed broad role list.
2. Search hit = lead: fetch/validate the real listing before pooling; reuse include_raw_content if usage meter confirms no extra cost.
3. Canonicalization/dedupe against ATS-sourced jobs.
4. Shared query cache and Job-Pool-first logic.
5. Queue for user live-search top-ups (system-side, quota-limited).
### P5 Pool to user and guest flow (Batch 5)
1. Scheduled per-user sync from central pool using non-identifying preferences; user-set frequency.
2. Pool search screen/API reads pool only; filters/freshness shown honestly.
3. Guest first search (name + email), rate-limited, reads pool.
4. Requirement auto-accept with sampling.
5. Punt recommendations from pooled jobs; first application package flow.
### P6 Operations and resilience (continuous, gates each phase)
- Admin visibility: sources, per-source last success, freshness distribution, credit ledger, alerts.
- Resilience: independent fetchers, circuit breaker per provider, safe retry, no single provider dependence, memory-safe on the 512 MB host (skip-empty cycles live; batch size 5; consider moving heavy workers off the web instance).
- Security/privacy: allow-list payload tests for any outbound provider call; secrets only in Render env.
- Test: unit + Postgres integration per task; live evidence (Neon/Render logs) before COMPLETE.

## Risks / open items
- ATS terms or rate limits could change; mitigated by class review evidence and per-provider throttling.
- Search-discovered tokens can be wrong or private boards; probe before registering.
- Freshness by absence needs full-listing completeness checks to avoid false closures.
- Gemini free-tier quotas and model retirements; keep model in env/config.
- Pushes to main bypass the PR rule (owner aware).
