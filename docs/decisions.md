# 决策记录

跨模块或容易被反复质疑的决定,每条写「决定是什么」与「它对后来的改动施加什么约束」。领域内部的取舍记在模块文档里。

## 工程流程

- **直接在 `main` 提交,不建分支、不发 PR。** 约束:回退粒度是单个提交,「一个提交一件事」是硬要求。
- **本地闸门是事实源,CI 是复跑。** 闸门按改动范围跑,红了不提交。依赖漏洞、gitleaks、kubeconform、kind 冒烟只在 CI 跑。
- **不设覆盖率阈值。** 用例必须能回答「它挂了说明什么坏了」。
- **命令清单只有一份**:CLAUDE.md「常用命令」;README 只放快速开始。
- **停机发布,不留兼容窗口、不支持回滚。** 顺序恒为「先 `alembic upgrade head`,后替换代码」;`/readyz` 只认 DB == 代码 head,落后/领先/未知版本一律摘流。破坏性 DDL 允许,提交说明写明数据影响;downgrade 一律 raise;alembic 历史以单条基线 `20260901_1620c05976ce` 起线性追加,`alembic check` 把关。
- **私有仓库,不放 LICENSE。** 转公开或对外交付前先定许可证。
- **release notes 不手维护。** `gh release create --generate-notes`,不设 CHANGELOG。
- **orval 只生成 fetcher 与 model 类型,不生成 TanStack Query hooks。** `packages/api-client` 用 `client: "fetch"`;两端在 `apps/web/src/api/*.ts`、`apps/admin/src/api.ts` 用 useQuery/useMutation 包 fetcher。约束:`client` 保持 `"fetch"`,不加 `query` 块。
- **UI 占位项的去留有判据。** 只有「已排期、按当前设计确定要做」的能力留 disabled 占位并注「即将上线」。约束:变动时 `docs/ui-ux-spec.md` 占位清单与 `docs/reference/web.md` 同提交更新。

## 界面

- **用户端没有独立概览页,首页 = 实例列表。** 与 GPU 租赁产品惯例一致(登录后直接落到资源列表);KPI 由顶栏余额与费用中心承接,公告 / 余额预警 / 欠费聚合成实例列表页顶一条 `AttentionBar`,新手引导落在真空态。约束:不再新增「仪表盘」类汇总页;新的全站提醒一律并入 `AttentionBar`,不另开横幅。
- **控制台顶栏中性色,品牌渐变只在公开层与登录页。** 渐变条在暗色下与基板明度落差过大,且顶栏是每页常驻元素,不该与内容争夺注意力。约束:渐变底上的反白 CTA 走 `brandInverseButtonStyle`;控制台内不再出现 `brand.topBarBg`。
- **主导航分组且只放资源与购买类页面。** web:资源 / 购买与账务 / 支持;admin:总览 / 资源 / 业务 / 治理。通知与账户设置经顶栏铃铛与用户菜单到达。约束:分组事实源分别是 `consoleNav.tsx` 与 `lib/menu.ts`,侧栏 / 抽屉 / 命令面板只许读这一份。
- **常驻提示有准入判据:有时效、可行动。** 政策与口径说明不做常驻 `Alert`,进 tooltip / 规则弹窗 / 卡内脚注。约束:一页至多一条横幅,多项聚合;合规声明只在公开页脚与市场页脚。
- **确认强度组件两端强制,`Popconfirm` 禁用。** L1 / L2 `useConfirm`、L3 `TypeConfirmModal`、管理端审计型 `ReasonAction`;危险确认按钮一律红色。约束:新增确认不许手搓 `modal.confirm`;确认文案 = 标题问句(含目标)+ 后果正文。
- **管理端密度「紧凑」、用户端「舒适」,分档写死在 token。** 管理端表格 13px / `cellPaddingBlock 8`,宽表固定列 + sticky 表头 + 全宽页;用户端保持 antd 默认密度。约束:两端不互相借用密度;宽表判据 `scroll.x ≥ 1000`。
- **轮询周期只有一份(`POLL`),轮询页必须可见新鲜度。** 约束:新轮询不写裸毫秒数;页头用 `PageHeader.freshness` 给「更新于 / 自动刷新 / 暂停」。
- **URL 参数不做旧名兼容,白名单丢弃即全部策略。** 与「停机发布、无兼容窗口」同源:产品未对外发布,不为旧书签/旧链接留映射层;`validateSearch` 只认当前白名单,非法值剥离回默认。约束:重命名 URL 参数或枚举值时直接改,不加归一化代码。

## 计费与资金

- **余额归零即回收,判据一律取可用余额(balance − frozen)。** 解冻判据 `available > 0`,停机判据 `effective <= 0`。约束:欠费巡检六处判据(粗筛 / 锁内二次读 / 低余额预警 payload / stopped→frozen / frozen→解冻 / 数据盘欠费链)不许退回裸余额。由 `apps/api/tests/test_billing_flow.py::test_zero_balance_stops_then_freezes_then_reclaims` 与 `apps/api/tests/test_patrol_arrears_frozen.py` 锁定。
- **计费只认事件流水,指标只做对账。** Prometheus 全挂结算照常。见 `docs/architecture.md` §4、`docs/reference/billing.md`。
- **不做渠道原路退款。** 审批不动钱包,财务登记打款成功才负向核销,审批与打款分人。见 `docs/reference/payment.md`。
- **票款双重兑现闸有两道**:申请退款时拒已开票账期(对该账期活跃发票申请行加锁);开票时行锁内按当前口径重算,不符即驳回。见 `docs/reference/payment.md`、`apps/api/tests/test_invoices.py`。
- **出金与入账的审计同事务。** 审计行写失败即出金回滚(`write_audit_sync`)。
- **结算缺口只登记不自愈。** 追平截断 / 死信 / 水位线丢失落 `settlement_gaps` 并持续告警,人工重放或核销。见 `docs/reference/billing.md`。
- **包周期走独立订阅单一次性预扣,不摊成零元小时账。** `bills_hourly` 结构、幂等键、水位线与缺口机制不动。约束:跳过点只有 `apps/api/app/modules/orchestrator/queries.py::billing_candidates` 一处;购买模式在四处配套过滤同时排除(燃烧率 / 停机判据 / 冻结与解冻 / 尾账),逐条列在 `docs/reference/billing.md`,各有用例锁住。
- **周期取定长小时,不取自然月。** day=24 / week=168 / month=720 / year=8760,到期时刻与定价同一个数。约束:`PERIOD_HOURS` 是常量、不进策略参数。
- **预付不退款,中途释放只作废订阅;从未运行即 failed 的例外原额退回。** 实例进入 `releasing` 时把 active 订阅转 `cancelled`,不生成退款流水;确需退款走人工 `refund_requests`。约束:作废挂在状态迁移监听器上,覆盖用户释放、欠费回收、到期回收、管理端强制回收四条路径;已 `expired` 的历史行不动。首次 creating 调度超时(Pod 从未 ready)不属于「中途释放」:reconciler 同事务退回预付并作废订阅。
- **到期不自动转按量。** 到期即停机 → 冻结 → 72 小时后回收(与欠费共用 `freeze_grace_hours`)。配套:到期前 3 天预警、可开自动续费(默认关)、冻结窗口内续费即解冻。
- **续费按下单时的原价快照重新定价。** 基准是 `subscriptions.unit_price`,不是 SKU 现价(换周期续按新周期取折扣)。起算时刻 `max(老到期时刻, 现在)`。约束:续费**新开一行**并用 `renewed_from_id` 串链,不在原行累加。
- **未到期的包周期实例即使已停机也仍占库存。** 软准入的 `_reserved_slots` 把同一条 SKU 上「stopped / frozen 且仍在保」的实例计为占用。**只在平台层预留,物理层不预留**,创建页与续费入口的说明里必须写。只算同一条 SKU。
- **按量可以就地转包周期,转换点两侧各结各的账。** `POST /api/v1/instances/{uuid}/subscribe` 同事务**先结后翻**:先 `settle_on_demand_up_to` 用转换前的按量时价结清到当前自然小时,再落订阅行、预扣、翻 `market`、刷 `price_hourly`。约束:顺序不可颠倒;**幂等重放在全部守卫之前判**;**结算滞后超 48 小时拒绝转换(409)**。反向不开。`subscribe_instance` 是唯一会改 `market` 为 subscription 的路径。见 `docs/reference/billing.md`、`docs/reference/orchestrator.md`。
- **竞价被抢占照常结算,不免单;结算引擎零改动。** 迁 `stopping` 时由既有 `edge_listener` 出尾账。约束:`billing_candidates` 跳过条件仍只有 `market != 'subscription'`;折扣只落 `instances.price_hourly`,由 `apps/api/app/core/pricing.py` 的 `price_for` 单点算。见 `docs/reference/billing.md`。
- **竞价转按量按「一小时一价」处理跨价的那个小时,滞后小时先按竞价价结清。** `bills_hourly` 一小时一行一个 `unit_price`,以结算时的实例单价为准:先 `settle_on_demand_up_to`(与转包周期同款)把水位线之后未结的整点按竞价价结掉,再由 `settlement.reprice_current_hour` 在钱包行锁内改写当前小时的 `unit_price`、按新价重算 `amount`、补扣差价并打 `detail.repriced`。约束:只补扣、不退款(`spot_discount_pct` 上界 90);必须写进转换确认弹窗。见 `docs/reference/billing.md`。
- **微信支付验签只走微信支付公钥模式,不做平台证书模式。** `wechat_public_key` 与 `wechat_public_key_id` 为渠道必填,缺一即 `PAYMENT_CHANNEL_ERROR`;回调 `Wechatpay-Serial` 不等于公钥 ID 直接拒,不进 SDK。见 `docs/reference/payment.md`。
- **渠道反向通知的处置标记只写不清。** `channel_reversed_at` 是「收到过」,`channel_reversal_resolved_at` + `channel_reversal_action` 是「处置过」;release 不清标记,同一通知重放不再二次冻结。回调新鲜度窗口 ±15 分钟。约束:release 单操作人但条条告警;收款验签根(payment / crypto 配置组)任何写入条条告警(`PaymentConfigWritten`),不做双人制衡。见 `docs/reference/payment.md`。
- **结算引导不登记缺口。** 无水位线且窗口前没有任何可计费对象 = 首次部署,只建水位线;有历史才登记 `watermark_missing`。见 `docs/reference/billing.md`。

## 安全

- **安全取舍集中在一处。** token 存 localStorage、固定窗口限流、用户端无 2FA、仅 +86、双人制衡残余等,见 `docs/reference/security.md`「已接受取舍」,勿再单独立项。
- **管理端全角色强制 TOTP。** 开关 `admin_mfa_enabled`(默认开),只有全员开 / 全员关两档;关闭是运营决定(prod 在线禁关,只可经部署层变更),关闭时产生配置中心告警并记审计 reason;登录限流四层桶保留。见 `docs/reference/admin.md`。
- **日志 PII / 凭据全局脱敏。** `apps/api/app/core/logging.py` 按键名(phone / id_number / token / secret / password / code)兜底打码。
- **账号级登录锁定。** 账号维 15 分钟窗 + 日窗阶梯锁定,与 IP 维桶叠加。约束:**管理端的日窗账号桶只在失败后计数,不进 bcrypt 前的准入预检**(用户端仍进)。见 `docs/reference/admin.md`。
- **租户 SSH 入方向只排 Pod 网段,不排整段私网。** 22 端口的 from 是 `0.0.0.0/0` except `tenant_pod_cidr`(默认 `10.42.0.0/16`)。约束:改 CNI 网段必须同步改 `tenant_pod_cidr`;留空只作排障临时回退。见 `docs/reference/security.md`。
- **安全功能是开关,不是 mock 提供方。** 人机验证、实名、管理端 MFA 用 `*_enabled` 布尔开关(平台配置·安全策略组);只有流程无它完不成的第三方各保留唯一替身(`sms_provider=mock` / `payment_mock` / `k8s_backend=fake`),prod 拒绝。prod 在线写库层禁关安全开关(`SettingSpec.prod_forbidden`);人机验证 / 实名 / 充值强制实名另有启动合规闸(同一 spec 上的 `prod_gate=True`,配置页红牌、启动 fail-fast 与写入守卫都从这一处派生)。约束:`/auth/sms-code` 的 `captcha_token` 可选(开启时缺失 400)。
- **停机实例保留 SSH NodePort,池水位进指标。** 停机不释放端口(用户重启后端口不变);`superdl_ssh_port_pool_ports{state}` 由 reconciler 每轮刷新,`SshPortPoolLow` / `SshPortPoolExhausted` 告警;长期停机实例由保留期回收释放。见 `docs/reference/limits.md`。
- **同一证件绑定账号数有上限。** 实名通过时落带密钥摘要 `users.id_number_hmac`(`crypto.hash_id_number`,原文仍不落库),同摘要的非注销账号数 ≥ `real_name_max_accounts_per_identity`(默认 3)即 409;注册计数 `superdl_user_signup_total` 与短信计数 `superdl_sms_sent_total` 进滥用告警组。见 `docs/reference/account.md`。
- **库应用角色与 owner 分离,连接串 verify-full。** api / worker 用无 DDL 的 `superdl_app`(`balance_ledger` 只追加、`audit_log` 不可改),迁移 Job 单独挂 `superdl-db-migrate`(owner);自签 CA 经 ConfigMap `superdl-db-ca` 挂进 Pod,`sslrootcert` 在连接串里由 `db._split_db_tls` 翻成 SSLContext。约束:迁移 Job 与备份 CronJob 与其它平台组件同锚点钉 infra 节点。见 `deploy/pg/README.md`、`deploy/app/secrets.example.yaml`。
- **平台自己生成的 K8s 对象必须能过自家准入。** 受管 Job(擦盘/配额)模板显式 `hostUsers: false`;CI 在准入策略仍在集群里时用 `scripts/render_admission_probes.py` 渲染实例五种形态与三类 Job 及其派生 Pod 做 `--dry-run=server`,之后才删策略跑裸 kind 冒烟。见 `docs/reference/orchestrator.md`。
- **prod 启动校验只管 provider,不管凭据齐全性;真实集群不绑定 prod。** `_validate_prod` 只拒 sms / payment 的 mock provider 与基础设施占位值,凭据齐全性交给运行期渠道工厂 fail-closed。组合约束 `real_name_required_for_recharge ⇒ real_name_enabled` 任意环境生效(`_validate_invariants` 与写入侧 `_check_real_name_invariant` 同口径);`alertmanager_token` 缺失与 `prometheus_url` 指向本地是启动 WARNING;dev + real 允许共存。约束:边缘收口 `edge_guard` 在 prod 恒开、无开关,类生产环境一律以 prod 运行。

## 编排与平台

- **策略参数与平台配置同一注册表,读取面强类型。** 运营策略(盘价 / 宽限天数 / 配额 / 折扣 / 抢占宽限等)是平台配置中心的 `policy` 组,与渠道凭据、安全开关同一份 `SETTING_SPECS`、同一张 `platform_settings` 表;`get_runtime_config(session)` 返回 `RuntimeConfig`(bool / int / Decimal / str 按 kind 定型),业务代码不再拿字符串字典比较 `"true"`。约束:新增配置项 = 加一条 `SettingSpec` + `RuntimeConfig` 一个字段 + `Settings` 同名默认值(单测锁定三者一致);`/policies`(ops)只收 policy 组、`/platform-config`(admin)不收 policy 组;prod 禁止取值、启动合规闸、配置页红牌全部由 spec 的 `prod_forbidden` / `prod_gate` 派生,不另写清单。
- **模块公开面显式列出,依赖单向。** `orchestrator/service.py` 依赖 `billing/service.py`(建实例扣款、转换结算);billing 反向只经 `orchestrator/queries.py`(只读查询)与 `orchestrator/transitions.py`(`transition` 原语、`system_stop` / `freeze_instance` / `unfreeze_instance` / `reclaim_frozen` / `stop_all_for_user`、数据盘欠费链 `arrears_transition_disks`),二者不 import billing。约束:新增「billing 需要编排做的事」放 `transitions.py` 并以回调注入结算(如 `settle_pending`),不许在 billing 里函数内 import `orchestrator.service`;`account/deletion.py` 与 `account/sshkeys.py` 因依赖 billing / orchestrator 而独立于 `account/service.py`(后者被 billing 依赖)。见 `apps/api/pyproject.toml` 的 import-linter 契约与 `tests/test_import_order.py`。

- **reconciler 两阶段。** 事务内只做状态迁移 / 标记 / enqueue,K8s 动作在 commit 后或经 outbox 执行;失败由泄漏回收宽限后强删兜底。见 `apps/api/app/modules/orchestrator/reconciler.py`。
- **抢占排序只按创建时间。** `created_at` 从新到旧(同刻 `id` 降序),配套**只在同池同型号内选**、**凑不够一台都不动**。约束:`preempt.pick_victims` 的排序与候选谓词改动**等同于改用户可见文案**,两边同提交。见 `docs/reference/orchestrator.md`。
- **抢占宽限窗用 outbox 的延迟投递实现。** `apps/api/app/core/outbox.py` 的 `enqueue` 带 `delay_seconds`;状态机立刻迁 `stopping`、Pod 到期才删。约束:`spot_grace_seconds` 真实上限是 `creating_timeout_seconds − PREEMPT_TIME_RESERVE_SECONDS`,由 `validate_policy_value` 跨键校验拦住。见 `docs/reference/limits.md`。
- **worker 拆成 5 个组件 Deployment**(core / tenant-mgr / node-mgr / prewarm / disk-ops),RBAC 按组件最小化。约束:发布必须成组。见 `deploy/README.md`、`deploy/app/k8s/03-worker.yaml`。
- **控制面 HA 与平台组件落点。** 公众生产强制 3 台 server 堆叠 etcd + VIP;平台组件以 `node-restriction.kubernetes.io/superdl-infra` 标签选址。**前缀取 kubelet 打不上的那一族**(`node-restriction.kubernetes.io/*` 被 NodeRestriction 拉黑,两份 server-config 的 `kube-apiserver-arg` 显式钉住该插件),标签由 `deploy/ansible/site.yml` 装机后用管理凭据打,平台 SA 无权改(准入策略③只放行 `superdl.io/*`)。约束:light 档(k3s 单机)只做试点与联调,禁止公众生产。见 `deploy/cluster/README.md`。
- **节点退役的角色门是 `ops`,不抬到 admin-only。** 与 cordon / 强制停止 / 强制回收同档;不可逆性由必填 reason + 审计承担。约束:「能删哪些节点」由准入策略⑦(拒删控制面 / etcd / infra 节点)界定而不由 RBAC;join token 轮换与 kubelet 证书吊销是控制面动作,平台不执行,回执文案里明写交回运维。见 `docs/reference/nodes.md`。
- **实例盘销毁带 TRIM。** TopoLVM lvmd `issue_discards=1`。
- **JuiceFS 关闭 writeback。**
- **镜像仓库定为 Harbor,接入参数入配置中心。** `registry_host / registry_project / registry_robot_name / registry_robot_secret(加密)/ registry_ca_pem / registry_proxy_projects` 与 `image_allowed_registries` 在平台配置·镜像仓库组;平台自身镜像(api/web/admin)的仓库地址在部署侧。约束:白名单不是 prod 启动硬闸(Harbor 地址自动放行,为空只给配置告警)。
- **拉取凭据由平台托管为 imagePullSecrets。** `image_ref` 存 Harbor 全限定名(无逻辑名);worker 在建实例 Pod / 预热 Job 前按生效配置把 dockerconfigjson Secret `superdl-registry-pull` 按指纹写入 superdl 与各租户 ns(`ensure_pull_secret`),Pod / Job 以 `imagePullSecrets` 引用;节点 registries.yaml 只留 Spegel / CA / 代理缓存 mirror。约束:轮换 = 配置中心保存新 Secret;换 Harbor 域名要 SQL 批量改 `images.image_ref`(实例快照不改)。
- **平台镜像 tag 语义化且可覆盖重推,目录 `image_ref` 钉 digest。** tag 只表达「框架版本 + CUDA 线 + Python」;`images.image_ref` 一律 `<repo>:<tag>@sha256:<digest>`。约束:重推后在管理端把该镜像 ref 换成新 digest,`admin_update_image` 同事务清掉节点缓存行,巡检按新 ref 重新预热;实例 Pod 与预热 Job 保持 `IfNotPresent`;实例 ref 是创建时快照,镜像修复只对新建实例生效。
- **加固基线为 SSH 让出 `SYS_CHROOT` / `SETUID` / `SETGID` 三个 capability。** 配套:entrypoint 起 sshd 前 `chmod g-w,o-w /root`;推送前自检真连一次 SSH。
- **light 单机的 server 兼 GPU 节点走同一条 node-join 命令。** 脚本检测到本机 `k3s.service` / `rke2-server.service` 在运行即跳过 agent 三步,池标签经本机 kubectl 打到节点对象;`--uninstall` 在 server 本机不执行发行版卸载脚本。约束:server 本机的实例盘 VG 须先于 helmfile 手工建好;`canonical_gpu_model` 识别 `CMP<数字>HX`(型号取自 lspci 方括号名)。见 `docs/reference/nodes.md`。
- **两档集群装同一套 GPU 栈:light 也上 gpu-operator 与 kata-deploy。** 差异收敛成 `deploy/cluster/values/light/` 覆盖(light 只多 `toolkit.enabled=false`)。operand 落点标签(hami 池 `nvidia.com/gpu.deploy.device-plugin=false`、kata 池 `nvidia.com/gpu.workload.config=vm-passthrough`)由 node-join 随池标签一起打。约束:档位可用性只看「池里有没有 Ready 节点 + 运行时是否到位」;单机 light 只有一个池标签。见 `deploy/cluster/README.md`。
- **`helmfile apply` 收进 `deploy/cluster/apply.sh`。** 两个开关每次都带:`HELM_DIFF_USE_UPGRADE_DRY_RUN=true`、`--skip-diff-on-install`。
- **DCGM 采集面按档位分:light 收 device 级,exporter 镜像钉 4.8.3。** 约束:节点维标签是小写 `hostname`;`prom.py` 的 `DCGM_NODE_LABEL` 与两条 GPU 告警的 `$labels.hostname` 按小写写死。
- **chart 默认值里的 `0` 会让 helm upgrade 失败,在 values 里显式钉成等效值。** kube-prometheus-stack 的 `prometheusSpec.maximumStartupDurationSeconds` 钉 900。新加 release 照此复核。
- **light 档 TopoLVM controller 取 1 副本。**
- **档位收敛成 dedicated / shared / cpu,隔离机制的派发键是节点池。** `skus.tier` 只表达售卖分类;隔离机制按 `pool_label`(kata / mig / hami)派发。用户看到的「共享·标准 / 共享·经济」由池派生(mig = 硬切分标准档,hami = 软切分经济档)。约束:合法配对由 `apps/api/app/core/gpu_adapter` 的 `TIER_POOLS` + `apps/api/app/modules/catalog/service.py::_check_tier_pool` 收口;管理端表单只让运营选展示档位;「改池」仅下架态可用;`unknown pool` fail-closed。
- **HAMi 池不是安全边界,是成本优化手段。** hami 池定位是**软件限额/性能隔离**,不作多租户安全隔离。运营方可用 `SUPERDL_SHARED_TIER_ALLOWED_POOLS` 把共享档限制为仅 MIG,或以前端知情同意 modal 告知用户。同写进 `reference/security.md` 隔离级别分级表。
- **纯 CPU 实例是第三档 `tier=cpu`,允许挂 hami 池。** 每个 GPU 节点由策略 `gpu_node_cpu_instance_vcpu_cap`(默认 16,0 = 禁止)封顶,只是**库存口径**,不下发调度。约束:① `build_gpu_request` 里 `gpu_count == 0` 的判定**先于池分支**,且下发门禁同一判据(CPU 实例 `schedulerName` 为空);② `build_pod_spec` 显式写「GPU 档按卡数放大、CPU 档倍率恒 1」,不写 `max(1, gpu_count)`。
- **CPU 实例的计费份数收口到 `apps/api/app/core/money.py` 的 `billing_units`,不动 `price_hourly` 语义。** 金额 = `单价 × 份数 × 秒 / 3600`,`billing_units(gpu_count) = gpu_count or 1`。约束:所有「单价 × 份数」只经 `billing_units` 与 `hourly_cost`;`price_hourly` 语义写进 `docs/reference/catalog.md`(GPU 规格 = 单卡时价,CPU 规格 = 整机时价);账单行照实存 `gpu_count=0`。
- **DNS01 走 acme-dns 中转,按档位启用。** 集群内只持有能改 `_acme-challenge` 子域 TXT 的账户。约束:light 档 `acmeDns.enabled=false`,泛域名证书(`superdl-jupyter-wildcard-tls` 与 `superdl-svc-wildcard-tls`)手工灌入 `superdl` ns。见 `deploy/cluster/runbooks/acme-dns.md`。
- **集群配置键中性化。** 键统一 `cluster_*`,发行版由平台探测 gitVersion 派生,无 `k8s_distro` 键。
- **不引 Sentry 类 SaaS。** 未捕获异常统一 500 留痕并经 Loki / Prometheus 告警。
- **管理端监控自绘,Grafana 只作外链。** 不做 iframe,`grafana_url` 未配置只显示一行提示。
- **告警 `runbook_url` 只加在有专属 runbook 的规则上。** 其余告警第一步写在 summary 与 `deploy/cluster/runbooks/README.md` 索引表。
- **北向唯一入口是 Gateway API + Envoy Gateway。** `GatewayClass superdl` + 一个 `Gateway superdl`(ns `superdl`)带 6 个 listener(`http` / `api-https` / `console-https` / `admin-https` / `app-https` / `svc-https`)+ 4 条平台域 HTTPRoute 与 1 条 80→443 跳转;租户 Jupyter 与服务端点**每实例一条 HTTPRoute**,建在租户 ns,跨 ns 靠 `allowedRoutes.namespaces.from: Selector` + `superdl.io/managed=true`。约束:`deploy/cluster/values/cilium.yaml` 的 `gatewayAPI` 保持 false。
  - **CRD 的 channel 首装即定(experimental)。** CRD 生命周期单点收进 `deploy/cluster/gateway-api-crds.sh`(chart 侧一律 `crds.enabled=false`),脚本自带 channel 前置闸门,`deploy/cluster/preflight.sh` 复核。
  - **`limit-connections`(每源 IP 并发连接)没有等价物**,EG 只有每 Envoy 实例的连接总量。每源 IP 限流靠 `BackendTrafficPolicy` 的 `sourceCIDR.type: Distinct`,源 IP 白名单靠 `SecurityPolicy.authorization`(`defaultAction: Deny` + `clientCIDRs`),三者以 `externalTrafficPolicy: Local` 为前提。见 `docs/reference/security.md`「限流分层」。
  - 入口坐标是 `apps/api/app/core/k8s/base.py` 的三个常量;`cluster_status.gateway_ready` 判据是 Gateway 对象 `Programmed=True`;凡按 ns 名认入口的地方(NetworkPolicy 来源、准入豁免名单)一律 `envoy-gateway-system`。
  - TLS 不用 ingress-shim 注解,在 `deploy/app/k8s/05-cert-manager.yaml` 显式写 Certificate,由 listener 的 `certificateRefs` 引用。
  - CI 闸门 `scripts/check-gateway-manifests.py` 按钉死那版 chart 的 CRD 校验 `deploy/app/k8s/04-gateway.yaml`。
  - `streamIdleTimeout` 显式配 1h;listener `sectionName` 写错只让策略静默失效,线索在 `status.ancestors[].conditions`。
  - 路由条数随活跃实例线性增长,light 档 memory limit 必须实机压过再定。

  见 `deploy/cluster/README.md`、`deploy/app/k8s/04-gateway.yaml`。

- **部署服务是独立聚合根,实例是它的不可变版本。** `services` 只存身份与网关侧属性(`public_slug`、`require_api_key`、`desired_state`、当前 / 候选实例指针、版本计数);服务状态由 `desired_state` + 当前 / 候选实例的状态与就绪位**派生、不落库**。每次部署 = 一台新实例,`instances.service_id` 反指。**计费、配额、回收、reconciler、迁移监听的主体仍是实例**;服务级操作委托 `orchestrator.service` 的 row 级函数,orchestrator 不反向依赖 services、不查 `services` 表。约束:实例级生命周期端点(stop / start / restart / DELETE)对服务实例一律 409,购买模式类端点照常;`POST /instances` 不接受服务字段;版本更新 v1 只做 recreate(期间端点 503;slug / URL / API Key 不变),不对包周期服务开放;HTTPRoute 仍每实例一条,`rollout_instance_id` 为蓝绿预留。见 `docs/reference/services.md`。
- **`instances.workload_type` 只决定 Pod 形态。** `dev` / `service` 在 `build_pod_spec` 分叉,reconciler 的 `pod_unready` 豁免也按它判;与 `service_id` 同真同假(CHECK)。
- **对外服务的鉴权放在网关,不要求用户容器自己实现。** 一条 `extAuth` SecurityPolicy 挂 `svc-https` listener,对象数 O(1),吊销即时生效。约束:**鉴权结果没有任何缓存**,控制面是全部对外服务的同步依赖。见 `docs/reference/services.md`。
- **部署服务与创建实例的入口分流,不共享页面。** 算力市场结算条只有「下一步:配置实例」一个 CTA,只建开发机;部署服务只从「在线服务」页与命令面板进 `/services/new`,规格在部署页内选。
- **服务型实例持续 not-ready 不判故障。** `workload_type='service'` 时 reconciler 跳过 `pod_unready` 一支,实例留在 running;`pod_lost` 与 `node_lost` 不豁免。配套:服务容器必配 `startupProbe`(15 分钟启动预算)。见 `apps/api/tests/test_orchestrator_lifecycle.py::TestServiceWorkloadUnreadyExemption`。

## 功能缺口路线图(仅方向,未排期;实施前各自补设计)

约束:每一项动工前先在本文档补「决定与约束」条目,不得在代码里先行留半成品。

| 优先级 | 功能                              | 设计方向要点                                                                  |
| ------ | --------------------------------- | ----------------------------------------------------------------------------- |
| R1     | 配额主动展示页                    | 设置页加「我的配额」卡;数据源 policies + user_quota_overrides,无新后端        |
| R2     | 优惠券/营销体系                   | 新模块 coupon:码 → 抵扣规则 → 下单/充值核销;先定「能否提现/退款回流」资金口径 |
| R3     | 磁盘快照                          | 依赖 TopoLVM VolumeSnapshot;编排侧新增 snapshot 状态机与计费口径              |
| R4     | 发票在线版式                      | 对接电子发票服务或自生成 PDF;人工开具流程保留兜底                             |
| R5     | 变配/重装/VNC                     | 变配涉及价格差结算与 SKU 迁移语义;VNC 需 console 网关                         |
| R6     | 帮助文档站                        | 静态站(如 VitePress)挂 help 子域,替换现有 8 条 FAQ                            |
| R7     | admin 端 token 改 HttpOnly Cookie | 同 web 端模式,待 MFA 体系稳定后随动                                           |
| R8     | 敏感配置双人闸                    | pending change + 第二 admin 批准队列,替代/叠加 prod 降防开关的硬禁止          |
