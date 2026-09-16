# SuperDL 工程规范(人与 AI 代理共用)

GPU 算力租赁平台。架构 `docs/architecture.md`,模块契约与不变量 `docs/reference/`(索引 `docs/README.md`),UI/UX 规格 `docs/ui-ux-spec.md`,决策记录 `docs/decisions.md`,上手与工作流 `CONTRIBUTING.md`。

## 仓库布局

```
apps/api       FastAPI 模块化单体(uv;同镜像双入口 serve / worker)
apps/web       用户控制台(Vite + React 19 + antd 6,浅色)
apps/admin     管理控制台(同栈,深色 NOC 风)
packages/api-client  orval 从 openapi.json 生成(禁止手改 src/generated)
packages/ui    主题 token、状态徽标映射、金额/时长格式化、共享文案
deploy/        ansible 控制面装机 / cluster helmfile 与 runbook / app K8s 清单与本地 compose / node-join 测试 / 实例镜像
e2e/           Playwright 浏览器冒烟
docs/          架构、模块参考、UI/UX 规格、文案规范、决策记录
scripts/       发布脚本与仓库级闸门脚本
```

## 常用命令

后端命令在 `apps/api` 下执行;先从仓库根启动本地依赖(`docker compose -f deploy/app/compose.yaml up -d`)。API 与 worker 各用独立终端。`seed_dev.py` 仅供 dev/test,`bootstrap_admin.py` 用于创建生产首个管理员,口令只打印一次。
后端 pytest 需要 Docker(testcontainers PG18);每个并行 worker 单独启动容器,单文件测试不加 `-n`。

```bash
uv sync
uv run alembic upgrade head
uv run python scripts/seed_dev.py
uv run python scripts/bootstrap_admin.py
uv run uvicorn app.main:app --reload
uv run python -m app.workers.main
uv run ruff format . && uv run ruff check --fix .
uv run pyright
uv run lint-imports
uv run pytest -n 8
uv run pytest tests/test_x.py
uv run alembic revision --autogenerate -m "..."
uv run alembic check
uv run python -m app.export_openapi
uv run python scripts/export_error_messages.py
```

以下命令在仓库根执行。浏览器冒烟需要 API 与 worker 正在运行;可设置 `SUPERDL_ADMIN_E2E=1` 启用管理端用例。

```bash
pnpm install
pnpm dev / build / format / lint / typecheck / test / i18n
pnpm api-client
pnpm --filter @superdl/e2e test:e2e

bash -n apps/api/app/modules/nodes/assets/node-join.sh && shellcheck apps/api/app/modules/nodes/assets/node-join.sh
bats deploy/node-join/tests
python3 scripts/check-docs-links.py
python3 scripts/check-page-skeleton.py
```

## 硬性规范(违反即返工)

1. **金额**:全链路 `Decimal`/`numeric`,禁止 float。单价 4 位小数,入账 2 位小数,`ROUND_HALF_EVEN`,统一走 `app/core/money.py`。
2. **时间**:DB 一律 `timestamptz`,代码一律 aware-UTC(`app/core/timeutil.now_utc()`)。
3. **改 DB + 动 K8s 走 outbox**:业务写入与 `outbox_tasks` 同事务;请求路径禁止直接调 K8s(唯一例外:实例日志只读直读,见 `docs/reference/orchestrator.md`)。worker 侧巡检可直连 K8s。
4. **钱包更新 `SELECT ... FOR UPDATE`**,同事务写 `balance_ledger`(带 balance_after)。
5. **计费主依据 `instance_events`**(running↔非 running 边);Prometheus 指标只做展示与对账。
6. **模块边界**:`app/modules/*` 之间只许 import 对方的公开面:`service.py`、`schemas.py`,以及 `account/deps.py`、`account/deletion.py`;`orchestrator` 另开放 `queries.py`(只读)、`transitions.py`(系统侧迁移)、`statemachine.py`(状态常量)、`ports.py`(端口池),它们不依赖 billing,是 billing 结算与巡检访问编排的唯一通道。`billing` 禁止 import `orchestrator/service.py`(后者依赖 `billing/service.py`,反向即环)。函数内 import 只许出现在 `wiring.py` / 进程入口 / 第三方 SDK 按需加载(ruff PLC0415 强制),模块环一律靠调整归属打破。import-linter 强制。
7. **API 契约**:OpenAPI-first。改路由/schema 后重导 openapi.json 并跑 `pnpm api-client`;前端禁止手写 fetch,用生成 fetcher(hooks 在 `apps/web/src/api/*.ts` / `apps/admin/src/api.ts` 自建)。
8. **统一错误体** `{code, message, message_key, params, detail, request_id}`(`app/core/errors.py` AppError);创建类 POST 支持 `Idempotency-Key`。
9. **所有写操作过审计中间件**;管理端与用户端 API 物理分离(JWT audience `user` / `admin`)。
10. **状态机迁移**只经 `orchestrator/service.py` 的 transition 函数(同事务写 instance_events),禁止直接 UPDATE status。
11. **前端**:antd 6 原生组件自封装,不引 pro-components;服务端状态全走 TanStack Query;文案与状态映射集中在 `packages/ui`。
12. **文案**:单一事实源是后端 `core/messages.py` 与两端 locales JSON;zh-CN 与 en-US 同时提交,风格见 `docs/copy-style-guide.md`。
13. **测试**:每条用例能答出「它挂了说明什么坏了」。必须有用例:金额与舍入、透支、结算幂等、跨小时/跨日/跨月与时区边界、状态机迁移、幂等键与 outbox 重放、鉴权与角色边界。不设覆盖率阈值。端到端事实源 `apps/api/tests/test_e2e_lifecycle.py`,浏览器冒烟 `e2e/tests/`。
14. **密钥/凭据不入 git**:只经环境变量或平台配置中心注入;deploy 模板一律 `CHANGE_ME`(`deploy/app/secrets.example.yaml`)。prod 必配项以 `docs/reference/security.md` 的 `_validate_prod` 清单为准。Licensing: `LICENSE` (Apache-2.0) and `NOTICE` at the repository root are authoritative; no per-file license headers.
15. **迁移与发布**:停机发布(stop → `alembic upgrade head` → start),无兼容窗口、不支持回滚(downgrade 一律 raise);破坏性 DDL 允许,提交说明写明数据影响;`uv run alembic check` 必过;`/readyz` 只认 DB == 代码 head。
16. **文档随代码同一提交**:改了端点、表、角色、默认值、巡检周期、命令或流程,同一提交更新对应 `docs/reference`、runbook 或 README;新决策写 `docs/decisions.md`;文档与注释只写当前事实,不写评审编号、变更史与日期。引用由 `python3 scripts/check-docs-links.py` 检查。

## 禁改清单

- `packages/api-client/src/generated/**`(orval 产物,只经 `pnpm api-client` 再生成)
- `apps/api/alembic/versions/*`:只许新增;已上线迁移的 DDL / 数据语句与 revision 链不许改。零行为改动(注释与 docstring 换行、按原顺序拆辅助函数)允许,须附行为未变的证明:语句序列逐条比对 + 空库 `alembic upgrade head` + `alembic check`

## 工作流与提交约定

- `main` 受保护:改动在短生命周期分支上提交,经 PR 合入;PR 须 CI 全绿并至少一人评审,squash 合并;不 force-push `main`。
- 提交信息前缀 `feat:` / `fix:` / `chore:` / `docs:` / `test:` / `ci:` / `refactor:`,一句话说清改了什么。
- 一个 PR 一件事,可单独回滚。纯机械改动单独成 PR;契约再生成(openapi.json / orval 产物 / errors 文案 / i18n 类型)随引发它的改动同一提交。
- 本地闸门按改动范围跑,带红不推送;CI(`.github/workflows/ci.yml`)是合并闸门与全部闸门清单:
  - 后端:ruff format/check → pyright → import-linter → pytest;动模型/迁移加 `alembic check`,动路由/schema 加 openapi.json 无 diff
    - ruff 复杂度上限(圈复杂度 12 / 分支 14 / 语句 60,`apps/api/pyproject.toml`)超了拆函数,不加 noqa;pyright 配置只认仓库根 `pyrightconfig.json`(standard + 多余 ignore / 多余比较 / 私有访问报错),白盒测试直探模块内部时文件头声明 `# pyright: reportPrivateUsage=false`
  - 前端:prettier --check → eslint → tsc → vitest;动文案/locale 加 `pnpm i18n`,动构建配置加 build
  - 脚本:bash -n → shellcheck → bats
  - 文档:`python3 scripts/check-docs-links.py`;`deploy/` 与 `apps/api/` 之外的 Markdown 另过 `pnpm format:check`;只改文档或注释只需跑这两项
  - 用户可见主链路:Playwright 冒烟
  - 只在 CI:pip-audit / pnpm audit、gitleaks、kubeconform(`deploy/app/k8s`)、kind 上的 RealOrchestrator 冒烟
