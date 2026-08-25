# 决策记录

记「为什么这样定」而不是「做了什么」。每条一段:背景 → 决定 → 后果。新的决策追加在对应章节,
不在代码注释或文档正文里用裸编号指代。领域内部的取舍就近记在模块文档里,这里只登记跨模块或
容易被反复质疑的决定,并给出指向。

## 工程流程

- **直接在 `main` 提交,不建分支、不发 PR。** 单维护者 + AI 代理协作,PR 评审环节没有第二个人来做;
  质量靠每个提交自身过闸门(CLAUDE.md「提交约定」)与 CI 在 `main` 上复跑。后果:回滚粒度是单个提交,
  所以「一个提交一件事」是硬要求。
- **本地闸门是事实源,CI 是复跑。** 闸门按改动范围跑,红了不提交。CI 另有几项只在 CI 跑的检查
  (依赖漏洞、gitleaks、kubeconform、kind 冒烟),本地不强求。
- **不设覆盖率阈值。** 用例必须能回答「它挂了说明什么坏了」;为覆盖率补的测试不回答这个问题。
- **命令清单只有一份。** `Taskfile.yml` 已删除(`task` 本地未装、CI 不用、内容已落后):常用命令以 CLAUDE.md 为准,README 只放快速开始。
- **私有仓库,不放 LICENSE。** 仓库在 GitHub 为 PRIVATE,默认保留全部权利;若要转公开或对外交付,先定许可证再开。
- **release notes 不手维护。** 打 tag 用 `gh release create --generate-notes`,提交信息已按 `feat:`/`fix:` 前缀写,自动生成即够用;不设 CHANGELOG 文件。

## 计费与资金

- **余额归零即回收。** 余额恰好 0.00 的账户进入停机 → 冻结 → 回收链,冻结判据是 `balance > 0` 才放行。
  0.00 的用户已无支付能力,停机后继续免费占用实例盘不合理;改成 `>= 0` 放行会让零余额用户永久免费占盘。
  与停机判据 `effective <= 0` 自洽,由 `tests/test_billing_flow.py::test_zero_balance_stops_then_freezes_then_reclaims` 锁定。
- **计费只认事件流水,指标只做对账。** 见 `architecture.md` §4 与 `reference/billing.md`:Prometheus 全挂结算照常。
- **不做渠道原路退款。** 退款单审批不动钱包,财务登记打款成功才负向核销,审批与打款分人;见 `reference/payment.md`。
- **票款双重兑现闸。** 已开票账期的订单不可退款,在途退款预扣 + 开票重算 + 打款复查三件套互为兜底;
  见 `reference/payment.md`、`apps/api/tests/test_invoices.py`。
- **出金与入账的审计同事务。** 审计行写失败即出金失败回滚——宁可不出金,不可无留痕(`write_audit_sync`)。
- **结算缺口只登记不自愈。** 追平截断 / 死信 / 水位线丢失一律落 `settlement_gaps` 并持续告警,由人工重放或核销;
  自动补结会掩盖账期为什么追不平。见 `reference/billing.md`。

## 安全

- 安全取舍(token 存 localStorage、固定窗口限流、用户端无 2FA、仅 +86、双人制衡残余等)集中在
  `reference/security.md`「已接受取舍」,评审在案,勿再单独立项。
- **管理端全角色强制 TOTP。** 早期只对 admin/finance 强制,ops/readonly 免 MFA 时账号级锁定是口令喷洒的唯一纵深;
  现已全角色强制,登录限流四层桶保留为纵深(`reference/admin.md`)。
- **日志 PII / 凭据全局脱敏。** `app/core/logging.py` 按键名(phone / id_number / token / secret / password / code)兜底打码,
  防新增日志点漏脱敏。
- **账号级登录锁定。** 撞库可以换 IP,换不了目标账号:账号维 15 分钟窗 + 日窗阶梯锁定,与 IP 维桶叠加。
- **短信发码前置人机校验。** `/auth/sms-code` 是刷码与撞库的头号口子,prod 强制阿里云验证码 2.0,mock 拒绝启动。
  代价:首次上线前必须先开通验证码服务(资质有 lead time)。

## 编排与平台

- **reconciler 两阶段。** 事务内只做状态迁移 / 标记 / enqueue,K8s 动作在 commit 后或经 outbox 执行;
  失败由泄漏回收宽限后强删兜底。见 `apps/api/app/modules/orchestrator/reconciler.py`。
- **worker 拆成 5 个组件 Deployment**(core / tenant-mgr / node-mgr / prewarm / disk-ops),RBAC 按组件最小化,
  发布与回滚必须成组;见 `deploy/README.md`、`deploy/app/k8s/03-worker.yaml`。
- **控制面 HA 与平台组件落点。** 公众生产强制 3 台 server 堆叠 etcd + VIP;平台组件以 `node-role.superdl.io/infra`
  标签选址,不再绑死 control-plane;light 档(k3s 单机)只做试点与联调,禁止公众生产。见 `deploy/cluster/README.md`。
- **实例盘销毁带 TRIM。** TopoLVM lvmd `issue_discards=1`,`lvremove` 对 extent 发 NVMe TRIM;大 LV 实测耗时记录在
  `deploy/cluster/runbooks/cluster-validation.md` D 节。
- **JuiceFS 关闭 writeback。** 数据盘一致性优先于顺序写性能;fio 对比记录同上。
- **镜像仓迁托管仓。** 集群内自建 registry(明文 http + htpasswd)废弃,`registry.superdl.local` 保留为逻辑名,
  节点 mirror 到 ACR / Harbor;见 `deploy/cluster/runbooks/image-prewarm.md`。过渡期残留:平台默认生成的
  registries.yaml 仍指向 NodePort 30500,已迁移集群靠 `node_registries_yaml` 覆盖(`reference/nodes.md`);
  保留这个默认(未迁移集群仍正确),不为托管仓地址新增配置键。
- **DNS01 走 acme-dns 中转。** 集群内只持有能改 `_acme-challenge` 子域 TXT 的账户,不再持有全域 RAM DNS 凭据;
  见 `deploy/cluster/runbooks/acme-dns.md`。
- **集群键中性化,砍掉 `k8s_distro`。** `rke2_*` 改 `cluster_*`(旧名保留别名),发行版由平台探测 gitVersion 派生。
- **不引 Sentry 类 SaaS。** 未捕获异常统一 500 留痕并经 Loki / Prometheus 告警,少一个外部依赖与数据出境面。
- **管理端监控自绘,Grafana 只作外链。** 不做 iframe 嵌入,`grafana_url` 未配置只显示一行提示。
- **告警 `runbook_url` 只加在有专属 runbook 的规则上。** 其余告警的第一步写在 summary 与 `deploy/cluster/runbooks/README.md` 索引表里,不为每条告警生造一页。

## 评审编号索引

历史上的生产就绪评审与安全审查用 `P0-n` / `P1-n` 编号,工作包用 `WPn`;编号的说明文档已随
「工作包交付流水账」一并删除,代码与清单注释里仍有引用。存量注释里的编号不做批量清理,改到所在文件时顺手换成本页的决策标题;
对外可见文本(路由 docstring → `openapi.json`、平台配置 hint 等 UI 文案)不得带编号。对照如下(不再新增编号,新决策写上面的章节):

| 编号 | 主题 | 现存于 |
|---|---|---|
| P0-1 | 控制面 HA + 平台组件 infra 标签选址 + light 档禁公众生产 | `deploy/cluster/rke2/server-config.yaml`、`deploy/app/k8s/02-api.yaml` 等清单注释 |
| P0-3 | 实例盘销毁 TRIM(TopoLVM `issue_discards`) | `deploy/cluster/topolvm/lvm-config.configmap.yaml`、`deploy/ansible/site.yml` |
| P1-1 | 票款双重兑现闸 | `apps/api/tests/test_invoices.py`、`apps/api/tests/test_refunds.py` |
| P1-2 | 结算缺口闭环 | `apps/api/tests/test_billing_settlement.py` |
| P1-6 | acme-dns 替代 alidns webhook | `deploy/cluster/acme-dns.yaml`、`deploy/app/k8s/05-cert-manager.yaml` |
| P1-7 | 镜像仓迁托管仓 | `deploy/cluster/rke2/registries.yaml`、`deploy/cluster/preflight.sh` |
| P1-8 | 出金 / 入账同步审计 | `apps/api/app/modules/billing/refunds.py`、`apps/api/app/modules/adminapi/router_finance.py` |
| P1-9 | reconciler 两阶段 | `apps/api/app/modules/orchestrator/reconciler.py` |
| P1-10 | JuiceFS 关 writeback | `deploy/cluster/values/juicefs.yaml` |
| P1-13 | 日志 PII 脱敏 + 管理端全角色 MFA | `apps/api/app/core/logging.py`、`apps/api/app/modules/adminapi/service.py` |
| P1-16 | 账号级登录锁定 | `apps/api/tests/test_security_hardening.py` |
| P1-17 | 发码前置人机校验 | `apps/api/app/core/captcha.py`、`apps/web/src/lib/captcha.ts` |
| P1-18 | worker 组件集群拆分 + RBAC 拆分 | `apps/api/app/workers/components.py`、`deploy/app/k8s/01-rbac.yaml` |
| WP6 / 8 / 9 | 用量 / 数据盘 / 通知模块 | git 历史 |
| WP10 / 11 | 用户前端 / 管理前端 | git 历史 |
| WP21 | 评估修复轮 | git 历史 |
| WP22 / 23 | 镜像缓存与预热 / 节点一键加入 | `reference/images.md`、`reference/nodes.md` |
| WP24 | 文案 i18n 化 | `reference/i18n.md`、`copy-style-guide.md` |
| WP25 / 26 | 可观测性收口 / SKU 节点感知 | `reference/observability.md`、`reference/catalog.md` |
| WP27 | 集群双档简化与键中性化 | `reference/nodes.md`、`deploy/cluster/README.md` |
