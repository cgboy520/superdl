# Tickets

The conversation between a user and support: the user opens a ticket → both sides reply in turn → marked resolved → closed; the stale patrol pushes tickets without a reply to the on-call. Module `app/modules/tickets/`.

## Data model

- `tickets`: ticket_no unique (`T` + yyyymmdd + a two-digit sequence within the day, e.g. `T20260823-01`), user_id, category (instance/billing/data/account/other), subject (≤128), status (open/pending_staff/pending_user/resolved/closed), instance_uuid?, idempotency_key (unique together with user_id), closed_at (set only on closed)
- `ticket_messages`: ticket_id, sender_kind (user/staff), sender_id (interpreted by sender_kind as users.id or admin_users.id, no foreign key), body (≤4000)

State machine: open → pending_staff (user reply) / pending_user (staff reply); either side marks → resolved; resolved → closed. resolved / closed accept no further replies.

## Contract

| Endpoint                                                          | Role / auth          | Notes                                                                                                                                                                                                               |
| ----------------------------------------------------------------- | -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `POST /api/v1/tickets`                                            | user                 | `{category, subject, body, instance_uuid?}` → 201; the first message is stored with the ticket; an `Idempotency-Key` replay returns the existing ticket (200 + `X-Idempotent-Replay`, no rate-limit quota consumed) |
| `GET /api/v1/tickets?cursor=&limit=`                              | user                 | The caller's tickets, descending cursor pagination                                                                                                                                                                  |
| `GET /api/v1/tickets/{ticket_id}`                                 | user                 | Detail + message stream (ascending); the owner check is in the SQL WHERE, someone else's ticket and a missing one both return 404                                                                                   |
| `POST /api/v1/tickets/{ticket_id}/messages`                       | user                 | Append a reply → pending_staff; terminal tickets 409                                                                                                                                                                |
| `POST /api/v1/tickets/{ticket_id}/close`                          | user                 | Only resolved tickets can be closed                                                                                                                                                                                 |
| `GET /api/admin/v1/tickets?status=&category=&user_id=&ticket_no=` | ops/finance/readonly | Cursor pagination, exact filters and search                                                                                                                                                                         |
| `GET /api/admin/v1/tickets/{ticket_id}`                           | ops/finance/readonly | Detail + message stream                                                                                                                                                                                             |
| `POST /api/admin/v1/tickets/{ticket_id}/reply`                    | ops/admin            | Staff reply → pending_user, in-app notification to the user (dedup_key against duplicates); audited                                                                                                                 |
| `POST /api/admin/v1/tickets/{ticket_id}/status`                   | ops/admin            | `{action: resolve \| close}`; close only after resolved; audited                                                                                                                                                    |

Frontend: user console `/support` (FAQ + my tickets) and `/support/:ticketId`; admin console `/tickets`.

## Rules and invariants

- Open tickets per user (open/pending_*) and creation frequency are capped (`MAX_OPEN_TICKETS` and the `ticket-create:{user_id}` rate-limit key, see [limits.md](./limits.md)); idempotent replays do not count. Replies per ticket are capped by `MAX_MESSAGES_PER_TICKET` (200) and rate-limited by `ticket-reply:{user_id}` (30 per 10 minutes); detail reads truncate at the cap.
- Every state transition happens under a row lock (`FOR UPDATE`).
- A user reply writes one admin_alert (info) into the admin alert feed; a staff reply writes an in-app notification for the user.
- Stale patrol (every 30 minutes, advisory lock): a ticket in pending_staff for more than 24 h writes one admin_alert (warning), `dedup_key = ticket-stale:{ticket_id}`, reported once per ticket lifetime.
- The daily ticket_no sequence is generated by counting in the service layer + retrying on unique conflict, without a sequence object.
- Admin writes go through the audit middleware; ticket bodies never enter the audit detail.
