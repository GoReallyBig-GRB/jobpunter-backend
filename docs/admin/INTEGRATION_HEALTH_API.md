# Integration health observations

## Current contract

GET /api/v1/admin/integrations/status reports configuration facts separately from observed health. An integration is not considered healthy just because credentials or configuration exist. The response includes the latest persisted observation, if one exists, and otherwise reports UNKNOWN.

POST /api/v1/admin/integrations/smtp_email/health-check performs a bounded SMTP connection check. It uses only the configured SMTP host/port, applies the configured timeout, performs EHLO, optional STARTTLS and configured authentication, and **never sends an email**. The check reports HEALTHY only for the operations actually completed; if authentication is not configured, the check type is CONNECTION_ONLY_NO_SEND rather than implying that mail delivery was verified.

The endpoint records status, check type, observation time, duration, administrator, a safe error category and non-sensitive boolean details in integration_health_observations. It writes an AdminAuditEvent for the check. Raw SMTP responses, host credentials, passwords and message content are not persisted in the observation or returned.

GET /api/v1/admin/integrations/observations provides bounded history with optional integration, status and time-range filters.

## Status meanings

- HEALTHY: the configured connection check succeeded for the check type reported.
- DEGRADED: configuration is incomplete for the attempted check.
- FAILED: the check raised a provider/connection exception; only the exception class is stored.
- UNCONFIGURED: the check was not attempted because required SMTP configuration is missing.
- UNKNOWN: no persisted observation exists. This is not a failure or a success.

## Explicit limitations

A successful SMTP connection/authentication check does not prove that a future email will be accepted or delivered. Delivery outcomes remain represented by the separate persisted email-delivery records. No email is sent by this health check.

Document storage, OCR, Google Drive backup and Firebase identity remain UNKNOWN until a safe provider-specific health-check adapter is implemented. The backend does not report those integrations as healthy based on configuration alone.
