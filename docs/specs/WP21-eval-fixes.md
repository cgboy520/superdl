# WP21 · 评估修复

横切修正批,无新功能。本文记录由此生效的行为与约束,细节以代码为准。

## 资金与支付

- 调账复核以 `with_for_update` 行锁读取:并发复核后到者见非 pending → 409;并发双复核恰一次入账、ledger 仅一条 adjust,有用例锁定。
- 支付宝回调应答为纯文本 `success`(渠道对 JSON 应答判失败并反复重试)。
- 关单后到达、验签有效且金额一致的成功回调自动入账(与人工补单同等校验;failed 单、金额不符仍拒);指标 `superdl_payment_closed_order_rescued_total` 非零说明本地关单 TTL 与渠道过期不同步,配套告警。
- 下单向渠道传过期时间(微信 `time_expire` RFC3339 / 支付宝 `timeout_express` 分钟),与本地关单同步,不留资金悬置窗口。

## 覆盖率测量

- coverage 必须声明 `concurrency = ["greenlet", "thread"]`:SQLAlchemy async 把同步核心跑在 greenlet 里,缺声明会丢失 `await` 之后的行,系统性低估。`coverage.json` 不入库(.gitignore)。

## 监控与部署

- 监控抓取:`deploy/app/k8s/08-monitoring.yaml` = API ServiceMonitor(Bearer `SUPERDL_METRICS_TOKEN`)+ worker PodMonitor;worker 进程自起 `/metrics`(默认 9000,`SUPERDL_WORKER_METRICS_PORT`)——结算失败/死信/泄漏回收等指标在 worker 进程内,只抓 API 看不到。
- WorkerDown 告警按心跳 Gauge 判定:带 label 的 Counter 首次 inc 前无序列,`absent()` 会常驻误报。
- `deploy/cluster/rke2/audit-policy.yaml` 必须随 server-config 一并拷贝到位(server-config 引用它,缺失则 apiserver 起不来)。
- Secret 模板在 `deploy/app/secrets.example.yaml`,不放整目录 apply 路径(防占位值被 apply)。
- Alertmanager critical 走双通道(webhook + 外部 SMTP):防「API/DB 挂时 webhook 恰好送不出去」的自引用死结;SMTP 参数为上线前人工事项。
- Grafana 管理口令走 Secret;备份失败/超期有告警(kube-state-metrics);备份 Job 用 initContainer pg_dump + 官方 aws-cli 镜像,运行期不装包;前端镜像 `nginx-unprivileged` 8080 + 完整 securityContext;两个构建上下文带 `.dockerignore`(防 .git/.env 进镜像层)。

## 报表正确性

- 管理端「超卖率 vs 利用率」按池加权聚合:metering 出 per-instance 小时聚合(sum, n),orchestrator 出实例→池映射,adminapi 组装(模块边界不破);无数据池返 `null`,不用全集群均值冒充。

## 契约与前端

- adminapi 端点全部有响应模型(kind/group/source 用 Literal 出联合类型);admin 行类型一律从生成契约再导出,不手写、不强转。
- web:market/storage/settings/实例详情统一接 QueryState 错误态;详情页加载失败为错误横幅 + 重试,不整页白屏。
- 盘费日估算走 `ui.diskDailyEstimate(priceGbMonth, gb)`(BigInt 万分位,HALF_UP 到分),前端不做 float 估算。
- admin 侧栏菜单按角色过滤(`lib/menu.ts` 与后端 require_roles 逐端点对齐;直接输 URL 由后端 403 兜底);message 走 `App.useApp()`;有菜单过滤/角色写权限单测。
- Jupyter `window.open` 带 `noopener,noreferrer`。
- failed 徽标文案为中性「已失败」(创建失败与运行故障共用该状态,精确原因在事件时间线)。
- 行内添加 SSH 公钥后自动勾选;充值轮询到终态(paid/closed/failed)即停;创建失败后重新生成 Idempotency-Key(改参重提不撞旧键);echarts 按需注册收口在两端 `components/EChart.tsx`;admin 色值集中 `adminColors` token。

## 明示不做

- select-then-insert 幂等竞态(注册/钱包创建等)表现为 500:唯一约束兜底正确,仅体验问题,后置。
- 进程内限流多副本失效 / SSH 指纹全局唯一可探测 / advisory lock 会话 idle-in-transaction:已知项,随生产化压测批处理。
- 查单 poller 不扫 closed 单:time_expire + 关单回调自动入账后,残余窗口仅「过期前最后 <2min 支付且回调丢失」,异常清单 + 人工补单兜底足够。
