# 安全策略

## 报告漏洞

- 仓库为私有(见 `docs/decisions.md`「私有仓库,不放 LICENSE」),GitHub 的私密漏洞报告只对公开仓库开放。协作者直接私信维护者,或在仓库内开 issue 并加 `security` 标签(私有仓的 issue 仅协作者可见);不要在提交信息里描述漏洞细节。转公开时改用 Settings → Code security 的 Private vulnerability reporting。
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
