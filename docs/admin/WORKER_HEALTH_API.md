# Worker health and operational heartbeat contract

## API

GET /api/v1/admin/operations/workers returns bounded heartbeat records for instrumented worker processes. Optional filters support worker name and status; STALE is derived from a RUNNING or IDLE heartbeat older than the requested threshold. The threshold defaults to 120 seconds and is bounded between 30 and 3600 seconds.

GET /api/v1/admin/operations/summary includes stored worker-status counts, stale worker count and the latest observed heartbeat timestamp alongside acquisition, outbox, work-item and source-state counts.

## Instrumented workers

The current heartbeat instrumentation covers the outbox dispatcher, discovery scheduler, discovery mission worker, discovery evaluation worker, job verification worker, job requirement extraction worker and document-retention worker. Each process instance has its own persisted identity. A graceful exit records STOPPED; an unhandled worker-loop exception records FAILED with only the exception class; an abruptly terminated process becomes STALE after its last heartbeat ages beyond the configured query threshold.

## Metrics and truthfulness

Metrics are limited to small scalar operational counters and labels. Payloads, message bodies, candidate data, email values, tokens, secrets and raw exception messages are filtered out. Heartbeat persistence errors are logged without masking the worker's primary operation.

A heartbeat means that the process reported in at a particular time. It does not prove that every item succeeded, that a provider is healthy, or that a worker will remain available. The separate work-item and outbox endpoints remain authoritative for individual work outcomes. Workers not yet emitting heartbeats have no record and must be treated as UNKNOWN, not FAILED.
