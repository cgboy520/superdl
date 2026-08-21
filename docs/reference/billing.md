# 计费

钱包、账本、小时结算、尾账、欠费冻结回收与策略参数下发。

## 数据模型

- `wallets`:user_id 唯一、balance numeric(14,2)、frozen_amount numeric(14,2)
- `balance_ledger`:user_id、type(recharge/consume/refund/adjust)、amount 带符号 numeric(14,2)、balance_after、ref_type/ref_id —— 追加式
- `bills_hourly`:instance_id、hour_start、seconds_used、unit_price numeric(12,4)、gpu_count、amount numeric(14,2)、UNIQUE(instance_id, hour_start)
- `bills_daily_disk`:disk_id、day、size_gb、unit_price、amount、UNIQUE(disk_id, day)
- `settlement_watermarks`:key(PK)、settled_through、updated_at —— 结算水位线,漏掉的时段由后续轮次追平
- `policy_overrides`:策略参数在线覆盖层,`GET /api/v1/policies` 读生效值

## 契约

| 端点 | 角色/鉴权 | 说明 |
|---|---|---|
| `GET /api/v1/policies` | 匿名 | 盘价(Decimal 串)、盘容量上下限、宽限与冻结天数、冻结 72h、默认预警阈值、`real_name_required_for_recharge` |
| `GET /api/v1/wallet` | user | 余额与冻结额 |
| `GET /api/v1/wallet/ledger` | user | 资金流水,游标分页 |
| `GET /api/v1/bills/hourly` | user | 小时账单,游标分页 |
| `GET /api/v1/bills/summary` | user | 消费概览与成本归因 |
| `GET /api/v1/bills/daily-summary?date=YYYY-MM-DD&tz_offset_minutes=480` | user | 本地日界折 UTC 聚合小时账单(按实例)+ 当日数据盘日账 |

## 规则与不变量

- 计费主依据是 `instance_events` 的 running↔非 running 边;Prometheus 指标只做展示与对账,永不参与计费,监控全挂时结算照常。
- 小时结算(每小时 :02,advisory lock):扫上一自然小时事件重建每实例 running 秒数 → `INSERT ... ON CONFLICT DO NOTHING` → 仅对新插入行扣款。重复执行与并发执行必须零重复扣款。
- 尾账:stop/release 时对当前小时已用秒数立即入账,靠同一 UNIQUE 键保持幂等。
- 退款:creating 失败全额退;该实例未产生 running 时段即无账,预检冻结不落账。
- 开机前校验 balance ≥ price_hourly × gpu_count × 1h,不足报 `INSUFFICIENT_BALANCE`。
- 欠费链路(5min 巡检):预估可用时长 <24h → 预警;balance ≤0 → 停机 → frozen(72h)→ releasing。
- 钱包更新必须 `SELECT ... FOR UPDATE`,且同事务写 `balance_ledger`(带 balance_after 快照)。
- 金额全链路 Decimal:单价 4 位小数,入账 2 位小数,ROUND_HALF_EVEN;0 秒不出账。
- 策略参数改动即时生效,盘价快照、巡检、扩容全链路跟随。
- 数据盘按日计费的口径见 [disks.md](./disks.md);充值与支付见 [payment.md](./payment.md)。
