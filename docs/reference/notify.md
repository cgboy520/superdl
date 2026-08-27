# 通知

站内信、短信、Alertmanager webhook 与余额预警。

## 数据模型

- `notifications`:user_id、type(account/instance/balance_warn/arrears/subscription/preempted/gpu_fault/ticket/announcement/admin_alert 及资金类 recharge/consume/adjust)、title、content、severity、dedup_key?、read_at?
- 预警阈值存 `users.low_balance_warn_hours`,用户可配

## 契约

| 端点 | 角色/鉴权 | 说明 |
|---|---|---|
| `GET /api/v1/notifications?unread=&cursor=&limit=` | user | 列表,降序(最新在前)游标分页 |
| `POST /api/v1/notifications/{notification_id}/read` | user | 标记已读 → 204 |
| `GET /api/v1/notifications/unread-count` | user | 未读数(顶栏角标轮询):DB count,与列表分页解耦 |
| `POST /api/v1/notifications/read-all` | user | 全部已读(幂等)→ 204 |
| `POST /api/v1/webhooks/alertmanager` | Bearer token | GPU XID 致命告警 → 通知受影响租户 + 进管理端告警流 |

## 规则与不变量

- 余额预警由计费 5min 巡检触发:预估可用时长 <24h → 站内信 + 短信。
- 同类型预警 24h 去重;充值后解除预警状态。
- Alertmanager webhook 按 fingerprint 去重,重复投递不产生重复通知;端点加固:IP 限流 120 次/分、报文 ≤1 MiB、字符串字段截断至 1024 字符、单次 alerts 封顶 500 条。
- 通知短信经 outbox(`notify.sms`)与业务事务同库入队、worker 异步投递:请求与巡检事务里不做渠道网络调用;失败退避重试,超预算进死信告警。短信通道侧无法去重,at-least-once 下同一通知可能收到多条;渠道 seam 见 [security.md](./security.md)。
- 站内信存的是已渲染文案,不随用户语言切换,见 [i18n.md](./i18n.md)。
