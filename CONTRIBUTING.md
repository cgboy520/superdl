# 贡献指南

规范只有一份:[CLAUDE.md](./CLAUDE.md)(硬性规范、闸门、提交约定)。这里只放第一次上手需要的。

## 前置工具

| 工具 | 版本 | 用途 |
|---|---|---|
| Docker | 任意近期版本 | 本地 PG18 与 pytest(testcontainers) |
| uv | 0.11+ | 后端依赖与命令 |
| Python | 3.13(`.python-version`) | uv 自动选用 |
| Node.js | 24(`.node-version`) | 前端 |
| pnpm | 11(`package.json` `packageManager`,建议 `corepack enable`) | 前端 |
| shellcheck、bats | 任意 | 改 `node-join.sh` 时 |

## 上手

1. 按 [README.md](./README.md)「快速开始」起本地环境并登录两端。
2. 读 [docs/README.md](./docs/README.md) 找到要改的模块参考;先读它的「规则与不变量」。
3. 改动范围决定跑哪些闸门,见 CLAUDE.md「提交约定」;红了不提交。
4. 文档随代码同一提交:改了端点 / 表 / 角色 / 默认值 / 流程,更新对应 `docs/reference`、runbook 或 README。

## 提交

- 直接在 `main` 提交,不建分支、不发 PR(见 [docs/decisions.md](./docs/decisions.md))。
- 前缀 `feat:` / `fix:` / `chore:` / `docs:` / `test:` / `ci:` / `refactor:`,一句话说清改了什么;一个提交一件事。

## 安全问题

不要开 issue,按 [SECURITY.md](./SECURITY.md) 私密报告。
