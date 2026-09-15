# 安全策略

## 报告漏洞

- 私密渠道报告,不开公开 issue、不在提交信息或 PR 描述里写漏洞细节:优先用 GitHub 仓库 Security → Advisories 的私密报告;不可用时直接私信仓库维护者。
- 报告内容:影响组件(api / web / admin / deploy / node-join.sh / 实例镜像)、复现步骤、影响面判断;凭据与租户数据脱敏。
- 处置:收到后先确认并给出处置方向,修复随下一次 `main` 合并发布。不设漏洞赏金。

## 支持范围

- 只有 `main` 受支持,没有发布分支;部署以 tag 构建的镜像为准(`.github/workflows/release.yml`)。
- 只在自己部署的实例上做安全测试;禁止针对他人租户、生产数据或第三方(支付、短信、验证码渠道)。

## 设计与加固

- 安全设计、租户隔离、限流分层与已接受取舍:`docs/reference/security.md`。
- 生产启动校验(密钥、渠道、域名占位 fail-fast):同上「规则与不变量」首条。
- 凭据不入 git:只经环境变量或平台配置中心注入,模板一律 `CHANGE_ME`(`deploy/app/secrets.example.yaml`)。
- 供应链:CI 跑 gitleaks(当前工作树)、pip-audit、pnpm audit(HIGH+ 阻断);发布镜像经 Trivy 扫描并附 SBOM。
