# SuperDL 文档地图

本目录是平台的工程事实源:架构与硬约束、各模块契约与不变量、UI/UX 规格、文案规范、决策记录。
运维侧的部署说明与 runbook 在 [`deploy/`](../deploy/README.md)。

## 读什么

| 想知道 | 看 |
|---|---|
| 系统怎么拼起来的、哪些是不可破坏的约束 | [architecture.md](./architecture.md) |
| 某个模块的表、端点、角色、规则 | [reference/](./reference/)(按模块一份,结构统一:数据模型 / 契约 / 规则与不变量) |
| 平台对用户与运维施加的配额、限流、保留期 | [reference/limits.md](./reference/limits.md) |
| 某个页面该长什么样、交互怎么走 | [ui-ux-spec.md](./ui-ux-spec.md) |
| 用户可见文案怎么写、哪些词禁用 | [copy-style-guide.md](./copy-style-guide.md) |
| 某个跨模块的决定为什么这么定 | [decisions.md](./decisions.md) |
| 改代码要守的硬性规范、闸门、提交约定 | [../CLAUDE.md](../CLAUDE.md) |
| 生产部署、发布、数据库要求 | [../deploy/README.md](../deploy/README.md) |
| 集群装机、双档路径、北向入口与 Gateway API CRD、token 轮换 | [../deploy/cluster/README.md](../deploy/cluster/README.md) |
| 告警响了先做什么 | [../deploy/cluster/runbooks/](../deploy/cluster/runbooks/README.md) |
| 安全漏洞怎么报 | [../SECURITY.md](../SECURITY.md) |

### 模块参考(`reference/`)

| 文件 | 范围 |
|---|---|
| [account.md](./reference/account.md) | 注册登录、JWT 会话、SSH 公钥、实名、账号注销 |
| [catalog.md](./reference/catalog.md) | SKU、平台镜像目录、近似库存 |
| [orchestrator.md](./reference/orchestrator.md) | 实例状态机、outbox 编排、reconciler、SSH / JupyterLab 接入、K8s 抽象 |
| [services.md](./reference/services.md) | 在线服务聚合根:数据模型与状态派生、部署 / 停止 / 删除契约、域名规则、网关 API Key 鉴权、限流与可用性取舍 |
| [disks.md](./reference/disks.md) | 数据盘生命周期、配额、扩容与日结 |
| [images.md](./reference/images.md) | 镜像目录管理、集群内 P2P 缓存与逐节点预热 |
| [billing.md](./reference/billing.md) | 钱包、账本、小时结算、包周期与竞价口径、欠费回收、策略参数 |
| [payment.md](./reference/payment.md) | 充值单、微信 / 支付宝、回调与查单、退款与发票边界 |
| [metering.md](./reference/metering.md) | Prometheus 代理查询、usage_hourly 聚合、对账 |
| [nodes.md](./reference/nodes.md) | 节点一键加入、节点台账、集群能力探测 |
| [notify.md](./reference/notify.md) | 站内信、短信、Alertmanager webhook、余额预警 |
| [tickets.md](./reference/tickets.md) | 工单对话流与滞留巡检 |
| [legal.md](./reference/legal.md) | 法务文档版本流与注册同意存证 |
| [platform-config.md](./reference/platform-config.md) | 管理端在线配置中心与加密存储 |
| [security.md](./reference/security.md) | 生产启动校验、限流分层、租户隔离、已接受取舍 |
| [observability.md](./reference/observability.md) | 指标、日志、探针、告警规则、管理端自绘监控 |
| [i18n.md](./reference/i18n.md) | 文案与国际化机制及闸门 |
| [limits.md](./reference/limits.md) | 配额、限流、时钟与保留期汇总 |
| [admin.md](./reference/admin.md) | 管理控制台契约与角色边界 |
| [web.md](./reference/web.md) | 用户控制台路由与前端不变量 |

## 维护约定

- **文档随代码同一提交**:改了端点、表、角色、默认值、巡检周期、命令或流程,同一提交里更新对应的 reference / runbook / README。没有单独的「文档补齐」阶段。
- **只写事实,不写流水账**:reference 记「现在是什么」与「不可破坏的约束」;做过什么、哪一轮改的,留给 git 历史。不写评审编号、变更史与日期,跨模块决策写进 [decisions.md](./decisions.md)。
- **数字要能在代码里找到**:限额、周期、默认值写进文档时给出承载它的文件或配置键(如 `policies.py`、`SUPERDL_*`),读者能核对,改动时能搜到。
- **引用必须存在**:相对链接与反引号里的仓库路径由 `python3 scripts/check-docs-links.py` 检查(告警规则里 `runbook_url` 指向的文件与锚点同款检查),CI 的 `docs` job 同款。
- **模块参考的固定结构**:`数据模型` → `契约`(端点 / 角色 / 说明表)→ `规则与不变量`;无自有表的模块省去 `数据模型`,`limits.md` 是跨模块汇总不套此结构。新模块照此新建一份,并在上表登记。
- **标点**:正文用半角标点(`,` `;` `()`),引用名词用「」。
