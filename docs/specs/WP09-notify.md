# WP9 · 通知

## 目标
站内信 + 短信(mock/aliyun)+ Alertmanager webhook + 余额预警。

## 数据
- `notifications`:user_id、type(balance_warn/freeze_warn/instance_failed/gpu_fault/announcement)、title、content、read_at?、created_at
- 用户可配预警阈值(users.low_balance_warn_hours 或 settings 表)

## API
- `GET /notifications?unread=`、`POST /notifications/{id}/read`
- `POST /webhooks/alertmanager`(basic auth):GPU XID 致命告警 → 通知受影响租户 + 管理端告警流
- 余额巡检(WP4 的 5min 任务)触发:预估<24h → 站内信+短信(去重:同类型 24h 一条)

## 验收
- 阈值可配;预警可审计(notifications + audit)
- 同类型预警 24h 去重;充值后解除预警状态
- alertmanager webhook 幂等(fingerprint 去重)
