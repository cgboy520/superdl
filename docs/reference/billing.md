# 计费

钱包、账本、小时结算、尾账、欠费冻结回收与策略参数下发。

## 数据模型

- `wallets`:user_id 唯一、balance numeric(14,2)、frozen_amount numeric(14,2)(预留字段,冻结预占未实现,恒 0)
- `balance_ledger`:user_id、type(recharge/consume/refund/adjust)、amount 带符号 numeric(14,2)、balance_after、ref_type/ref_id —— 追加式
- `bills_hourly`:instance_id、hour_start、seconds_used、unit_price numeric(12,4)、gpu_count、amount numeric(14,2)、UNIQUE(instance_id, hour_start)
- `bills_daily_disk`:disk_id、day、size_gb、unit_price、amount、UNIQUE(disk_id, day)
- `settlement_watermarks`:key(PK)、settled_through、updated_at —— 结算水位线,漏掉的时段由后续轮次追平
- `settlement_gaps`:kind、window_start、object_id、reason、resolved_at —— 结算缺口登记(追平截断 catchup_truncated / 单对象连续失败死信 dead_letter / 水位线丢失 watermark_missing / 宽限期重叠 grace_overlap),UNIQUE(kind, window_start, object_id);水位线被越过但账未结清的窗口一律留痕。闭环:管理端「财务 › 结算缺口」列表 + 人工重放(幂等入账原语,成功回写 resolved_at;grace_overlap 拒重放走人工核销)+ DB 口径持续告警 `superdl_settlement_gap_unresolved`(缺口不自愈,不自动补结)
- `reconcile_checkpoints`:user_id(PK)、last_ledger_id、balance_after、updated_at —— 资金核对的增量游标(链式校验断点续扫)
- `policy_overrides`:策略参数在线覆盖层,`GET /api/v1/policies` 读生效值

## 契约

| 端点 | 角色/鉴权 | 说明 |
|---|---|---|
| `GET /api/v1/policies` | 匿名 | 盘价(Decimal 串)、盘容量上下限、宽限与冻结天数、冻结 72h、默认预警阈值、`real_name_required_for_recharge` |
| `GET /api/v1/wallet` | user | 余额与冻结额 |
| `GET /api/v1/wallet/ledger` | user | 资金流水,游标分页 |
| `GET /api/v1/bills/hourly` | user | 小时账单,游标分页(含 instance_name 展示冗余,非对账字段) |
| `GET /api/v1/bills/summary` | user | 消费概览与成本归因 |
| `GET /api/v1/bills/daily-summary?date=YYYY-MM-DD&tz_offset_minutes=480` | user | 本地日界折 UTC 聚合小时账单(按实例)+ 当日数据盘日账 |
| `GET /api/v1/billing/export?dataset=hourly\|ledger&month=YYYY-MM&tz_offset_minutes=480&lang=` | user | 小时账单 / 收支明细 CSV(流式);month 仅作用于 hourly;行数硬上限,触顶时在文件末尾写 `#SUPERDL_EXPORT_TRUNCATED#` 标记行,前端据以提示已截断 |

## 规则与不变量

- 计费主依据是 `instance_events` 的 running↔非 running 边;Prometheus 指标只做展示与对账,永不参与计费,监控全挂时结算照常。
- 小时结算(每小时 :02,advisory lock):由 `settlement_watermarks` 水位线驱动,从上次已结窗口追平到上一整点(停机跨整点下一轮自动补);每实例按事件重建窗口 running 秒数,`UNIQUE(instance_id, hour_start)` 幂等 upsert,秒数单调递增时只补差价。重复执行与并发执行必须零重复扣款。
- 追平截断(超 72h/14d 上限)与单对象连续失败(3 轮)死信,跳窗前一律登记 `settlement_gaps`(未核销数经 `superdl_settlement_gap_unresolved` 持续告警,不自愈)。
- 尾账:stop/release 时对当前小时已用秒数立即入账,靠同一 UNIQUE 键保持幂等。
- 平台责任失联(node_lost/pod_lost):计费截断到 Pod 首次 not-ready 的时刻(事件 `metadata.unready_since`),宽限观察期不计费;截断在事件重建层(`settlement._billing_view`)生效,尾账/整点/追平三路径同口径(尾账监听器按同一 `metadata.unready_since` 取窗口末,并把 `truncated_at` / `truncate_reason` 留进 `bills_hourly.detail`);`unready_since` 由 reconciler 只在当前 running 段内写入,每条进入 running 的路径先清零,不做残留判定。pod_unready(节点正常)不截断。
- 无水位线行只结最近窗口,落 `{kind}_watermark_missing` 告警日志:非首次部署出现即水位线行被误删或库回退,更早窗口需人工补结。
- 退款:creating 失败全额退;该实例未产生 running 时段即无账,预检冻结不落账。
- 开户前校验(`assert_can_afford`):余额 ≥ (在途 running 实例时费 + 新增时费) × `afford_cover_hours`(默认 1h)+ (在途盘日费 + 新增盘日费) × `disk_grace_days`;在钱包 FOR UPDATE 锁内统计,与资源创建同事务。不足报 `INSUFFICIENT_BALANCE`(文案含在途资源预计消耗)。
- 欠费链路(5min 巡检):预估可用时长 <24h → 预警;余额 − 当前小时未结算实时估算消耗 ≤ 0 → 停机 → frozen(72h)→ releasing。实时估算与结算同口径:事件重建秒数 − 已出账秒数。
- 余额恰好 0.00 即进入停机→冻结→回收链(冻结判据 `balance > 0` 才放行,与停机判据 `effective <= 0` 自洽);边界测试 `tests/test_billing_flow.py::test_zero_balance_stops_then_freezes_then_reclaims` 锁定。为何不是 `>= 0`:见 [../decisions.md](../decisions.md)「余额归零即回收」。
- 钱包更新必须 `SELECT ... FOR UPDATE`,且同事务写 `balance_ledger`(带 balance_after 快照)。
- 金额全链路 Decimal:单价 4 位小数,入账 2 位小数,ROUND_HALF_EVEN;0 秒不出账。SKU 时价须使单卡满 1 小时至少入账 ¥0.01(4 位时价 ≥ 0.0051,0.0050 恰为 tie 向偶舍 0),否则上架/改价拒绝。
- 营收报表(revenue_summary)按账单归属期(hour_start/day)切窗,不按扣款入账时间(ledger.created_at)。
- 日终资金核对:钱包侧按 `reconcile_checkpoints` 增量链式校验(逐笔 balance_after 链接 + 游标边界行复核,只扫增量,断链定位到 ledger id);出账 vs 消费两侧都按账单归属期切窗(ledger 经 ref_id 回连)。
- 策略参数改动即时生效,盘价快照、巡检、扩容全链路跟随。
- 数据盘按日计费的口径见 [disks.md](./disks.md);充值与支付见 [payment.md](./payment.md)。
