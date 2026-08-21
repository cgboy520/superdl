# WP0 · 脚手架

## 目标
monorepo 骨架 + CI + 本地环境一条命令可起。

## 内容
- pnpm workspace + turbo:`apps/web`、`apps/admin`、`packages/ui`、`packages/api-client`
- `apps/api`:uv 管理的 FastAPI 单体(core 全件:config/db/errors/money/timeutil/security/audit/outbox/locks/pagination),同镜像双入口(serve / worker)
- alembic(async env)+ 初始迁移(outbox_tasks / audit_log)
- docker compose:PG18(postgres:18 volume 约定 `/var/lib/postgresql`)
- CI:后端 ruff→pyright→pytest→alembic check→import-linter→openapi 一致性;前端 eslint→tsc→vitest→build

## 验收
- `uv run pytest` 全绿(testcontainers 起真 PG18)
- `pnpm lint && pnpm build && pnpm test` 全绿
- `alembic upgrade head && alembic check` 干净
- import-linter 模块边界契约全部 KEPT
