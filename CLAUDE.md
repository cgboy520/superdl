# SuperDL — AI 代理工程规范

GPU 算力租赁平台。架构见 `docs/architecture.md`，UI/UX 规格见 `docs/ui-ux-spec.md`，各模块契约与不变量见 `docs/reference/`。

## 仓库布局

```
apps/api       FastAPI 模块化单体（uv 管理；同镜像双入口：serve / worker）
apps/web       用户控制台（Vite + React 19 + antd 6，浅色）
apps/admin     管理控制台（同栈，深色 NOC 风）
packages/api-client  orval 从 openapi.json 生成（禁止手改 src/generated）
packages/ui    主题 token、状态徽标映射、金额/时长格式化共享件
deploy/        ansible 装机基线 / cluster helmfile / app 部署与本地 compose / node-join 脚本与测试
e2e/           Playwright 浏览器冒烟
docs/reference 各模块契约、数据模型与不变量
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
pnpm dev / build / lint / typecheck / test / i18n
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
3. **改 DB + 动 K8s 必须走 outbox**：业务写入与 `outbox_tasks` 插入同一事务；请求路径禁止直接调 K8s。（worker 侧的收敛巡检可直连 K8s。）
4. **钱包更新必须 `SELECT ... FOR UPDATE`** 且同事务写 `balance_ledger`（带 balance_after 快照）。
5. **计费主依据是 `instance_events`**（running↔非 running 的边），Prometheus 指标只做展示与对账，不参与计费。
6. **模块边界**：`app/modules/*` 之间只许 import 对方的 `service.py` 与 `schemas.py`，禁止跨模块 import 其他文件或跨模块查表；唯一例外是 `account/deps.py`（`CurrentUser` 为全站鉴权依赖）。import-linter 按「整包禁止 + 只放行 service/schemas」强制，新增文件默认受约束。
7. **API 契约**：OpenAPI-first。改了路由/schema 必须重新导出 openapi.json 并跑 `pnpm api-client`；前端禁止手写 fetch，一律用生成的 hooks。
8. **统一错误体** `{code, message, message_key, params, detail, request_id}`（`app/core/errors.py` 的 AppError）；创建类 POST 支持 `Idempotency-Key`。
9. **所有写操作过审计中间件**；管理端 API 与用户端 API 物理分离（独立 JWT audience：`user` / `admin`）。
10. **状态机迁移**只能通过 `orchestrator/service.py` 的 transition 函数（同事务写 instance_events），禁止直接 UPDATE status。
11. **前端**：antd 6 原生组件自封装，不引 pro-components；服务端状态全走 TanStack Query；文案与状态映射集中在 `packages/ui`。
12. **文案**：用户可见文案的单一事实源是后端 `core/messages.py` 与两端 locales JSON；zh-CN 与 en-US 必须同时提交，风格见 `docs/copy-style-guide.md`。
13. **测试**：每条用例都要能答出「它挂了说明什么坏了」。必须有用例的是：金额与舍入、透支、结算幂等（「重复执行零重复扣款」）、跨小时/跨日/跨月与时区边界、状态机迁移、幂等键与 outbox 重放、鉴权与角色边界。不为覆盖率补测试——覆盖率只作参考，不设阈值闸门。端到端事实源是 `apps/api/tests/test_e2e_lifecycle.py`，浏览器冒烟在 `e2e/tests/`（smoke / admin / i18n）。
14. **密钥/凭据不入 git**：只经环境变量或平台配置中心注入；deploy 模板一律 `CHANGE_ME` 占位（`deploy/app/secrets.example.yaml`）。
15. **大表迁移规范**（新迁移必守；存量迁移属禁改清单不回改）：
    - CHECK 约束一律 `NOT VALID` 创建 + 独立迁移 `VALIDATE CONSTRAINT`（对齐 `20260823_38fe91299a8b_refund_requests.py` 惯例），避免全表校验锁写；
    - 大表（bills_hourly/balance_ledger/audit_log/instance_events 及随时间单调增长表）新建索引评估 `op.get_context().autocommit_block()` + `CREATE INDEX CONCURRENTLY`；注意 `env.py` 默认整 run 单事务，CONCURRENTLY 与单事务互斥——启用 autocommit_block 时该迁移必须独立成文件且接受失去跨迁移原子性；
    - 加列只加可空列或带 server_default 的列（PG 11+ 非易失 default 不重写表）；
    - 存量库升级窗口预案：生产升级前在预演库执行同版本迁移并记录各迁移耗时,大表迁移安排在低峰窗（见 deploy/cluster/runbooks/pg-backup-restore.md 的演练节奏）。

## 禁改清单

- `packages/api-client/src/generated/**`（orval 产物）
- `apps/api/alembic/versions/*`（已合并的迁移只许新增，不许改）

## 提交约定

- 所有工作直接在 `main` 提交，不新建分支、不发 PR。
- commit message 前缀按性质：`fix:` / `feat:` / `chore:` / `docs:`，一句话说清改了什么。
- 一个提交一件事：自身能过全部闸门、能被单独回滚。机械改动（重命名、格式化、契约再生成）单独成提交，不与逻辑改动混。
- 闸门按改动范围跑，带红不许提交；不要每改一行就跑全量。以本地执行为准（`.github/workflows` 是同套闸门的镜像）：
  - 后端代码：ruff format/check → pyright → import-linter → pytest；动了模型/迁移再加 alembic check，动了路由/schema 再加 openapi.json 无 diff
  - 前端代码：eslint → tsc → vitest；动了文案或 locale 再加 i18n，动了构建配置再加 build
  - 脚本：bash -n → shellcheck → bats
  - 用户可见主链路：Playwright 冒烟
  - 只改文档或注释：跑对应的 lint（ruff / eslint / shellcheck）即可，不必跑测试
