## What

<!-- One PR, one thing; commit subject uses feat: / fix: / chore: / docs: / test: / ci: / refactor:. -->

## Checklist

Run the gates that match the change (see `CLAUDE.md` → 工作流与提交约定 / CI is the merge gate):

- [ ] Backend: `uv run ruff format . && uv run ruff check --fix .`, `uv run pyright`, `uv run lint-imports`, `uv run pytest -n 8`
- [ ] Models / migrations: `uv run alembic check` (migrations are append-only; `downgrade` raises)
- [ ] Routes / schemas: `uv run python -m app.export_openapi` + `pnpm api-client` with no diff in `packages/api-client`
- [ ] Frontend: `pnpm format`, `pnpm lint`, `pnpm typecheck`, `pnpm test`; copy changes also `pnpm i18n` (zh-CN and en-US together)
- [ ] Scripts: `bash -n`, `shellcheck`, `bats deploy/node-join/tests`
- [ ] Docs: `python3 scripts/check-docs-links.py`, `pnpm format:check`; docs / runbooks / README updated in this PR for every changed endpoint, table, role, default, schedule, command or flow
- [ ] No secrets or credentials in the diff; deploy templates keep `CHANGE_ME` placeholders
