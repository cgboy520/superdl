# SuperDL

GPU 算力租赁平台:租户按量租用整卡 / MIG / 共享 GPU 容器实例(SSH + JupyterLab),按秒累计、按小时出账;运营侧有节点一键加入、SKU 与库存、财务对账、工单与告警闭环。
后端 FastAPI 模块化单体 + PostgreSQL 18,前端 React 19 + antd 6 双控制台,平台层 RKE2 / k3s + Kata / HAMi。

状态:未发布(版本 0.1.0,尚无 tag);代码侧功能齐备,上线依赖实机验证与资质联调。

## 文档地图

| 想知道 | 看 |
|---|---|
| 架构、模块边界、数据模型、核心流程与硬约束 | [docs/architecture.md](docs/architecture.md) |
| 各模块契约与不变量 | [docs/reference/](docs/README.md) |
| 配额、限流、保留期 | [docs/reference/limits.md](docs/reference/limits.md) |
| UI/UX 规格、文案规范 | [docs/ui-ux-spec.md](docs/ui-ux-spec.md)、[docs/copy-style-guide.md](docs/copy-style-guide.md) |
| 为什么这样定 | [docs/decisions.md](docs/decisions.md) |
| 工程规范、闸门、提交约定 | [CLAUDE.md](CLAUDE.md)(人与 AI 代理共用);上手见 [CONTRIBUTING.md](CONTRIBUTING.md) |
| 部署、发布、回滚、集群装机、runbook | [deploy/README.md](deploy/README.md) |
| 报告安全漏洞 | [SECURITY.md](SECURITY.md) |

## 快速开始

前置:Docker、uv、Python 3.13、Node 24、pnpm 11(版本见 `.python-version` / `.node-version` / `package.json`)。

```bash
# 1. 本地依赖(PostgreSQL 18 + mock 短信/支付)
docker compose -f deploy/app/compose.yaml up -d

# 2. 后端
cd apps/api
cp .env.example .env                        # SUPERDL_ENVIRONMENT=dev 等
uv sync
uv run alembic upgrade head
uv run python scripts/seed_dev.py           # 四档 SKU + 平台镜像 + 管理员(口令只打印这一次;固定口令用 SUPERDL_SEED_ADMIN_PASSWORD)
uv run uvicorn app.main:app --reload        # http://localhost:8000/docs
uv run python -m app.workers.main           # 另开终端:outbox worker + 定时任务

# 3. 前端(仓库根)
pnpm install
pnpm --filter web dev                       # 用户控制台 http://localhost:5173(短信 mock 固定码 123456)
pnpm --filter admin dev                     # 管理控制台 http://localhost:5174(seed 的 admin 账号)
```

K8s 在 dev 下是 `FakeOrchestrator`(进程内存态),不需要真实集群;真实集群只能在 `SUPERDL_ENVIRONMENT=prod` 下启用。

## 闸门

按改动范围跑,红了不提交。命令清单只有一份:见 [CLAUDE.md](CLAUDE.md)「常用命令」与「提交约定」。

## 工程结构

| 目录 | 说明 |
|---|---|
| `apps/api` | FastAPI 模块化单体(account / catalog / orchestrator / billing / metering / notify / nodes / legal / tickets / adminapi),同镜像双入口 serve / worker |
| `apps/web` | 用户控制台(React 19 + antd 6,浅色) |
| `apps/admin` | 管理控制台(同栈,深色 NOC 风) |
| `packages/api-client` | orval 从 `openapi.json` 生成的 TanStack Query hooks(禁止手改) |
| `packages/ui` | 两端共享的主题 token / 状态映射 / 格式化工具 / 共享文案 |
| `deploy/` | ansible 装机基线、集群 helmfile 与 runbook、平台 K8s 清单与本地 compose、node-join 测试、实例镜像 |
| `e2e/` | Playwright 浏览器冒烟(smoke / admin / i18n) |
| `docs/` | 架构、模块参考、UI/UX 规格、文案规范、决策记录 |
| `scripts/` | 发布脚本与仓库级闸门脚本(禁词、CJK、手写 URL、迁移 DDL、文档引用) |
