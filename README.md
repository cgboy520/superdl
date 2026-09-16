# SuperDL

GPU 算力租赁平台:租户按量租用整卡 / MIG / 共享 GPU 容器实例(SSH + JupyterLab),按秒累计、按小时出账;运营侧有节点一键加入、SKU 与库存、财务对账、工单与告警闭环。
后端 FastAPI 模块化单体 + PostgreSQL 18,前端 React 19 + antd 6 双控制台,平台层 RKE2 / k3s + Kata / HAMi。

状态:未发布;代码侧功能齐备,上线依赖实机验证与资质联调。

## 文档地图

完整索引 [docs/README.md](docs/README.md)。常用:

- [docs/architecture.md](docs/architecture.md) —— 架构、模块边界、数据模型、核心流程与硬约束
- [docs/reference/](docs/README.md) —— 各模块契约与不变量
- [docs/decisions.md](docs/decisions.md) —— 跨模块决策与约束
- [CLAUDE.md](CLAUDE.md) —— 工程规范、闸门、提交约定;上手与 PR 工作流见 [CONTRIBUTING.md](CONTRIBUTING.md)
- [deploy/README.md](deploy/README.md) —— 部署、发布、集群装机与 runbook
- [SECURITY.md](SECURITY.md) —— 报告安全漏洞

## 快速开始

前置:Docker、uv、Python 3.13、Node 24、pnpm 11(版本见 `.python-version`、`package.json` 与 CI workflow)。
后端命令从仓库根开始;API 与 worker 分别在 `apps/api` 下的独立终端运行。`seed_dev.py` 仅供 dev/test,创建 SKU、平台镜像与管理员,管理员口令只打印一次(固定口令可用 `SUPERDL_SEED_ADMIN_PASSWORD`)。

```bash
docker compose -f deploy/app/compose.yaml up -d

cd apps/api
cp .env.example .env
uv sync
uv run alembic upgrade head
uv run python scripts/seed_dev.py
uv run uvicorn app.main:app --reload
uv run python -m app.workers.main
```

前端在仓库根执行,web 与 admin 各开一个终端。API 文档位于 `http://localhost:8000/docs`,web 位于 `http://localhost:5173`,admin 位于 `http://localhost:5174`,使用 seed 创建的管理员登录。

```bash
pnpm install
pnpm --filter web dev
pnpm --filter admin dev
```

K8s 默认 `FakeOrchestrator`(进程内存态);接真实集群把 `SUPERDL_K8S_BACKEND` 设为 `real`,与 `SUPERDL_ENVIRONMENT` 无关(prod 不允许 `fake`)。

## 闸门

本地按改动范围跑,红了不推送;PR 须 CI 全绿才可合入。命令清单只有一份:[CLAUDE.md](CLAUDE.md)「常用命令」与「工作流与提交约定」。

## 工程结构

| 目录                  | 说明                                                                                                                                                           |
| --------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `apps/api`            | FastAPI 模块化单体(account / catalog / orchestrator / services / billing / metering / notify / nodes / legal / tickets / adminapi),同镜像双入口 serve / worker |
| `apps/web`            | 用户控制台(React 19 + antd 6,浅色)                                                                                                                             |
| `apps/admin`          | 管理控制台(同栈,深色 NOC 风)                                                                                                                                   |
| `packages/api-client` | orval 从 `openapi.json` 生成的 fetcher 与 model 类型(禁止手改)                                                                                                 |
| `packages/ui`         | 两端共享的主题 token / 状态映射 / 格式化工具 / 共享文案                                                                                                        |
| `deploy/`             | ansible 控制面装机、集群 helmfile 与 runbook、平台 K8s 清单与本地 compose、node-join 测试、实例镜像                                                            |
| `e2e/`                | Playwright 浏览器冒烟                                                                                                                                          |
| `docs/`               | 架构、模块参考、UI/UX 规格、文案规范、决策记录                                                                                                                 |
| `scripts/`            | 发布脚本与仓库级闸门脚本(禁词、CJK、CSP hash、文档引用、网关清单)                                                                                              |

## License

Apache-2.0 — see [LICENSE](./LICENSE) and [NOTICE](./NOTICE).
