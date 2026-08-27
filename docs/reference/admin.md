# 管理控制台

`apps/admin`:深色 NOC 风管理端,角色 admin/ops/finance/readonly。视觉规格见 `docs/ui-ux-spec.md`。

## 数据模型

- `admin_users`:username 唯一、password_hash、role(admin/ops/finance/readonly)、status
- `admin_adjustments`:调账单,发起 → 复核 → 生效

## 契约

| 路由/端点 | 角色/鉴权 | 说明 |
|---|---|---|
| `POST /api/admin/v1/auth/login` | 匿名 | 管理端登录,JWT audience 与用户端隔离。安全策略 `admin_mfa_enabled`(默认开)开启时全角色强制 TOTP,响应为挑战票 `{status: mfa_setup | mfa_required, ticket}`(未绑定发绑定票 10 分钟、已绑定发二要素票 5 分钟),正式 token 由 `/auth/mfa/setup/confirm` 或 `/auth/login/mfa` 签发;关闭时密码校验通过即 `{status: ok, access_token, admin}`(已绑定者也不挑战,审计 detail 记 `login_without_mfa`),重新开启即恢复 |
| `POST /api/admin/v1/auth/mfa/setup/begin` `/setup/confirm` `/auth/login/mfa` | 短时票据 | TOTP 绑定与二要素校验;恢复码用后作废 |
| `POST /api/admin/v1/me/mfa/recovery-codes` | 全角色(本人) | 重新生成恢复码,旧码全部作废,明文仅此一次返回;进审计 |
| `GET /api/admin/v1/me` | 全角色 | 路由守卫每次进入/切换受保护路由都调用:角色只信服务端响应,token 失效直跳登录(带 returnTo) |
| `GET /api/admin/v1/overview` | 全角色 | 总览只读聚合:实例分状态 COUNT(非终态)、付费租户 COUNT、`subscriptions_active`(**在保订阅数**,精确 COUNT)、池级 GPU 台账(含非 Ready 段;每池另带 `gpu_spot_used` = 已租那段里属于竞价实例的卡数,**已按 `gpu_used` 截断**,见下)、节点 Ready/Missing 计数 |
| `/` 运营总览 | 全角色 | KPI 行(接 /overview 精确计数,含节点健康卡)+ 「实际超卖率 vs 真实利用率」双曲线(60%/85% 辅助线)+ GPU 池占用条(含未就绪段,已租那段内再分出「其中竞价(可回收)」)+ 告警流 + 收入 KPI(计量出账 + 包周期预付之和,另有 `today_prepaid` / `month_prepaid` 拆出预付部分,口径见 [billing.md](./billing.md))+ 死信卡(重放/忽略都需原因) |
| `/nodes` 节点与 GPU | ops/readonly | 节点表(台账;最近心跳列/排序/池与状态筛选;cordon 需原因)+ 每卡热力网格 + 添加节点 + 注册记录(进行中/全部) |
| `/skus` SKU 与定价 | ops 可写 | SKU 表(容量/已售/实际超卖率列,行内上下架开关)+ 编辑抽屉(改价必填原因+二次确认+影响预览;含 `period_enabled` 开关「包周期」,关掉后该规格只能按量购买、已售出的订阅不受影响;含 `spot_enabled` 开关「竞价档」,**新建时默认关**,开启后该规格可按竞价价售卖、而竞价实例在容量紧张时会被平台回收)+ 从集群资源创建 + 容量预览 |
| `GET /api/admin/v1/skus/{sku_id}/impact` | ops/finance/readonly | 改价影响面(只读):活跃实例数/涉及用户数/占用卡数 |
| `/tenants` 租户与实例 | ops 可写 | 租户表(q 纯数字按 id 精确命中+手机号后缀;冻结文案含影响预览、响应回显 instances_stopped)+ 账单下钻侧滑(游标加载更多)+ 全局实例表(强制停止;**强制回收**,只对 `market='spot'` 且 running 的实例可用;**购买模式**列 = 按量 / 竞价 / 包日 / 包周 / 包月 / 包年);各页 user_id 单元格一律链接到 `/tenants?q=<id>` |
| `POST /api/admin/v1/tenants/{user_id}/freeze` `/unfreeze` | ops | `{reason}` 必填;冻结与 status 变更同事务对该用户全部实例下发停机(经 outbox),响应回显 `instances_stopped`(creating/starting 由巡检收敛,不计入);解冻不自动开机,站内信告知用户手动开机 |
| `POST /api/admin/v1/instances/{uuid}/force-stop` | ops | `{reason}` 必填;仅 running(其余 409),下发关机并结算尾账 |
| `POST /api/admin/v1/instances/{uuid}/preempt` | ops | `{reason}` 必填;**强制回收一台竞价实例**(腾容量)。仅 `market='spot'`(否则 `orchestrator.preemptNotSpot`)且 running(否则 409)。走与自动抢占**同一条**回收路径:同一个 reason `preempted`、同样的宽限窗与短信 / 站内信通知,宽限窗内 Pod 仍在,尾账按实际运行秒数结算 |
| `GET /api/admin/v1/tenants/{user_id}/adjust-context` | ops/finance/readonly | 调账前置上下文(只读,敏感读落审计):掩码手机号/当前余额/近 3 条流水/在跑台数;不存在 → 404 |
| `/finance` 财务对账 | finance 可写 | 日对账卡(diff% >2% 标红)+ 充值流水 + 小时账单 + 调账(发起回显租户上下文,不存在的租户前端禁提交+后端 404;单笔绝对值上限 `ADJUST_MAX_ABS`;复核框列出租户/余额/调账后余额/发起人/原因)+ 异常清单 |
| `/images` `/cluster` `/tickets` `/platform` `/settings` `/audit` | 见各页 | 镜像与预热、集群、工单(读全角色/写 ops·admin)、平台配置(左侧分组导航:安全 / 第三方渠道 / 基础设施 / 站点信息;顶部服务端配置风险告警;安全策略页为开关行)、系统设置(策略参数 / 公告 / 法务文档 / 管理员账号)、审计(limit 选择 + 游标翻页 + 分钟级时间窗) |
| `GET /api/admin/v1/tenants/{user_id}/ledger` `/bills` `/ledger/export` | ops/finance/readonly | 游标分页;与用户端同一函数(`billing.wallet.ledger_page` / `hourly_bills_page`);`/ledger/export` 为流式 CSV,行数硬上限 + 截断标记行 |
| `GET /api/admin/v1/outbox/dead` `POST .../{task_id}/retry` `/discard` | ops(读含 readonly) | 死信列表、重放(需原因)、忽略(需原因) |
| `POST /api/admin/v1/announcements` | ops | 公告群发 |
| `GET/POST /api/admin/v1/admins` `PATCH .../{admin_id}` `POST .../{admin_id}/reset-password` | admin | 管理员账号 CRUD;改角色/停用/重置密码即 token_version+1 |
| `POST /api/admin/v1/me/password` | 全角色 | 自助改密,成功即撤销本人全部在外会话 |
| `GET/PUT /api/admin/v1/policies` | 读 ops/finance/readonly,写 ops | 策略参数在线化,落 `policy_overrides` |
| `GET /api/admin/v1/reports/revenue` `/reports/oversell` | ops/finance/readonly | 收入报表、超卖率报表 |
| `GET /api/admin/v1/finance/anomalies` | finance/readonly | 丢回调/关单/负余额异常清单 |
| `GET /api/admin/v1/orders?status=&order_no=&user_id=&day=` `/orders/export` | finance/readonly | 充值订单列表;export 为流式 CSV,筛选口径一致,行数硬上限 + 截断标记行 |
| `GET /api/admin/v1/finance/settlement-gaps?kind=&reason=&unresolved=` | finance/readonly | 结算缺口列表(游标分页,默认只看未核销;口径见 [billing.md](./billing.md)) |
| `POST /api/admin/v1/finance/settlement-gaps/{gap_id}/replay` `/resolve` | finance | replay 重放该窗口的幂等入账原语(人工触发,不自动改账),成功回写 resolved_at,grace_overlap / 对象已不存在 409;resolve 为人工核销不重放,`{note}` 必填 |
| `POST /api/admin/v1/finance/orders/{order_no}/verify` `/backfill` | finance | 渠道核验与补单 |
| `POST /api/admin/v1/adjustments` `/{adjustment_id}/review` | finance 发起,复核双人 | 调账双管理员复核 |
| `GET /api/admin/v1/refunds` `POST .../{refund_id}/review` `/payout` `/cancel` | finance/admin | 退款审批与登记打款分人:审批不动钱包,登记打款成功才负向核销 |
| `GET /api/admin/v1/invoices` `POST .../{invoice_id}/issue` `/reject` | 读 ops/finance/readonly,写 finance/admin | 人工开票(填发票号)/ 驳回,站内信告知 |
| `GET /api/admin/v1/tickets` `/{ticket_id}` `POST .../reply` `/status` | 读 ops/finance/readonly,写 ops/admin | 工单对话流与状态流转 |
| `GET/POST/PUT/POST /api/admin/v1/legal-docs*` | 读全角色,写仅 admin | 法务文档草稿 → 发布 → 归档;每 (doc_key, locale) 仅一条 published |
| `GET/PUT /api/admin/v1/tenants/{user_id}/quota` | 读全角色,写 ops | 用户级配额覆盖(留空 = 该维走 policy → env 默认链) |
| `GET /api/admin/v1/deletion-requests` `POST .../{request_id}/approve` `/reject` | 读 ops/finance/readonly,执行仅 admin | 账号注销:满冷静期且前置校验全过才可执行 |
| `GET /api/admin/v1/alerts` `/alerts/unread-count` `POST .../{alert_id}/ack` | 读 ops/finance/readonly,写 ops | 告警流与确认闭环 |
| `GET /api/admin/v1/nodes/port-pool` | ops/readonly | SSH 端口池水位 `{total, assigned, blocked}`;blocked = 被集群其它对象撞占(周期复检自动放回),持续上涨要查孤儿端点 |
| `GET /api/admin/v1/audit?actor_type=&actor_id=&q=&since=&until=` `/audit/export` | readonly/ops/finance | 审计检索(actor / 动作前缀 / 时间区间,游标向前翻页);export 为流式 CSV,筛选口径同,行数硬上限 + 截断标记行,导出动作本身落一条检索审计(只记筛选参数) |

## 规则与不变量

- 管理端与用户端 API 物理分离,token 不通用;侧栏菜单按角色过滤(`lib/menu.ts` 与后端 `require_roles` 逐端点对齐),直接输 URL 由后端 403 兜底。
- 管理端登录限流只计失败,四层桶:`admin-login:{ip}:{username}` 5 次/5 分钟与 `admin-login-acct:{username}` 10 次/15 分钟(成功即清零),`admin-login-ip:{ip}` 30 次/时与 `admin-login-acct-daily:{username}` 30 次/日(只计失败、不清零;账号维桶让换 IP 的口令喷洒也逃不掉)。TOTP 校验 `admin-mfa:{admin_id}` 5 次/10 分钟。全部限额汇总见 [limits.md](./limits.md)。
- readonly 全站只读;finance 只在财务区可写。
- 调账复核必须以 `with_for_update` 行锁读取:并发复核的后到者见非 pending 即返 409,保证恰一次入账、ledger 只有一条 adjust。复核人不得是发起人,且必须是调账发起前已创建的账号。
- 调账发起与人工补单均支持 Idempotency-Key(调账落 `(created_by, idempotency_key)` 唯一约束;补单落 `orders.backfill_idempotency_key`,同键重放回当前状态而非 409)。
- 公告群发为分块批量 INSERT(单事务 ⌈N/1000⌉ 条语句),只触达 active 用户。
- 补单为渠道核验制:服务端实时查渠道,已支付且金额一致才入账,不接受人工填写的支付结果。
- 「超卖率 vs 利用率」按池加权聚合(metering 出 per-instance 小时聚合,orchestrator 出实例→池映射,adminapi 组装);无数据的池返 `null`,不用全集群均值代替。
- 管理端所见账单与用户所见同源。
- 策略参数页含包周期五键:`period_discount_day` / `period_discount_week` / `period_discount_month` / `period_discount_year`
  (各 50~100,百分数)与 `period_expire_warn_days`(1~30);改动即时生效,只作用于**之后**的报价 ——
  已售出的订阅按下单时的原价快照续费,不追已购用户。取值与承载见 [limits.md](./limits.md)。
- 竞价两键同在策略参数页:`spot_discount_pct`(10~90,百分数,40 = 4 折)与 `spot_grace_seconds`
  (静态区间 30~600 秒)。**`spot_grace_seconds` 另有跨键上限**(不得超过 `creating_timeout_seconds`
  减去 120 秒调度余量),越界时后端回一条带具体上限的错误文案,**前端原样展示、不自己再算一遍** ——
  上限随 `creating_timeout_seconds` 变,前端算的是它自己那份可能已经过期的副本。理由见 [limits.md](./limits.md)。
  两个值经公开的 `GET /api/v1/policies` 下发给用户端(知情同意里的折扣与通知提前量),改动即时对外生效。
- 财务对账的日对账卡覆盖包周期:出账侧含 `subscriptions.amount_paid`,消费侧含 `ref_type='subscription'` 的流水,
  两侧按同一切窗口径(见 [billing.md](./billing.md))。预付那段钱不进对账就等于全无核对。
- 全局实例表的「购买模式」取 `AdminInstanceOut.market`,到期日取 `AdminInstanceOut.subscription.expires_at` ——
  管理端与用户端**走同一条批量回填路径**(`attach_instance_details`),两端看到的到期时刻恒一致;
  按量实例的 `subscription` 为 null,列里渲染为「—」。
- **`OverviewPoolOut.gpu_spot_used` 是 `gpu_used` 的子段,不是可与它相减的独立口径。**
  它来自 `orchestrator/queries.py::running_spot_gpus_by_pool`(running + `market='spot'` 的 `gpu_count` 按池累加,
  **Python 侧聚合** —— PG 不认参数化的 `spec ->> $1` 在 GROUP BY 里与 SELECT 列相等,实测 GroupingError;
  竞价 running 是小集合,与紧邻的 `running_gpu_share_by_pool` 同一写法)。两个数**单位相同但来源不同**:
  `gpu_used` 来自节点台账,`gpu_spot_used` 来自实例侧 —— **超卖档下后者可能大于前者**,多个共享实例共用
  一张卡时台账只记一张、实例侧却各记一张。所以 service 组装时按 `min(spot, gpu_used)` **截断**:
  不截断就会画出一段比它所在容器还长的堆叠条。前端把它当作「已租段里的一部分」渲染,
  **不要拿 `gpu_used − gpu_spot_used` 当作「非竞价已租」的精确值** —— 截断之后那个差值是下界,不是等式。
- 总览的 `subscriptions_active` 是**在保订阅数,不是实例状态计数**:停机的包月实例只要周期未满就仍在保
  (也仍占库存,见 [orchestrator.md](./orchestrator.md)),拿 `instances_by_status` 里的 running 数替代必然偏小。
- adminapi 端点全部声明响应模型(kind/group/source 用 Literal 出联合类型);前端行类型一律从生成契约再导出,不手写、不强转。
- **「强制回收」与「强制停止」是两个入口,不合并。** 强制停止是处置(违规 / 风控),强制回收是履行竞价
  那份「可能被回收」的约定。同一个 reason 会让用户在自己的事件时间线上分不出是被处置了还是被回收了,
  也会让「被回收过几次」这类竞价可靠性统计算不出来。两者都走 `ReasonAction`(原因必填 → 二次确认 → 审计),
  回收另走抢占那条路径(宽限窗 + 通知),口径见 [orchestrator.md](./orchestrator.md)。
- **原「驱逐重调度」占位已删除,不是兑现。** 实例盘是 TopoLVM 的节点本地 LV,实例重开机要 pin 回原节点 ——
  **「重调度」对任何带实例盘的实例都不成立**,换节点等于丢盘,那是释放而不是驱逐。`preempt.py` 给出的
  能力是「回收一台竞价实例腾容量」(终态 stopped、实例盘保留、用户可自行开机),与「换个节点重跑」
  是两件事。同批删掉 `tenants.evict` / `tenants.evictP1` 两个文案键,在同一位置放「强制回收」。
  占位去留的判据见 [../ui-ux-spec.md](../ui-ux-spec.md) §1 规则 2 与 [../decisions.md](../decisions.md)。
- 高危操作原因必填 → 二次确认 → 审计;色值集中在 `adminColors` token,message 走 `App.useApp()`。
