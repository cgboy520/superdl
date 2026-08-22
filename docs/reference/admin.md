# 管理控制台

`apps/admin`:深色 NOC 风管理端,角色 admin/ops/finance/readonly。视觉规格见 `docs/ui-ux-spec.md`。

## 数据模型

- `admin_users`:username 唯一、password_hash、role(admin/ops/finance/readonly)、status
- `admin_adjustments`:调账单,发起 → 复核 → 生效

## 契约

| 路由/端点 | 角色/鉴权 | 说明 |
|---|---|---|
| `POST /api/admin/v1/auth/login` | 匿名 | 管理端登录,JWT audience 与用户端隔离 |
| `GET /api/admin/v1/me` | 全角色 | 路由守卫每次进入/切换受保护路由都调用:角色只信服务端响应,token 失效直跳登录(带 returnTo) |
| `GET /api/admin/v1/overview` | 全角色 | 总览只读聚合:实例分状态 COUNT(非终态)、付费租户 COUNT、池级 GPU 台账(含非 Ready 段)、节点 Ready/Missing 计数 |
| `/` 运营总览 | 全角色 | KPI 行(接 /overview 精确计数,含节点健康卡)+ 「实际超卖率 vs 真实利用率」双曲线(60%/85% 辅助线)+ GPU 池占用条(含未就绪段)+ 告警流 + 收入 KPI + 死信卡(重放/忽略都需原因) |
| `/nodes` 节点与 GPU | ops/readonly | 节点表(台账;最近心跳列/排序/池与状态筛选;cordon 需原因)+ 每卡热力网格 + 添加节点 + 注册记录(进行中/全部) |
| `/skus` SKU 与定价 | ops 可写 | SKU 表(容量/已售/实际超卖率列,行内上下架开关)+ 编辑抽屉(改价必填原因+二次确认+影响预览)+ 从集群资源创建 + 容量预览 |
| `GET /api/admin/v1/skus/{sku_id}/impact` | ops/finance/readonly | 改价影响面(只读):活跃实例数/涉及用户数/占用卡数 |
| `/tenants` 租户与实例 | ops 可写 | 租户表(q 纯数字按 id 精确命中+手机号后缀;冻结文案含影响预览、响应回显 instances_stopped)+ 账单下钻侧滑(游标加载更多)+ 全局实例表(强制停止;驱逐重调度为占位按钮,未开放);各页 user_id 单元格一律链接到 `/tenants?q=<id>` |
| `GET /api/admin/v1/tenants/{user_id}/adjust-context` | ops/finance/readonly | 调账前置上下文(只读,敏感读落审计):掩码手机号/当前余额/近 3 条流水/在跑台数;不存在 → 404 |
| `/finance` 财务对账 | finance 可写 | 日对账卡(diff% >2% 标红)+ 充值流水 + 小时账单 + 调账(发起回显租户上下文,不存在的租户前端禁提交+后端 404;单笔绝对值上限 `ADJUST_MAX_ABS`;复核框列出租户/余额/调账后余额/发起人/原因)+ 异常清单 |
| `/images` `/cluster` `/platform` `/settings` `/audit` | 见各页 | 镜像与预热、集群、平台配置、系统设置(策略参数 / 公告 / 管理员账号)、审计(limit 选择 + 游标翻页 + 分钟级时间窗) |
| `GET /api/admin/v1/tenants/{user_id}/ledger` `/bills` | ops/finance/readonly | 游标分页;与用户端同一函数(`billing.wallet.ledger_page` / `hourly_bills_page`) |
| `GET /api/admin/v1/outbox/dead` `POST .../{task_id}/retry` `/discard` | ops(读含 readonly) | 死信列表、重放(需原因)、忽略(需原因) |
| `POST /api/admin/v1/announcements` | ops | 公告群发 |
| `GET/POST /api/admin/v1/admins` `PATCH .../{admin_id}` `POST .../{admin_id}/reset-password` | admin | 管理员账号 CRUD;改角色/停用/重置密码即 token_version+1 |
| `POST /api/admin/v1/me/password` | 全角色 | 自助改密,成功即撤销本人全部在外会话 |
| `GET/PUT /api/admin/v1/policies` | 读 ops/finance/readonly,写 ops | 策略参数在线化,落 `policy_overrides` |
| `GET /api/admin/v1/reports/revenue` `/reports/oversell` | ops/finance/readonly | 收入报表、超卖率报表 |
| `GET /api/admin/v1/finance/anomalies` | finance/readonly | 丢回调/关单/负余额异常清单 |
| `POST /api/admin/v1/finance/orders/{order_no}/verify` `/backfill` | finance | 渠道核验与补单 |
| `POST /api/admin/v1/adjustments` `/{adjustment_id}/review` | finance 发起,复核双人 | 调账双管理员复核 |

## 规则与不变量

- 管理端与用户端 API 物理分离,token 不通用;侧栏菜单按角色过滤(`lib/menu.ts` 与后端 `require_roles` 逐端点对齐),直接输 URL 由后端 403 兜底。
- 管理端登录限流只计失败:账号桶 `admin-login:{ip}:{username}` 5 次/5 分钟(成功清零),纯 IP 桶 `admin-login-ip:{ip}` 30 次/时(只计失败、不清零,防遍历用户名的口令喷洒;阈值按办公网 NAT 出口多管理员放宽)。
- readonly 全站只读;finance 只在财务区可写。
- 调账复核必须以 `with_for_update` 行锁读取:并发复核的后到者见非 pending 即返 409,保证恰一次入账、ledger 只有一条 adjust。复核人不得是发起人,且必须是调账发起前已创建的账号(防自建第二账号绕双人制衡)。
- 调账发起与人工补单均支持 Idempotency-Key(调账落 `(created_by, idempotency_key)` 唯一约束;补单落 `orders.backfill_idempotency_key`,同键重放回当前状态而非 409)。
- 公告群发为分块批量 INSERT(单事务 ⌈N/1000⌉ 条语句),只触达 active 用户。
- 补单为渠道核验制:服务端实时查渠道,已支付且金额一致才入账,不接受人工填写的支付结果。
- 「超卖率 vs 利用率」按池加权聚合(metering 出 per-instance 小时聚合,orchestrator 出实例→池映射,adminapi 组装);无数据的池返 `null`,不用全集群均值冒充。
- 管理端所见账单与用户所见同源,避免两套查询对不上。
- adminapi 端点全部声明响应模型(kind/group/source 用 Literal 出联合类型);前端行类型一律从生成契约再导出,不手写、不强转。
- 高危操作原因必填 → 二次确认 → 审计;色值集中在 `adminColors` token,message 走 `App.useApp()`。
