# 编排

实例状态机、outbox 编排、reconciler 对账、接入(SSH / JupyterLab)与 K8s 抽象。

## 数据模型

- `instances`:uuid、user_id、SKU 快照(sku_id + `spec` jsonb + price_hourly;`spec.base_price_hourly` 是 SKU **原价**快照,竞价转按量据它还原)、market(CHECK ∈ {on_demand, spot, subscription},默认 on_demand)、gpu_count(CHECK ≥0;**0 = 纯 CPU 实例**,见 [catalog.md](./catalog.md))、status、k8s(namespace/node_name(253))、ssh_port?、jupyter_token(AES-GCM 密文)、image_ref、data_disk_id?、workload_type(CHECK ∈ {dev, service},与 service_id 同真同假)、service_id?/service_revision?/service_slug?/service_port?/health_path?(所属在线服务与该版本的暴露规格快照)、with_ssh、container_command?/container_args?(jsonb)、env_encrypted?、idempotency_key 唯一?(24h 窗口)、version(乐观锁)
- `instance_events`:instance_id、from_status、to_status、reason、actor(user/system/admin)、metadata —— 追加式,计费主依据
- `port_allocations`:port 唯一(30000~32767)、instance_id nullable(部分唯一:一台实例至多一个端口)
- 在线服务(`services`、`service_api_keys`)与其版本实例的关系见 [services.md](./services.md)

状态机:creating→running/failed/releasing;running→stopping/failed;stopping→stopped/releasing;stopped→starting/frozen/releasing;starting→running/failed;frozen→stopped/releasing;failed→stopped/releasing;releasing→released(唯一终态)。running↔非 running 的边即计费边。failed→stopped 是故障恢复边(复用实例盘重开机);stopping→releasing 是悬挂放弃边。包周期到期不新增状态与边,只多两个 reason:欠费 `arrears_stop` / `arrears_freeze`,到期 `subscription_expired` / `subscription_freeze`。

## 契约

| 端点 | 角色/鉴权 | 说明 |
|---|---|---|
| `POST /api/v1/instances` | user | 只建开发机(拒收服务容器参数)。Idempotency-Key;软准入(台账无货 409)→ 钱包行锁(余额校验、配额)→ 事务写 instances(creating)+event+outbox → 202。`market` 默认 `on_demand`;`subscription` 时 `period` 必填、`period_count` 1~36,同事务预扣(见 [billing.md](./billing.md));`on_demand` 却带 `period`/`period_count` 422;`spot` 要求 SKU `spot_enabled`(否则 400 `orchestrator.spotNotEnabled`)且不带 `period`,软准入判无货时先抢占竞价实例,腾不出才 409 `NO_CAPACITY` |
| `GET /api/v1/instances` | user | 降序游标分页 `?cursor=&limit=`;`status` 精确过滤,`name` 模糊(含 uuid 前缀);**只列开发机** |
| `GET /api/v1/instances/{uuid}` | user | 详情 |
| `PATCH /api/v1/instances/{uuid}` | user | 改名 |
| `POST /api/v1/instances/{uuid}/stop\|start\|restart` | user | 均经 outbox;start 对 failed 放行 |
| `POST /api/v1/instances/{uuid}/subscribe` | user | **按量转包周期**,入参与响应同 `/renew`;Idempotency-Key。前置:`market='on_demand'` 且 running / stopped(其余 409 `orchestrator.convertNeedsRunningOrStopped`)、SKU `period_enabled`;已在保报 `billing.subscriptionAlreadyActive`;结算滞后超 48h 报 409 `billing.settlementBehind` |
| `POST /api/v1/instances/{uuid}/to-on-demand` | user | **竞价转按量**。无 body、**不需要 Idempotency-Key**(已是按量原样返回 200)。前置:`market='spot'`(否则 `orchestrator.toOnDemandNotSpot`)、running / stopped |
| `POST /api/v1/instances/{uuid}/renew` | user | 包周期续费,body `{period, period_count}`;Idempotency-Key(重放 200 + `X-Idempotent-Replay`);返回 `{instance, quote}`。非包周期 / 已释放 `SUBSCRIPTION_NOT_RENEWABLE`(400),余额不足 `INSUFFICIENT_BALANCE`。冻结中续费即解冻(回 stopped) |
| `POST /api/v1/instances/{uuid}/auto-renew` | user | body `{enabled}`;默认关 |
| `DELETE /api/v1/instances/{uuid}` | user | 释放(stopped/frozen/failed/creating/stopping);releasing/released 重放回当前状态。**包周期实例释放不退款**,订阅转 cancelled |
| `GET /api/v1/instances/{uuid}/events` | user | 事件时间线,即计费依据;降序游标分页 |
| `GET /api/v1/instances/{uuid}/access` | user | SSH 指令 + Jupyter 一次性 bootstrap 票据 URL(单次、60s;核销后种第一方 cookie);非 running 报错 |
| `GET /api/v1/instances/{uuid}/logs` | user | 容器日志:**只读**、**owner 校验**(非属主 404)、**限流 20/h/user**、**K8s 读 5s 超时**;仅 running/stopping(其余 409);`?tail_lines=` 默认 200、超 2000 截断;返回 `{lines, truncated}`;不记审计 |
| `POST /api/v1/instances/{uuid}/reset-jupyter-token` | user | 轮换 token,旧票据与旧 URL 立即失效 |
| `POST /api/admin/v1/instances/{uuid}/preempt` | ops | 强制回收一台竞价实例,reason 必填;非竞价 `orchestrator.preemptNotSpot`,非 running `orchestrator.forceStopNeedsRunning`(见 [admin.md](./admin.md)) |

在线服务的全部端点见 [services.md](./services.md)。实例级生命周期端点(stop / start / restart / DELETE / 重置 token)对服务的版本实例一律 409 `orchestrator.serviceInstanceLifecycle`;只读端点与购买模式类端点照常。

## 规则与不变量

- 状态迁移只经 `orchestrator/service.py` 的 transition 函数(同事务写 `instance_events`);非法迁移 `INSTANCE_INVALID_TRANSITION`。
- 生命周期分两层:`create_instance_row` / `stop_instance_row` / `start_instance_row` / `release_instance_row` 是不 commit 的 row 级核心(services 模块在自己的事务里调),用户端入口 `create_instance` 等只做取实例 + commit。`create_instance_row` 的 `service=ServiceBinding(...)` 把实例建成某个在线服务的一个版本;`exclude_instance_id` 让即将被替换的旧实例不占配额与软准入名额(余额不让)。锁序 instance → service → disk → wallet。
- 请求路径不许调 K8s:业务写入与 `outbox_tasks` 同事务。唯一例外是日志端点只读直读,由 owner / 限流 / 超时三道闸兜住。
- **购买模式变更不写 `instance_events`**(`subscribe_instance` 与 `convert_to_on_demand`);变更痕迹在审计日志与资金流水。
- K8s 访问收敛在 `app/core/k8s`,`K8sOrchestrator` 协议是唯一接口面(`app/core/k8s/base.py`)。FakeOrchestrator 与 RealOrchestrator 同步实现协议全部方法。
- RealOrchestrator 每租户:独立 namespace(PSA enforce=baseline + audit=restricted)、ResourceQuota 兜底、Egress 隔离 NetworkPolicy、JuiceFS PVC;`disk.wipe` 为真实擦除 Job(幂等 + 退避,模板显式 `hostUsers: false`,镜像 digest 钉死 `WIPE_IMAGE`)。ns/NetPol/Quota 已存在时 patch 收敛;K8s list 一律分页(limit=500 + continue),同步调用走专属有界执行器。对象构造是纯函数(`build_instance_pod` / `build_managed_job` / `build_disk_quota_container` / `build_prewarm_job`),`scripts/render_admission_probes.py` 用它们渲染成清单供 CI 在准入策略下 `--dry-run=server` 对账。
- 镜像拉取凭据不落节点、不进 Pod spec 明文:outbox 建 Pod 前在 `ensure_namespace` 之后调 `core/k8s.ensure_registry_pull_secret`,把 `superdl-registry-pull` 托管到租户 ns(annotation 指纹相同跳过),Pod spec 以 `imagePullSecrets` 引用;未配机器人 `image_pull_secret=None`。预热 Job 同一条链。
- 端口从 `port_allocations` 池分配,释放/失败回池(停机不回);池耗尽创建失败并给明确错误;水位每轮 reconciler 刷进 `superdl_ssh_port_pool_ports{state}`。
- 每用户实例数、GPU 数与 CPU 实例 vCPU 数三维互不相交;数值见 [limits.md](./limits.md)。
- 实例释放后触发擦盘任务;数据盘独立,见 [disks.md](./disks.md)。

### 规格与调度

- gpu_adapter 按**池**产出资源请求语法(HAMi `nvidia.com/gpu` + `gpucores`/`gpumem`;MIG profile;整卡)、选 RuntimeClass(kata-qemu / runc)、按 canonical 型号产出 `superdl.io/gpu-model` nodeSelector,以及 `annotations` 透传口(`nvidia.com/use-gputype`,开关 `SUPERDL_HAMI_USE_GPUTYPE` 默认关)。
- **`gpu_count == 0` 的判定先于池分支**:资源请求为空、不钉型号、`runtimeClass=None`、`hostUsers=false`,nodeSelector 只有池标签。
- Pod 规格倍率:GPU 实例 vCPU/内存按卡数放大,CPU 实例倍率恒 1;系统盘不放大。
- 创建时 `gpu_count` 合法区间随 SKU 形态走:`max_gpus_per_instance == 0` 只收 0(否则 `orchestrator.cpuSkuNoGpu`),否则 `1..max`(否则 `orchestrator.gpuCountRange`)。契约层 `ge=0, le=8`,配对闸门在 service。
- 型号 nodeSelector 由 spec 快照的 `gpu_model_selector` 键决定(未识别型号存 None)。
- create/start/restart 三入口读集群能力缓存做门禁,未就绪 `CLUSTER_NOT_READY`;判据与 `build_gpu_request` 同源,见 [nodes.md](./nodes.md)。

### reconciler 与保留期

- reconciler(30s,advisory lock)是唯一收敛点:Pod Ready 而 DB creating/starting → running;Pod 消失而 DB running → failed;Pod 存在而 DB 终态 → 强删;creating 超 5min → failed(包周期实例首次 creating 超时同事务退回预付)。每轮一次 `list_instance_pods` 即状态源,不逐实例 `get_status`。节点集合变化触发的租户 NetPol 重下发逐 ns 记账(`_netpol_synced_nodes`):成功的 ns 下轮不重跑,失败的单独重试,一个 ns 出错不阻塞其余。
- stopping/releasing 悬挂两档超时(`stopping_timeout_seconds`/`releasing_timeout_seconds`):一档经 outbox 重发删除,二档 force 强删后按正常边收敛。悬挂实例数见 `superdl_reconcile_stuck_instances`。
- 泄漏回收熔断:未知 Pod 占比超 `leak_reclaim_abort_ratio` 即中止本轮并计 `superdl_reconcile_leak_aborted_total`;在途删除宽限同两档超时,其余 force 强删。
- 保留期 GC 在 reconciler 内:failed 超 `failed_retention_days` → 通知并转 releasing;stopped 超 `stopped_retention_days` → 转 releasing,提前 `stopped_retention_warn_days` 预警。数据盘不受影响。阈值见 [limits.md](./limits.md)。
- 节点失联判定先看节点 Ready(`list_nodes`):持续 not-ready 超 `running_unready_timeout_seconds` 且节点 NotReady/未知 → node_lost(通知用户);节点正常 → pod_unready。**`workload_type='service'` 不走 pod_unready 一支**,实例留在 running;`pod_lost` 与 `node_lost` 不豁免。
- **`instances.unready_since` 跨轮累积,清零只有两处**:任一进入 running 的路径与「本轮观测到 Pod 重新 ready」。宽限期内与服务型实例的 `pod_unready` 豁免返回「无失联原因」但**不得**据此清零。它同时是事件 `metadata.unready_since` 的来源,平台责任失联的计费截断据它算(见 [billing.md](./billing.md))。

### 购买模式

三种购买模式(`instances.market`),与 `skus.tier` 正交:一条 SKU 三种卖法。

| 值 | 含义 |
|---|---|
| `on_demand` | 按量,进 `bills_hourly` |
| `subscription` | 包周期,下单一次性预扣,小时结算在 `billing_candidates` 一处跳过(见 [billing.md](./billing.md)) |
| `spot` | 竞价(按量价 × `spot_discount_pct`),容量紧张时**可被平台回收**。与按量同一条计费链,折扣只落 `price_hourly`。前置 SKU `spot_enabled`(见 [catalog.md](./catalog.md)) |

- `market` 创建时定,**只有两条路径会改它**:`subscribe_instance`(按量 → 包周期)与 `convert_to_on_demand`(竞价 → 按量)。包周期 → 按量、按量 → 竞价不开。
- **`instances.price_hourly` 是该购买模式下的有效时价**,由 `app/core/pricing.py` 的 `price_for` 单点算出。市场页报价、创建预估、续费报价共用同一组函数。
- **包周期实例的开机门禁看周期,不看余额**:`start` 对 `market='subscription'` 走 `assert_subscription_active`(到期报 `SUBSCRIPTION_EXPIRED` 409),不走 `assert_can_afford`。订阅行缺失判过期。
- **未到期的包周期实例即使已停机也仍占软准入库存。** `_reserved_slots` 把同一条 SKU 上「stopped / frozen 且仍在保」的实例计为占用。**控制面层面预留,物理层不预留**,创建页与续费入口写给用户看。只算同一条 SKU。
- 续费同事务刷新 `instances.price_hourly`;冻结中的实例续费即回 `stopped` 并清 `frozen_deadline`,不自动开机。
- **按量转包周期「先结后翻」**:`subscribe_instance` 在钱包行锁内先结清转换前那段按量账(用**转换前**的按量时价),再落订阅行、翻 `market`、刷 `price_hourly`。口径见 [billing.md](./billing.md)。
- **幂等重放在全部守卫之前判**。
- 两条转换路径只收 running / stopped。
- 列表与详情的包周期概要(`InstanceOut.subscription`:period / period_count / expires_at / status / auto_renew / amount_paid)与服务端点 slug 由 `attach_instance_details` **各一次批量查询**回填(见 [web.md](./web.md));管理端走同一条回填路径。

### 竞价抢占

选择器与回收在 `app/modules/orchestrator/preempt.py`,触发点是软准入 `_soft_admit_capacity`,用例在 `apps/api/tests/test_spot.py`。钱的口径见 [billing.md](./billing.md)。

三条硬规矩逐字写在竞价知情同意 modal 里(见 [../ui-ux-spec.md](../ui-ux-spec.md) §1 规则 5),**改代码等于改文案,两边同提交**:

1. **只在同池同型号内选。** 候选谓词 `status='running' AND market='spot' AND spec->>'pool_label' = 池 AND spec->>'gpu_model_selector' IS NOT DISTINCT FROM canonical 型号`(未识别型号快照为 NULL)。
2. **按 `created_at` 从新到旧。** 唯一排序规则(同刻 `id` 降序)。
3. **凑不够一台都不动。** 按 `gpu_count` 累加到够为止;不够返回空列表,请求方 409 `NO_CAPACITY`。

- 只有**非竞价的 GPU 档请求**触发抢占。
- 缺口换算 `cards_needed = ⌈缺的槽位 ÷ sellable_per_gpu⌉`;「一台实例腾出 `gpu_count` 张卡」与软准入 `_sku_free_capacity` 同一套近似口径。
- **抢占与请求方的建实例同一个事务**,`preempt()` 不 commit。
- 宽限窗:对每台选中实例同事务做三件事 —— `transition(→ stopping, reason='preempted', actor='system')` + `enqueue('instance.stop', delay_seconds=spot_grace_seconds)` + 短信与站内信。**状态机立刻迁 `stopping`**,**Pod 到期才删**。终态 `stopped`,实例盘保留。
- 通知 dedup_key 带实例 id 与分钟位,**不按天分桶**。
- 宽限窗与 `creating_timeout_seconds` 共用一段时间预算,`spot_grace_seconds` 真实上限见 [limits.md](./limits.md)。
- 每回收一台计 `superdl_spot_preempted_total`(见 [observability.md](./observability.md))。
- 管理端 `/preempt` **不复用强制停止**,reason 分开。
- 转按量**不动 Pod、零中断**:单价还原成 `spec.base_price_hourly`,**不从折后价反推**;当前整点小时整体改按按量价结算(见 [billing.md](./billing.md),必须写进转换确认弹窗);转完再过一次 `assert_can_afford`。

### 实例形态

两种形态(`instances.workload_type`),差别只在 `build_pod_spec` 分叉与建哪些 K8s 对象;状态机、计费、配额、回收、reconciler、监控、审计全部共用。`service` 形态的实例是某个在线服务的一个版本(`service_id` 反指),暴露规格(`service_slug` / `service_port` / `health_path`)快照在实例行上:

| | `dev`(SSH + JupyterLab) | `service`(在线服务的版本) |
|---|---|---|
| `restartPolicy` | `Never` | `Always`(kubelet 原地重启容器,Pod 不重建;reconciler 建立在「Pod 名 = 实例 uuid」上) |
| command / args | 不设 | 用户可覆盖(`container_command` / `container_args`) |
| 用户 env | 无 | `env_encrypted`(整包 AES-GCM,AAD 绑实例 uuid);密文项经 per-instance Secret 以 `secretKeyRef` 引用 |
| SSH NodePort Service | 恒建 | `with_ssh` 才建;为假时**不进端口池** |
| Jupyter Service + HTTPRoute | 恒建 | 不建 |
| 服务 Service + HTTPRoute | 无 | `<uuid>-svc` ClusterIP + 挂 `svc-https` listener 的 HTTPRoute |
| 探针 | 无 | `health_path` 非空时 startupProbe(90 × 10s = 15 分钟)+ readinessProbe |

在线服务的域名规则、鉴权链路、状态派生与 API Key 生命周期见 [services.md](./services.md)。

### 网关与接入

- **服务路由只能挂 `svc-https` listener**:`app-https` 上没有 `SecurityPolicy.extAuth`,挂过去照样通、不鉴权、无报错。两个 listener 名在 `core/k8s/base.py` 的 `GATEWAY_APP_LISTENER` / `GATEWAY_SVC_LISTENER` 钉死,离线用例 `tests/test_k8s_real_units.py::TestServiceWorkloadObjects` 逐条断言。
- **租户 HTTPRoute 的挂载契约**由 `core/k8s/base.py` 三个常量钉死(`GATEWAY_NAMESPACE` / `GATEWAY_NAME` / `GATEWAY_APP_LISTENER`,须与 `deploy/app/k8s/04-gateway.yaml` 逐字一致;无对应 `SUPERDL_*` 配置):路由建在**租户 ns**,`parentRefs` 指平台 ns 的 `Gateway superdl`,`sectionName` 钉死 `app-https`。跨 ns 挂载由 listener 的 `allowedRoutes.namespaces.from: Selector` 授权,选择器是 `ensure_namespace` 打在租户 ns 上的 `superdl.io/managed=true`;**不需要 ReferenceGrant**。三个名字任一写错都不报错:`create` 返 201,路由停在 `status.parents[].conditions` 的 `Accepted=False` / `NotAllowedByListeners`。不写 `sectionName` 则路由挂到全部同端口 listener。
- **Jupyter 的 WebSocket 与 SSE 靠网关 `streamIdleTimeout: 1h`**(`ClientTrafficPolicy superdl-gateway`);调整网关策略别落回默认 5 分钟。
- **路由规模是容量变量**:一实例一条 HTTPRoute,数据面与 EG 控制面内存随路由数涨。light 档要么给足 `EnvoyProxy` memory limit,要么对单机实例数设硬上限;取值实机压过再定,见 `deploy/app/k8s/04-gateway.yaml`。
- 孤儿端点清理按 `superdl.io/managed` 标签全集群 LIST(Service 与 HTTPRoute),HTTPRoute 无 typed model,走 `CustomObjectsApi` 收发裸 dict,分页游标在 `metadata.continue`。
- SSH 仅密钥登录;连接串 `ssh root@<实例域名> -p 3xxxx`:主机名就是实例自己的域名(与 Jupyter 同名,`orchestrator/service.jupyter_host`),靠 NodePort 区分。部署约束:泛域名解析到的地址必须同时转发 80/443 与 `ssh_port_range` 端口段。
- SSH 依赖租户容器的三个 capability(`SYS_CHROOT` / `SETUID` / `SETGID`,见 [security.md](./security.md))与 entrypoint 起 sshd 前对 `/root` 的 `chmod g-w,o-w`。
- SSH host key 持久化在实例盘(`/root/.ssh/host_keys`);jupyter 由 entrypoint 守护循环拉起(不做 PID 1),连续秒退 5 次才让 Pod 失败。
- JupyterLab 每实例一条 HTTPRoute 按 host 路由,主机名 = `SUPERDL_JUPYTER_HOST_PREFIX`(默认空)+ uuid + `.` + `SUPERDL_JUPYTER_DOMAIN_SUFFIX`,由 `orchestrator/service.jupyter_host` 单点拼接。与其它业务共用一级域时用前缀区分(如 `jupyter-<uuid>.<域>`),此时 Jupyter 与服务端点按端口分开,`SUPERDL_JUPYTER_URL_PORT`(默认 443)把端口带进入场 URL 与 `JUPYTER_ALLOW_ORIGIN`,**只影响 origin,不影响 HTTPRoute hostname 与 SSH 连接串**;两种分法见 [services.md](./services.md)「域名规则」。证书由 listener 的 `certificateRefs` 提供。
- Jupyter token 由控制面生成、AES-GCM 密文落库、注入 Pod env;access 端点签发一次性 bootstrap 票据(HMAC 密钥 = token 本体,单次、60s),镜像内 `/superdl-bootstrap` handler 核销后种第一方 cookie;`?token=` stock 登录为回落通道。**实例镜像须先于控制面发布**。
