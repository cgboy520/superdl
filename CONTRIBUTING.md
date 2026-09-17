# Contributing

There is one rulebook: [CLAUDE.md](./CLAUDE.md) (hard rules, gates, commit conventions). This page only covers what you need the first time.

## Prerequisites

| Tool             | Version                                                             | Used for                                        |
| ---------------- | ------------------------------------------------------------------- | ----------------------------------------------- |
| Docker           | any recent release                                                  | local PostgreSQL 18 and pytest (testcontainers) |
| uv               | latest stable                                                       | backend dependencies and commands               |
| Python           | 3.13 (`.python-version`)                                            | selected automatically by uv                    |
| Node.js          | 24 (pinned in CI; `package.json` `engines` is the floor)            | frontends                                       |
| pnpm             | 11 (`package.json` `packageManager`; `corepack enable` recommended) | frontends                                       |
| shellcheck, bats | any                                                                 | when changing `node-join.sh`                    |

## Getting started

1. Bring up the local environment and sign in to both consoles following the "Quick start" in [README.md](./README.md).
2. Find the module reference you are about to change in [docs/README.md](./docs/README.md) and read its "规则与不变量" (rules and invariants) section first. <!-- cjk-ok -->
3. The scope of your change decides which gates to run; see CLAUDE.md "工作流与提交约定". Do not push red. <!-- cjk-ok -->
4. Docs travel with code in the same commit: when an endpoint, table, role, default, schedule or procedure changes, update the matching `docs/reference` page, runbook or README.

## Workflow

1. Branch from the latest `main`: `<type>/<short-description>` (type = commit prefix, e.g. `feat/spot-preempt`).
2. Commit in small steps; messages use a `feat:` / `fix:` / `chore:` / `docs:` / `test:` / `ci:` / `refactor:` prefix plus one sentence. One PR does one thing.
3. Run the local gates before pushing; open a PR and wait for a green CI plus at least one review.
4. Squash-merge into `main`; delete the branch afterwards. `main` accepts neither direct pushes nor force-pushes.
5. Releases are tag-triggered; see "生产发布流程" in [deploy/README.md](./deploy/README.md). <!-- cjk-ok -->

## Language

New code, comments, docs and operator-facing output are written in English. Simplified Chinese lives only in the UI locale files (`locales/zh-CN/**`, the hand-maintained `zh-CN/errors.json`) and in legal presets; the remaining Chinese in older documents is being translated in dedicated `docs:` / `chore:` PRs.

## Security issues

Do not open an issue; report privately as described in [SECURITY.md](./SECURITY.md).
