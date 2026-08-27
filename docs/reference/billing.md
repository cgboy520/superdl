# 计费

钱包、账本、小时结算、尾账、包周期(预付订阅)、欠费冻结回收与策略参数下发。

## 数据模型

- `wallets`:user_id 唯一、balance numeric(14,2)
- `balance_ledger`:user_id、type(recharge/consume/refund/adjust)、amount 带符号 numeric(14,2)、balance_after、ref_type/ref_id —— 追加式
- `bills_hourly`:instance_id、hour_start、seconds_used、unit_price numeric(12,4)、gpu_count(**照实存,CPU 实例为 0**)、amount numeric(14,2)、detail jsonb、UNIQUE(instance_id, hour_start)。`detail.source` 记这一行由哪条路径落的:`hourly`(整点结算与追平)/ `tail`(离开 running 的尾账)/ `convert`(按量转包周期前的结清)/ `gap_replay`(缺口人工重放);秒数单调递增时原地补差价的行另带 `topped_up`,竞价转按量时被整体改价的行另带 `repriced`
- `bills_daily_disk`:disk_id、day、size_gb、unit_price、amount、UNIQUE(disk_id, day)
- `subscriptions`:user_id、instance_id、sku_id、period(CHECK ∈ {day, week, month, year})、period_count(CHECK ≥1)、unit_price numeric(12,4)(下单时的 SKU **原价**时价快照,续费据它重新报价)、amount_paid numeric(14,2)(实扣,已含折扣)、started_at、expires_at、status(active/expired/cancelled)、auto_renew(默认 false)、renewed_from_id?(续费链)、warned_for_expiry?(到期预警去重锚点,存「已预警到哪个到期时刻」)、idempotency_key?、UNIQUE(user_id, idempotency_key);另有部分索引 `ix_subscriptions_active_expiry`(`expires_at` WHERE status='active')供巡检取「到期在即 / 已到期」两种谓词
- `settlement_watermarks`:key(PK)、settled_through、updated_at —— 结算水位线,漏掉的时段由后续轮次追平
- `settlement_gaps`:kind、window_start、object_id、reason、resolved_at —— 结算缺口登记(追平截断 catchup_truncated / 单对象连续失败死信 dead_letter / 水位线丢失 watermark_missing / 宽限期重叠 grace_overlap),UNIQUE(kind, window_start, object_id);水位线被越过但账未结清的窗口一律留痕。闭环:管理端「财务 › 结算缺口」列表 + 人工重放(幂等入账原语,成功回写 resolved_at;grace_overlap 拒重放走人工核销)+ DB 口径持续告警 `superdl_settlement_gap_unresolved`(缺口不自愈,不自动补结)
- `reconcile_checkpoints`:user_id(PK)、last_ledger_id、balance_after、updated_at —— 资金核对的增量游标(链式校验断点续扫)
- `policy_overrides`:策略参数在线覆盖层,`GET /api/v1/policies` 读生效值

## 契约

| 端点 | 角色/鉴权 | 说明 |
|---|---|---|
| `GET /api/v1/policies` | 匿名 | 盘价(Decimal 串)、盘容量上下限、宽限与冻结天数、冻结 72h、默认预警阈值、**包周期四档折扣 `period_discount_day/week/month/year`(百分数,80 = 8 折)与 `period_expire_warn_days`**、**竞价的 `spot_discount_pct`(40 = 4 折)与 `spot_grace_seconds`(抢占通知到真删 Pod 的宽限窗)**、`real_name_required_for_recharge`。折扣与宽限窗一律从这里读,前端硬编码就意味着运营调完、页面还在承诺一个已经不成立的数 |
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
- **计费份数只经 `core/money.billing_units(gpu_count)` 换算**:GPU 实例 = 卡数(`price_hourly` 是单卡时价),CPU 实例 `gpu_count=0` = 1 份整机(`price_hourly` 是整机时价)。金额 = `单价 × 份数 × 秒 ÷ 3600`。`bill_amount`、钱包护栏 `assert_can_afford` 的在途时费、欠费巡检的 `burn_per_hour`、对账的实例时费、创建/开机的预估,全部走 `billing_units` / `hourly_cost`,不许各处写 `max(1, n)` —— 直接写 `单价 × gpu_count` 会让 CPU 实例每小时算出 ¥0.00,连带余额护栏与停机判据一起归零。账单行照实存 `gpu_count`,复算时按同一函数还原份数,行仍自洽(理由见 [../decisions.md](../decisions.md)「CPU 实例计费为 0 的解法」)。
- 小时结算的候选集**只在 `orchestrator/queries.py::billing_candidates` 一处**排除包周期实例(`market != 'subscription'`),结算引擎本身不感知购买模式;口径见下节。**竞价实例不在排除之列** —— 它与按量走同一条计费链,折扣只落在 `price_hourly` 上,见「竞价(spot)」。
- 营收报表(revenue_summary)分两段切窗:**计量出账**(`bills_hourly` / `bills_daily_disk`)按账单归属期(hour_start / day),不按扣款入账时间(ledger.created_at);**包周期预付**(`subscriptions.amount_paid`)按收款当日(`subscriptions.created_at`)——它不产生任何账单行,归属期就是收款那一刻,没有延迟入账的问题。`today_revenue` / `yesterday_revenue` / `month_revenue` 是两段之和;`today_prepaid` / `month_prepaid` 单独拆出预付部分,因为一笔包年会在当天造成一个尖峰,看环比时必须能把它剥掉。
- 日终资金核对:钱包侧按 `reconcile_checkpoints` 增量链式校验(逐笔 balance_after 链接 + 游标边界行复核,只扫增量,断链定位到 ledger id);出账 vs 消费两侧都按账单归属期切窗(ledger 经 ref_id 回连)。**包周期是第三条腿**:出账侧取 `SUM(subscriptions.amount_paid)`(按 `created_at` 切窗),消费侧取 `ref_type='subscription'` 的 ledger 经 `ref_id` 回连订阅行,同一窗口;`dangling_consume_refs`(有扣款无出账)同样加了这条腿。**不加就等于 `ref_type='subscription'` 那段钱全无核对** —— 金额写错、写重、写漏都没有任何机制会发现,而它是单笔金额最大的一类流水。
- 策略参数改动即时生效,盘价快照、巡检、扩容全链路跟随。
- 数据盘按日计费的口径见 [disks.md](./disks.md);充值与支付见 [payment.md](./payment.md)。

## 包周期(预付订阅)

`instances.market='subscription'` 的实例在下单时一次性预扣整段周期的费用,**不进小时结算**。
折扣与周期口径的唯一计算点是 `app/core/pricing.py`;下单、续费、到期巡检在
`app/modules/billing/subscriptions.py`;用例在 `apps/api/tests/test_subscriptions.py`。

### 计价口径

- **周期取定长小时**:day=24、week=168、month=720、year=8760(`PERIOD_HOURS`)。到期时刻与定价**同源**
  (`period_hours` 与 `period_delta` 是同一个数):按自然月算到期而按 30 天算价,会造出「二月买的包月比一月便宜三天」
  与「1-31 续费到 2-28 还是 3-3」两类谁也说不清的争议。代价是 31 天的月份平台少收一天 —— 这是定价模型的一部分,
  与数据盘「月按 30 天」同款取舍。
- 应付 = 折后时价 × 计费份数 × 周期小时数;份数仍只经 `core/money.billing_units`(GPU 实例 = 卡数,CPU 实例 = 1 份整机)。
- 折扣按周期分四档,是可在线调整的策略参数(默认 95 / 90 / 80 / 70,范围 50~100);**上界 100 = 不打折,不设加价档** ——
  预付比按量贵在任何定价模型里都讲不通,写错一个数就是全站涨价。
- `period_count` 只收 1~36(`core/pricing.MAX_PERIOD_COUNT`):它是用户可控的乘数,不封顶一次请求就能算出溢出
  `numeric(14,2)` 的应付额。
- **报价三件套由后端保证自洽**:`SubscriptionQuote` 构造时即满足 `discount_amount == list_amount - amount`,
  按 `SubscriptionQuoteOut` 逐行下发(period / period_count / hours / discount_pct / base_hourly / unit_price /
  list_amount / discount_amount / amount)。前端逐行渲染、不自己做乘法:4 位单价 × 8760 小时的舍入差在前端算,
  必然与实扣金额对不齐。
- `instances.price_hourly` 落的是**折后时价**——「这台实例的有效时价」在三种购买模式下含义一致,读的人不用先看
  `market` 再决定这个数是什么意思。续费的重新定价基准另存在 `subscriptions.unit_price`(下单时的 SKU **原价**快照),
  不从折后价反推。

### 下单预扣(与建实例同一个事务)

`POST /api/v1/instances` 带 `market=subscription` + `period`(必填)+ `period_count`(默认 1),在钱包行锁临界区内:
落 `instances(creating, market='subscription')` → 写 `subscriptions` → `wallet.debit(type_='consume',
ref_type='subscription', ref_id=<订阅 id>, allow_negative=False)` → `assert_can_afford` → `instance_events` + `outbox`。

- 扣款 `allow_negative=False`:预付是先付后用,不允许透支买断一个月。余额不足抛 `INSUFFICIENT_BALANCE`,
  实例**不进 creating**。
- 扣完**再过一次燃烧率校验**:此刻钱包余额已是扣后值,校验的正是「付完这一单还撑不撑得住已经在跑的按量资源」。
  两种不足分开报,文案才能指向正确的动作(充值 vs 先关掉一台按量机)。
- `market='on_demand'` 却显式带 `period` / `period_count` 一律 422(不是忽略);SKU 的 `period_enabled=false`
  报 `orchestrator.periodNotEnabled`(见 [catalog.md](./catalog.md))。
- 下单那条订阅行**不带幂等键**:整笔创建的幂等由同事务的 `instances` 行担保。两张表共用一个键会在 24h 幂等窗口
  过后撞车 —— 实例行到期释放键位、订阅行还占着,同一个键第二次用就炸在订阅表的唯一约束上。
  `subscriptions.idempotency_key` 只服务续费。
- 流水:`type='consume'`、`ref_type='subscription'`、`ref_id` 为订阅行 id、remark 形如「<实例名> 包月×1」。
  续费同款,remark 里多一个「续费」。

### 唯一结算跳过点

`orchestrator/queries.py::billing_candidates` 里的 `market != 'subscription'` 是**唯一**的跳过点。
`upsert_hour_bill`、水位线、缺口机制、幂等键一行不动,加一种购买模式不必再碰结算引擎。
不把预付摊成 720 条零元小时账,是因为那会让「重复执行零重复扣款」的幂等证明凭空多一个维度,
而它换不来任何用户看得见的东西。

### 四处配套过滤(漏一处就是事故)

| 位置 | 不排除会怎样 |
|---|---|
| `wallet.assert_can_afford` 的在途燃烧率 | 把余额全买成包月的用户**开不出任何新机**:护栏把他已经付过的钱又扣了一遍 |
| `billing/patrol.py` `_patrol_running` 的停机判据(`burn_per_hour` 与未结算实时估算的集合) | 余额为 0 的包周期用户被欠费巡检**误停机** |
| `billing/patrol.py` `_patrol_frozen_and_arrears_stopped` 的两支 | stopped→frozen 那支会按「余额 ≤ 0」把在保实例提前冻结;frozen 的「充值即解冻」那支会让到期没续费但余额充足的用户被无限解冻,冻结倒计时永远走不到头(等于免费续期) |
| `billing/edge_listener.py` 的尾账 | 离开 running 时再出一次小时尾账,对已预付的用户二次收费 |

**frozen 到期回收那一支刻意不过滤**:回收仍由余额巡检统一做,状态机与回收逻辑只有一处实现。
创建路径上还有一处相关过滤:`_pending_hourly`(creating/starting 的待燃时费)同样排除包周期实例 ——
它跑起来不会再动余额。

### 按量转包周期(`POST /api/v1/instances/{uuid}/subscribe`)

已经在跑的按量实例可以就地转成包周期,不必重建。入参与响应形态同 `/renew`(都是「给这台机器买一段周期」),
**区别只在起点**:转换从**现在**起算,续费从老周期的到期时刻接上。

**顺序是这个功能的全部要害:先结清转换前那段按量账,再翻 `market`。**
`billing_candidates` 按实例**当前**的 market 挑候选 —— 先翻 market 的话,水位线之后那些还没出账的小时
就再也没人管,用户白拿转换前那段算力,而且账面上看不出少了什么。同理,结清必须用**转换前**的按量时价:
那一刻 `instance.price_hourly` 还没被改成折后价。两段各按各的口径收费,既不重复也不留缝。

完整顺序(全部在钱包行锁内、同一个事务):
幂等重放判定 → 前置校验 → `lock_wallet` → running 则 `settle_on_demand_up_to` 结清 → 落订阅行 + 预扣 →
翻 `market='subscription'` → 刷 `price_hourly` 为折后价 → `assert_can_afford`(转换后余额还得撑得住其它在途按量资源)。

- **`settle_on_demand_up_to` 逐小时结**,从「水位线 + 1 小时」到当前自然小时(水位线为空则只结当前小时)。
  逐小时切不是为了好看:`bills_hourly` 的幂等键是 `(instance_id, hour_start)`,一个窗口只能落一行,
  一笔跨小时的账没地方放。落下的行 `detail.source = "convert"`,与整点结算、尾账、缺口重放区分得开。
  `stopped` 实例没有未结的 running 秒数,跳过这一步。
- **结算滞后超过 48 小时(`settlement.MAX_CONVERT_SETTLE_HOURS`)直接拒绝转换**,409 `billing.settlementBehind`。
  结算追不上不是用户的问题,**但也不该由用户免单**:放行等于把那段真实消费永久免掉,而且不留任何痕迹 ——
  账单页少了几小时、缺口表里也没有它,事后无从追溯。拒绝是可恢复的(结算追平后再转即可),免单不是。
- **幂等重放必须最先判**,在「只有按量实例可以转」那条守卫之前。转换成功后 `market` 已经是 subscription,
  重放会撞上守卫拿到一个与真实情况毫不相干的 400;更糟的是它还会先跑一遍结算,而此刻 `price_hourly`
  已是折后价 —— 等于拿包周期的价格去补一笔本该按按量收的账。
- **报价基准是 `instance.price_hourly`**(按量实例上它就是建实例时的 SKU 原价快照),不是 SKU 现价:
  与「变更 SKU 仅影响新实例」同一条口径,用户锁定的价格延续到包周期。这个原价同时落进新订阅行的
  `unit_price`,后续续费继续按它报价。
- 前置条件:`market='on_demand'`;状态 `running` 或 `stopped`(其余 409 `orchestrator.convertNeedsRunningOrStopped`
  —— creating/starting/stopping/releasing 是在途态,翻 market 会和收敛路径抢同一行;frozen 是欠费处置中,
  那笔账得先还清而不是转成预付);SKU `period_enabled` 为真。非按量实例报 `orchestrator.convertNotOnDemand`,
  已在保的实例再转报 `billing.subscriptionAlreadyActive`(放行会开出第二张单、扣两份钱,多半是重复提交没带幂等键)。
- **反向不开**:包周期转不回按量。那等于要求平台把没用完的那段退成余额,与「预付不退款」直接冲突。

### 续费

`POST /api/v1/instances/{uuid}/renew`(见 [orchestrator.md](./orchestrator.md);支持 `Idempotency-Key`,
重放回 200 + `X-Idempotent-Replay`,响应带完整 `quote`):

- **按原价快照重新定价**:基准是 `subscriptions.unit_price`(下单时的 SKU 原价),不是 SKU 现价 ——
  与「变更 SKU 仅影响新实例」同一条口径,涨价不追已购用户。
- 可换周期续(包月转包年),按**新周期**的折扣报价;同事务刷新 `instances.price_hourly`,
  否则列表页会一直显示上一个周期的折后价。
- **起算时刻 = max(老到期时刻, 现在)**:提前续费从老到期时刻起算,不白丢手上剩余的天数(而提前续费恰恰是
  我们希望用户做的事);到期之后才来续则从现在起算,不然会续出一个开局就少几天的周期。
- 老行转 `expired`,新行 `renewed_from_id` 指向老行。**不在原行累加 `expires_at`**:跨月续费时两段周期的金额
  必须落在各自的行上,否则账期归属只能靠流水反推。
- 冻结中的实例续费即解冻(回 `stopped` 并清 `frozen_deadline`),**不自动开机** ——
  自动开机要过容量与调度,失败了反而给出「续费成功但机器没起来」的坏体验。
- 非包周期实例、releasing/released 的实例报 `SUBSCRIPTION_NOT_RENEWABLE`(400)。订阅行查不到与已作废是**两个文案**:
  没有订阅行报 `billing.subscriptionMissing`(压根没买过),`status='cancelled'` 报 `billing.subscriptionCancelled`
  —— 作废只可能是实例被释放过,用户需要知道钱不会回来、也不会因为续费而复活这台机器。`auto-renew` 同一套判据。
- 并发同幂等键由 `UNIQUE(user_id, idempotency_key)` 兜住:撞键方 rollback 后回查胜出方按重放返回
  (rollback 同时撤掉「老行已转 expired」那半步)。

`POST /api/v1/instances/{uuid}/auto-renew` 开关自动续费。**默认关**:自动扣款必须是用户主动打开的,
默认打开等于替用户签了一份可以无限重复的扣款授权。

### 到期链路(`subscription_patrol`,30 分钟一轮,advisory lock,worker `core` 组件)

1. **临期预警** `expires_at - now < period_expire_warn_days` → 短信 + 站内信。去重锚点是
   `subscriptions.warned_for_expiry`(存「已预警到哪个到期时刻」而不是布尔):续费后 `expires_at` 变了,
   新周期自然重新可预警,不需要额外清位;站内信另有按日分桶的 dedup_key,防同一轮反复发。
2. **自动续费**:到期 + `auto_renew` + 余额够 → 扣款、新开一行、通知。**先算价再比余额**,不靠 `debit`
   抛 `INSUFFICIENT_BALANCE` 兜底:抛错时 `renew` 已经把老行改成 expired,捕获异常继续用同一个 session
   就会把那个改动一起提交(老周期凭空作废)。余额不够则发「自动续费失败」通知,落到停机链路,绝不透支。
3. **到期停机**:订阅行转 `expired`;running → `system_stop(reason='subscription_expired')`;
   stopped → 直接冻结。creating/starting/stopping/frozen/releasing 本轮动不了(状态机不允许),
   收敛到终态后由下一轮接手 —— 订阅行已 expired,不会重复计费。
4. **冻结**:单独一趟把「最后一期已到期且已停稳」的实例转 `frozen`(reason `subscription_freeze`),
   写 `frozen_deadline = now + freeze_grace_hours`。分两趟是因为停机是异步的(outbox 删 Pod → reconciler 确认),
   到期那一刻实例还在 stopping,当场冻不了。候选集是「status='expired' 且 expires_at ≤ now」**减去在保集合** ——
   续过费的实例在链上既有 expired 老行也有 active 新行,只看 expired 会把刚续过费的实例送进冻结候选。
5. **回收**:仍由 `balance_patrol` 既有的 frozen 分支做(到期 → releasing → 销毁实例盘)。冻结窗口复用
   `freeze_grace_hours`(与欠费同款):对用户是同一句承诺「停机后 72 小时内还能救回来」,两条链路给不同天数
   只会制造投诉。

到期与欠费用**不同的 `instance_events.reason`**(`subscription_expired` / `subscription_freeze` 对
`arrears_stop` / `arrears_freeze`):在用户时间线上是两件不同的事,合成一个 reason 会让工单无从查起。

### 预付语义的三条硬规矩

- **中途释放不退款。** 实例进入 `releasing` 时由计费边监听器把该实例的 active 订阅转 `cancelled`,
  **不生成任何退款流水**;确需退款走人工 `refund_requests`(见 [payment.md](./payment.md))。挂在迁移监听器上而不是
  `release_instance` 里,是为了一次覆盖用户释放、欠费回收、到期回收、管理端强制回收四条路径 —— 它们最终都经过这条边。
  已 `expired` 的历史行不动:那是账期凭证,改成 cancelled 会让财务口径多出一类「被追溯改写的收入」。
  作废还有一层运维意义 —— 留着 active 会让软准入继续替一台不存在的实例预留容量,也会让到期巡检去停一台已释放的机器。
- **到期不自动转按量。** 到期即停机。自动转按量等于替用户开了一份他没同意过的持续扣款。
- **余额为零不停机。** 整段周期的钱已经收过了;停机判据、燃烧率、冻结与解冻四处都把它排除(见上表)。

### 与其它口径的边界

- 包周期扣款是 `type='consume'`,与小时账一样计入消费;发票口径不变
  (`invoices._period_billable_amount` 只认 `orders.status='paid'` 的充值额)。
- **预付这段钱不是账外收入**:营收报表与日终资金核对都各自加了包周期一条腿(见上「规则与不变量」),
  切窗一律用 `subscriptions.created_at`(下单与扣款同一事务,不存在延迟入账)。管理端总览另有
  `subscriptions_active`(在保订阅数,停机的包月实例仍在保),见 [admin.md](./admin.md)。
- **数据盘不在包周期覆盖范围内**:它仍按日出 `bills_daily_disk`,余额为 0 时照走数据盘自己的欠费链
  (宽限只读 → 冻结 → 清除)。包周期买断的只是实例本身。
- 未到期的包周期实例即使已停机也仍占软准入库存(平台层预留、物理层不预留),口径与理由见
  [orchestrator.md](./orchestrator.md)。

## 竞价(spot)

`instances.market='spot'` 的实例拿折后价,对价是容量紧张时可被平台回收。抢占的选择规则、宽限窗与
两个入口在 [orchestrator.md](./orchestrator.md);这里只写钱的口径。用例在 `apps/api/tests/test_spot.py`。

### 折扣落在 `price_hourly`,结算引擎零改动

竞价时价 = SKU 原价 × `spot_discount_pct` / 100,由 `app/core/pricing.py` 的 `price_for` 单点算出,
建实例时快照进 `instances.price_hourly`。此后它和按量实例完全一样:一样进 `billing_candidates`、
一样出 `bills_hourly`、一样走水位线与尾账、一样计入燃烧率与欠费巡检 —— **结算引擎不知道有竞价这回事**,
`billing_candidates` 的跳过条件仍然只有 `market != 'subscription'` 一条。

`spot_discount_pct` 的范围是 10~90,**上界 90 = 至少打九折**:竞价的对价是「可被回收」,
不打折的竞价档没有存在理由,只会让用户白担一份风险。取值与承载见 [limits.md](./limits.md)。

### 被抢占按实际运行秒数正常结算,不免单

被回收的实例迁 `stopping` 时,由既有的计费边监听器(`billing/edge_listener.py`)照常出尾账,
**按到那一刻为止的实际运行秒数结算**,金额与「用户自己在同一秒关机」逐分相等。

不做免单有两条理由。其一,免单要在结算链上引入第二种判据(「哪几段秒数不算钱」),而结算引擎最贵的
那条性质 ——「重复执行零重复扣款」—— 正建立在「秒数只有一个来源:`instance_events` 的 running 边」之上。
其二,回收发生在用户**已经用掉**那段算力之后,免单等于让「反正会被回收」变成一条比按量更便宜的使用路径。

**宽限窗那段不计费**,但这不是一条额外规则,是「先迁状态、后删 Pod」的顺序自带的结果:状态机在发通知
的那一刻就迁到了 `stopping`,计费边随之落定,后面 Pod 多活的那几十秒本来就在计费窗口之外。
平台单方面决定回收,不该让用户为等待期买单 —— 而实现上不需要为它写任何代码。

### 转按量:一小时一价

`POST /api/v1/instances/{uuid}/to-on-demand` 把 `market` 翻成 `on_demand`、单价还原成
`spec.base_price_hourly`(SKU 原价快照,不从折后价反推 —— 折扣是在线可调的策略)。转换点通常落在一个
自然小时中间,于是那一小时横跨两个单价,而 **`bills_hourly` 一小时只有一行、只有一个 `unit_price`**
(幂等键就是 `(instance_id, hour_start)`),横跨两个价的小时没有第二种表达方式。

定下的口径是**「一小时一价,以结算时的实例单价为准」**:转换把当前整点小时**整体**改按按量价。
`settlement.reprice_current_hour` 在钱包行锁内 `FOR UPDATE` 取当前小时那一行 ——

- **常见路径是这一行还不存在**(整点结算在次小时 :02,尾账要到离开 running 才落),那就什么都不用做,
  之后的整点结算自然按新单价出账;
- 已经出过账(转换前刚好跨过一次整点结算或补差价)且**新价更高**,才按新单价重算 `amount`、改写
  `unit_price`、补扣差价,并在 `detail` 上打 `repriced`;三件事一起做,不拆开。

不改 `unit_price` 的话,后续整点结算会用新价重算秒数、只更新 `amount` 不更新 `unit_price`,留下一行
`unit_price × 秒数 ≠ amount` 的账 —— 那种行没法向用户解释,也没法在对账里自动判对错。

**只在涨价时动这一行,降价整行不动。** 竞价转按量必然涨价(`spot_discount_pct` 上界 90),
降价路径经 `/to-on-demand` 不可达;真出现了(手工改价,或将来有人复用这个原语)就**整行原样留着** ——
`unit_price` 也不改。只改 `unit_price` 不改 `amount` 写出的恰恰是这个函数存在的意义所要避免的那种行,
而按新价往下改、补一笔负数流水更不行:**退款要走人工 `refund_requests`**(双人制衡、登记打款才动钱包,
见 [payment.md](./payment.md)),不该由一个结算原语顺手写出来。用例直接锁降价路径:整行未动、无扣款流水。

转换对用户是一次涨价,所以这一条必须写进转换确认弹窗而不是只写在文档里
(见 [../ui-ux-spec.md](../ui-ux-spec.md) §3.5)。转完再过一次 `assert_can_afford`:
单价涨了,余额撑不住新燃烧率的话转完立刻会被欠费巡检停机。

**反向不开**:按量转不回竞价。给一台已经在跑的实例单方面降价并附上回收风险,用户没有在下单时同意过。
