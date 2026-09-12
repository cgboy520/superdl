# 工单

用户与客服的对话流:用户建单 → 双方交替回复 → 标记解决 → 关闭;滞留巡检把久未回复的单推给值班。模块 `app/modules/tickets/`。

## 数据模型

- `tickets`:ticket_no 唯一(`T` + yyyymmdd + 两位日内序列,如 `T20260823-01`)、user_id、category(instance/billing/data/account/other)、subject(≤128)、status(open/pending_staff/pending_user/resolved/closed)、instance_uuid?、idempotency_key(与 user_id 联合唯一)、closed_at(仅 closed 落)
- `ticket_messages`:ticket_id、sender_kind(user/staff)、sender_id(按 sender_kind 解读为 users.id 或 admin_users.id,不建外键)、body(≤4000)

状态机:open → pending_staff(用户回复)/ pending_user(客服回复);任一方标记 → resolved;resolved → closed。resolved / closed 不可再回复。

## 契约

| 端点                                                              | 角色/鉴权            | 说明                                                                                                                                        |
| ----------------------------------------------------------------- | -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| `POST /api/v1/tickets`                                            | user                 | `{category, subject, body, instance_uuid?}` → 201;首条消息同单落库;`Idempotency-Key` 重放回既有单(200 + `X-Idempotent-Replay`,不耗限流配额) |
| `GET /api/v1/tickets?cursor=&limit=`                              | user                 | 本人工单,降序游标分页                                                                                                                       |
| `GET /api/v1/tickets/{ticket_id}`                                 | user                 | 详情 + 消息流(升序);owner 校验在 SQL WHERE,他人工单与不存在同回 404                                                                         |
| `POST /api/v1/tickets/{ticket_id}/messages`                       | user                 | 追加回复 → pending_staff;终态单 409                                                                                                         |
| `POST /api/v1/tickets/{ticket_id}/close`                          | user                 | 仅 resolved 可关                                                                                                                            |
| `GET /api/admin/v1/tickets?status=&category=&user_id=&ticket_no=` | ops/finance/readonly | 游标分页,精确过滤与检索                                                                                                                     |
| `GET /api/admin/v1/tickets/{ticket_id}`                           | ops/finance/readonly | 详情 + 消息流                                                                                                                               |
| `POST /api/admin/v1/tickets/{ticket_id}/reply`                    | ops/admin            | 客服回复 → pending_user,站内信告知用户(dedup_key 防重);审计                                                                                 |
| `POST /api/admin/v1/tickets/{ticket_id}/status`                   | ops/admin            | `{action: resolve \| close}`;close 仅 resolved 后可;审计                                                                                    |

前端:用户端 `/support`(FAQ + 我的工单)与 `/support/:ticketId`;管理端 `/tickets`。

## 规则与不变量

- 每用户进行中(open/pending_*)工单数与创建频次有上限(`MAX_OPEN_TICKETS` 与 `ticket-create:{user_id}` 限流键,见 [limits.md](./limits.md));幂等重放不计数。单工单回复数上限 `MAX_MESSAGES_PER_TICKET`(200)与 `ticket-reply:{user_id}` 限流(30/10 分钟),详情读取按上限截断。
- 所有状态迁移在行锁(`FOR UPDATE`)内进行。
- 用户回复落一条 admin_alert(info)进管理端告警流;客服回复落用户站内信。
- 滞留巡检(30 分钟一轮,advisory lock):pending_staff 超 24h 的单落一条 admin_alert(warning),`dedup_key = ticket-stale:{ticket_id}` 整个生命周期只报一次。
- ticket_no 日内序列由服务层计数 + 唯一冲突重试生成,不依赖序列对象。
- 管理端写操作过审计中间件;工单正文不进审计 detail。
