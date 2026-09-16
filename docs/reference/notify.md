# Notifications

In-app notifications, SMS, the Alertmanager webhook and balance warnings.

## Data model

- `notifications`: user_id, type (account/instance/service/balance_warn/arrears/subscription/preempted/gpu_fault/ticket/announcement/admin_alert plus the money types recharge/consume/adjust/refund/invoice), title, content, severity, dedup_key?, read_at?, target_id? (navigation target), target_kind? (deep-link kind for the admin alert feed: tenant / node / ticket, written in full at insert time, never inferred from the title)
- The warning threshold is stored in `users.low_balance_warn_hours`

## Contract

| Endpoint                                            | Role / auth                                                                             | Notes                                                                                                                                     |
| --------------------------------------------------- | --------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| `GET /api/v1/notifications?unread=&cursor=&limit=`  | user                                                                                    | Descending cursor pagination                                                                                                              |
| `POST /api/v1/notifications/{notification_id}/read` | user                                                                                    | Mark read → 204                                                                                                                           |
| `GET /api/v1/notifications/unread-count`            | user                                                                                    | Unread count: DB count                                                                                                                    |
| `POST /api/v1/notifications/read-all`               | user                                                                                    | Mark all read (idempotent) → 204                                                                                                          |
| `POST /api/v1/webhooks/alertmanager`                | Bearer token (`SUPERDL_ALERTMANAGER_TOKEN` unset → always 401, no environment backdoor) | Fatal GPU XID alert → notify the affected tenants (target tenant) + admin alert feed (alerts carrying a `hostname` label target the node) |

## Rules and invariants

- Balance warnings are triggered by the 5-minute billing patrol: estimated remaining hours below `users.low_balance_warn_hours` → in-app notification + SMS; warnings of the same type are deduplicated per UTC calendar day.
- The Alertmanager webhook deduplicates by fingerprint; endpoint hardening values in [limits.md](./limits.md).
- SMS providers: `sms_provider` mock / aliyun / twilio (`app/core/sms.py`); email providers: `email_provider` mock / smtp (`app/core/email.py`, aiosmtplib) carrying verification codes, bodies from `app/core/verification/templates.py`. Both have independent platform budgets (`sms-platform:*`, `email-platform:*`, see [limits.md](./limits.md)); counter `superdl_verification_sent_total{channel, purpose}`.
- Notification SMS is enqueued through the outbox (`notify.sms`) in the same database as the business transaction and delivered asynchronously by the worker: request and patrol transactions make no channel network calls; failures retry with backoff, and exhausting the budget lands in the dead-letter alert. At-least-once: the same notification may arrive more than once; the channel seam is in [security.md](./security.md).
- In-app notifications store the rendered copy and do not switch with the language, see [i18n.md](./i18n.md).
