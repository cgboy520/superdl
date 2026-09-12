# 通知

站内信、短信、Alertmanager webhook 与余额预警。

## 数据模型

- `notifications`:user_id、type(account/instance/service/balance_warn/arrears/subscription/preempted/gpu_fault/ticket/announcement/admin_alert 及资金类 recharge/consume/adjust/refund/invoice)、title、content、severity、dedup_key?、read_at?、target_id?(跳转目标)、target_kind?(管理端告警流深链种类 tenant / node / ticket,写入时给全,不由标题反推)
- 预警阈值存 `users.low_balance_warn_hours`

## 契约

| 端点                                                | 角色/鉴权                                                            | 说明                                                                                                   |
| --------------------------------------------------- | -------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| `GET /api/v1/notifications?unread=&cursor=&limit=`  | user                                                                 | 降序游标分页                                                                                           |
| `POST /api/v1/notifications/{notification_id}/read` | user                                                                 | 标记已读 → 204                                                                                         |
| `GET /api/v1/notifications/unread-count`            | user                                                                 | 未读数:DB count                                                                                        |
| `POST /api/v1/notifications/read-all`               | user                                                                 | 全部已读(幂等)→ 204                                                                                    |
| `POST /api/v1/webhooks/alertmanager`                | Bearer token(`SUPERDL_ALERTMANAGER_TOKEN` 未配置一律 401,无环境后门) | GPU XID 致命告警 → 通知受影响租户(target tenant)+ 进管理端告警流(带 `hostname` 标签的告警 target node) |

## 规则与不变量

- 余额预警由计费 5min 巡检触发:预估可用时长低于 `users.low_balance_warn_hours` → 站内信 + 短信;同类型预警按 UTC 自然日去重。
- Alertmanager webhook 按 fingerprint 去重;端点加固数值见 [limits.md](./limits.md)。
- 通知短信经 outbox(`notify.sms`)与业务事务同库入队、worker 异步投递:请求与巡检事务里不做渠道网络调用;失败退避重试,超预算进死信告警。at-least-once,同一通知可能收到多条;渠道 seam 见 [security.md](./security.md)。
- 站内信存已渲染文案,不随语言切换,见 [i18n.md](./i18n.md)。
