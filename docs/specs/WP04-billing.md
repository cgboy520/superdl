# WP4 · 计费引擎

> ✅ 已交付并通过验收用例(tests/test_billing_*.py;覆盖率 92.8%≥90% CI 闸门)。

## 目标
钱包/账本/小时结算/尾账/冻结回收。金额单测全过,结算重复执行零重复扣款。

## 数据
- `wallets`:user_id 唯一、balance numeric(14,2)、frozen_amount numeric(14,2)
- `balance_ledger`:user_id、type(recharge/consume/refund/adjust)、amount 带符号 numeric(14,2)、balance_after、ref_type/ref_id、created_at —— 追加式
- `bills_hourly`:instance_id、hour_start、seconds_used、unit_price numeric(12,4)、gpu_count、amount numeric(14,2)、UNIQUE(instance_id, hour_start)
- `bills_daily_disk`:disk_id、day、size_gb、unit_price、amount、UNIQUE(disk_id, day)

## 规则
- 钱包更新一律 `SELECT FOR UPDATE` + 同事务 ledger(balance_after 快照)
- 小时结算(每小时 :02,advisory lock):扫上一自然小时 instance_events 重建每实例 running 秒数 → `INSERT ON CONFLICT DO NOTHING` → 仅对新插入行扣款
- 尾账:stop/release 时对当前小时已用秒数立即入账(同一 UNIQUE 键幂等)
- 退款:creating 失败 → 全额退(该实例未产生 running 时段即无账;预检冻结不落账)
- 欠费链路(5min 巡检):预估可用时长<24h → 预警(WP9);balance≤0 → 停机→frozen(72h)→releasing
- 开机前校验:balance ≥ price_hourly × gpu_count × 1h

## 验收
- 结算函数重复执行、并发执行均零重复扣款(UNIQUE + ON CONFLICT)
- 跨小时开关机的秒数重建正确(含事件恰在整点、多次开关机)
- 舍入:2 位 HALF_EVEN;0 秒不出账
- Prometheus 完全不可用时结算照常(事件驱动)
- 余额不足开机报 INSUFFICIENT_BALANCE
