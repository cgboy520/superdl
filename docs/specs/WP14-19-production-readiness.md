# WP14~WP19 · 生产就绪加固(2026-08-19)

来源:《生产就绪评估》三路全仓调研(mock 审计 / 就绪度 / 前端 UX)。
原则:凭据/实机之外的问题全部代码闭环;外部依赖把 seam 做完,联调即启用。

## WP14 后端安全(P0)

- 验证码防爆破:`sms_codes.attempts` 失败计次 ≥5 作废(DB 持久化);验证码登录/注册路径限流;发码 IP(20/h)+ 手机号(10/日)维度限流
- `Settings` prod fail-fast 校验:弱 JWT/mock 短信/fake 编排/mock 支付/本地库/localhost CORS/占位域名/缺 token 启动即拒(`app/core/config.py`)
- 短信渠道 seam:`app/core/sms.py`(Protocol+工厂)+ 阿里云 dysmsapi(RPC 签名,含签名快照测试);发送失败作废落库码返 502;通知短信尽力而为
- 安全响应头(纯 ASGI)/`/metrics` Bearer 鉴权/审计端点角色修复
- refresh 轮换与撤销:`used_refresh_tokens` 一次性消费,重放全撤;`users.token_version` 撤销闸,冻结即失效

## WP15 前端 UX(P0)

- 401 静默续期重放(mutator single-flight)+ 会话失效跳登录带回跳;两端 Error Boundary + 404
- 查询错误不伪装成数据:`moneyOr`/表格错误态/页级横幅(`components/QueryState.tsx`);金额未就绪显示 —
- 创建失败闭环:failed 行内原因/未扣费说明(修正与计费事实不符的"已退款"文案)/重新创建;NO_CAPACITY 引导
- 杂修:表格 scroll-x、响应式断点、Drawer width、月份本地时区、余额 BigInt 比较(`compareAmounts`)、SSH 锚点、admin `App.useApp()`、aria/键盘可达、admin 60s 轮询

## WP16 支付闭环 + 运营刚需(P0)

- `PaymentChannel.query_order` seam(微信/支付宝/Mock 渠道侧账本);查单 poller 每 2 分钟收敛丢回调(advisory lock 1007)
- `AlipayChannel` 当面付完整实现(precreate + RSA2 验签 + 查单;待商户凭据联调)
- 管理端:订单核验/补单(**渠道核验制**——服务端实时查渠道,已支付且金额一致才入账)/异常清单(丢回调/关单/负余额);outbox 死信 列表/重放/忽略;公告群发;策略参数在线化(`policy_overrides` 表,GET /policies 读生效值,盘价快照/巡检/扩容全链路跟随);收入报表(今日/本月/新注册)
- 管理端 UI:财务异常清单 Tab、总览收入 KPI+死信卡、系统设置屏(策略+公告)

## WP17 可观测性与运维(P0/P1)

- 业务指标(`app/core/metrics.py`):死信/结算失败/泄漏 Pod/回调金额不符/查单收敛/HTTP 直方图(完整路由模板);kps.yaml 平台告警 6 条
- request-id 贯穿(contextvars+响应头);未捕获异常统一 500 错误体 + Sentry seam(可选依赖)
- worker SIGTERM 优雅停机 + 心跳文件(K8s exec 探针);`/readyz` 探 DB;每日数据保洁(验证码/refresh/outbox 已完成/审计超保留期)
- CI 绿化:存量 ruff 清零;import-linter 契约改 `allow_indirect_imports`(语义=只禁直接越界),修复 adminapi 直查 metering 表的真实违规 —— 8/8 契约通过

## WP18 生产部署物 + CI/CD(P0)

- `deploy/app/k8s/`:API/worker Deployment(探针/PDB/非 root/只读根)、前端 nginx 镜像与清单、Ingress TLS、cert-manager 泛域名证书(DNS-01)、平台 RBAC、迁移 Job、PG 每日备份 CronJob
- helmfile 补 cert-manager/ingress-nginx(default-ssl-certificate 承载租户 Jupyter TLS)
- `runbooks/pg-backup-restore.md`(RPO 分层 + 恢复演练验收 + 资金一致性核验)
- release 流水线(三镜像 + Trivy HIGH/CRITICAL 阻断);Playwright e2e 进 CI

## WP19 编排真实化 + 合规(P1)

- RealOrchestrator 五空洞:ResourceQuota 兜底、Egress 隔离(禁内网/元数据段)、每租户 JuiceFS PVC、`disk.wipe` 真实擦除 Job(幂等+退避)、HAMi/MIG 感知库存 + list_nodes O(n) 修复;Protocol/Fake 同步 `wipe_disk`
- 每用户配额(实例数/GPU 数,config 可调);Jupyter Ingress TLS
- 合规:`/legal/terms` `/legal/privacy`(模板,**待法务审定**)+ 注册勾选(前后端强校验)+ 备案号 `VITE_ICP_NUMBER` 配置化
- 实名认证 seam:三要素 provider(mock 可跑通)、身份证号仅存脱敏、`verification_status` 迁移、充值前强制开关(`real_name_required_for_recharge`)

## 明确后置(有 roadmap 背书)

渠道原路退款(资金退回走调账双复核;原路退待凭据联调期)、admin 全列表服务端分页、patrol N+1 收敛、租户详情下钻、连接 Tab host/port 分列与 Windows 指引、移动端卡片流、i18n、Web 终端、Grafana iframe(依赖集群)。

## 验收

后端 202 pytest 全绿(billing 覆盖率闸门保持);pyright/ruff/import-linter/alembic check 全绿;
前端 lint/typecheck/vitest/build 全绿;e2e 冒烟(含注册勾选)全绿。
外部依赖清单与上线时间线见《生产就绪评估》报告。
