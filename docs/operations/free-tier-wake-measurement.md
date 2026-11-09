# Free-tier wake measurement (DEP-01 evidence runbook)

Status: NOT RUN. This runbook produces the evidence that `zero-cost-hosting-decision.md` marks NOT VERIFIED for Render Free
and Neon Free. It needs accounts the owner creates; nothing here provisions anything or changes application code.

## What it measures
| Question | How |
|---|---|
| Render cold start after idle | `/api/v1/health` timing after idle gaps of 0, 10, 16 and 30 minutes (Render spins down after 15 idle minutes) |
| Neon resume time | `/api/v1/readiness` timing right after `/health` (it queries the database; Neon suspends after 5 idle minutes) |
| Neon compute floor and CU-hours used | Neon console monitoring/usage page, read by the owner before and after the run |
| Render instance hours used | Render dashboard usage page, read by the owner before and after the run |

Not measured here: the longest background cycle that survives Render spin-down. That needs a trigger endpoint that runs one
bounded cycle, which does not exist yet and is not part of this runbook.

## Steps (owner)
1. Create a free Render web service and a free Neon project. Confirm that neither asks for a card; record the plan pages.
2. Set `DATABASE_URL` on the service to Neon's pooled (`-pooler`) host using the `postgresql+psycopg://` scheme. Run Alembic
   migrations once with the direct (non-pooler) host. Set `JOBPUNTER_DEPLOYMENT_REVISION` to the deployed Git SHA.
3. Note the Neon CU-hours and Render instance hours currently used.
4. From any machine with Python 3.11+, in a checkout of this repo:
   `python -m scripts.measure_free_tier_wake --base-url https://<service>.onrender.com --rounds 3 --output free-tier-wake-evidence.json`
   One round takes about 57 minutes (sum of the idle gaps). The file holds only timings and status codes.
5. Note the Neon CU-hours and Render instance hours again, and send back the evidence file with both before/after readings.

## Acceptance
Record, per idle gap: Render cold-start seconds, Neon resume seconds, and the CU-hours and instance hours consumed. The
15 to 30 minute cadence in `zero-cost-hosting-decision.md` stays a proposal until these numbers exist.
