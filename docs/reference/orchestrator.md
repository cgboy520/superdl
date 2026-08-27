# 编排

实例状态机、outbox 编排、reconciler 对账、接入(SSH / JupyterLab)与 K8s 抽象。

## 数据模型

- `instances`:uuid、user_id、SKU 快照(sku_id + spec_snapshot jsonb + price_hourly)、market(CHECK ∈ {on_demand, spot, subscription},默认 on_demand)、gpu_count(CHECK ≥0;**0 = 纯 CPU 实例**,见 [catalog.md](./catalog.md))、status、k8s(namespace/node_name(253))、ssh_port?、jupyter_token(AES-GCM 密文)、image_ref、data_disk_id?、workload_type(CHECK ∈ {dev, service})、with_ssh、container_command?/container_args?(jsonb)、env_encrypted?、idempotency_key 唯一?(24h 窗口,窗外同键按新单)、version(乐观锁)
- `instance_events`:instance_id、from_status、to_status、reason、actor(user/system/admin)、metadata —— 追加式,计费主依据
- `port_allocations`:port 唯一(30000~32767)、instance_id nullable(部分唯一:一台实例至多一个端口)
- `service_endpoints`:instance_id 唯一(一实例一端点)、public_slug 唯一(`ep-<10 位 base32>`,公网域名左标签——刻意不用 instance.uuid,内部主键不进公网域名/TLS SNI/访问日志/第三方 Referer)、container_port(CHECK 1–65535 且 ∉ {22, 8888},那两个是 sshd 与 JupyterLab)、protocol、health_path?、require_api_key
- `service_api_keys`:user_id、instance_id、name、key_hash 唯一(HMAC-SHA256,见 [security.md](./security.md))、key_prefix(列表页回显)、last_used_at?、revoked_at?(吊销不删行)

状态机:creating→running/failed;running→stopping;stopping→stopped/releasing;stopped→starting/frozen/releasing;
starting→running/failed;frozen→stopped/releasing;failed→stopped/releasing;releasing→released。
running↔非 running 的边即计费边。failed→stopped 是故障恢复边(复用同一块实例盘重开机,
start 端点对 failed 放行);stopping→releasing 是悬挂放弃边(关机删不掉时允许直接释放)。
**包周期到期不新增状态与边**,只多两个 reason:系统侧停机与冻结按原因分开命名(欠费 `arrears_stop` / `arrears_freeze`,
包周期到期 `subscription_expired` / `subscription_freeze`)—— 在用户时间线上是两件不同的事,合成一个 reason 会让工单无从查起。

## 契约

| 端点 | 角色/鉴权 | 说明 |
|---|---|---|
| `POST /api/v1/instances` | user | Idempotency-Key;软准入(台账无货 409)→ 钱包行锁临界区(在途+新增余额校验、配额)→ 事务写 instances(creating)+event+outbox → 202。`market` 默认 `on_demand`,取 `subscription` 时 `period` 必填、`period_count` 1~36,同事务预扣整段周期(见 [billing.md](./billing.md));`market='on_demand'` 却显式带 `period`/`period_count` 一律 422 |
| `GET /api/v1/instances` `GET /api/v1/instances/{uuid}` | user | 列表(不分页)与详情 |
| `PATCH /api/v1/instances/{uuid}` | user | 改名等 |
| `POST /api/v1/instances/{uuid}/stop\|start\|restart` | user | 同构,均经 outbox;start 对 failed 放行(恢复边) |
| `POST /api/v1/instances/{uuid}/subscribe` | user | **按量转包周期**,入参与响应同 `/renew`;Idempotency-Key。先结清转换前那段按量账再翻 `market`(见下)。前置:`market='on_demand'` 且状态 running / stopped(其余 409 `orchestrator.convertNeedsRunningOrStopped`)、SKU `period_enabled` 为真;已在保报 `billing.subscriptionAlreadyActive`;结算滞后超 48h 报 409 `billing.settlementBehind` |
| `POST /api/v1/instances/{uuid}/renew` | user | 包周期续费,body `{period, period_count}`;Idempotency-Key(重放回 200 + `X-Idempotent-Replay`);返回 `{instance, quote}`,报价三件套由后端算好逐行下发。非包周期 / 已释放报 `SUBSCRIPTION_NOT_RENEWABLE`(400),余额不足 `INSUFFICIENT_BALANCE`。冻结中续费即解冻(回 stopped,不自动开机)。挂在 instances 下而不是 billing 下:用户心智是「给这台机器续费」,而实例状态也只能由 orchestrator 这一侧改 |
| `POST /api/v1/instances/{uuid}/auto-renew` | user | body `{enabled}`;开关到期自动续费,默认关 |
| `DELETE /api/v1/instances/{uuid}` | user | 释放(stopped/frozen/failed/creating/stopping);幂等:releasing/released 重放回当前状态而非 400。**包周期实例释放不退款**,订阅转 cancelled(见 [billing.md](./billing.md)) |
| `GET /api/v1/instances/{uuid}/events` | user | 事件时间线,即计费依据;降序(最新在前)游标分页 `?cursor=&limit=` |
| `GET /api/v1/instances/{uuid}/access` | user | SSH 指令 + Jupyter 一次性 bootstrap 票据 URL(单次、60s;核销后种第一方 cookie,token 不进 URL);非 running 报错并说明 |
| `GET /api/v1/instances/{uuid}/logs` | user | 容器日志:**只读**;**owner 校验**(非属主 404 不暴露存在性);**限流 20/h/user**;**K8s 读 5s 超时**;仅 running/stopping(其余 409,已关机无 Pod 日志);`?tail_lines=` 默认 200、超 2000 截断,`?since_seconds=` 超 86400 截断;返回 `{lines, truncated}`;不记审计 |
| `POST /api/v1/instances/{uuid}/reset-jupyter-token` | user | 轮换 token(密文落库),旧票据与旧 URL 立即失效 |
| `GET /api/v1/instances/{uuid}/service` | user | 服务端点信息(URL / 容器端口 / 健康检查 / 是否需 Key);非服务型实例 404 |
| `GET\|POST /api/v1/instances/{uuid}/api-keys` | user | 列出 / 新建;**明文只在新建响应里出现一次**(与恢复码同款一次性语义) |
| `DELETE /api/v1/instances/{uuid}/api-keys/{id}` | user | 吊销(写 `revoked_at`,不删行) |
| `/api/internal/v1/endpoint-auth/...` | 无(集群内) | 网关 `SecurityPolicy.extAuth` 的回调,**不对公网开放**(prod 下带 `X-Forwarded-For` 一律 404);详见 [services.md](./services.md) |

## 规则与不变量

- K8s 访问收敛在 `app/core/k8s`:`K8sOrchestrator` 协议:ensure_namespace / create_instance / delete_instance / delete_instance_disk / get_status / read_instance_logs / list_instance_pods / wipe_disk / list_nodes / set_node_labels / prewarm_image / get_prewarm_status / delete_prewarm_job / probe_cluster / set_node_unschedulable。
- FakeOrchestrator(dev/test,内存态,可注入故障)与 RealOrchestrator(kubernetes 官方客户端)必须同步实现协议全部方法。
- gpu_adapter 按**池**产出资源请求语法(HAMi `nvidia.com/gpu` + `gpucores`/`gpumem`;MIG profile;整卡)、选 RuntimeClass(kata-qemu / runc)、按 canonical 型号产出 `superdl.io/gpu-model` nodeSelector,以及 `annotations` 透传口(`nvidia.com/use-gputype`,开关 `SUPERDL_HAMI_USE_GPUTYPE` 默认关,仅混卡节点池需要)。
- **`gpu_count == 0`(纯 CPU 实例)的判定先于池分支**:资源请求为空、不钉型号、`runtimeClass=None`、`hostUsers=false`,nodeSelector 只有池标签。cpu 档允许挂 hami 池(见 [catalog.md](./catalog.md)),按池分支走就会替不用卡的实例申请 `nvidia.com/gpu`。
- Pod 规格倍率:GPU 实例的 vCPU/内存按卡数放大(N 卡收 N 倍价即给 N 份资源),CPU 实例倍率恒 1;系统盘任何形态都不放大。
- 创建时 `gpu_count` 的合法区间随 SKU 形态走:`max_gpus_per_instance == 0`(CPU 规格)只收 0(否则 `orchestrator.cpuSkuNoGpu`),否则只收 `1..max`(否则 `orchestrator.gpuCountRange`)。契约层是 `ge=0, le=8`,真正的配对闸门在 service。
- RealOrchestrator 每租户:独立 namespace(PSA enforce=baseline + audit=restricted 标签)、ResourceQuota 兜底(对象数 + cpu/memory/ephemeral-storage 总量)、Egress 隔离 NetworkPolicy(私网黑名单 + 滥用端口黑名单,DNS 收敛到 CoreDNS Pod)、JuiceFS PVC;`disk.wipe` 为真实擦除 Job(幂等 + 退避)。ns/NetPol/Quota 已存在时 patch 收敛,加固覆盖存量租户;K8s list 调用一律分页(limit=500 + continue),同步调用走专属有界执行器。
- 状态迁移只能经 `orchestrator/service.py` 的 transition 函数(同事务写 `instance_events`),禁止直接 UPDATE status;非法迁移报 `INSTANCE_INVALID_TRANSITION`。
- 请求路径不许调 K8s:业务写入与 `outbox_tasks` 插入同一事务,K8s 动作一律由 worker 执行。唯一例外是日志端点的只读直读(实时性要求;owner/限流/超时三道闸兜住,见契约表)。
- reconciler(30s,advisory lock)是唯一收敛点:Pod Ready 而 DB creating/starting → running(开始计费);Pod 消失而 DB running → failed(停费并告警);Pod 存在而 DB 终态 → 强删(force);creating 超 5min → failed 并退款。每轮一次 `list_instance_pods` 即状态源(存在性/ready/phase/node_name/deleting,与 `get_status` 同形),不逐实例 `get_status`。
- stopping/releasing 悬挂两档超时(默认各 10min,`stopping_timeout_seconds`/`releasing_timeout_seconds`):一档经 outbox 重发删除任务,二档 force 强删后按正常边收敛(stopped 保留端口与实例盘;released 回收端口并销毁实例盘)。悬挂实例数见指标 `superdl_reconcile_stuck_instances`。
- 泄漏回收熔断:未知(DB 无记录)Pod 占比超 `leak_reclaim_abort_ratio`(默认 0.5)即中止本轮并计 `superdl_reconcile_leak_aborted_total`;在途删除(stopping/releasing)宽限同两档超时,其余一律 force 强删。
- 保留期 GC(reconciler 内):failed 超 `failed_retention_days`(默认 7 天)→ 通知并转 releasing;stopped 超 `stopped_retention_days`(默认 30 天)→ 转 releasing,提前 `stopped_retention_warn_days`(默认 7 天)预警。数据盘不受影响。
- 节点失联判定先看节点 Ready 状况(`list_nodes`):持续 not-ready 超 `running_unready_timeout_seconds`(默认 600s,须宽于 unreachable toleration 的 300s)且节点 NotReady/未知 → node_lost(通知用户);节点正常 → pod_unready(Pod 自身问题,不告警失联)。**`workload_type='service'` 不走 pod_unready 这一支**:它的 not-ready 判据是用户自己声明的 readinessProbe,长期不过是用户容器的问题,判 failed 等于平台替用户停掉一台还在占卡、还在计费的实例;实例留在 running,就绪与否如实呈现在服务 Tab。`pod_lost` 与 `node_lost` 两支不豁免。
- **实例有三种购买模式**(`instances.market`),与 `skus.tier`(买什么档)正交 —— 一条 SKU 三种卖法,不为包周期或竞价另建 SKU 行:

  | 值 | 含义 |
  |---|---|
  | `on_demand` | 按量,唯一进 `bills_hourly` 的模式 |
  | `subscription` | 包周期,下单一次性预扣,小时结算在 `billing_candidates` 一处跳过(见 [billing.md](./billing.md)) |
  | `spot` | 竞价(折扣价 + 可被平台回收)。DB CHECK 与 `core/pricing` 已预留,**契约层此刻不收** —— `InstanceCreate.market` 只有 `on_demand` / `subscription`,`price_for` 对 spot 显式抛错而不是静默按原价:静默会让「竞价上线了但没打折」在账单出来之前没人发现 |

  `market` 由创建时定,**唯一会改它的路径是 `subscribe_instance`(按量 → 包周期)**;反向不开 ——
  包周期是已预付的整段周期,转回按量等于要求平台把没用完的那段退成余额,与「预付不退款」直接冲突。
- **`instances.price_hourly` 落的是该购买模式下的有效时价**,由 `app/core/pricing.py` 的 `price_for` 单点算出
  (按量即 SKU 原价,包周期按周期折扣打折)。计费引擎因此完全不用感知折扣:它拿到的永远是「这台实例的时价」。
  折扣的其它三个消费方(市场页报价、创建预估、续费报价)共用同一组函数,不得各算各的 ——
  四处各算各的迟早出现「页面显示 8 折、实际扣 8.5 折」这类没人能复现的差异(与 `sellable_per_gpu` 同一条口径纪律)。
- **包周期实例的开机门禁看周期,不看余额**:`start` 对 `market='subscription'` 走 `assert_subscription_active`
  (周期内才放行,到期报 `SUBSCRIPTION_EXPIRED` 409),不走 `assert_can_afford` —— 整段周期已经付过钱了。
  订阅行缺失也判过期(fail-closed):`market='subscription'` 却查不到订阅行是数据不一致,放行等于白送一台机器,
  拦下最坏只是用户来提一张工单。
- **未到期的包周期实例即使已停机,也仍占软准入库存。** `_reserved_slots` 把「同一条 SKU 上 stopped / frozen 且仍在保」
  的实例计为占用,从可售数里扣掉。台账的 `gpu_used` 只数真在跑的 Pod,包月用户关一晚机、那张卡在台账上就是空闲的,
  被别人买走后他早上开不了机 —— 那是比超卖更难向他解释的事故。**这是控制面层面的预留,物理层不预留**(卡确实空着,
  谁调度到就是谁的),所以创建页与续费入口必须把这一条写给用户看。只算同一条 SKU:同池同型号但规格不同的实例槽位大小不一样,
  折算成本 SKU 的槽位数只会给出一个假精确的值,而软准入本来就是近似闸门。
- 续费同事务刷新 `instances.price_hourly`(用户可以换周期续,有效时价随之变),冻结中的实例续费即回 `stopped`
  并清 `frozen_deadline`,**不自动开机**。
- **按量转包周期的顺序是「先结后翻」,不可颠倒**:`subscribe_instance` 在钱包行锁内先把转换前那段按量账结清
  (running 才有账要结),再落订阅行、翻 `market`、刷 `price_hourly`。翻在前的话 `billing_candidates` 会按翻新后的
  market 把这台实例整个排除,水位线之后还没出账的小时就永远没人结 —— 用户白拿转换前那段算力;而且结算必须用
  **转换前**的按量时价(那一刻 `price_hourly` 还没被改)。口径与拒绝条件见 [billing.md](./billing.md)。
- **幂等重放在全部守卫之前判**:转换成功后 `market` 已是 subscription,重放请求会撞上「只有按量实例可以转」
  那条守卫拿到一个毫不相干的 400;更糟的是它会先跑一遍结算,而此刻 `price_hourly` 已是折后价 ——
  等于拿包周期的价格去补一笔本该按按量收的账。
- 只收 running / stopped:creating / starting / stopping / releasing 是在途态,翻 `market` 会和收敛路径抢同一行;
  frozen 是欠费处置中,那笔账得先还清而不是转成预付。
- 列表与详情的包周期概要(`InstanceOut.subscription`:period / period_count / expires_at / status / auto_renew /
  amount_paid)与服务端点 slug 一样,由 `attach_instance_details` **各一次批量查询**回填,不逐行打接口
  (列表页禁止「接口调用随行数放大」,见 [web.md](./web.md))。**管理端走同一条回填路径**
  (`admin_list_instances` 与强制停止的响应都过 `attach_instance_details`):`AdminInstanceOut.subscription`
  在包周期实例上有值、按量实例为 null —— 客服问的第一个问题就是「他这台什么时候到期」,
  管理端另起一套投影只会让两端的到期日在边界上对不齐。
- **实例有两种形态**(`instances.workload_type`),差别只在 `build_pod_spec` 的分叉与建哪些 K8s 对象;状态机、计费、配额、回收、reconciler、监控、审计全部共用:

  | | `dev`(SSH + JupyterLab) | `service`(对外 HTTP 服务) |
  |---|---|---|
  | `restartPolicy` | `Never`(容器退出即故障) | `Always`(kubelet 原地重启容器,Pod 不重建 —— 重建会换名字,而全套 reconciler 都建立在「Pod 名 = 实例 uuid」上) |
  | command / args | 不设,用镜像 ENTRYPOINT | 用户可覆盖(`container_command` / `container_args`) |
  | 用户 env | 无 | `env_encrypted`(整包 AES-GCM,AAD 绑实例 uuid);密文项经 per-instance Secret 以 `secretKeyRef` 引用,明文不落 Pod spec |
  | SSH NodePort Service | 恒建 | `with_ssh` 才建;为假时**不进端口池**(端口池 30000–32767 是全平台硬上限) |
  | Jupyter Service + HTTPRoute | 恒建 | 不建 |
  | 服务 Service + HTTPRoute | 无 | `<uuid>-svc` ClusterIP + 挂 `svc-https` listener 的 HTTPRoute |
  | 探针 | 无(无探针时 ready ≡ 容器已启动) | `health_path` 非空时 startupProbe(失败阈值 90 × 10s = 15 分钟启动预算)+ readinessProbe |

  服务端点的域名规则、鉴权链路与 API Key 生命周期见 [services.md](./services.md)。
- **服务路由挂错 listener 是本形态最危险的单点**:`app-https` 上没有 `SecurityPolicy.extAuth`,把服务路由挂过去照样通、返回 200,只是**完全不鉴权**,且没有任何报错。两个 listener 名在 `core/k8s/base.py` 的 `GATEWAY_APP_LISTENER` / `GATEWAY_SVC_LISTENER` 钉死,离线用例 `tests/test_k8s_real_units.py::TestServiceWorkloadObjects` 逐条断言。
- 端口从 `port_allocations` 池分配,释放必须回池;池耗尽时创建失败并给出明确错误。
- SSH 仅密钥登录(公钥注入 authorized_keys),禁用密码;连接串形如 `ssh root@<实例域名> -p 3xxxx`——SSH 协议没有主机名,实例只靠 NodePort 区分,所以不设单独的 SSH 入口域名,主机名就是实例自己的域名(与 Jupyter 同名,`orchestrator/service.jupyter_host`);部署约束:泛域名解析到的地址必须同时转发 80/443 与 `ssh_port_range` 端口段(单节点即节点本身,多节点为转发该端口段的 LB/VIP)。
- SSH 依赖租户容器的三个 capability(`SYS_CHROOT` / `SETUID` / `SETGID`,见 [security.md](./security.md))与 entrypoint 起 sshd 前对 `/root` 的 `chmod g-w,o-w`(TopoLVM 把挂载点留成 2777,sshd StrictModes 会拒认证);缺任一条 SSH 都不可用,而 `ssh_command` 只是拼串,断言它不等于验证过连接——镜像自检里有「真连一次」那一步。
- SSH host key 持久化在实例盘(`/root/.ssh/host_keys`),Pod 重建指纹不变;jupyter 由 entrypoint 守护循环拉起(不做 PID 1),连续秒退 5 次才让 Pod 失败收敛。
- JupyterLab 走 `<instance-uuid>.app.<域名>`,由**每实例一条 HTTPRoute** 按 host 路由到该实例的 Jupyter Service(主机名 = `SUPERDL_JUPYTER_HOST_PREFIX`(默认空)+ uuid + `.` + `SUPERDL_JUPYTER_DOMAIN_SUFFIX`,由 `orchestrator/service.jupyter_host` 单点拼接;后缀与其它业务共用一级域以复用 `*.<域>` 通配证书时用前缀区分,如 `superdl-<uuid>.<域>`)。TLS 不在路由上出现,证书由 listener 的 `certificateRefs` 提供(泛域名一张)。token 由控制面生成、AES-GCM 密文落库、注入 Pod env;access 端点签发一次性 bootstrap 票据(HMAC 密钥=token 本体,单次、60s),镜像内 `/superdl-bootstrap` handler 核销后种第一方 cookie,token 不出现在 URL;`?token=` stock 登录为回落通道。实例镜像须先于控制面发布:镜像内的 bootstrap handler 是票据流的前提。
- **租户 HTTPRoute 的挂载契约**由 `core/k8s/base.py` 的三个常量钉死(`GATEWAY_NAMESPACE` / `GATEWAY_NAME` / `GATEWAY_APP_LISTENER`,须与 `deploy/app/k8s/04-gateway.yaml` 逐字一致;**没有对应的 `SUPERDL_*` 配置项** —— 入口拓扑不是按环境变的东西,与 StorageClass 常量同一做法):路由建在**租户 ns**,`parentRefs` 指平台 ns 的 `Gateway superdl`,`sectionName` 钉死 `app-https`。跨 ns 挂载由该 listener 的 `allowedRoutes.namespaces.from: Selector` 授权,选择器就是 `ensure_namespace` 已经打在每个租户 ns 上的 `superdl.io/managed=true`(平台自身 ns 不带此标,于是它精确等于「全部租户 ns 且仅租户 ns」,不必再造一个标签);**不需要 ReferenceGrant** —— 那东西只管 backendRef 跨 ns,而 backend 与路由同 ns。三个名字任一写错都不报错:`create` 照样返 201,路由停在 `status.parents[].conditions` 的 `Accepted=False` / `NotAllowedByListeners`,用户侧只看到域名 404。不写 `sectionName` 则路由会挂到全部同端口 listener 上,把平台三个域一起拉进同一份路由表。
- **Jupyter 的 WebSocket 与 SSE 靠网关的 `streamIdleTimeout: 1h` 撑着**(`ClientTrafficPolicy superdl-gateway`)。Envoy Gateway 该项默认 5 分钟,不显式配就会把内核连接与终端按时掐断,症状是「用着用着内核断了 / 页面反复重连」,极易被误诊成 token 过期或网络抖动,排查成本远高于这一行配置。调整网关策略时别把它落回默认。
- **路由规模是容量变量,不是常量**:一实例一条 HTTPRoute,活跃实例多了就是几百上千条,全量经 xDS 下发进每个 Envoy,数据面与 EG 控制面的内存都跟着涨(独立基准显示 5000 条路由时部分数据面到 1–2 GB)。light 档单机要么给足 `EnvoyProxy` 的 memory limit,要么对单机实例数设硬上限——这里 OOMKill 掉的是全站入口,不是单个租户。取值必须实机压过再定,见 `deploy/app/k8s/04-gateway.yaml` 注释。
- 孤儿端点清理按 `superdl.io/managed` 标签做全集群 LIST(Service 与 HTTPRoute 两类),HTTPRoute 无 typed model,增删查一律走 `CustomObjectsApi` 收发裸 dict,分页游标在 `metadata.continue`(不是 typed model 的 `_continue`)。
- 镜像拉取凭据不落节点、不进 Pod spec 明文:outbox 建 Pod 前在 `ensure_namespace` 之后调 `core/registry.ensure_registry_pull_secret`,按生效 `registry_*` 把 `superdl-registry-pull` 托管到租户 ns(annotation 指纹相同跳过),Pod spec 以 `imagePullSecrets` 引用;未配机器人则 `image_pull_secret=None`。预热 Job 同一条链(平台 ns)。
- 型号 nodeSelector 由 spec 快照的 `gpu_model_selector` 键决定(未识别型号存 None,不钉型号)。
- shared 档 create/start/restart 三入口读集群能力缓存做 HAMi 门禁,未就绪直接报 `CLUSTER_NOT_READY`,见 [nodes.md](./nodes.md)。门禁判据与 `build_gpu_request` 同源:**先看要不要卡,再看落哪个池** —— `gpu_count == 0` 的实例只过 StorageClass(它不申请 `nvidia.com/*`、走默认调度器,挂在 hami 池上也不需要 hami-scheduler);要卡的才按池过 HAMi / Kata 门禁。
- 每用户实例数、GPU 数与 CPU 实例 vCPU 数配额由策略/config 控制,三维互不相交(CPU 实例不计入 GPU 维,GPU 实例不计入 vCPU 维),见 [limits.md](./limits.md)。
- 实例释放后触发擦盘任务;数据盘生命周期独立,见 [disks.md](./disks.md)。
