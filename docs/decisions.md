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
- **orval 只生成 fetcher 与 model 类型,不生成 TanStack Query hooks。** `packages/api-client` 用 `client: "fetch"`;两端在各自的 api 层
  (`apps/web/src/api/*.ts`、`apps/admin/src/api.ts`)用 useQuery/useMutation 包 fetcher,查询键、失效域与轮询策略都在那里定义。
  曾经生成的 hooks 与 QueryKey/QueryOptions 占生成物六成、两端零引用,还把 `@tanstack/react-query` 拖成 api-client 的 peer 依赖。
  后果:`pnpm api-client` 重生成只影响 fetcher 签名与 model 类型;不要再把 `client` 改回 `react-query` 或加 `query` 块。
- **UI 占位项的去留有判据,不是想留就留。** 背景:`ui-ux-spec.md` 规则 2 同一句里既写「预留功能一律可见但禁用」
  又写「未实现的能力不进 UI」,两条互斥,实际执行成了前者 —— 于是「我的镜像」「保存镜像」这类没有任何排期的
  能力在控制台挂了很久的 disabled 占位,tooltip 恒写「即将上线」,对用户是承诺,对维护者是每次改这块都要绕开的死代码。
  决定:只有「已排期、按当前设计确定要做」的能力才留占位并注「即将上线」;没有排期的直接不进 UI,想做时再加。
  兑现或删除时,`ui-ux-spec.md` 的占位清单与 `reference/web.md` 同提交更新。
  后果:本次删掉「我的镜像」Tab(自定义镜像栏已能填任意仓库地址,该 Tab 无独立价值)与「保存镜像」菜单项;
  保留「无卡模式开机」与「转包年包月」(两者都已排期)。**「转包年包月」已随包周期上线兑现**
  (前端叫「转包周期」——四档周期不止年月),后端是 `POST /instances/{uuid}/subscribe`;
  见下方「按量可以就地转包周期」。这条占位从此不再是承诺,是入口。

## 计费与资金

- **余额归零即回收。** 余额恰好 0.00 的账户进入停机 → 冻结 → 回收链,冻结判据是 `balance > 0` 才放行。
  0.00 的用户已无支付能力,停机后继续免费占用实例盘不合理;改成 `>= 0` 放行会让零余额用户永久免费占盘。
  与停机判据 `effective <= 0` 自洽,由 `tests/test_billing_flow.py::test_zero_balance_stops_then_freezes_then_reclaims` 锁定。
- **计费只认事件流水,指标只做对账。** 见 `architecture.md` §4 与 `reference/billing.md`:Prometheus 全挂结算照常。
- **不做渠道原路退款。** 退款单审批不动钱包,财务登记打款成功才负向核销,审批与打款分人;见 `reference/payment.md`。
- **票款双重兑现闸只有两道。** 申请退款时拒已开票账期(并对该账期的活跃发票申请行加锁,与开票串行);
  开票时行锁内按当前口径(在途退款预扣)重算、不符即驳回重申。曾经的第三道「登记打款时复查账期是否已开票」已去掉:
  能走到打款的退款在开票时就已从票额扣除,复查只会把它打成死胡同(只能取消,再申请又被已开票拦下,且无红冲端点)。
  见 `reference/payment.md`、`apps/api/tests/test_invoices.py`。
- **出金与入账的审计同事务。** 审计行写失败即出金失败回滚——宁可不出金,不可无留痕(`write_audit_sync`)。
- **结算缺口只登记不自愈。** 追平截断 / 死信 / 水位线丢失一律落 `settlement_gaps` 并持续告警,由人工重放或核销;
  自动补结会掩盖账期为什么追不平。见 `reference/billing.md`。
- **包周期走独立订阅单一次性预扣,不摊成零元小时账。** 背景:包周期要和已经跑通的小时结算共存,两条路 ——
  (a) 下单时按整段周期预扣一笔,实例在结算候选里被跳过;(b) 照常出 720 条 `bills_hourly`,只是金额为 0 或按
  预付额摊平。选 (a):`bills_hourly` 的结构、幂等键(UNIQUE(instance_id, hour_start))、水位线、缺口机制
  一行不动,**跳过点只有 `orchestrator/queries.py::billing_candidates` 一处**。选 (b) 会让「重复执行零重复扣款」
  这条最贵的幂等证明凭空多一个维度(零元行也要幂等、也要进水位线、也要在缺口里被重放),
  而它换不来任何用户看得见的东西 —— 用户账单页要看的是「我买了一个月,扣了多少钱」,不是 720 行 ¥0.00。
  代价是购买模式从此是计费链上的一个分支,必须在四处配套过滤里同时排除(燃烧率 / 停机判据 / 冻结与解冻 / 尾账),
  漏一处就是对预付用户二次收费或误停机 —— 四处逐条列在 `reference/billing.md`,并各有一条用例锁住。
- **周期取定长小时,不取自然月。** day=24 / week=168 / month=720 / year=8760,到期时刻与定价用同一个数。
  按自然月算到期而按 30 天算价,会造出两类谁也说不清的争议:「二月买的包月比一月便宜三天」和
  「1-31 续费到 2-28 还是 3-3」。定长把两者钉死成同一个数,代价是 31 天的月份平台少收一天 ——
  这是定价模型的一部分,与数据盘「月按 30 天」同款取舍。后果:`PERIOD_HOURS` 是常量、不进策略参数,
  改它等于同时改价与改到期口径。
- **预付不退款,中途释放只作废订阅。** 实例进入 `releasing` 时把 active 订阅转 `cancelled`,不生成任何退款流水;
  确需退款走既有的人工 `refund_requests`(双人制衡、登记打款才动钱包)。自动按剩余天数退款要定义一套
  「按什么口径折算已用」的规则,而那正是包周期想避开的东西(它的卖点就是不按用量算);更实际的问题是
  自动退款会把「买一年 → 用三天 → 全额退掉大部分」变成一条无成本的套利路径。作废挂在状态迁移监听器上
  而不是 `release_instance` 里:用户释放、欠费回收、到期回收、管理端强制回收四条路径最终都经过这条边,
  挂一处就全覆盖。已 `expired` 的历史行不动 —— 它是账期凭证,追溯改写会让财务口径多出一类被改过的收入。
- **到期不自动转按量。** 到期即停机 → 冻结 → 72 小时后回收(与欠费同一个 `freeze_grace_hours`,对用户是同一句
  承诺)。自动转按量看起来更「不打断用户」,实际是替他开了一份他没同意过的持续扣款,而包周期用户恰恰是
  为了「花固定的钱」才选它的。留给用户的路径是:到期前 3 天预警、可开自动续费(默认关)、冻结窗口内续费即解冻。
- **续费按下单时的原价快照重新定价。** 基准是 `subscriptions.unit_price`(下单那一刻的 SKU **原价**时价),
  不是 SKU 现价,与「变更 SKU 仅影响新实例」同一条口径 —— SKU 涨价不追已购用户。存原价而不是折后价,
  是因为用户可以换周期续(包月转包年),折扣要按**新周期**重新取;存折后价就得反推原价,而反推会带舍入。
  起算时刻取 `max(老到期时刻, 现在)`:提前续费从老到期时刻起算(不白丢手上剩的天数,而提前续费正是我们
  希望用户做的事),到期之后才来续则从现在起算(否则会续出一个开局就少几天的周期)。续费**新开一行**
  并用 `renewed_from_id` 串链,不在原行累加 —— 跨月续费的两段周期金额必须落在各自的行上,不然账期归属
  只能靠流水反推。
- **未到期的包周期实例即使已停机也仍占库存。** 软准入把同一条 SKU 上「stopped / frozen 且仍在保」的实例
  计为占用。节点台账的 `gpu_used` 只数真在跑的 Pod,包月用户关一晚机、那张卡在台账上就是空闲的,
  被别人买走之后他早上开不了机 —— 那是比超卖更难向他解释的事故,而他已经付过整个月的钱。
  **只在平台层预留,物理层不预留**:卡确实空着,谁调度到就是谁的,平台不为此空转硬件;
  所以这条必须出现在创建页与续费入口的说明里,不能只写在代码注释里。只算同一条 SKU:同池同型号但规格
  不同的实例槽位大小不一样,折算成本 SKU 的槽位数只会给出一个假精确的值,而软准入本来就是近似闸门。
- **按量可以就地转包周期,代价是转换点两侧必须各结各的账。** 背景:一度判定这事不成立 ——
  「转的那一刻在跑的这一小时算谁的」看起来是个没有干净答案的问题,于是打算让用户重建实例。
  想清楚之后发现结算边界其实是干净的:转换点把时间轴切成两段,前一段按按量结清、后一段按预付买断,
  两段各按各的口径收费,既不重复也不留缝 —— 需要的只是**保证这个顺序**。
  决定:新增 `POST /api/v1/instances/{uuid}/subscribe`,同一事务内**先结后翻** ——
  先 `settle_on_demand_up_to` 把水位线之后到当前自然小时的按量账逐小时结清(用**转换前**的按量时价),
  再落订阅行、预扣、翻 `market`、刷 `price_hourly`。顺序反过来是一个静默的资损:`billing_candidates`
  按实例当前的 market 挑候选,market 一翻,那些还没出账的小时就再也没人结,而账面上看不出少了什么。
  同理幂等重放必须在全部守卫之前判 —— 重放时 `market` 已是 subscription、`price_hourly` 已是折后价,
  晚一步就会拿包周期的价格去补一笔本该按按量收的账。
  **结算滞后超 48 小时直接拒绝转换(409),这是刻意的 fail-closed**:结算追不上不是用户的问题,
  但也不该由用户免单 —— 放行会把那段真实消费永久免掉且不留痕迹(账单页少几小时、缺口表里也没有它),
  而拒绝是可恢复的,追平后再转即可。反向不开:包周期转不回按量,那等于要求平台把没用完的那段退成余额,
  与「预付不退款」直接冲突。后果:`instances.market` 不再是绝对不可变的快照,`subscribe_instance` 是
  唯一会改它的路径;实例列表的「转包年包月」占位随之兑现。见 `reference/billing.md`、`reference/orchestrator.md`。

## 安全

- 安全取舍(token 存 localStorage、固定窗口限流、用户端无 2FA、仅 +86、双人制衡残余等)集中在
  `reference/security.md`「已接受取舍」,评审在案,勿再单独立项。
- **管理端全角色强制 TOTP。** 早期只对 admin/finance 强制,ops/readonly 免 MFA 时账号级锁定是口令喷洒的唯一纵深;
  现已全角色强制,登录限流四层桶保留为纵深(`reference/admin.md`)。后果:强制与否收成安全策略开关 `admin_mfa_enabled`
  (默认开),只有全员开 / 全员关两档——按账号 opt-in 需要自助绑定入口,收益不抵复杂度;关闭是运营决定,配置中心告警 + 审计 reason。
- **日志 PII / 凭据全局脱敏。** `app/core/logging.py` 按键名(phone / id_number / token / secret / password / code)兜底打码,
  防新增日志点漏脱敏。
- **账号级登录锁定。** 撞库可以换 IP,换不了目标账号:账号维 15 分钟窗 + 日窗阶梯锁定,与 IP 维桶叠加。
- **安全功能是开关,不是 mock 提供方。** 背景:人机验证曾以 `captcha_provider=mock` 表达「关闭」,为这个替身要养 mock 渠道、
  固定放行串 `mock-pass`(前后端各一份)、prod 启动与写入双闸,e2e 也要带串。决定:关掉就是跳过的安全功能(人机验证、实名、管理端 MFA)
  一律用 `*_enabled` 布尔开关表达(平台配置·安全策略组),删除 mock 提供方;只有流程无它完不成的第三方(短信要有码、支付要有回调、
  K8s 要有 Pod)各保留唯一一个替身(`sms_provider=mock` / `payment_mock` / `k8s_backend=fake`),prod 照旧拒绝。prod 允许关闭
  安全开关——那是运营决定,代价是配置中心红牌 + 审计 reason,而不是启动拒绝。后果:`/auth/sms-code` 的 `captcha_token` 变为可选
  (开启时缺失 400),首次上线可先关人机验证再补资质(不再被验证码资质 lead time 卡住)。
- **prod 启动校验只管 provider,不管凭据齐全性;真实集群不再绑定 prod。** 背景:启动期曾要求 prod 配齐阿里云短信
  5 项与验证码 4 项,且 `k8s_backend=real` 强制 `environment=prod`——连真实集群必须先拿齐短信签名、验证码、实名资质,
  实机验证被资质链阻塞,也与平台配置中心「资质到位后在线录入即生效」自相矛盾。决定:`_validate_prod` 只拒
  sms / payment 的 mock provider 与基础设施占位值,凭据齐全性交给运行期渠道工厂 fail-closed
  (缺凭据是首条短信失败而不是启动失败,管理端 test-sms 可验);实名没有 mock,唯一组合约束
  `real_name_required_for_recharge ⇒ real_name_enabled` 任意环境生效(启动 `_validate_invariants` 与写入侧 `_check_real_name_invariant` 同口径);`alertmanager_token` 缺失与 `prometheus_url`
  指向本地降为启动 WARNING;dev + real 允许共存。后果:真实集群上的暴露面由部署拓扑(ingress / 公网 DNS)决定,
  不由 environment 决定;边缘收口(`edge_guard`)随之改为 prod 恒开、删掉显式开关,类生产环境一律以 prod 运行。

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
- **镜像仓迁托管仓。** 集群内自建 registry(明文 http + htpasswd)退役,清单已从仓库删除(未迁移集群为零);
  `registry.superdl.local` 保留为逻辑名,节点 mirror 到 ACR / Harbor;见 `deploy/cluster/runbooks/image-prewarm.md`。
  平台默认生成的 registries.yaml 仍指向历史 NodePort 30500(已无服务在该端口),集群必须在平台配置填
  `node_registries_yaml` 覆盖(`reference/nodes.md`);不为托管仓地址新增配置键。
  **已被下一条「镜像仓库定为 Harbor」取代**:仓库地址、项目、机器人账户与 CA 收进平台配置 `registry` 组。
- **镜像仓库定为 Harbor,接入参数入配置中心。** 背景:托管仓「ACR 优先、Harbor 备选」的两可表述让仓库地址与拉取凭据只能靠
  `node_registries_yaml` 手填整段文本(明文落库、每台节点各一份、轮换要碰全部 GPU 节点)。决定:Harbor 是第一方——
  `registry_host / registry_project / registry_robot_name / registry_robot_secret(加密)/ registry_ca_pem / registry_proxy_projects`
  与镜像来源白名单 `image_allowed_registries` 都在平台配置·镜像仓库组,管理端「测试连接」按生效配置探测 Harbor API;
  平台自身镜像(api/web/admin)的仓库地址在部署侧(CI 与清单占位),不能依赖 DB。后果:白名单不再是 prod 启动硬闸(Harbor 地址自动放行,
  为空只给配置告警);拉取凭据的托管方式与逻辑名去留见下一条。
- **拉取凭据由平台托管为 imagePullSecrets,`registry.superdl.local` 逻辑名退役。** 背景:逻辑名靠节点 registries.yaml 的
  mirror + auth 解析,凭据落每台 GPU 节点磁盘,轮换要分发全部节点并重启 agent(GPU 节点不走 ansible);而 kubelet 的
  imagePullSecrets 按镜像主机名匹配、containerd 不会把它转给 mirror 主机(ParseAuth 校验 ServerAddress),逻辑名与 K8s 原生
  凭据不可兼得。决定:`image_ref` 存 Harbor 全限定名;worker 在建实例 Pod / 预热 Job 前按生效配置把 dockerconfigjson Secret
  `superdl-registry-pull` 按指纹写入 superdl 与各租户 ns(`ensure_pull_secret`,指纹相同跳过),Pod / Job 以 `imagePullSecrets`
  引用;节点 registries.yaml 只留 Spegel / CA / 代理缓存 mirror。后果:轮换 = 配置中心保存新 Secret;换 Harbor 域名要 SQL
  批量改 `images.image_ref`(实例快照是历史值,不改);测试里的 `registry.superdl.local/...` 只是不透明串,不再有语义。
- **平台镜像 tag 语义化且可覆盖重推,目录 `image_ref` 钉 digest。** 背景:原规则「tag 不可变、改一个字就换 `-rN`」
  让仓库里堆一代代垃圾 tag、目录 ref 跟着漂。想改成「同名 tag 重推 + 预热 Job 用 `imagePullPolicy: Always`」时实测发现行不通:
  k3s 内置 registry(Spegel,节点 `registries.yaml` 的 `mirrors "*"`)按 **tag** 解析会返回节点自己缓存的旧 digest,
  `Always` 照样拉到旧镜像。证据:手工 apply 的探测 Pod(`imagePullPolicy: Always`、按 tag 引用)记录的 `imageID`
  是旧 digest;直接拿 containerd 的 `ns=` 参数问节点上的内置 registry,同一个 tag 至今仍回一个落后数代的 digest。
  外部依据:k3s 文档「other nodes will trust the tag advertised by the node, and use it **without checking with the
  upstream registry** … you should use image digests instead of tags」(https://docs.k3s.io/installation/registry-mirror#potential-concerns),
  上游 Spegel 写得更直白「Once an image has been pulled by a reusable tag reference, that tag will resolve to the first
  digest for as long as the image is present in the cluster」(https://spegel.dev/docs/usage/resolving-tags/)。
  「`Always` 也救不回来」这一条没有官方明文,是实测 + 机制推断(mirror 本地有内容就直接返回,不回上游)。
  决定:tag 只表达「框架版本 + CUDA 线 + Python」并允许覆盖重推(不再 `-rN`);`images.image_ref` 一律写
  `<repo>:<tag>@sha256:<digest>`(`core.registry.is_valid_image_ref` 本就支持),按 digest 拉取是内容寻址。
  考虑过但没选的替代:把 Harbor 从节点 `registries.yaml` 的 `mirrors "*"` 里摘出去(平台有 `node_registries_yaml`
  覆盖键,不改代码就能做),此后按 tag 拉恒回上游、tag 重推立即生效,digest 钉扎可以整个不要——代价是平台自己那批
  8–27GB 的 GPU 镜像彻底退出 P2P,每加一个节点就多一份直连 Harbor 的全量拉取。单节点时它更简单,但 node-join 与
  管理端加节点流程都在,按多节点取向保留 digest 钉扎。k3s 没有暴露 Spegel 的 `resolveTags` 开关,那条路走不通。
  后果:重推后在管理端把该镜像的 ref 换成新 digest 即可——`admin_update_image` 会同事务清掉该镜像的节点缓存行,
  巡检按新 ref 重新预热(缓存行另记 `cached_ref`,SQL 直改绕过服务层时由巡检兜底作废);实例 Pod 与预热 Job 都保持
  `IfNotPresent`(开机不依赖仓库可达)。实例的 ref 是创建时快照且终身不变,镜像修复只对新建实例生效。
- **加固基线为 SSH 让出 `SYS_CHROOT` / `SETUID` / `SETGID` 三个 capability。** 背景:`tenant_security_context()`
  原本无条件 `drop ALL`,而 OpenSSH 的预认证特权分离是强制且不可配置的(必须 `chroot("/run/sshd")` 再 setgid/setuid),
  结果每个连接在密钥交换阶段就 Connection reset——平台在控制台和 `reference/orchestrator.md` 里承诺的 `ssh root@` 入口
  从来没通过,而测试只断言了 `ssh_command` 这个字符串的形状,没有一处真连过 22 端口,所以坏了很久无人发现。
  评估过换 dropbear:它能在零 capability 下完成密钥交换与公钥认证,但仍卡在 `initgroups()`(`setgroups` 恒需
  CAP_SETGID),最少也要 1 个 capability,零 capability 只能靠自编译打补丁或 LD_PRELOAD 垫片——为此自己维护一个
  安全关键守护进程,比多给两个 capability 更糟。决定:保留 OpenSSH,drop ALL 之后 add 回这三个。
  依据:容器本就以 root 跑在自己的 user namespace 里,这三个能力不产生新的宿主侧权限。
  配套:entrypoint 起 sshd 前 `chmod g-w,o-w /root`(TopoLVM 把实例盘挂载点留成 2777,sshd 的 StrictModes 会
  因此拒绝公钥认证,这是与 capability 相互独立的第二道拦阻),推送前自检增加「真连一次 SSH」。
- **light 单机的 server 兼 GPU 节点走同一条 node-join 命令,脚本按「server 服务在运行」切换路径。** 背景:light 档
  单机时 server 就是唯一的 GPU 节点,而 node-join 原本无条件写 agent config、装 agent,在 server 本机执行会覆盖 server
  配置并装出第二个 k3s 单元;手工打标签又拿不到装机登记(gpu_info / 驱动版本),台账型号只能靠 GFD。决定:不另开
  「导入现有节点」入口,node-join 检测到本机 `k3s.service` / `rke2-server.service` 在运行即跳过 agent 三步,池标签经本机
  kubectl 打到节点对象,驱动版本在打标签前上报(节点已 Ready,标签一落对账器即判 joined 终态);`--uninstall` 在 server
  本机不执行发行版卸载脚本。后果:server 本机的实例盘 VG 不在 node-join 建时须先于 helmfile 手工建好;nvidia-smi 只报
  通用名的卡(CMP 系列)型号来自 lspci 方括号名,`canonical_gpu_model` 识别 `CMP<数字>HX`。见 `reference/nodes.md`。
- **两档集群装同一套 GPU 栈:light 也上 gpu-operator 与 kata-deploy。** 背景:light 档原先绕开 gpu-operator,
  用独立的 `gpu-feature-discovery` + `dcgm-exporter` 两个 chart 顶 GFD/DCGM,dedicated 档所需的 kata 则完全不装,
  于是集群体检页恒有两条红叉,修复命令给的还是在 light 档下什么也不做的 `-l name=gpu-operator apply`;
  两条并行的 GPU 栈也意味着任何 GPU 相关改动都要验两遍。k3s 两者都支持:gpu-operator 官方文档列了 k3s,
  kata-deploy 4.x 的 helm chart 有 `k8sDistribution: k3s`(自动写 k3s 的 containerd 配置目录,并在与节点自检
  不符时拒装)。决定:light 与 full 用同一份 release 清单,差异收敛成 `values/light/` 的覆盖——light 只多一条
  `toolkit.enabled=false`,因为装机基线已在宿主装了 nvidia-container-toolkit、k3s 自行探测生成 RuntimeClass
  `nvidia`,再让 operator 改一遍 k3s 自己从模板生成的 containerd 配置只会被下次启动覆盖回去。
  配套:删掉独立的 gfd / dcgm-exporter 两个 release 与 `kata/kata-runtimeclass.yaml`(RuntimeClass 由 chart 建,
  kata-deploy 4.x 已移除 3.x 的 `overlays/<distro>` kustomize 目录);GPU Operator 的 operand 落点标签
  (hami 池 `nvidia.com/gpu.deploy.device-plugin=false`、kata 池 `nvidia.com/gpu.workload.config=vm-passthrough`)
  由 node-join 随池标签一起打,不再是 README 里的手工步骤——漏打会让官方 device-plugin 与 HAMi 抢注
  `nvidia.com/gpu`。后果:档位可用性从此只看「池里有没有 Ready 节点 + 运行时是否到位」,与发行版无关;
  单机 light 仍只有一个池标签,选了 hami 就没有 kata/mig 池。见 `deploy/cluster/README.md`。
- **`helmfile apply` 收进 `deploy/cluster/apply.sh`。** 两个开关必须每次都带,漏一个 apply 就中途失败,
  而两处报错都不指向真正的原因:`HELM_DIFF_USE_UPGRADE_DRY_RUN=true`(helm-diff 默认客户端渲染,模板里的
  `lookup` 恒空,kata-deploy 的身份校验据此判定「无法确认上一次安装的 multiInstallSuffix / deploymentMode」
  而拒绝升级,即便那个 ConfigMap 就在集群里)、`--skip-diff-on-install`(gpu-operator 首装时 ClusterPolicy
  CRD 尚不存在,服务端 dry-run 报 no matches for kind)。不写进文档靠人记,写成脚本。
- **DCGM 采集面按档位分:light 收到 device 级,并把 exporter 镜像钉到 4.8.3。** 实机(CMP 170HX)上
  gpu-operator 默认的 DCGM 字段清单含 `DCGM_FI_PROF_*`(DCP),这类卡的 profiling 模块初始化即
  unrecoverable error,exporter 起不来 —— 节点 GPU 曲线与 GPU 告警整条链路没数据。light 档改用
  device 级清单(与独立 dcgm-exporter chart 的默认同源,平台用到的四个指标全在里面),full 档保留
  chart 默认。另:节点维标签在 dcgm-exporter 4.8.3 由 `Hostname` 改成小写 `hostname`,而 gpu-operator
  v26.3.3 默认还是 4.8.2;`prom.py` 的 `DCGM_NODE_LABEL` 与两条 GPU 告警的 `$labels.hostname` 都按小写
  写死,跟着 chart 默认走会让指标在、选择器选不中(静默失效),故把镜像钉到 4.8.3 而不是改三处选择器。
- **chart 默认值里的 `0` 会让 helm upgrade 直接失败,在 values 里显式钉成等效值。**
  kube-prometheus-stack 的 `prometheusSpec.maximumStartupDurationSeconds: 0` 被 CRD 拒(须 ≥60),
  钉 900(prometheus-operator 自身默认),行为不变,只为 upgrade 能过。同款的第二例原本是 ingress-nginx 的
  `controller.progressDeadlineSeconds: 0`(被 API server 拒:must be greater than minReadySeconds),钉 600;
  该 release 已随「北向入口迁 Gateway API」删除,这一半随之作废。**保留本条**是因为「chart 默认值给 `0`」
  这类坑不止这两处,新加 release 时照此复核。
- **light 档 TopoLVM controller 取 1 副本。** chart 默认 2 副本 + 按 hostname 的 required 反亲和,
  单节点上第二个副本永远 Pending:功能不受影响,但集群里长期挂着一个红 Pod,会把真问题淹掉,
  也让「全部 Pod Running」这类巡检判据失效。
- **档位收敛成 dedicated / shared,隔离机制的派发键改成节点池。** 背景:`skus.tier` 四值
  `dedicated / mig / shared_std / shared_eco` 把「隔离与切分技术」和「价格档」揉在一个枚举里 ——
  用户要在市场页四选一而 `mig` 与 `shared_std` 的差别说不清,代码侧则是 `("shared_std","shared_eco")`
  这个元组在 catalog / orchestrator / metering / gpu_adapter 四处各抄一份,且 tier 与 pool_label
  各承载一半机制判断(tier 决定资源语法、pool 决定 nodeSelector),天然可能对不齐。
  决定:`tier` 收敛成 `dedicated` / `shared` 两值,只表达售卖分类;隔离机制一律按 `pool_label`
  (kata / mig / hami)派发 —— 它装机时定死,是物理事实源。用户看到的「共享·标准 / 共享·经济」由池派生
  (mig 池 = 硬切分标准档,hami 池 = 软切分超卖经济档),消费级卡没有 MIG,因而只有专用 + 经济两档。
  配套:档位与池的合法配对表 `core/gpu_adapter.TIER_POOLS` + 服务端 `catalog/service.py::_check_tier_pool`
  (建 SKU 与改池两条路径共用),管理端表单只让运营选展示档位、tier 与 pool 由它派生 —— 否则可以建出
  「卖整卡直通、实际跑 HAMi 超卖」的 SKU。业务唯一键补上 pool_label / vcpu / mem_gb。
  后果:四份重复的档位元组归零;`build_gpu_request` 不再收 tier;容量预览端点不再收 tier;
  旧档位值一次迁移改到位(含 `instances.spec` 快照),**不留别名兜底** —— 无生产库,
  `unknown pool` 保持 fail-closed。另一个后果是「改池」被收窄成**仅下架态**并配上可改的
  `mig_profile`:配对约束一上,原来的在售改池在三个方向上全部不可达(dedicated 只有 kata;
  shared 的 mig↔hami 都卡在切片填不了/清不掉),留着就是死字段。收窄后语义也更正:
  改池或改切片 = 换隔离方式 = 换商品,在售的商品不该在用户眼皮底下换芯 —— 两者同门禁。
- **纯 CPU 实例:第三档 `tier=cpu`,允许挂 hami 池。** 无卡机是很多客户的常态需求(数据预处理、
  推理前后处理、跑 Jupyter 写代码),把它做成第三个售卖档而不是另起一条产品线,是因为整条链路
  (SKU / 实例 / 计费 / 配额 / 节点池)只差「不申请 GPU」一件事。**允许 cpu 档挂 hami 池**是刻意的:
  平台上线初期未必有专门的无卡服务器,而 GPU 机的 CPU 长期闲置(一张卡配 8~16 核,跑训练时 CPU 常年低载),
  让 CPU 规格吃这部分空闲即可先把档位卖起来。代价是 CPU 实例会挤占 GPU 实例的配套 CPU,因此加策略
  `gpu_node_cpu_instance_vcpu_cap`(默认 16,0 = 禁止)给每个 GPU 节点封顶;这只是**库存口径**上的封顶,
  不下发调度,和超卖参数一样属于「运营给自己划的线」。
  实现上有两个「0 是合法值」的坑必须点名:①`build_gpu_request` 里 `gpu_count == 0` 的判定**必须先于池分支**,
  否则挂 hami 池的 CPU 实例会照 HAMi 语法申请 `nvidia.com/gpu`,把真卡判给不用卡的实例;
  **下发门禁必须用同一个判据** —— CPU 实例 `schedulerName` 为空走默认调度器,hami-scheduler 不是它的前置,
  拿 HAMi 就绪去拦它等于让 HAMi 一挂就连带挡住一批根本不用 GPU 的实例;
  ②`build_pod_spec` 原来的 `gpu_n = max(1, gpu_count)` 要改成显式的「GPU 档按卡数放大、CPU 档倍率恒 1」——
  数值碰巧一样,但 `max(1, …)` 表达的是「把 0 卡当 1 卡放大」,语义反了。
- **CPU 实例计费为 0 的解法:计费份数收口到 `core/money.billing_units`,不动 `price_hourly` 语义。**
  账单金额一直是 `单价 × gpu_count × 秒 / 3600`,CPU 实例 `gpu_count=0` 会算出 ¥0.00 —— 不只是白送算力,
  余额护栏(`assert_can_afford`)、欠费停机判据(巡检的 `burn_per_hour`)、对账的实例时费也一并归零。
  比选过三条路:(a) 账单行里存 `gpu_count=1` —— 列名说的是卡数,存 1 就是在账单与导出 CSV 里撒谎;
  (b) 给 `bills_hourly` 加一列 `billing_units` —— 诚实,但为一个恒等于 `max(1, gpu_count)` 的派生值
  加列、加迁移、改导出与前端账单,收益不抵成本;(c) 让 CPU SKU 的 `price_hourly` 语义仍是「单卡价」而把
  `gpu_count` 存 1 —— 同 (a),且会让「单实例 GPU 数」的校验失去意义。
  选定:新增 `billing_units(gpu_count) = gpu_count or 1` 与 `hourly_cost(price, gpu_count)`,
  计费链上所有「单价 × 份数」只经这两个函数(`bill_amount` / 钱包护栏 / 燃烧率 / 对账 / 在途估算);
  `price_hourly` 的语义随 SKU 形态分化并写进 `reference/catalog.md`(GPU 规格 = 单卡时价,CPU 规格 = 整机时价);
  账单行照实存 `gpu_count=0`,复算金额时按同一函数还原份数,行仍然自洽。
  之所以不散写 `max(1, n)`:那种写法碰巧算对,但读的人无从判断 0 是合法值还是脏数据 —— 这正是本次要修掉的
  `build_pod_spec` 旧写法的毛病,不该在计费链上再复制一遍。
- **DNS01 走 acme-dns 中转。** 集群内只持有能改 `_acme-challenge` 子域 TXT 的账户,不再持有全域 RAM DNS 凭据;
  见 `deploy/cluster/runbooks/acme-dns.md`。
- **集群键中性化,砍掉 `k8s_distro`。** `rke2_*` 改 `cluster_*`,发行版由平台探测 gitVersion 派生。改名时没有任何
  生产库,旧名别名(env `SUPERDL_RKE2_*` 与 DB 旧键读回落)服务对象为零,已删除不再保留。
- **不引 Sentry 类 SaaS。** 未捕获异常统一 500 留痕并经 Loki / Prometheus 告警,少一个外部依赖与数据出境面。
- **管理端监控自绘,Grafana 只作外链。** 不做 iframe 嵌入,`grafana_url` 未配置只显示一行提示。
- **告警 `runbook_url` 只加在有专属 runbook 的规则上。** 其余告警的第一步写在 summary 与 `deploy/cluster/runbooks/README.md` 索引表里,不为每条告警生造一页。
- **北向入口从 ingress-nginx 迁到 Gateway API + Envoy Gateway。** 背景:ingress-nginx 2026-03 退休,最后版本
  controller-v1.15.1,此后不再修任何 CVE —— 而它是全站唯一的公网入口,继续用等于长期背着未修漏洞。
  决定:整体换成 Gateway API 的实现 Envoy Gateway v1.9.0(对齐 Gateway API v1.6.1,内置 Envoy distroless-v1.39.0),
  版本钉在 `deploy/cluster/helmfile.yaml.gotmpl` 与 `deploy/cluster/gateway-api-crds.sh`;本次**只做 1:1 平移**,
  不借机加新入口能力。选 Envoy Gateway 而不是 Cilium 的 Gateway API:light 档(k3s + 内置 flannel)根本不装 Cilium,
  而两档必须跑同一套入口;`values/cilium.yaml` 的 `gatewayAPI` 因此保持 false —— 两个控制器 reconcile 同一批
  Gateway/HTTPRoute 会互相覆盖 status 与 LB 地址,现象是入口地址来回翻,而两边日志都「一切正常」。
  形态:`GatewayClass superdl` + 一个 `Gateway superdl`(ns `superdl`)带 5 个 listener
  (`http` / `api-https` / `console-https` / `admin-https` / `app-https`)+ 4 条平台 HTTPRoute;租户 Jupyter 是
  **每实例一条 HTTPRoute**,建在租户 ns 挂 `app-https`,跨 ns 靠 `allowedRoutes.namespaces.from: Selector` 加租户 ns
  已有的 `superdl.io/managed=true`(不需要 ReferenceGrant,也不新增标签)。不拿一张 `*.superdl.example.com` 通配
  listener 顶掉三个平台域:那会强制 api/console/admin 与租户域同根,现网清单允许它们落在互不相干的域名上。
  **CRD 的 channel 首装即定,事后换不回去**:平台用到的每源 IP 本地限流落在 experimental channel,而随 CRD 一起装的
  safe-upgrades ValidatingAdmissionPolicy 用 CEL 明文拒绝「standard 之上装 experimental」;装错只能把 Gateway API CRD
  删净重装,而删 CRD 会连带删掉集群内全部 Gateway/HTTPRoute —— 平台三个域名加全部租户 Jupyter 入口一起消失。
  所以 CRD 的生命周期单点收进 `deploy/cluster/gateway-api-crds.sh`(chart 侧一律 `crds.enabled=false`:gateway-helm 的
  CRD 子 chart 走 helm 的 `crds/` 目录,该目录在 `helm upgrade` 时永不更新,跟着 chart 装等于 CRD 永远停在首装那一版),
  脚本自带 channel 前置闸门,`preflight.sh` 另有一道复核。
  **三条 nginx annotation 的等价与不等价**(取值与症状见 `reference/security.md`「限流分层」):`limit-rps` / `limit-rpm`
  → `BackendTrafficPolicy` 的 local 限流,靠 `sourceCIDR.type: Distinct` 拿回「每源 IP 一个桶」,**等价**;
  `whitelist-source-range` → `SecurityPolicy.authorization`(`defaultAction: Deny` + `clientCIDRs`),**等价**,
  但占位符换成 RFC 5737 的 `192.0.2.0/24` 而不是 `CHANGE_ME_*` —— CRD 上有 CIDR 正则,非法串会被 apiserver
  单独拒收该对象而其余照常生效,那等于管理端无声敞开,换成合法但无真实主机的网段才是 fail-closed;
  `limit-connections: 20`(每源 IP 并发连接)**没有等价物**,EG 只有每 Envoy 实例的连接总量,这是一处有意接受的
  **能力回退**,不要当无损迁移。三者连同白名单都以 `externalTrafficPolicy: Local` 为前提。
  后果:①`SUPERDL_INGRESS_CLASS_NAME` 删除,入口坐标改成 `core/k8s/base.py` 的三个常量(与 StorageClass 同一做法,
  入口拓扑不是按环境变的东西);②`cluster_status.ingress_ready` 改名 `gateway_ready`,判据从「控制器 Deployment
  ready≥1」换成「Gateway 对象 `Programmed=True`」(前者探不到 listener 证书缺失、hostname 撞车这类「控制器活着但
  流量进不来」),管理端组件 key 同步 `ingress` → `gateway`;③凡按 ns 名认入口的地方(NetworkPolicy 来源、准入豁免
  名单)一律 `ingress-nginx` → `envoy-gateway-system`(未开 Gateway Namespace Mode,Envoy 数据面与 EG 控制面同 ns),
  漏改就是网关起不来,且报错只在 EG 控制器日志里;④TLS 弃用 ingress-shim 注解,改在 `05-cert-manager.yaml` 里显式写
  Certificate(gateway-shim 要给 cert-manager 开 `--enable-gateway-api`,不值得为省几行多挂一个依赖);
  ⑤`04-ingress.yaml` 改名 `04-gateway.yaml`,并新增 CI 闸门 `scripts/check-gateway-manifests.py` 按钉死那版 chart 的
  真实 CRD 校验它 —— kubeconform 内置 schema 没有这 7 种对象,只能 skip,而这个文件恰恰是「写错不报错」的重灾区;
  ⑥两个不报错的默认值必须记住:`streamIdleTimeout` 默认 5 分钟会切断 Jupyter 的 WebSocket 与 SSE(症状极像鉴权过期
  或网络抖动),listener 的 `sectionName` 写错只让策略静默失效、唯一线索在策略对象的 `status.ancestors[].conditions`;
  ⑦一实例一条 HTTPRoute 意味着路由条数随活跃实例线性增长、Envoy 内存跟着涨,light 档的 memory limit 必须实机压过再定。
  见 `deploy/cluster/README.md`、`deploy/app/k8s/04-gateway.yaml`。

- **服务容器是实例的第二种形态,不另立实体。** 背景:用户要把模型跑成对外 API,平台只有「SSH + JupyterLab 开发机」
  一种商品。可选做法是新建一套 `services` 实体与独立生命周期。决定:加一列 `instances.workload_type`(`dev` / `service`),
  Pod spec 在 `build_pod_spec` 按形态分叉,其余全部复用 —— 状态机、计费、配额、回收、reconciler、监控、审计一行不改。
  后果:服务实例天然继承「按 `instance_events` 边计费」「欠费冻结回收」等全部既有不变量;代价是 `instances` 表多了四列
  对 dev 形态恒为空。见 `reference/services.md`、`reference/orchestrator.md`。
- **对外服务的鉴权放在网关,不要求用户容器自己实现。** 背景:让用户在容器里自己校验 API Key,等于每个租户重新实现一遍鉴权,
  且平台无法吊销。可选项三个:Envoy Gateway 的 `apiKeyAuth`(0 往返)、`jwt`(0 往返)、`extAuth`(每请求 1 次回源)。
  前两个**都表达不了「这把 Key 只能访问它自己那个端点」**——`apiKeyAuth` 是「client-id → key」平铺表,校验通过即放行,
  租户 A 的 Key 能调租户 B 的服务;`jwt` 的 `audiences` 是策略里的静态列表,写不了「aud 必须等于请求 Host」。
  要用它们表达归属,只能每端点一条策略 + 一个 Secret,对象数 O(端点数)。决定:用 `extAuth`,一条 SecurityPolicy 挂在
  `svc-https` listener 上服务全部端点,对象数 O(1),且吊销即时生效。后果见「安全」节:**鉴权结果没有任何缓存**,
  控制面成为全部对外服务的同步依赖。见 `reference/services.md`。
- **服务型实例持续 not-ready 不判故障。** 背景:`reconciler` 原本对 running 实例持续 not-ready 超宽限一律判 failed。
  对开发机是对的(Jupyter 起不来 = 平台侧故障),对服务实例是错的:它的 not-ready 判据是用户自己声明的 readinessProbe,
  长期不过是用户容器的 bug,而判 failed 之后卡还占着、钱照扣、状态却成了故障。决定:`workload_type='service'` 时跳过
  `pod_unready` 一支,实例留在 running,就绪与否如实呈现在服务 Tab;`pod_lost` 与 `node_lost` 两支不豁免。
  配套:服务容器必配 `startupProbe`(15 分钟启动预算),否则加载大模型权重的容器从第一秒起就 not-ready。
  见 `apps/api/tests/test_orchestrator_lifecycle.py` 的 `TestServiceWorkloadUnreadyExemption`。
- **迁移门禁补上 alembic 约束 helper。** 背景:`scripts/check-migration-ddl.py` 只扫 `op.execute` 裸 SQL 里的
  `ADD CONSTRAINT`,而 `op.create_check_constraint` / `create_unique_constraint` / `create_foreign_key` 三个 helper
  渲染出的就是不带 `NOT VALID` 的 `ADD CONSTRAINT` —— 同样持 ACCESS EXCLUSIVE 全表扫描,只是从 SQL 文本里看不见,
  于是能静默绕过 `CLAUDE.md` 第 15 条。决定:三个 helper 一并拦,本迁移内 `create_table` 新建的表豁免(空表零成本)。

## 评审编号索引

历史上的生产就绪评审与安全审查用 `P0-n` / `P1-n` 编号,工作包用 `WPn`;编号的说明文档已随
「工作包交付流水账」一并删除,代码与清单注释里仍有引用。存量注释里的编号不做批量清理,改到所在文件时顺手换成本页的决策标题;
对外可见文本(路由 docstring → `openapi.json`、平台配置 hint 等 UI 文案)不得带编号。对照如下(不再新增编号,新决策写上面的章节):

| 编号 | 主题 | 现存于 |
|---|---|---|
| P0-1 | 控制面 HA + 平台组件 infra 标签选址 + light 档禁公众生产 | `deploy/cluster/rke2/server-config.yaml`、`deploy/app/k8s/02-api.yaml` 等清单注释 |
| P0-3 | 实例盘销毁 TRIM(TopoLVM `issue_discards`) | `deploy/cluster/topolvm/lvm-config.configmap.yaml`、`apps/api/app/modules/nodes/assets/node-join.sh` |
| P1-1 | 票款双重兑现闸 | `apps/api/tests/test_invoices.py`、`apps/api/tests/test_refunds.py` |
| P1-2 | 结算缺口闭环 | `apps/api/tests/test_billing_settlement.py` |
| P1-6 | acme-dns 替代 alidns webhook | `deploy/cluster/acme-dns.yaml`、`deploy/app/k8s/05-cert-manager.yaml` |
| P1-7 | 镜像仓迁托管仓(已被「镜像仓库定为 Harbor」取代) | `docs/decisions.md`「编排与平台」 |
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
