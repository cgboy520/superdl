# 管理控制台

`apps/admin`:深色 NOC 风管理端,角色 admin/ops/finance/readonly。视觉规格见 `docs/ui-ux-spec.md`。

## 数据模型

- `admin_users`:username 唯一、password_hash、role(admin/ops/finance/readonly)、status
- `admin_adjustments`:调账单,发起 → 复核 → 生效

## 契约

| 路由/端点 | 角色/鉴权 | 说明 |
|---|---|---|
| `POST /api/admin/v1/auth/login` | 匿名 | 管理端登录,JWT audience 与用户端隔离 |
| `/` 运营总览 | 全角色 | KPI 行 + 「实际超卖率 vs 真实利用率」双曲线(60%/85% 辅助线)+ GPU 池占用条 + 告警流 + 收入 KPI + 死信卡 |
| `/nodes` 节点与 GPU | ops/readonly | 节点表(数据源为台账,cordon/uncordon 需原因)+ 每卡热力网格 + 添加节点 |
| `/skus` SKU 与定价 | ops 可写 | SKU 表(容量/已售/实际超卖率列,行内上下架开关)+ 编辑抽屉(改价必填原因)+ 从集群资源创建 + 容量预览 |
| `/tenants` 租户与实例 | ops 可写 | 租户表(冻结/解冻需原因)+ 账单下钻侧滑 + 全局实例表(强制停止;驱逐重调度为占位按钮,未开放) |
| `/finance` 财务对账 | finance 可写 | 日对账卡(diff% >2% 标红)+ 充值流水 + 小时账单 + 调账 + 异常清单 |
| `/images` `/cluster` `/platform` `/settings` `/audit` | 见各页 | 镜像与预热、集群、平台配置、系统设置(策略 + 公告)、审计 |
| `GET /api/admin/v1/tenants/{user_id}/ledger` `/bills` | ops/finance/readonly | 游标分页;与用户端同一函数(`billing.wallet.ledger_page` / `hourly_bills_page`) |
| `GET /api/admin/v1/outbox/dead` `POST .../{task_id}/retry` `/discard` | ops(读含 readonly) | 死信列表、重放、忽略 |
| `POST /api/admin/v1/announcements` | ops | 公告群发 |
| `GET/PUT /api/admin/v1/policies` | 读 ops/finance/readonly,写 ops | 策略参数在线化,落 `policy_overrides` |
| `GET /api/admin/v1/reports/revenue` `/reports/oversell` | ops/finance/readonly | 收入报表、超卖率报表 |
| `GET /api/admin/v1/finance/anomalies` | finance/readonly | 丢回调/关单/负余额异常清单 |
| `POST /api/admin/v1/finance/orders/{order_no}/verify` `/backfill` | finance | 渠道核验与补单 |
| `POST /api/admin/v1/adjustments` `/{adjustment_id}/review` | finance 发起,复核双人 | 调账双管理员复核 |

## 规则与不变量

- 管理端与用户端 API 物理分离,token 不通用;侧栏菜单按角色过滤(`lib/menu.ts` 与后端 `require_roles` 逐端点对齐),直接输 URL 由后端 403 兜底。
- readonly 全站只读;finance 只在财务区可写。
- 调账复核必须以 `with_for_update` 行锁读取:并发复核的后到者见非 pending 即返 409,保证恰一次入账、ledger 只有一条 adjust。
- 补单为渠道核验制:服务端实时查渠道,已支付且金额一致才入账,不接受人工填写的支付结果。
- 「超卖率 vs 利用率」按池加权聚合(metering 出 per-instance 小时聚合,orchestrator 出实例→池映射,adminapi 组装);无数据的池返 `null`,不用全集群均值冒充。
- 管理端所见账单与用户所见同源,避免两套查询对不上。
- adminapi 端点全部声明响应模型(kind/group/source 用 Literal 出联合类型);前端行类型一律从生成契约再导出,不手写、不强转。
- 高危操作原因必填 → 二次确认 → 审计;色值集中在 `adminColors` token,message 走 `App.useApp()`。
