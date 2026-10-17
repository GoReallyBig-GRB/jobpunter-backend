# Support API Contract

Base path: `/api/v1`. Every route requires the configured authenticated identity. Candidate routes scope tickets to the authenticated candidate. Administrator routes require an active `ADMIN` grant for an active user.

## Candidate routes

- `POST /candidate/support/tickets`: Create a ticket with `idempotency_key`, `title`, `issue_details`, and optional `follow_up_to_ticket_id`. A matching replay returns the original ticket with `created: false`; reuse with different content returns 409.
- `GET /candidate/support/tickets`: Paginated candidate-owned tickets; supports `status` and `unread_only`.
- `GET /candidate/support/tickets/{ticket_id}`: Ticket and message history. A ticket owned by another candidate returns 404.
- `POST /candidate/support/tickets/{ticket_id}/messages`: Add a message with `idempotency_key` and nonblank `body`. Matching retries return the original message; conflicting key reuse returns 409.
- `PATCH /candidate/support/tickets/{ticket_id}/status`: Candidate may resolve or close a ticket. A resolved/closed ticket is not reopened; create a linked follow-up ticket instead.

## Administrator routes

- `GET /admin/support/tickets`: Paginated inbox. Filters: `status`, `unread_only`, `search` (ticket title/details or candidate name/email), `created_after`, `created_before`, and `assigned_admin_user_id`. Invalid date bounds return 422. List entries include candidate context.
- `GET /admin/support/tickets/{ticket_id}`: Ticket thread plus candidate context.
- `POST /admin/support/tickets/{ticket_id}/messages`: Add an administrator reply using an idempotency key.
- `PATCH /admin/support/tickets/{ticket_id}/status`: Resolve or close a ticket. Closed tickets are terminal.
- `PATCH /admin/support/tickets/{ticket_id}/assignment`: Assign to an active administrator with an active grant, or set `admin_user_id` to null to unassign. Assignment changes create an audit event.

## Lifecycle and delivery

Ticket creation, status transitions, audit records, and outbox events commit together. The outbox worker creates idempotent in-app notifications for the candidate and active administrators on ticket creation and resolution/closure. Optional email work is queued only for recipients who enabled email notifications and have a verified email address. Delivery state and bounded retry outcomes are recorded by the existing email handler.

Responses use a `data` object; collection responses include `pagination.limit`, `pagination.offset`, and `pagination.total`. Message persistence is synchronous; `delivery_status: SENT` means the message was durably accepted into the ticket thread, not that an email was sent.
