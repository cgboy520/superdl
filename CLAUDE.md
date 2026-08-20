# SuperDL — AI 代理工程规范

GPU 算力租赁平台。方案与规格见 `docs/development-plan.md`、`docs/ui-ux-spec.md`，工作包 spec 见 `docs/specs/`。

## 仓库布局

```
apps/api       FastAPI 模块化单体（uv 管理；同镜像双入口：serve / worker）
apps/web       用户控制台（Vite + React 19 + antd 6，浅色）
apps/admin     管理控制台（同栈，深色 NOC 风）
packages/api-client  orval 从 openapi.json 生成（禁止手改 src/generated）
packages/ui    主题 token、状态徽标映射、金额/时长格式化共享件
deploy/        ansible 装机基线 / cluster helmfile / app 部署与本地 compose / node-join 脚本测试
e2e/           Playwright 浏览器冒烟
docs/specs     每个工作包一份 WPxx-*.md（目标/契约/数据变更/验收用例）
```

## 常用命令

```bash
# 后端（在 apps/api 下）
uv sync                                  # 安装依赖
uv run uvicorn app.main:app --reload     # 启动 API（需先 docker compose up -d postgres）
uv run python -m app.workers.main        # 启动 worker（outbox + 定时任务）
uv run ruff format . && uv run ruff check --fix .
uv run pyright
uv run pytest                            # 需要 Docker（testcontainers 起 PG18）
uv run alembic upgrade head              # 迁移
uv run alembic revision --autogenerate -m "..."
uv run lint-imports                      # 模块边界检查（import-linter）
uv run python -m app.export_openapi      # 导出 openapi.json 到 packages/api-client/

# 前端（仓库根）
pnpm install
pnpm dev / build / lint / typecheck / test
pnpm api-client                          # orval 重新生成 TanStack Query hooks

# 装机脚本（node-join.sh）
shellcheck apps/api/app/modules/nodes/assets/node-join.sh
bats deploy/node-join/tests              # PATH shim 伪造系统命令，不碰真实系统

# 本地环境
docker compose -f deploy/app/compose.yaml up -d      # PG18 + mock 短信/支付
```

## 硬性规范（违反即返工）

1. **金额**：全链路 `Decimal`/`numeric`，禁止 float。单价 4 位小数，账单入账 2 位小数，舍入 `ROUND_HALF_EVEN`。统一走 `app/core/money.py`。
2. **时间**：DB 一律 `timestamptz`，代码一律 aware-UTC（`app/core/timeutil.now_utc()`）；禁止 naive datetime。
3. **改 DB + 动 K8s 必须走 outbox**：业务写入与 `outbox_tasks` 插入同一事务；任何直接在请求路径调 K8s 的代码不许提交。
4. **钱包更新必须 `SELECT ... FOR UPDATE`** 且同事务写 `balance_ledger`（带 balance_after 快照）。
5. **计费主依据是 `instance_events`**（running↔非 running 的边），Prometheus 指标只做展示与对账，不参与计费。
6. **模块边界**：`app/modules/*` 之间只许 import 对方的 `service.py`（和 `schemas.py`），禁止跨模块 import `models.py`/`router.py` 或跨模块查表。CI 用 import-linter 强制。
7. **API 契约**：OpenAPI-first。改了路由/schema 必须重新导出 openapi.json 并跑 `pnpm api-client`；前端禁止手写 fetch，一律用生成的 hooks。
8. **统一错误体** `{code, message, detail}`（`app/core/errors.py` 的 AppError）；创建类 POST 支持 `Idempotency-Key`。
9. **所有写操作过审计中间件**；管理端 API 与用户端 API 物理分离（独立 JWT audience：`user` / `admin`）。
10. **状态机迁移**只能通过 `orchestrator/service.py` 的 transition 函数（同事务写 instance_events），禁止直接 UPDATE status。
11. **前端**：antd 6 原生组件自封装，**不引 pro-components**；服务端状态全走 TanStack Query；文案/状态映射集中在 `packages/ui`。
12. **测试**：计费模块（billing）覆盖率 ≥90%，金额/舍入/幂等/时区用例强制；结算函数必须有"重复执行零重复扣款"用例。

## 禁改清单

- `packages/api-client/src/generated/**`（orval 产物）
- `apps/api/alembic/versions/*`（已合并的迁移不许改，只许新增）
- `docs/development-plan.md` / `docs/ui-ux-spec.md`（方案文档，改动需人工确认）

## 提交约定

- **所有工作直接在 `main` 分支提交，不新建分支、不发 PR**。
- commit message 前缀 `WPxx:`（跨 WP 的整理用 `chore:` / `fix:`），一句话说清「改了什么 + 为什么」。
- **一个提交一件事**：每个提交自身能过全部质量闸门、能被单独回滚。这是首要判据，行数上限只是兜底。
- 体量上限：单个提交 **< 2000 手写行**（不含 orval 产物 / `openapi.json` / lockfile / alembic 自动生成的迁移）。超了就得在 commit message 里说明为什么不可再拆。
- 以下情形本就不该硬拆：新模块首次落地（models+迁移+service+router+tests 一体才自洽）、契约再生成、整屏前端交付、纯机械的重命名/格式化。机械改动要**单独成提交**，不与逻辑改动混。
- push 后 CI 四个 job 必须全绿；带红不许推下一个提交。本地先跑 `task check`（覆盖后端 + 前端两 job；alembic check、openapi 无 diff、脚本三件套、e2e 由 CI 兜底）：
  - 后端：ruff format/check → pyright → import-linter → pytest（billing 覆盖率 ≥90%）→ alembic check → openapi.json 无 diff
  - 前端：eslint → tsc → vitest → build
  - 脚本：bash -n → shellcheck → bats
  - e2e：迁移+seed 起 API/worker → Playwright 冒烟
