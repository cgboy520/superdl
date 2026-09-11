# 计费

钱包、账本、小时结算、尾账、包周期(预付订阅)、欠费冻结回收与策略参数下发。

## 数据模型

- `wallets`:user_id 唯一、balance numeric(14,2)
- `balance_ledger`:user_id、type(recharge/consume/refund/adjust)、amount 带符号 numeric(14,2)、balance_after、ref_type/ref_id —— 追加式
- `bills_hourly`:instance_id、hour_start、seconds_used、unit_price numeric(12,4)、gpu_count(**照实存,CPU 实例为 0**)、amount numeric(14,2)、detail jsonb、UNIQUE(instance_id, hour_start)。`detail.source`:`hourly`(整点结算与追平)/ `tail`(尾账)/ `convert`(按量转包周期前的结清)/ `gap_replay`(缺口人工重放);补差价的行另带 `topped_up`,竞价转按量改价的行另带 `repriced`
- `bills_daily_disk`:disk_id、day、size_gb、unit_price、amount、UNIQUE(disk_id, day)
- `subscriptions`:user_id、instance_id、sku_id、period(CHECK ∈ {day, week, month, year})、period_count(CHECK ≥1)、unit_price numeric(12,4)(下单时的 SKU **原价**快照)、amount_paid numeric(14,2)(实扣,含折扣)、started_at、expires_at、status(active/expired/cancelled)、auto_renew(默认 false)、renewed_from_id?、warned_for_expiry?(存「已预警到哪个到期时刻」)、idempotency_key?、request_fingerprint?(`动作 + user_id + instance_id + period + period_count` 的 sha256,动作分 `subscription:new` / `subscription:renew`)、UNIQUE(user_id, idempotency_key);部分索引 `ix_subscriptions_active_expiry`(`expires_at` WHERE status='active')
- `settlement_watermarks`:key(PK)、settled_through、updated_at
- `settlement_gaps`:kind、window_start、object_id、reason(catchup_truncated / dead_letter / watermark_missing / grace_overlap)、resolved_at,UNIQUE(kind, window_start, object_id)。缺口不自愈,闭环是管理端「财务 › 结算缺口」人工重放(成功回写 resolved_at;grace_overlap 拒重放走人工核销)+ 告警 `superdl_settlement_gap_unresolved`
- `reconcile_checkpoints`:user_id(PK)、last_ledger_id、balance_after、updated_at —— 资金核对增量游标
- `policy_overrides`:策略参数在线覆盖层,`GET /api/v1/policies` 读生效值

## 契约

| 端点 | 角色/鉴权 | 说明 |
|---|---|---|
| `GET /api/v1/policies` | 匿名 | 盘价(Decimal 串)、`disk_min_gb/disk_max_gb`、`disk_grace_days/disk_frozen_days`、`freeze_grace_hours`、**`period_discount_day/week/month/year`(百分数,80 = 8 折)与 `period_expire_warn_days`**、**`spot_discount_pct`(40 = 4 折)与 `spot_grace_seconds`**、`real_name_enabled`、`real_name_required_for_recharge`。折扣与宽限窗一律从这里读,前端禁止硬编码 |
| `GET /api/v1/wallet` | user | 余额与冻结额 |
| `GET /api/v1/wallet/ledger` | user | 资金流水,游标分页 |
| `GET /api/v1/bills/hourly` | user | 小时账单,游标分页(含 instance_name 展示冗余) |
| `GET /api/v1/bills/summary` | user | 消费概览与成本归因 |
| `GET /api/v1/bills/daily-summary?date=YYYY-MM-DD&tz_offset_minutes=480` | user | 本地日界折 UTC 聚合小时账单(按实例)+ 当日数据盘日账 |
| `GET /api/v1/billing/export?dataset=hourly\|ledger&month=YYYY-MM&tz_offset_minutes=480&lang=` | user | 小时账单 / 收支明细 CSV(流式);month 仅作用于 hourly;行数硬上限,触顶时文件末尾写 `#SUPERDL_EXPORT_TRUNCATED#` |

## 规则与不变量

- 计费主依据是 `instance_events` 的 running↔非 running 边;Prometheus 指标只做展示与对账。
- 小时结算(每小时 :02,advisory lock):由 `settlement_watermarks` 驱动,从上次已结窗口追平到上一整点;每实例按事件重建窗口 running 秒数,`UNIQUE(instance_id, hour_start)` 幂等 upsert,秒数单调递增时只补差价。重复执行与并发执行零重复扣款。
- 追平截断与单对象连续失败死信,跳窗前登记 `settlement_gaps`;阈值见 [limits.md](./limits.md)。
- 尾账:stop/release 时对当前小时已用秒数立即入账,同一 UNIQUE 键幂等。
- 平台责任失联(node_lost/pod_lost):计费截断到 Pod 首次 not-ready 时刻(事件 `metadata.unready_since`);截断在事件重建层(`settlement._billing_view`)生效,尾账/整点/追平三路径同口径(尾账监听器把 `truncated_at` / `truncate_reason` 留进 `bills_hourly.detail`)。`unready_since` 由 reconciler 跨轮累积、清零只有两处,见 [orchestrator.md](./orchestrator.md)。pod_unready(节点正常)不截断。
- 无水位线行只结最近窗口,落 `{kind}_watermark_missing` 告警日志,更早窗口需人工补结。
- 退款:creating 失败全额退;未产生 running 时段即无账。
- 开户前校验(`assert_can_afford`):余额 ≥ (在途 running 实例时费 + 新增时费) × `afford_cover_hours` + (在途盘日费 + 新增盘日费) × `disk_grace_days`;钱包 FOR UPDATE 锁内统计,与资源创建同事务。不足报 `INSUFFICIENT_BALANCE`。
- 欠费链路(5min 巡检):预估可用时长低于用户预警阈值 → 预警;可用余额 − 当前小时未结算实时估算消耗 ≤ 0 → 停机 → frozen → releasing。实时估算与结算同口径:事件重建秒数 − 已出账秒数。
- **欠费巡检全链路判据是可用余额**(balance − frozen,`wallet.available_of` / `get_available_balance`),与 `assert_can_afford` 同口径:粗筛、锁内二次读、低余额预警 payload、stopped→frozen、frozen→解冻、数据盘欠费链一律取它。
- 可用余额恰好 0.00 即进入停机→冻结→回收链(解冻判据 `available > 0`,停机判据 `effective <= 0`);由 `tests/test_billing_flow.py::test_zero_balance_stops_then_freezes_then_reclaims` 锁定。见 [../decisions.md](../decisions.md)「余额归零即回收」。
- 钱包更新 `SELECT ... FOR UPDATE`,同事务写 `balance_ledger`(带 balance_after)。
- 金额全链路 Decimal:单价 4 位,入账 2 位,ROUND_HALF_EVEN;0 秒不出账。SKU 时价须使单卡满 1 小时至少入账 ¥0.01(4 位时价 ≥ 0.0051),否则上架/改价拒绝。
- **计费份数只经 `core/money.billing_units(gpu_count)` 换算**:GPU 实例 = 卡数(`price_hourly` 单卡时价),CPU 实例 `gpu_count=0` = 1 份整机(`price_hourly` 整机时价)。金额 = `单价 × 份数 × 秒 ÷ 3600`。`bill_amount`、`assert_can_afford` 在途时费、欠费巡检 `burn_per_hour`、对账实例时费、创建/开机预估全部走 `billing_units` / `hourly_cost`,不许散写 `max(1, n)` 或 `单价 × gpu_count`。账单行照实存 `gpu_count`(见 [../decisions.md](../decisions.md)「CPU 实例的计费份数收口到 `core/money.billing_units`」)。
- 小时结算候选集**只在 `orchestrator/queries.py::billing_candidates` 一处**排除包周期实例(`market != 'subscription'`)。**竞价实例不在排除之列**。
- 营收报表(revenue_summary)分两段切窗:**计量出账**(`bills_hourly` / `bills_daily_disk`)按账单归属期(hour_start / day);**包周期预付**(`subscriptions.amount_paid`)按 `subscriptions.created_at`。`today_revenue` / `yesterday_revenue` / `month_revenue` 是两段之和;`today_prepaid` / `month_prepaid` 单独拆出。
- 日终资金核对:钱包侧按 `reconcile_checkpoints` 增量链式校验(逐笔 balance_after 链接 + 游标边界行复核,断链定位到 ledger id);出账 vs 消费两侧按账单归属期切窗(ledger 经 ref_id 回连)。**包周期是第三条腿**:出账侧 `SUM(subscriptions.amount_paid)`(按 `created_at`),消费侧 `ref_type='subscription'` 的 ledger 经 `ref_id` 回连订阅行;`dangling_consume_refs` 同样加了这条腿。
- 策略参数改动即时生效,盘价快照、巡检、扩容全链路跟随。
- 数据盘日计费见 [disks.md](./disks.md);充值与支付见 [payment.md](./payment.md);阈值与默认值见 [limits.md](./limits.md)。

## 包周期(预付订阅)

`instances.market='subscription'` 的实例下单时一次性预扣整段周期费用,**不进小时结算**。折扣与周期口径的唯一计算点 `app/core/pricing.py`;下单、续费、到期巡检在 `app/modules/billing/subscriptions.py`;用例 `apps/api/tests/test_subscriptions.py`。

### 计价口径

- **周期取定长小时**(`PERIOD_HOURS`,值见 [limits.md](./limits.md));到期时刻与定价**同源**(`period_hours` 与 `period_delta` 同一个数)。
- 应付 = 折后时价 × 计费份数 × 周期小时数;份数只经 `core/money.billing_units`。
- 折扣按周期四档,策略参数;`period_count` 只收 `core/pricing.MAX_PERIOD_COUNT` 以内。取值见 [limits.md](./limits.md)。
- **报价三件套由后端保证自洽**:`SubscriptionQuote` 构造时满足 `discount_amount == list_amount - amount`,按 `SubscriptionQuoteOut` 逐行下发(period / period_count / hours / discount_pct / base_hourly / unit_price / list_amount / discount_amount / amount)。前端逐行渲染。
- `instances.price_hourly` 落**折后时价**。续费重新定价的基准另存 `subscriptions.unit_price`(SKU **原价**快照)。

### 下单预扣(与建实例同一个事务)

`POST /api/v1/instances` 带 `market=subscription` + `period`(必填)+ `period_count`(默认 1),钱包行锁内:落 `instances(creating, market='subscription')` → 写 `subscriptions` → `wallet.debit(type_='consume', ref_type='subscription', ref_id=<订阅 id>, allow_negative=False)` → `assert_can_afford` → `instance_events` + `outbox`。

- 扣款 `allow_negative=False`:余额不足抛 `INSUFFICIENT_BALANCE`,实例**不进 creating**。
- 扣完**再过一次燃烧率校验**(此刻余额已是扣后值);两种不足分开报错。
- `market='on_demand'` 却带 `period` / `period_count` 一律 422;SKU `period_enabled=false` 报 `orchestrator.periodNotEnabled`。
- 下单那条订阅行**不带幂等键**,整笔创建的幂等由同事务 `instances` 行担保。`subscriptions.idempotency_key` 服务**转换与续费两条路径**,共用 `UNIQUE(user_id, idempotency_key)`,两处重放查询都带 `request_fingerprint` 比对,同键异参 409 `common.idempotencyKeyMismatch`。
- 流水:`type='consume'`、`ref_type='subscription'`、`ref_id` 为订阅行 id、remark 形如「<实例名> 包月×1」;续费同款,remark 多「续费」。

### 唯一结算跳过点

`orchestrator/queries.py::billing_candidates` 里的 `market != 'subscription'` 是**唯一**跳过点。`upsert_hour_bill`、水位线、缺口机制、幂等键不动。

### 四处配套过滤

| 位置 | 排除包周期实例 |
|---|---|
| `wallet.assert_can_afford` 的在途燃烧率 | 已预付的实例不计入护栏 |
| `billing/patrol.py` `_patrol_running` 的停机判据 | `burn_per_hour` 与未结算实时估算的集合 |
| `billing/patrol.py` `_patrol_frozen_and_arrears_stopped` 的两支 | stopped→frozen 与 frozen 的「充值即解冻」 |
| `billing/edge_listener.py` 的尾账 | 离开 running 不出小时尾账 |

**frozen 到期回收那一支不过滤**,回收由余额巡检统一做。创建路径上 `_pending_hourly`(creating/starting 的待燃时费)同样排除包周期实例。

### 按量转包周期(`POST /api/v1/instances/{uuid}/subscribe`)

入参与响应同 `/renew`,区别在起点:转换从**现在**起算,续费从老周期到期时刻接上。

**先结清转换前那段按量账,再翻 `market`。** 结清用**转换前**的按量时价。

完整顺序(钱包行锁内、同一事务):幂等重放判定 → 前置校验 → `lock_wallet` → running 则 `settle_on_demand_up_to` 结清 → 落订阅行 + 预扣 → 翻 `market='subscription'` → 刷 `price_hourly` 为折后价 → `assert_can_afford`。

- **`settle_on_demand_up_to` 逐小时结**,从「水位线 + 1 小时」到当前自然小时(水位线为空则只结当前小时);落下的行 `detail.source = "convert"`。`stopped` 实例跳过。
- **结算滞后超过 `settlement.MAX_CONVERT_SETTLE_HOURS` 拒绝转换**,409 `billing.settlementBehind`。
- **幂等重放最先判**,在「只有按量实例可以转」守卫之前。
- **重放查询带 `instance_id` / `period` / `period_count`** 做异参检测;指纹不符 409。
- **报价基准是 `instance.price_hourly`**(按量实例上即建实例时的 SKU 原价快照),不是 SKU 现价;这个原价落进新订阅行的 `unit_price`。
- 前置:`market='on_demand'`;状态 `running` 或 `stopped`(其余 409 `orchestrator.convertNeedsRunningOrStopped`);SKU `period_enabled`。非按量报 `orchestrator.convertNotOnDemand`,已在保报 `billing.subscriptionAlreadyActive`。
- **反向不开**。

### 续费

`POST /api/v1/instances/{uuid}/renew`(见 [orchestrator.md](./orchestrator.md);`Idempotency-Key`,重放 200 + `X-Idempotent-Replay`,响应带 `quote`):

- **按原价快照重新定价**:基准 `subscriptions.unit_price`,不是 SKU 现价。
- 可换周期续,按**新周期**折扣报价;同事务刷新 `instances.price_hourly`。
- **起算时刻 = max(老到期时刻, 现在)**。
- 老行转 `expired`,新行 `renewed_from_id` 指向老行。**不在原行累加 `expires_at`**。
- 冻结中的实例续费即解冻(回 `stopped` 并清 `frozen_deadline`),**不自动开机**。
- 非包周期、releasing/released 报 `SUBSCRIPTION_NOT_RENEWABLE`(400)。没有订阅行报 `billing.subscriptionMissing`,`status='cancelled'` 报 `billing.subscriptionCancelled`。`auto-renew` 同一套判据。
- 并发同幂等键由 `UNIQUE(user_id, idempotency_key)` 兜住:撞键方 rollback 后回查胜出方按重放返回;回查带 `request_fingerprint`,同键异参 409。

`POST /api/v1/instances/{uuid}/auto-renew` 开关自动续费,**默认关**。

### 到期链路(`subscription_patrol`,30 分钟一轮,advisory lock,worker `core` 组件)

1. **临期预警** `expires_at - now < period_expire_warn_days` → 短信 + 站内信。去重锚点 `subscriptions.warned_for_expiry`(存「已预警到哪个到期时刻」);站内信另有按日分桶的 dedup_key。
2. **自动续费**:到期 + `auto_renew` + **可用余额**够 → 扣款、新开一行、通知。**先算价再比可用余额**,不靠 `debit` 抛错兜底。余额不够发「自动续费失败」通知,落到停机链路。
3. **到期停机**:订阅行转 `expired`;running → `system_stop(reason='subscription_expired')`;stopped → 直接冻结。creating/starting/stopping/frozen/releasing 本轮不动,下一轮接手。
4. **冻结**:单独一趟把「最后一期已到期且已停稳」的实例转 `frozen`(reason `subscription_freeze`),写 `frozen_deadline = now + freeze_grace_hours`。**与上一步分两趟**(停机是异步的)。候选集是「status='expired' 且 expires_at ≤ now」**减去在保集合**。
5. **回收**:由 `balance_patrol` 的 frozen 分支做。冻结窗口复用 `freeze_grace_hours`。

到期与欠费用**不同的 `instance_events.reason`**(`subscription_expired` / `subscription_freeze` 对 `arrears_stop` / `arrears_freeze`)。

### 预付语义的三条硬规矩

- **中途释放不退款。** 实例进入 `releasing` 时由计费边监听器把 active 订阅转 `cancelled`,**不生成退款流水**;确需退款走人工 `refund_requests`(见 [payment.md](./payment.md))。挂在迁移监听器上,覆盖用户释放、欠费回收、到期回收、管理端强制回收四条路径。已 `expired` 的历史行不动。
- **到期不自动转按量。** 到期即停机。
- **余额为零不停机。** 停机判据、燃烧率、冻结与解冻四处都排除(见上表)。

### 与其它口径的边界

- 包周期扣款是 `type='consume'`,计入消费;发票口径不变(`invoices._period_billable_amount` 只认 `orders.status='paid'` 的充值额)。
- 营收报表与日终资金核对各加包周期一条腿(见「规则与不变量」),切窗用 `subscriptions.created_at`。管理端总览另有 `subscriptions_active`(在保订阅数),见 [admin.md](./admin.md)。
- **数据盘不在包周期覆盖范围内**:仍按日出 `bills_daily_disk`,余额为 0 时照走数据盘欠费链。
- 未到期的包周期实例即使已停机也仍占软准入库存,见 [orchestrator.md](./orchestrator.md)。

## 竞价(spot)

`instances.market='spot'` 的实例拿折后价,容量紧张时可被平台回收。抢占规则、宽限窗与两个入口在 [orchestrator.md](./orchestrator.md);这里只写钱的口径。用例 `apps/api/tests/test_spot.py`。

### 折扣落在 `price_hourly`,结算引擎零改动

竞价时价 = SKU 原价 × `spot_discount_pct` / 100,由 `app/core/pricing.py` 的 `price_for` 单点算出,建实例时快照进 `instances.price_hourly`。此后与按量实例完全一样:进 `billing_candidates`、出 `bills_hourly`、走水位线与尾账、计入燃烧率与欠费巡检;`billing_candidates` 跳过条件仍只有 `market != 'subscription'`。`spot_discount_pct` 取值见 [limits.md](./limits.md)。

### 被抢占按实际运行秒数正常结算,不免单

被回收的实例迁 `stopping` 时,由计费边监听器(`billing/edge_listener.py`)照常出尾账,**按到那一刻的实际运行秒数结算**。

**宽限窗那段不计费**:状态机在发通知那一刻迁到 `stopping`,计费边随之落定,之后 Pod 多活的几十秒在计费窗口之外。

### 转按量:一小时一价

`POST /api/v1/instances/{uuid}/to-on-demand` 把 `market` 翻成 `on_demand`、单价还原成 `spec.base_price_hourly`(不从折后价反推)。**`bills_hourly` 一小时只有一行、一个 `unit_price`**,口径是**「一小时一价,以结算时的实例单价为准」**:转换把当前整点小时**整体**改按按量价。`settlement.reprice_current_hour` 在钱包行锁内 `FOR UPDATE` 取当前小时那一行:

- 这一行不存在(常见路径):什么都不做,之后的整点结算按新单价出账;
- 已出过账且**新价更高**:按新单价重算 `amount`、改写 `unit_price`、补扣差价、`detail` 打 `repriced`;三件事一起做。

**只在涨价时动这一行,降价整行不动**(`unit_price` 也不改)。降价路径经 `/to-on-demand` 不可达;真出现了整行原样留着,**退款一律走人工 `refund_requests`**(见 [payment.md](./payment.md)),不由结算原语写负数流水。用例锁降价路径:整行未动、无扣款流水。

转换对用户是一次涨价,必须写进转换确认弹窗(见 [../ui-ux-spec.md](../ui-ux-spec.md) §3.5)。转完再过一次 `assert_can_afford`。

**反向不开**:按量转不回竞价。
