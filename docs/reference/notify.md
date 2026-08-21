# 通知

站内信、短信、Alertmanager webhook 与余额预警。

## 数据模型

- `notifications`:user_id、type(account/instance/balance_warn/arrears/gpu_fault/announcement/admin_alert 及资金类 recharge/consume/adjust)、title、content、severity、dedup_key?、read_at?
- 预警阈值存 `users.low_balance_warn_hours`,用户可配

## 契约

| 端点 | 角色/鉴权 | 说明 |
|---|---|---|
| `GET /api/v1/notifications?unread=` | user | 列表 |
| `POST /api/v1/notifications/{notification_id}/read` | user | 标记已读 → 204 |
| `POST /api/v1/webhooks/alertmanager` | basic auth | GPU XID 致命告警 → 通知受影响租户 + 进管理端告警流 |

## 规则与不变量

- 余额预警由计费 5min 巡检触发:预估可用时长 <24h → 站内信 + 短信。
- 同类型预警 24h 去重;充值后解除预警状态。
- Alertmanager webhook 按 fingerprint 去重,重复投递不产生重复通知。
- 通知短信为尽力而为,发送失败不阻塞业务;渠道 seam 见 [security.md](./security.md)。
- 站内信存的是已渲染文案,不随用户语言切换,见 [i18n.md](./i18n.md)。
