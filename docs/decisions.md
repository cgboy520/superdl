# 决策记录

跨模块或容易被反复质疑的决定,每条写「决定是什么」与「它对后来的改动施加了什么约束」。
领域内部的取舍就近记在模块文档里。

## 工程流程

- **直接在 `main` 提交,不建分支、不发 PR。** 单维护者 + AI 代理协作,没有第二个人做 PR 评审。
  约束:回滚粒度是单个提交,「一个提交一件事」是硬要求。
- **本地闸门是事实源,CI 是复跑。** 闸门按改动范围跑,红了不提交。依赖漏洞、gitleaks、kubeconform、
  kind 冒烟只在 CI 跑。
- **不设覆盖率阈值。** 用例必须能回答「它挂了说明什么坏了」。
- **命令清单只有一份**:CLAUDE.md「常用命令」;README 只放快速开始。
- **私有仓库,不放 LICENSE。** 默认保留全部权利;转公开或对外交付前先定许可证。
- **release notes 不手维护。** 打 tag 用 `gh release create --generate-notes`,不设 CHANGELOG 文件。
- **orval 只生成 fetcher 与 model 类型,不生成 TanStack Query hooks。** `packages/api-client` 用
  `client: "fetch"`;两端在各自 api 层(`apps/web/src/api/*.ts`、`apps/admin/src/api.ts`)用
  useQuery/useMutation 包 fetcher,查询键、失效域与轮询策略都在那里定义。
  约束:不要把 `client` 改回 `react-query` 或加 `query` 块。
- **UI 占位项的去留有判据。** 只有「已排期、按当前设计确定要做」的能力才留 disabled 占位并注「即将上线」。
  兑现前重新核对该能力是否仍成立(如「驱逐重调度」不成立——实例盘是节点本地 LV,换节点等于丢盘)。
  约束:变动时 `docs/ui-ux-spec.md` 的占位清单与 `docs/reference/web.md` 同提交更新。

## 计费与资金

- **余额归零即回收。** 冻结判据是 `balance > 0` 才放行,余额恰好 0.00 的账户照常进停机 → 冻结 → 回收链,
  与停机判据 `effective <= 0` 自洽。由 `apps/api/tests/test_billing_flow.py::test_zero_balance_stops_then_freezes_then_reclaims` 锁定。
- **计费只认事件流水,指标只做对账。** Prometheus 全挂结算照常。见 `docs/architecture.md` §4、`docs/reference/billing.md`。
- **不做渠道原路退款。** 退款单审批不动钱包,财务登记打款成功才负向核销,审批与打款分人。见 `docs/reference/payment.md`。
- **票款双重兑现闸有两道**:申请退款时拒已开票账期(并对该账期的活跃发票申请行加锁,与开票串行);
  开票时行锁内按当前口径(在途退款预扣)重算,不符即驳回重申。见 `docs/reference/payment.md`、`apps/api/tests/test_invoices.py`。
- **出金与入账的审计同事务。** 审计行写失败即出金失败回滚(`write_audit_sync`)。
- **结算缺口只登记不自愈。** 追平截断 / 死信 / 水位线丢失一律落 `settlement_gaps` 并持续告警,由人工重放或核销。
  见 `docs/reference/billing.md`。
- **包周期走独立订阅单一次性预扣,不摊成零元小时账。** `bills_hourly` 的结构、幂等键、水位线与缺口机制一行不动。
  约束:跳过点只有 `apps/api/app/modules/orchestrator/queries.py::billing_candidates` 一处;购买模式必须在四处配套
  过滤里同时排除(燃烧率 / 停机判据 / 冻结与解冻 / 尾账),四处逐条列在 `docs/reference/billing.md`,各有一条用例锁住。
- **周期取定长小时,不取自然月。** day=24 / week=168 / month=720 / year=8760,到期时刻与定价用同一个数。
  约束:`PERIOD_HOURS` 是常量、不进策略参数,改它等于同时改价与改到期口径。
- **预付不退款,中途释放只作废订阅。** 实例进入 `releasing` 时把 active 订阅转 `cancelled`,不生成退款流水;
  确需退款走人工 `refund_requests`。约束:作废挂在状态迁移监听器上而不是 `release_instance` 里,以覆盖用户释放、欠费回收、到期回收、
  管理端强制回收四条路径;已 `expired` 的历史行不动,它是账期凭证。
- **到期不自动转按量。** 到期即停机 → 冻结 → 72 小时后回收(与欠费共用 `freeze_grace_hours`)。
  配套:到期前 3 天预警、可开自动续费(默认关)、冻结窗口内续费即解冻。
- **续费按下单时的原价快照重新定价。** 基准是 `subscriptions.unit_price`(下单那一刻的 SKU 原价时价),不是 SKU 现价(换周期续时按新周期重新取折扣)。起算时刻取 `max(老到期时刻, 现在)`。
  约束:续费**新开一行**并用 `renewed_from_id` 串链,不在原行累加——跨月续费的两段金额必须落在各自的行上。
- **未到期的包周期实例即使已停机也仍占库存。** 软准入的 `_reserved_slots` 把同一条 SKU 上「stopped / frozen 且仍在保」
  的实例计为占用。**只在平台层预留,物理层不预留**,所以这一条必须出现在创建页与续费入口的说明里。
  只算同一条 SKU:软准入本来就是近似闸门。
- **按量可以就地转包周期,转换点两侧各结各的账。** `POST /api/v1/instances/{uuid}/subscribe` 同一事务内
  **先结后翻**:先 `settle_on_demand_up_to` 用转换前的按量时价结清水位线之后到当前自然小时的账,再落订阅行、
  预扣、翻 `market`、刷 `price_hourly`。约束:顺序反了 `billing_candidates` 会按新 market 排除该实例,
  未出账的小时永远没人结;**幂等重放必须在全部守卫之前判**(重放时 `price_hourly` 已是折后价);
  **结算滞后超 48 小时拒绝转换(409),刻意 fail-closed**。反向不开:包周期转不回按量。
  `instances.market` 不是不可变快照,`subscribe_instance` 是唯一会改它的路径。
  见 `docs/reference/billing.md`、`docs/reference/orchestrator.md`。
- **竞价被抢占照常结算,不免单;结算引擎零改动。** 被抢占实例迁 `stopping` 时由既有 `edge_listener` 出尾账,
  按实际运行秒数结算。约束:`billing_candidates` 的跳过条件仍只有 `market != 'subscription'` 一条,竞价对结算完全透明;
  折扣只落在 `instances.price_hourly` 上,由 `apps/api/app/core/pricing.py` 的 `price_for` 单点算。
  见 `docs/reference/billing.md`。
- **竞价转按量按「一小时一价」处理跨价的那个小时。** `bills_hourly` 一小时只有一行、一个 `unit_price`,口径是
  「以结算时的实例单价为准」:`settlement.reprice_current_hour` 在钱包行锁内改写 `unit_price`、按新价重算 `amount`、
  补扣差价并打 `detail.repriced`。约束:转换对用户必然是涨价(`spot_discount_pct` 上界 90),所以只补扣、不退款;
  这一条必须写进转换确认弹窗。见 `docs/reference/billing.md`。

## 安全

- **安全取舍集中在一处。** token 存 localStorage、固定窗口限流、用户端无 2FA、仅 +86、双人制衡残余等,
  见 `docs/reference/security.md`「已接受取舍」,勿再单独立项。
- **管理端全角色强制 TOTP。** 收成安全策略开关 `admin_mfa_enabled`(默认开),只有全员开 / 全员关两档。
  关闭是运营决定,代价是配置中心告警 + 审计 reason。登录限流四层桶保留为纵深,见 `docs/reference/admin.md`。
- **日志 PII / 凭据全局脱敏。** `apps/api/app/core/logging.py` 按键名(phone / id_number / token / secret /
  password / code)兜底打码,防新增日志点漏脱敏。
- **账号级登录锁定。** 账号维 15 分钟窗 + 日窗阶梯锁定,与 IP 维桶叠加——撞库可以换 IP,换不了目标账号。
- **安全功能是开关,不是 mock 提供方。** 人机验证、实名、管理端 MFA 一律用 `*_enabled` 布尔开关表达
  (平台配置·安全策略组);只有流程无它完不成的第三方各保留唯一一个替身(`sms_provider=mock` /
  `payment_mock` / `k8s_backend=fake`),prod 拒绝。prod 允许关闭安全开关,代价是配置中心红牌 + 审计 reason,
  不是启动拒绝。约束:`/auth/sms-code` 的 `captcha_token` 是可选参数(开启时缺失 400)。
- **prod 启动校验只管 provider,不管凭据齐全性;真实集群不绑定 prod。** `_validate_prod` 只拒 sms / payment 的
  mock provider 与基础设施占位值,凭据齐全性交给运行期渠道工厂 fail-closed。唯一组合约束
  `real_name_required_for_recharge ⇒ real_name_enabled` 任意环境生效(`_validate_invariants` 与写入侧
  `_check_real_name_invariant` 同口径);`alertmanager_token` 缺失与 `prometheus_url` 指向本地是启动 WARNING;
  dev + real 允许共存。约束:暴露面由部署拓扑(网关 / 公网 DNS)决定,不由 environment 决定;边缘收口
  `edge_guard` 在 prod 恒开、无开关,类生产环境一律以 prod 运行。

## 编排与平台

- **reconciler 两阶段。** 事务内只做状态迁移 / 标记 / enqueue,K8s 动作在 commit 后或经 outbox 执行;
  失败由泄漏回收宽限后强删兜底。见 `apps/api/app/modules/orchestrator/reconciler.py`。
- **抢占排序只按创建时间。** 按 `created_at` 从新到旧(同刻用 `id` 降序破平),配套两条同样刚性的规则:
  **只在同池同型号内选**、**凑不够一台都不动**。约束:`preempt.pick_victims` 的排序与候选谓词改动**等同于改用户可见文案**,两边同提交;「一台实例腾出
  `gpu_count` 张卡」沿用软准入既有的近似,偏乐观的后果由 creating 超时转 failed、全额不出账兜底。
  见 `docs/reference/orchestrator.md`。
- **抢占宽限窗用 outbox 的延迟投递实现,不新造机制。** `apps/api/app/core/outbox.py` 的 `enqueue` 带可选
  `delay_seconds`,写 `next_retry_at = now + delay`;状态机立刻迁 `stopping`、Pod 到期才删,宽限窗内 SSH 仍可登。
  约束:`spot_grace_seconds` 的真实上限是 `creating_timeout_seconds − PREEMPT_TIME_RESERVE_SECONDS`,
  由 `validate_policy_value` 的跨键校验拦住;调大宽限窗要先调大 creating 超时。见 `docs/reference/limits.md`。
- **worker 拆成 5 个组件 Deployment**(core / tenant-mgr / node-mgr / prewarm / disk-ops),RBAC 按组件最小化。
  约束:发布与回滚必须成组。见 `deploy/README.md`、`deploy/app/k8s/03-worker.yaml`。
- **控制面 HA 与平台组件落点。** 公众生产强制 3 台 server 堆叠 etcd + VIP;平台组件以
  `node-role.superdl.io/infra` 标签选址,不绑死 control-plane。约束:light 档(k3s 单机)只做试点与联调,
  禁止公众生产。见 `deploy/cluster/README.md`。
- **实例盘销毁带 TRIM。** TopoLVM lvmd `issue_discards=1`,`lvremove` 对 extent 发 NVMe TRIM。
- **JuiceFS 关闭 writeback。** 数据盘一致性优先于顺序写性能。
- **镜像仓库定为 Harbor,接入参数入配置中心。** `registry_host / registry_project / registry_robot_name /
  registry_robot_secret(加密)/ registry_ca_pem / registry_proxy_projects` 与镜像来源白名单
  `image_allowed_registries` 都在平台配置·镜像仓库组;平台自身镜像(api/web/admin)的仓库地址在部署侧
  (CI 与清单占位),不依赖 DB。约束:白名单不是 prod 启动硬闸(Harbor 地址自动放行,为空只给配置告警)。
- **拉取凭据由平台托管为 imagePullSecrets。** kubelet 的 imagePullSecrets 按镜像主机名匹配,containerd 不会把它
  转给 mirror 主机,因此逻辑名与 K8s 原生凭据不可兼得:`image_ref` 存 Harbor 全限定名;worker 在建实例 Pod /
  预热 Job 前按生效配置把 dockerconfigjson Secret `superdl-registry-pull` 按指纹写入 superdl 与各租户 ns
  (`ensure_pull_secret`,指纹相同跳过),Pod / Job 以 `imagePullSecrets` 引用;节点 registries.yaml 只留
  Spegel / CA / 代理缓存 mirror。约束:轮换 = 配置中心保存新 Secret;换 Harbor 域名要 SQL 批量改
  `images.image_ref`(实例快照是历史值,不改)。
- **平台镜像 tag 语义化且可覆盖重推,目录 `image_ref` 钉 digest。** k3s 内置 registry(Spegel)按 **tag** 解析
  会返回节点自己缓存的旧 digest,`imagePullPolicy: Always` 也拉不到新镜像
  。tag 只表达「框架版本 + CUDA 线 + Python」并允许覆盖重推,`images.image_ref` 一律写 `<repo>:<tag>@sha256:<digest>`。
  约束:重推后在管理端把该镜像的 ref 换成新 digest,`admin_update_image` 会同事务清掉该镜像的节点缓存行,
  巡检按新 ref 重新预热;实例 Pod 与预热 Job 都保持 `IfNotPresent`(开机不依赖仓库可达);实例的 ref 是创建时
  快照且终身不变,镜像修复只对新建实例生效。
- **加固基线为 SSH 让出 `SYS_CHROOT` / `SETUID` / `SETGID` 三个 capability。** OpenSSH 的预认证特权分离强制且
  不可配置(必须 `chroot("/run/sshd")` 再 setgid/setuid),`drop ALL` 会让每个连接在密钥交换阶段就 Connection reset。
  配套:entrypoint 起 sshd 前 `chmod g-w,o-w /root`(TopoLVM 把实例盘挂载点留成 2777,sshd 的 StrictModes 会因此
  拒绝公钥认证,与 capability 相互独立);推送前自检要真连一次 SSH。
- **light 单机的 server 兼 GPU 节点走同一条 node-join 命令。** 脚本检测到本机 `k3s.service` / `rke2-server.service`
  在运行即跳过 agent 三步,池标签经本机 kubectl 打到节点对象,驱动版本在打标签前上报;`--uninstall` 在 server
  本机不执行发行版卸载脚本。约束:server 本机的实例盘 VG 不在 node-join 建,须先于 helmfile 手工建好;
  `canonical_gpu_model` 识别 `CMP<数字>HX`(nvidia-smi 只报通用名的卡,型号取自 lspci 方括号名)。
  见 `docs/reference/nodes.md`。
- **两档集群装同一套 GPU 栈:light 也上 gpu-operator 与 kata-deploy。** 差异收敛成 `deploy/cluster/values/light/`
  的覆盖 —— light 只多一条 `toolkit.enabled=false`(装机基线已装 nvidia-container-toolkit、k3s 自行探测生成
  RuntimeClass `nvidia`,再让 operator 改一遍会被下次启动覆盖回去)。GPU Operator 的 operand 落点标签
  (hami 池 `nvidia.com/gpu.deploy.device-plugin=false`、kata 池 `nvidia.com/gpu.workload.config=vm-passthrough`)
  由 node-join 随池标签一起打,漏打会让官方 device-plugin 与 HAMi 抢注 `nvidia.com/gpu`。
  约束:档位可用性只看「池里有没有 Ready 节点 + 运行时是否到位」,与发行版无关;单机 light 只有一个池标签。
  见 `deploy/cluster/README.md`。
- **`helmfile apply` 收进 `deploy/cluster/apply.sh`。** 两个开关必须每次都带,漏一个 apply 就中途失败且报错不指向
  真正的原因:`HELM_DIFF_USE_UPGRADE_DRY_RUN=true`(helm-diff 默认客户端渲染,模板里的 `lookup` 恒空,
  kata-deploy 的身份校验据此拒绝升级)、`--skip-diff-on-install`(gpu-operator 首装时 ClusterPolicy CRD 尚不存在,
  服务端 dry-run 报 no matches for kind)。
- **DCGM 采集面按档位分:light 收 device 级,exporter 镜像钉 4.8.3。** gpu-operator 默认字段清单含 `DCGM_FI_PROF_*`
  (DCP),消费级卡(CMP 170HX)的 profiling 模块初始化即 unrecoverable error,exporter 起不来。
  约束:节点维标签在 dcgm-exporter 4.8.3 是小写 `hostname`,而 gpu-operator v26.3.3 默认仍是 4.8.2;
  `prom.py` 的 `DCGM_NODE_LABEL` 与两条 GPU 告警的 `$labels.hostname` 都按小写写死,故钉镜像而不是改三处选择器。
- **chart 默认值里的 `0` 会让 helm upgrade 直接失败,在 values 里显式钉成等效值。**
  kube-prometheus-stack 的 `prometheusSpec.maximumStartupDurationSeconds: 0` 被 CRD 拒(须 ≥60),钉 900
  (prometheus-operator 自身默认),行为不变。新加 release 时照此复核。
- **light 档 TopoLVM controller 取 1 副本。** chart 默认 2 副本 + 按 hostname 的 required 反亲和,单节点上第二个副本永远 Pending,会让「全部 Pod Running」这类巡检判据失效。
- **档位收敛成 dedicated / shared / cpu,隔离机制的派发键是节点池。** `skus.tier` 只表达售卖分类;隔离机制一律按
  `pool_label`(kata / mig / hami)派发——它装机时定死,是物理事实源。用户看到的「共享·标准 / 共享·经济」由池派生
  (mig 池 = 硬切分标准档,hami 池 = 软切分超卖经济档)。约束:合法配对由 `apps/api/app/core/gpu_adapter` 的
  `TIER_POOLS` + `apps/api/app/modules/catalog/service.py::_check_tier_pool` 收口(建 SKU 与改池两条路径共用),
  管理端表单只让运营选展示档位、tier 与 pool 由它派生;「改池」仅下架态可用并配可改的 `mig_profile`;
  `unknown pool` 保持 fail-closed。
- **纯 CPU 实例是第三档 `tier=cpu`,允许挂 hami 池。** 允许挂 hami 池是刻意的,让 CPU 规格吃 GPU 机长期闲置的 CPU;由策略 `gpu_node_cpu_instance_vcpu_cap`
  (默认 16,0 = 禁止)给每个 GPU 节点封顶——这只是**库存口径**上的封顶,不下发调度。
  两个「0 是合法值」的坑必须点名:① `build_gpu_request` 里 `gpu_count == 0` 的判定**必须先于池分支**,且
  **下发门禁用同一个判据**(CPU 实例 `schedulerName` 为空走默认调度器,hami-scheduler 不是它的前置);
  ② `build_pod_spec` 显式写「GPU 档按卡数放大、CPU 档倍率恒 1」而不是 `max(1, gpu_count)`
  (两者数值等价,但后者的语义是「把 0 卡当 1 卡放大」)。
- **CPU 实例的计费份数收口到 `apps/api/app/core/money.py` 的 `billing_units`,不动 `price_hourly` 语义。**
  账单金额是 `单价 × 份数 × 秒 / 3600`,`billing_units(gpu_count) = gpu_count or 1`。
  约束:计费链上所有「单价 × 份数」只经 `billing_units` 与 `hourly_cost` 两个函数(`bill_amount` / 钱包护栏 /
  燃烧率 / 对账 / 在途估算),不散写 `max(1, n)`;`price_hourly` 的语义随 SKU 形态分化并写进
  `docs/reference/catalog.md`(GPU 规格 = 单卡时价,CPU 规格 = 整机时价);账单行照实存 `gpu_count=0`。
- **DNS01 走 acme-dns 中转,按档位启用。** 集群内只持有能改 `_acme-challenge` 子域 TXT 的账户,不持有全域 DNS 凭据。
  约束:light 档 `acmeDns.enabled=false`——k3s(klipper-lb)会把它的 LoadBalancer 53 端口变成节点 hostPort 53,
  连节点自己发往 127.0.0.53 的查询也劫进来(只认 auth 子域,其余 NXDOMAIN),节点从此解析不了任何域名;
  该档的泛域名证书手工灌入 `superdl/superdl-jupyter-wildcard-tls`。见 `deploy/cluster/runbooks/acme-dns.md`。
- **集群配置键中性化。** 键统一 `cluster_*`,发行版由平台探测 gitVersion 派生,没有 `k8s_distro` 这类声明键。
- **不引 Sentry 类 SaaS。** 未捕获异常统一 500 留痕并经 Loki / Prometheus 告警,少一个外部依赖与数据出境面。
- **管理端监控自绘,Grafana 只作外链。** 不做 iframe 嵌入,`grafana_url` 未配置只显示一行提示。
- **告警 `runbook_url` 只加在有专属 runbook 的规则上。** 其余告警的第一步写在 summary 与
  `deploy/cluster/runbooks/README.md` 索引表里。
- **北向唯一入口是 Gateway API + Envoy Gateway。** 形态:`GatewayClass superdl` + 一个 `Gateway superdl`(ns `superdl`)
  带 6 个 listener(`http` / `api-https` / `console-https` / `admin-https` / `app-https` / `svc-https`)+ 4 条平台 HTTPRoute;
  租户 Jupyter 与对外服务端点都是**每实例一条 HTTPRoute**,建在租户 ns,跨 ns 靠 `allowedRoutes.namespaces.from: Selector`
  加租户 ns 已有的 `superdl.io/managed=true`。约束:`deploy/cluster/values/cilium.yaml` 的 `gatewayAPI` 保持 false——两个控制器 reconcile 同一批对象会
  互相覆盖 status 与 LB 地址。
  - **CRD 的 channel 首装即定,事后换不回去**:每源 IP 本地限流在 experimental channel,而随 CRD 一起装的
    safe-upgrades ValidatingAdmissionPolicy 用 CEL 拒绝「standard 之上装 experimental」;装错只能删净 CRD 重装,
    而删 CRD 会连带删掉集群内全部 Gateway/HTTPRoute。CRD 生命周期单点收进 `deploy/cluster/gateway-api-crds.sh`
    (chart 侧一律 `crds.enabled=false`:gateway-helm 的 CRD 子 chart 走 helm 的 `crds/` 目录,该目录在 `helm upgrade`
    时永不更新),脚本自带 channel 前置闸门,`deploy/cluster/preflight.sh` 另有复核。
  - **`limit-connections`(每源 IP 并发连接)没有等价物**,EG 只有每 Envoy 实例的连接总量,这是一处有意接受的能力回退。
    每源 IP 限流靠 `BackendTrafficPolicy` 的 `sourceCIDR.type: Distinct`,源 IP 白名单靠 `SecurityPolicy.authorization`
    (`defaultAction: Deny` + `clientCIDRs`),三者都以 `externalTrafficPolicy: Local` 为前提。
    取值与症状见 `docs/reference/security.md`「限流分层」。
  - 入口坐标是 `apps/api/app/core/k8s/base.py` 的三个常量而不是环境变量;`cluster_status.gateway_ready` 的判据是
    Gateway 对象 `Programmed=True`,不是控制器 Deployment ready;凡按 ns 名认入口的地方(NetworkPolicy 来源、
    准入豁免名单)一律是 `envoy-gateway-system`。
  - TLS 不用 ingress-shim 注解,在 `deploy/app/k8s/05-cert-manager.yaml` 里显式写 Certificate,由 listener 的
    `certificateRefs` 引用。
  - CI 闸门 `scripts/check-gateway-manifests.py` 按钉死那版 chart 的真实 CRD 校验 `deploy/app/k8s/04-gateway.yaml`——
    kubeconform 内置 schema 没有这 7 种对象只能 skip,而这个文件恰是「写错不报错」的重灾区。
  - 两个不报错的默认值:`streamIdleTimeout` 默认 5 分钟会切断 Jupyter 的 WebSocket 与 SSE;listener 的 `sectionName`
    写错只让策略静默失效,唯一线索在策略对象的 `status.ancestors[].conditions`。
  - 一实例一条 HTTPRoute 意味着路由条数随活跃实例线性增长、Envoy 内存跟着涨,light 档的 memory limit 必须实机压过再定。

  见 `deploy/cluster/README.md`、`deploy/app/k8s/04-gateway.yaml`。

- **服务容器是实例的第二种形态,不另立实体。** 加一列 `instances.workload_type`(`dev` / `service`),Pod spec 在
  `build_pod_spec` 按形态分叉,其余全部复用——状态机、计费、配额、回收、reconciler、监控、审计一行不改。见 `docs/reference/services.md`、`docs/reference/orchestrator.md`。
- **对外服务的鉴权放在网关,不要求用户容器自己实现。** 用 `extAuth`,一条 SecurityPolicy 挂在 `svc-https` listener 上服务全部端点,
  对象数 O(1),吊销即时生效。约束:**鉴权结果没有任何缓存**,控制面是全部对外服务的同步依赖。见 `docs/reference/services.md`。
- **服务型实例持续 not-ready 不判故障。** `workload_type='service'` 时 reconciler 跳过 `pod_unready` 一支,实例留在
  running,就绪与否如实呈现在服务 Tab;`pod_lost` 与 `node_lost` 两支不豁免——not-ready 判据是用户自己声明的 readinessProbe。配套:服务容器必配 `startupProbe`(15 分钟启动预算)。
  见 `apps/api/tests/test_orchestrator_lifecycle.py::TestServiceWorkloadUnreadyExemption`。
- **迁移门禁拦 alembic 的三个约束 helper。** `op.create_check_constraint` / `create_unique_constraint` /
  `create_foreign_key` 渲染出的是不带 `NOT VALID` 的 `ADD CONSTRAINT`,同样持 ACCESS EXCLUSIVE 全表扫描,只是从
  SQL 文本里看不见。`scripts/check-migration-ddl.py` 三个 helper 一并拦,本迁移内 `create_table` 新建的表豁免。

## 功能缺口路线图(仅方向,未排期;实施前各自补设计)

已知的功能完备度缺口,按资金风险与用户价值排序。约束:每一项动工前必须先在本文档补「决定与约束」条目,
不得在代码里先行留半成品(对照「UI 占位项的去留有判据」)。

| 优先级 | 功能 | 设计方向要点 |
|---|---|---|
| R1 | 配额主动展示页 | 设置页加「我的配额」卡(当前仅触限报错);数据源自 policies + user_quota_overrides,无新后端 |
| R2 | 优惠券/营销体系 | 新模块 coupon:码 → 抵扣规则 → 下单/充值核销;需先定「能否提现/退款回流」资金口径 |
| R3 | 磁盘快照 | 依赖 TopoLVM VolumeSnapshot;编排侧新增 snapshot 状态机与计费口径 |
| R4 | 发票在线版式 | 对接电子发票服务或自生成 PDF;当前人工开具流程保留作兜底 |
| R5 | 变配/重装/VNC | 变配涉及价格差结算与 SKU 迁移语义;VNC 需 console 网关;均为中型项目 |
| R6 | 帮助文档站 | 静态站(如 VitePress)挂 help 子域,替换现有 8 条 FAQ |
| R7 | admin 端 token 改 HttpOnly Cookie | 同 web 端模式,待 MFA 体系稳定后随动 |
| R8 | 敏感配置双人闸 | pending change + 第二 admin 批准队列,替代/叠加 prod 降防开关的硬禁止 |
