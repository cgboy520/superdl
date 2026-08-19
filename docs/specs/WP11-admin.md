# WP11 · 管理前端 5 屏

> ✅ 已交付(fork 子代理):5 屏+登录+审计页;闸门全绿;Grafana 为占位卡(生产反代接入),节点 cordon/drain 走集群 Runbook。

规格来源:`docs/ui-ux-spec.md` §4。深色 NOC 风,角色 admin/ops/finance/readonly。

## 屏清单
1. 运营总览 `/`:KPI 行 + 镇店图表「实际超卖率 vs 真实利用率」双曲线(60%/85% 阈值辅助线)+ GPU 池占用条 + 告警流
2. 节点与 GPU `/nodes`:节点表(cordon/drain 需原因)+ 每卡热力网格(util/显存/温度,XID 红框)+ Grafana iframe 区
3. SKU 与定价 `/skus`:SKU 表(含实际超卖率列)+ 编辑抽屉(超卖参数黄色风险提示,显存>1.2 二次确认)+ 库存趋势
4. 租户与实例 `/tenants`:租户表(冻结/解冻需原因)+ 详情侧滑 + 全局实例表(强制停止/仅经济档驱逐,其他档禁用+tooltip)
5. 财务对账 `/finance`:日对账卡(事件计费 vs 指标估算 diff%,>2% 标红)+ 充值流水/小时账单/调账(发起→复核→生效)/异常清单

## 技术约束
- 高危操作:原因必填 → 二次确认 → 审计;调账双管理员复核
- 管理端登录独立(/api/admin/v1/auth),token 与用户端隔离

## 验收
- 超卖率报表数据正确(与 seed 数据可对账)
- readonly 角色全站只读;finance 仅财务区可写(发起调账)
