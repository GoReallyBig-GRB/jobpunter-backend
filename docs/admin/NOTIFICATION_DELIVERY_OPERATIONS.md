# Notification delivery operations

## Inspecting delivery state

GET /api/v1/admin/notifications/email-deliveries provides bounded, filterable persisted delivery outcomes. It omits recipient email addresses and notification bodies. SENT means the SMTP client accepted the message for sending; it is not proof of final mailbox delivery.

## Requesting a retry

POST /api/v1/admin/notifications/email-deliveries/{delivery_id}/retry requires a reason and an active Admin grant. Only FAILED and SKIPPED_UNCONFIGURED deliveries are eligible. The endpoint checks that the recipient still has a verified email address, email notifications remain enabled, and SMTP is configured.

A retry requeues the existing NOTIFICATION_EMAIL_REQUESTED outbox event when possible, or creates one if no event remains. It resets the outbox retry state and moves the delivery back to PENDING without resetting the persisted delivery attempt count. A second retry request while a delivery is queued or in progress is rejected. Disabled preferences, unverified recipients, missing SMTP configuration, already-sent deliveries and other non-retryable states are not bypassed.

The endpoint writes an AdminAuditEvent containing the administrator, delivery/notification references, previous and new states, reason, and request correlation. It returns delivery_confirmed=false: the action only queues work and does not claim that an email was sent or delivered.

## Operational truthfulness

- PENDING: a retry is queued, not delivered.
- SENDING: a worker is attempting SMTP delivery.
- SENT: the SMTP client accepted the message for sending.
- FAILED: the most recent delivery attempt failed; only a safe exception category is persisted.
- SKIPPED_UNCONFIGURED: SMTP was not configured when the original event was handled; after configuration is corrected, an administrator can queue a retry.
- DISABLED / SKIPPED_UNVERIFIED: retry is blocked until recipient preferences or verification state is legitimately corrected.

The retry route does not send email synchronously and does not allow administrators to bypass user email preferences or verification requirements.
