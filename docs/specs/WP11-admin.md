# WP11 · 管理前端

规格来源:`docs/ui-ux-spec.md` §4。深色 NOC 风,角色 admin/ops/finance/readonly。

## 屏清单
1. 运营总览 `/`:KPI 行 + 镇店图表「实际超卖率 vs 真实利用率」双曲线(60%/85% 阈值辅助线,按池加权聚合)+ GPU 池占用条 + 告警流
2. 节点与 GPU `/nodes`:节点表(数据源为节点台账,见 WP26;cordon/uncordon 需原因,drain 后置)+ 每卡热力网格(util/显存/温度,XID 红点)+ 添加节点(见 WP23);Grafana 仅外链(见 WP25)
3. SKU 与定价 `/skus`:SKU 表(容量/已售/实际超卖率列)+ 编辑抽屉(超卖参数风险提示,显存>1.2 二次确认;从集群资源创建与容量预览见 WP26)+ 库存趋势
4. 租户与实例 `/tenants`:租户表(冻结/解冻需原因)+ 详情侧滑 + 全局实例表(强制停止/仅经济档驱逐,其他档禁用+tooltip)
5. 财务对账 `/finance`:日对账卡(事件计费 vs 指标估算 diff%,>2% 标红)+ 充值流水/小时账单/调账(发起→复核→生效)/异常清单

另:镜像与预热 `/images`(WP22)、集群 `/cluster`(WP27)、平台配置 `/platform`(WP20)、系统设置 `/settings`、审计 `/audit`。

## 技术约束
- 高危操作:原因必填 → 二次确认 → 审计;调账双管理员复核(复核行锁防并发双入账,见 WP21-eval-fixes.md)
- 管理端登录独立(/api/admin/v1/auth),token 与用户端隔离;侧栏菜单按角色过滤,后端 403 兜底

## 验收
- 超卖率报表数据正确(与 seed 数据可对账)
- readonly 角色全站只读;finance 仅财务区可写(发起调账)

## 租户账单下钻(现状)

- `GET /api/admin/v1/tenants/{user_id}/ledger`、`GET /api/admin/v1/tenants/{user_id}/bills`
  (ops/finance/readonly 可读,游标分页)。实现与用户端同一函数(`billing.wallet.ledger_page` /
  `hourly_bills_page`),管理端所见与用户所见同源,账单争议不会因两套查询而对不上。
- 前端在 `/tenants` 的租户表点行(或「账单」按钮)开侧滑:余额/累计消费概览 + 小时账单 + 资金流水两个 Tab。
  这同时兑现了 ui-ux-spec §4.5 的「租户详情侧滑」。
