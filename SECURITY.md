# 安全策略

## 报告漏洞

- 通过 GitHub 的私密漏洞报告(仓库 **Security → Report a vulnerability**)提交,不要开公开 issue,不要在 PR / 提交信息里描述。
- 请附:影响的组件(api / web / admin / deploy / node-join.sh / 实例镜像)、复现步骤、影响面判断。凭据、租户数据请脱敏。
- 收到后会先确认并给出处置方向,修复随下一次 `main` 提交发布。本项目不设漏洞赏金。

## 支持范围

- 只有 `main` 分支受支持,没有发布分支;部署以 tag 构建的镜像为准(`.github/workflows/release.yml`)。
- 只在你自己部署的实例上做安全测试;禁止针对他人租户、生产数据或第三方(支付、短信、验证码渠道)。

## 设计与加固

- 安全设计、租户隔离、限流分层与已接受的取舍:`docs/reference/security.md`。
- 生产启动校验(密钥、渠道、域名占位一律 fail-fast):同上「规则与不变量」首条。
- 凭据不入 git:只经环境变量或平台配置中心注入,模板一律 `CHANGE_ME` 占位(`deploy/app/secrets.example.yaml`)。
- 供应链与依赖:CI 跑 gitleaks(全历史)、pip-audit、pnpm audit(HIGH+ 阻断);发布镜像经 Trivy 扫描并附 SBOM。
