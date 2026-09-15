# 贡献指南

规范只有一份:[CLAUDE.md](./CLAUDE.md)(硬性规范、闸门、提交约定)。这里只放第一次上手需要的。

## 前置工具

| 工具             | 版本                                                       | 用途                                |
| ---------------- | ---------------------------------------------------------- | ----------------------------------- |
| Docker           | 任意近期版本                                               | 本地 PG18 与 pytest(testcontainers) |
| uv               | 最新稳定版                                                 | 后端依赖与命令                      |
| Python           | 3.13(`.python-version`)                                    | uv 自动选用                         |
| Node.js          | 24(CI 钉版;`package.json` `engines` 为下限)                | 前端                                |
| pnpm             | 11(`package.json` `packageManager`,建议 `corepack enable`) | 前端                                |
| shellcheck、bats | 任意                                                       | 改 `node-join.sh` 时                |

## 上手

1. 按 [README.md](./README.md)「快速开始」起本地环境并登录两端。
2. 读 [docs/README.md](./docs/README.md) 找到要改的模块参考;先读它的「规则与不变量」。
3. 改动范围决定跑哪些闸门,见 CLAUDE.md「工作流与提交约定」;红了不推送。
4. 文档随代码同一提交:改了端点 / 表 / 角色 / 默认值 / 流程,更新对应 `docs/reference`、runbook 或 README。

## 工作流

1. 从最新 `main` 切分支:`<type>/<短描述>`(type 同提交前缀,如 `feat/spot-preempt`)。
2. 小步提交,提交信息 `feat:` / `fix:` / `chore:` / `docs:` / `test:` / `ci:` / `refactor:` 前缀 + 一句话;一个 PR 一件事。
3. 推送前跑本地闸门;开 PR 后等 CI 全绿并至少一人评审。
4. squash 合并到 `main`;分支合并后删除。`main` 不接受直接推送与 force-push。
5. 发布以 tag 触发,见 [deploy/README.md](./deploy/README.md)「生产发布流程」。

## 安全问题

不要开 issue,按 [SECURITY.md](./SECURITY.md) 私密报告。
