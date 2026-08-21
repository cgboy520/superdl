# SuperDL

GPU 算力租赁平台。架构见 [docs/architecture.md](docs/architecture.md)，UI/UX 规格见 [docs/ui-ux-spec.md](docs/ui-ux-spec.md)，各模块契约见 [docs/reference/](docs/reference/)。

## 快速开始

```bash
# 1. 本地依赖（PostgreSQL 18 + mock 短信/支付）
docker compose -f deploy/app/compose.yaml up -d

# 2. 后端
cd apps/api
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload        # http://localhost:8000/docs
uv run python -m app.workers.main           # 另开终端：outbox worker + 定时任务

# 3. 前端
pnpm install
pnpm --filter web dev                       # 用户控制台 http://localhost:5173
pnpm --filter admin dev                     # 管理控制台 http://localhost:5174
```

## 工程结构

| 目录 | 说明 |
|---|---|
| `apps/api` | FastAPI 模块化单体（account / catalog / orchestrator / billing / metering / notify / nodes / adminapi），同镜像双入口 serve / worker |
| `apps/web` | 用户控制台（React 19 + antd 6） |
| `apps/admin` | 管理控制台（深色 NOC 风） |
| `packages/api-client` | orval 从 openapi.json 生成的 TanStack Query hooks |
| `packages/ui` | 两端共享的主题 token / 状态映射 / 格式化工具 |
| `deploy/` | ansible 装机基线、集群 helmfile、平台部署与本地 compose |
| `e2e/` | Playwright 浏览器冒烟（需 API 与 web dev server 在跑） |
| `docs/reference/` | 各模块的数据模型、端点契约与不变量 |

工程规范见 [CLAUDE.md](CLAUDE.md)。
