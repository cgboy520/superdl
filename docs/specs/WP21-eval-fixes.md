# WP21 — 评估修复(2026-08-19 全面评估销账)

依据 2026-08-19 六路评审 + 全链路实测的评估报告(综合 B+),对确认问题逐条修复。
本 WP 无新功能;目标是把「可上预发」推进到「生产一轮加固完成」。

## P0(全部完成)

| # | 问题 | 修复 |
|---|---|---|
| 1 | 调账复核并发双入账:`adminapi/service.py` 复核读取无行锁,两并发 approve 都见 pending → 双倍入账 | `session.get(..., with_for_update=True)`;后到者等锁醒来见非 pending → 409。新增并发复核单入账用例(gather 双复核,断言恰一次成功 + ledger 仅 1 条 adjust) |
| 2 | billing 覆盖率闸红灯 82.8% < 90% | 根因是测量而非测试:SQLAlchemy async 在 greenlet 里跑同步核心,coverage 未声明 `concurrency=["greenlet","thread"]` 时丢失所有 `await` 之后的行 → 系统性低估。修正声明后真实 92.8%;再补支付路径 21 项用例后 **96.3%**。WP4 期误提交的 `coverage.json` 出库并入 `.gitignore` |
| 3 | 部署三断线 | ① 告警抓取:新增 `deploy/app/k8s/08-monitoring.yaml`(API ServiceMonitor 带 SUPERDL_METRICS_TOKEN Bearer + worker PodMonitor);worker 起 `/metrics`(默认 9000,`SUPERDL_WORKER_METRICS_PORT`)—— 结算失败/死信/泄漏回收指标都在 worker 进程内,此前即使抓 API 也是盲的。WorkerDown 告警改心跳 Gauge 判定(带 label 的 Counter 首次 inc 前无序列,`absent()` 常驻误报)。② `deploy/cluster/rke2/audit-policy.yaml` 落盘(此前 server-config 引用不存在的文件,apiserver 起不来),README 补拷贝步骤。③ CHANGE_ME Secret 模板(api + pg-backup)迁出整目录 apply 路径 → `deploy/app/secrets.example.yaml` |
| 4 | 管理端镇店图利用率假象:全集群均值被画成每池曲线 | 按池加权聚合:metering 出 per-instance 小时聚合(sum, n),orchestrator 出实例→池映射,adminapi 组装(模块边界不破);无数据池返 `null` 不冒充。用例断言 hami=45.0 且 kata/mig=null |

## P1(全部完成)

- **支付**:支付宝回调应答改纯文本 `success`(原 JSON 会被渠道判失败重试 8 次);关单后验签有效且金额一致的成功回调**自动入账**(与人工补单同等校验;failed 单、金额不符仍拒;新指标 `superdl_payment_closed_order_rescued_total` + 告警,非零说明本地 TTL 与渠道过期不同步);下单向渠道传过期时间(微信 `time_expire` RFC3339 / 支付宝 `timeout_express` 分钟)与本地关单同步 —— 资金悬置窗口关闭。
- **契约**:adminapi 22 个裸 dict 端点补响应模型(kind/group/source 用 Literal 出联合类型),OpenAPI 重导出 + orval 再生成;admin `api.ts` 手写行类型与 14 处 `as unknown as` 全删,行类型改为生成契约再导出。
- **web 错误态**:market/storage/settings/实例详情接 QueryState;详情页加载失败从整页白屏改为错误横幅 + 重试;事件/账单 Tab 同步接错误态。
- **金额纪律**:新增 `ui.diskDailyEstimate(priceGbMonth, gb)`(BigInt 万分位,HALF_UP 到分,带用例),替换 create 页与 storage 页三处 float 估算。
- **admin**:侧栏菜单按角色过滤(`lib/menu.ts` 与后端 require_roles 逐端点对齐,修跨角色 403 假空态;直接输 URL 仍由后端 403 兜底);`skus.tsx` 静态 message 改 `App.useApp()`。
- **安全**:Jupyter `window.open` 补 `noopener,noreferrer`(两处)。
- **部署加固批**:Alertmanager critical 双通道(外部 SMTP 收件,防「API/DB 挂时 webhook 恰好送不出去」自引用死结;SMTP 参数为上线前人工事项);Grafana 管理口令走 Secret(废默认 prom-operator);备份失败/超期告警(kube-state-metrics);备份 Job 弃运行期 `apt-get`(initContainer pg_dump + 官方 aws-cli 镜像);前端切 `nginx-unprivileged` 8080 + 完整 securityContext;两个构建上下文补 `.dockerignore`(防 .git/.env 进镜像层)。

## P2(完成)

- failed 徽标改中性「已失败」(创建失败/运行故障共用状态,精确原因在事件时间线;不再把运行故障说成创建失败)。
- 390px 顶栏折行:`.topbar-nav-center` 布局收敛到 CSS 类(内联 display 压过媒体查询是根因)。
- 行内添加 SSH 公钥后自动勾选(`useAddSshKey` onSuccess 透传新建 key)。
- 充值轮询到终态(paid/closed/failed)即停(QueryOpts 支持函数式 refetchInterval)。
- 创建失败后重生成 Idempotency-Key(改参重提不撞旧键)。
- echarts 按需注册(两端 `components/EChart.tsx` 收口;web chunk 1.1MB→559KB minified)。
- admin 硬编码色值 8 文件收敛进 `adminColors` token(扩 textSecondary/textMuted/gridLine/chartNeutral/divider/positive/negative/critical)。
- admin 单测 0→8(菜单过滤/角色写权限),移除 `--passWithNoTests`。

## 明示不做(维持评估时判断)

- select-then-insert 幂等竞态(注册/钱包创建等)表现为 500:唯一约束兜底正确,仅体验问题,后置。
- 进程内限流多副本失效 / SSH 指纹全局唯一可探测 / advisory lock 会话 idle-in-transaction:P2 已知项,随生产化压测批处理。
- 查单 poller 不扫 closed 单:time_expire + 关单回调自动入账后,残余窗口仅「过期前最后 <2min 支付且回调丢失」,异常清单 + 人工补单兜底足够。
- 节点屏 cordon/drain 实操、Grafana iframe URL 配置通道:依赖实机,归 W1 验证清单。

## 验收基线(本 WP 完成时)

- 后端 237 用例全绿(评估时 216,+21),billing 覆盖率 96.3%(闸 90%)。
- ruff / pyright / import-linter(8 契约)/ alembic check 全绿;OpenAPI 与 orval 产物零 diff。
- 前端 eslint / tsc / vitest(ui 23 + web 5 + admin 8)/ build 全绿。
