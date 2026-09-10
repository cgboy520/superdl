# 编排

实例状态机、outbox 编排、reconciler 对账、接入(SSH / JupyterLab)与 K8s 抽象。

## 数据模型

- `instances`:uuid、user_id、SKU 快照(sku_id + `spec` jsonb + price_hourly;`spec.base_price_hourly` 是 SKU **原价**时价快照,竞价转按量据它还原单价)、market(CHECK ∈ {on_demand, spot, subscription},默认 on_demand)、gpu_count(CHECK ≥0;**0 = 纯 CPU 实例**,见 [catalog.md](./catalog.md))、status、k8s(namespace/node_name(253))、ssh_port?、jupyter_token(AES-GCM 密文)、image_ref、data_disk_id?、workload_type(CHECK ∈ {dev, service},与 service_id 同真同假)、service_id?/service_revision?/service_slug?/service_port?/health_path?(所属在线服务与该版本的暴露规格快照,orchestrator 建 Pod 只读它们)、with_ssh、container_command?/container_args?(jsonb)、env_encrypted?、idempotency_key 唯一?(24h 窗口,窗外同键按新单)、version(乐观锁)
- `instance_events`:instance_id、from_status、to_status、reason、actor(user/system/admin)、metadata —— 追加式,计费主依据
- `port_allocations`:port 唯一(30000~32767)、instance_id nullable(部分唯一:一台实例至多一个端口)
- 在线服务(`services`、`service_api_keys`)与其版本实例的关系见 [services.md](./services.md)

状态机:creating→running/failed/releasing;running→stopping/failed;stopping→stopped/releasing;
stopped→starting/frozen/releasing;starting→running/failed;frozen→stopped/releasing;
failed→stopped/releasing;releasing→released(唯一终态)。running↔非 running 的边即计费边。
failed→stopped 是故障恢复边(复用同一块实例盘重开机,start 端点对 failed 放行);
stopping→releasing 是悬挂放弃边(关机删不掉时允许直接释放)。包周期到期不新增状态与边,只多两个
reason:欠费 `arrears_stop` / `arrears_freeze`,包周期到期 `subscription_expired` / `subscription_freeze`。

## 契约

| 端点 | 角色/鉴权 | 说明 |
|---|---|---|
| `POST /api/v1/instances` | user | 只建开发机(拒收服务容器参数;部署在线服务走 `POST /services`)。Idempotency-Key;软准入(台账无货 409)→ 钱包行锁临界区(在途+新增余额校验、配额)→ 事务写 instances(creating)+event+outbox → 202。`market` 默认 `on_demand`;取 `subscription` 时 `period` 必填、`period_count` 1~36,同事务预扣整段周期(见 [billing.md](./billing.md));`market='on_demand'` 却显式带 `period`/`period_count` 一律 422;`market='spot'` 要求 SKU `spot_enabled` 为真(否则 400 `orchestrator.spotNotEnabled`)且不带 `period`,软准入判无货时先抢占竞价实例腾容量,腾不出才 409 `NO_CAPACITY` |
| `GET /api/v1/instances` | user | 降序游标分页 `?cursor=&limit=`;`status` 精确过滤,`name` 模糊匹配(含 uuid 前缀);**只列开发机**,在线服务的版本实例不出列表 |
| `GET /api/v1/instances/{uuid}` | user | 详情 |
| `PATCH /api/v1/instances/{uuid}` | user | 改名 |
| `POST /api/v1/instances/{uuid}/stop\|start\|restart` | user | 同构,均经 outbox;start 对 failed 放行(恢复边) |
| `POST /api/v1/instances/{uuid}/subscribe` | user | **按量转包周期**,入参与响应同 `/renew`;Idempotency-Key。前置:`market='on_demand'` 且状态 running / stopped(其余 409 `orchestrator.convertNeedsRunningOrStopped`)、SKU `period_enabled` 为真;已在保报 `billing.subscriptionAlreadyActive`;结算滞后超 48h 报 409 `billing.settlementBehind` |
| `POST /api/v1/instances/{uuid}/to-on-demand` | user | **竞价转按量**(转完不再被回收)。无 body、**不需要 Idempotency-Key**(目标状态唯一,已是按量则原样返回 200)。前置:`market='spot'`(否则 `orchestrator.toOnDemandNotSpot`)、状态 running / stopped(其余 409 `orchestrator.convertNeedsRunningOrStopped`) |
| `POST /api/v1/instances/{uuid}/renew` | user | 包周期续费,body `{period, period_count}`;Idempotency-Key(重放回 200 + `X-Idempotent-Replay`);返回 `{instance, quote}`,报价三件套由后端算好逐行下发。非包周期 / 已释放报 `SUBSCRIPTION_NOT_RENEWABLE`(400),余额不足 `INSUFFICIENT_BALANCE`。冻结中续费即解冻(回 stopped,不自动开机) |
| `POST /api/v1/instances/{uuid}/auto-renew` | user | body `{enabled}`;到期自动续费开关,默认关 |
| `DELETE /api/v1/instances/{uuid}` | user | 释放(stopped/frozen/failed/creating/stopping);幂等:releasing/released 重放回当前状态而非 400。**包周期实例释放不退款**,订阅转 cancelled(见 [billing.md](./billing.md)) |
| `GET /api/v1/instances/{uuid}/events` | user | 事件时间线,即计费依据;降序游标分页 `?cursor=&limit=` |
| `GET /api/v1/instances/{uuid}/access` | user | SSH 指令 + Jupyter 一次性 bootstrap 票据 URL(单次、60s;核销后种第一方 cookie,token 不进 URL);非 running 报错并说明 |
| `GET /api/v1/instances/{uuid}/logs` | user | 容器日志:**只读**、**owner 校验**(非属主 404 不暴露存在性)、**限流 20/h/user**、**K8s 读 5s 超时**;仅 running/stopping(其余 409,已关机无 Pod 日志);`?tail_lines=` 默认 200、超 2000 截断;返回 `{lines, truncated}`;不记审计 |
| `POST /api/v1/instances/{uuid}/reset-jupyter-token` | user | 轮换 token(密文落库),旧票据与旧 URL 立即失效 |
| `POST /api/admin/v1/instances/{uuid}/preempt` | ops | 强制回收一台竞价实例腾容量,reason 必填;非竞价报 `orchestrator.preemptNotSpot`,非 running 报 `orchestrator.forceStopNeedsRunning`(见 [admin.md](./admin.md)) |

在线服务的全部端点见 [services.md](./services.md)。实例级生命周期端点(stop / start / restart / DELETE / 重置 token)对服务的版本实例一律 409 `orchestrator.serviceInstanceLifecycle`;只读端点与购买模式类端点(续费 / 转换)照常。

## 规则与不变量

- 状态迁移只能经 `orchestrator/service.py` 的 transition 函数(同事务写 `instance_events`),禁止直接 UPDATE status;非法迁移报 `INSTANCE_INVALID_TRANSITION`。
- 生命周期分两层:`create_instance_row` / `stop_instance_row` / `start_instance_row` / `release_instance_row` 是不 commit 的 row 级核心(services 模块在自己的事务里调它们),用户端入口 `create_instance` 等只做取实例 + commit。`create_instance_row` 的 `service=ServiceBinding(...)` 把实例建成某个在线服务的一个版本;`exclude_instance_id` 让即将被替换的旧实例不占配额与软准入名额(余额不让)。锁序 instance → service → disk → wallet。
- 请求路径不许调 K8s:业务写入与 `outbox_tasks` 插入同一事务,K8s 动作一律由 worker 执行。唯一例外是日志端点的只读直读,由 owner / 限流 / 超时三道闸兜住。
- **购买模式变更不写 `instance_events`**(`subscribe_instance` 与 `convert_to_on_demand` 同理):该表是计费主依据(running↔非 running 的边),非状态迁移的行会污染 `running_seconds_in_window` 的重建。变更痕迹在审计日志与资金流水里。
- K8s 访问收敛在 `app/core/k8s`,`K8sOrchestrator` 协议是唯一接口面(方法清单见 `app/core/k8s/base.py`)。FakeOrchestrator(dev/test,内存态,可注入故障)与 RealOrchestrator(kubernetes 官方客户端)必须同步实现协议全部方法。
- RealOrchestrator 每租户:独立 namespace(PSA enforce=baseline + audit=restricted 标签)、ResourceQuota 兜底(对象数 + cpu/memory/ephemeral-storage 总量)、Egress 隔离 NetworkPolicy(私网黑名单 + 滥用端口黑名单,DNS 收敛到 CoreDNS Pod)、JuiceFS PVC;`disk.wipe` 为真实擦除 Job(幂等 + 退避)。ns/NetPol/Quota 已存在时 patch 收敛,加固覆盖存量租户;K8s list 调用一律分页(limit=500 + continue),同步调用走专属有界执行器。
- 镜像拉取凭据不落节点、不进 Pod spec 明文:outbox 建 Pod 前在 `ensure_namespace` 之后调 `core/registry.ensure_registry_pull_secret`,按生效 `registry_*` 把 `superdl-registry-pull` 托管到租户 ns(annotation 指纹相同跳过),Pod spec 以 `imagePullSecrets` 引用;未配机器人则 `image_pull_secret=None`。预热 Job 同一条链(平台 ns)。
- 端口从 `port_allocations` 池分配,释放必须回池;池耗尽时创建失败并给出明确错误。
- 每用户实例数、GPU 数与 CPU 实例 vCPU 数配额三维互不相交(CPU 实例不计入 GPU 维,GPU 实例不计入 vCPU 维);全部数值见 [limits.md](./limits.md)。
- 实例释放后触发擦盘任务;数据盘生命周期独立,见 [disks.md](./disks.md)。

### 规格与调度

- gpu_adapter 按**池**产出资源请求语法(HAMi `nvidia.com/gpu` + `gpucores`/`gpumem`;MIG profile;整卡)、选 RuntimeClass(kata-qemu / runc)、按 canonical 型号产出 `superdl.io/gpu-model` nodeSelector,以及 `annotations` 透传口(`nvidia.com/use-gputype`,开关 `SUPERDL_HAMI_USE_GPUTYPE` 默认关,仅混卡节点池需要)。
- **`gpu_count == 0`(纯 CPU 实例)的判定先于池分支**:资源请求为空、不钉型号、`runtimeClass=None`、`hostUsers=false`,nodeSelector 只有池标签。cpu 档允许挂 hami 池(见 [catalog.md](./catalog.md)),按池分支走就会替不用卡的实例申请 `nvidia.com/gpu`。
- Pod 规格倍率:GPU 实例的 vCPU/内存按卡数放大(N 卡收 N 倍价即给 N 份资源),CPU 实例倍率恒 1;系统盘任何形态都不放大。
- 创建时 `gpu_count` 的合法区间随 SKU 形态走:`max_gpus_per_instance == 0`(CPU 规格)只收 0(否则 `orchestrator.cpuSkuNoGpu`),否则只收 `1..max`(否则 `orchestrator.gpuCountRange`)。契约层是 `ge=0, le=8`,真正的配对闸门在 service。
- 型号 nodeSelector 由 spec 快照的 `gpu_model_selector` 键决定(未识别型号存 None,不钉型号)。
- create/start/restart 三入口读集群能力缓存做门禁,未就绪直接报 `CLUSTER_NOT_READY`;判据与 `build_gpu_request` 同源,口径见 [nodes.md](./nodes.md)。

### reconciler 与保留期

- reconciler(30s,advisory lock)是唯一收敛点:Pod Ready 而 DB creating/starting → running(开始计费);Pod 消失而 DB running → failed(停费并告警);Pod 存在而 DB 终态 → 强删(force);creating 超 5min → failed 并退款。每轮一次 `list_instance_pods` 即状态源(存在性/ready/phase/node_name/deleting,与 `get_status` 同形),不逐实例 `get_status`。
- stopping/releasing 悬挂两档超时(`stopping_timeout_seconds`/`releasing_timeout_seconds`):一档经 outbox 重发删除任务,二档 force 强删后按正常边收敛(stopped 保留端口与实例盘;released 回收端口并销毁实例盘)。悬挂实例数见指标 `superdl_reconcile_stuck_instances`。
- 泄漏回收熔断:未知(DB 无记录)Pod 占比超 `leak_reclaim_abort_ratio` 即中止本轮并计 `superdl_reconcile_leak_aborted_total`;在途删除(stopping/releasing)宽限同两档超时,其余一律 force 强删。
- 保留期 GC 在 reconciler 内:failed 超 `failed_retention_days` → 通知并转 releasing;stopped 超 `stopped_retention_days` → 转 releasing,提前 `stopped_retention_warn_days` 预警。数据盘不受影响。阈值见 [limits.md](./limits.md)。
- 节点失联判定先看节点 Ready 状况(`list_nodes`):持续 not-ready 超 `running_unready_timeout_seconds`(须宽于 unreachable toleration 的 300s)且节点 NotReady/未知 → node_lost(通知用户);节点正常 → pod_unready(Pod 自身问题,不告警失联)。**`workload_type='service'` 不走 pod_unready 这一支**(not-ready 判据是用户自己声明的 readinessProbe):实例留在 running,就绪与否如实呈现在服务 Tab。`pod_lost` 与 `node_lost` 两支不豁免。
- **`instances.unready_since` 跨轮累积,清零只有两处**:任一进入 running 的路径(开机 / 重启 / 状态机迁移)与
  「本轮观测到 Pod 重新 ready」。宽限期内与服务型实例的 `pod_unready` 豁免都返回「无失联原因」,但**不得**据此清零 ——
  在那里清等于每轮把计时抹平、超时分支永不可达,卡在 Running-but-not-ready 的 Pod 就永远不判故障、一直计费。
  它同时是事件 `metadata.unready_since` 的来源,平台责任失联的计费截断据它算(见 [billing.md](./billing.md))。

### 购买模式

实例有三种购买模式(`instances.market`),与 `skus.tier`(买什么档)正交:一条 SKU 三种卖法,不为包周期或竞价另建 SKU 行。

| 值 | 含义 |
|---|---|
| `on_demand` | 按量,唯一进 `bills_hourly` 的模式 |
| `subscription` | 包周期,下单一次性预扣,小时结算在 `billing_candidates` 一处跳过(见 [billing.md](./billing.md)) |
| `spot` | 竞价(按量价 × `spot_discount_pct`),对价是容量紧张时**可被平台回收**。与按量走同一条计费链:一样进 `bills_hourly`、一样出尾账,折扣只落在 `price_hourly` 上,结算引擎不感知竞价。前置是 SKU `spot_enabled`(默认关,见 [catalog.md](./catalog.md)) |

- `market` 由创建时定,**只有两条路径会改它**:`subscribe_instance`(按量 → 包周期)与 `convert_to_on_demand`(竞价 → 按量)。**包周期 → 按量**与**按量 → 竞价**两个方向都不开。
- **`instances.price_hourly` 落的是该购买模式下的有效时价**,由 `app/core/pricing.py` 的 `price_for` 单点算出(按量即 SKU 原价,竞价按 `spot_discount_pct` 打折,包周期按周期折扣打折)。市场页报价、创建预估、续费报价共用同一组函数,不得各算各的;计费引擎只拿「这台实例的时价」。
- **包周期实例的开机门禁看周期,不看余额**:`start` 对 `market='subscription'` 走 `assert_subscription_active`(周期内才放行,到期报 `SUBSCRIPTION_EXPIRED` 409),不走 `assert_can_afford`。订阅行缺失也判过期(fail-closed)。
- **未到期的包周期实例即使已停机,也仍占软准入库存。** `_reserved_slots` 把「同一条 SKU 上 stopped / frozen 且仍在保」的实例计为占用,从可售数里扣掉(台账的 `gpu_used` 只数真在跑的 Pod)。**这是控制面层面的预留,物理层不预留**,创建页与续费入口必须把这一条写给用户看。只算同一条 SKU,不跨规格折算槽位。
- 续费同事务刷新 `instances.price_hourly`(用户可以换周期续);冻结中的实例续费即回 `stopped` 并清 `frozen_deadline`,不自动开机。
- **按量转包周期的顺序是「先结后翻」,不可颠倒**:`subscribe_instance` 在钱包行锁内先把转换前那段按量账结清(running 才有账要结),再落订阅行、翻 `market`、刷 `price_hourly`。翻在前则 `billing_candidates` 会按新 market 整个排除这台实例,水位线之后未出账的小时永远不结;结算必须用**转换前**的按量时价。口径与拒绝条件见 [billing.md](./billing.md)。
- **幂等重放在全部守卫之前判**,否则重放会撞上「只有按量实例可以转」那条守卫拿到 400,并按已是折后价的 `price_hourly` 再跑一遍结算。
- 两条转换路径都只收 running / stopped:在途态(creating / starting / stopping / releasing)翻 `market` 会和收敛路径抢同一行;frozen 是欠费处置中,那笔账得先还清。
- 列表与详情的包周期概要(`InstanceOut.subscription`:period / period_count / expires_at / status / auto_renew / amount_paid)与服务端点 slug 一样,由 `attach_instance_details` **各一次批量查询**回填,不逐行打接口(见 [web.md](./web.md))。管理端走同一条回填路径(`admin_list_instances` 与强制停止的响应都过 `attach_instance_details`),两端到期日口径一致。

### 竞价抢占

选择器与回收在 `app/modules/orchestrator/preempt.py`,触发点是创建软准入 `_soft_admit_capacity`,用例在 `apps/api/tests/test_spot.py`。钱的口径见 [billing.md](./billing.md)。

三条硬规矩逐字写在竞价知情同意 modal 里给用户看(见 [../ui-ux-spec.md](../ui-ux-spec.md) §1 规则 5),**改代码等于改文案,两边同提交**:

1. **只在同池同型号内选。** 候选谓词 `status='running' AND market='spot' AND spec->>'pool_label' = 池 AND spec->>'gpu_model_selector' IS NOT DISTINCT FROM canonical 型号`。用 `IS NOT DISTINCT FROM` 而非 `=`:未识别型号的快照为 NULL。
2. **按 `created_at` 从新到旧,最晚创建的先回收。** 这是**唯一**的排序规则(同刻用 `id` 降序破平),不按用量、单价或用户等级排。
3. **凑不够一台都不动。** 按 `gpu_count` 累加到够为止;不够就返回空列表,请求方照旧拿 409 `NO_CAPACITY`。

- 只有**非竞价的 GPU 档请求**会触发抢占:竞价请求不抢别人;CPU 档的容量口径是 vCPU / 内存而非卡数。
- 缺口换算 `cards_needed = ⌈缺的槽位 ÷ sellable_per_gpu⌉`(向上取整)。「一台实例腾出 `gpu_count` 张卡」与软准入的 `_sku_free_capacity` 同一套近似口径;偏乐观的后果由 creating 超时转 failed、全额不出账兜底。
- **抢占与请求方的建实例在同一个事务里**,`preempt()` 因此不 commit。请求方后续任何一步失败(余额不足、幂等撞车、配额超限)都会把回收一起回滚。
- 宽限窗:对每台选中的实例同事务内做三件事 —— `transition(→ stopping, reason='preempted', actor='system')` + `enqueue('instance.stop', delay_seconds=spot_grace_seconds)` + 短信与站内信通知。**状态机立刻迁 `stopping`**(用户当即看到「关机中」),**Pod 到期才删**:宽限窗内 Pod 还在、SSH 还能登、进度还能存。终态是 `stopped` 而非 `frozen`(它没欠费),实例盘保留,有容量时用户可自行开机。
- 通知的 dedup_key 带实例 id 与分钟位、**不按天分桶**(同一台实例一天内可能被回收多次)。
- 宽限窗与 `creating_timeout_seconds` 共用一段时间预算,`spot_grace_seconds` 的真实上限由跨键校验兜住,见 [limits.md](./limits.md)。
- 每回收一台计一次 `superdl_spot_preempted_total`(见 [observability.md](./observability.md))。
- 管理端 `/preempt` **不复用强制停止**:强制停止是处置(违规 / 风控),回收是履行竞价约定,两者 reason 分开。
- 转按量**不动 Pod、不重调度、零中断**:单价还原成 `spec.base_price_hourly`(建实例时落的 SKU 原价快照),**不从折后价反推**(折扣是在线可调策略);当前整点小时整体改按按量价结算(一小时一价,口径见 [billing.md](./billing.md),必须写进转换确认弹窗);转完再过一次 `assert_can_afford`。

### 实例形态

实例有两种形态(`instances.workload_type`),差别只在 `build_pod_spec` 的分叉与建哪些 K8s 对象;状态机、计费、配额、回收、reconciler、监控、审计全部共用。`service` 形态的实例是某个在线服务的一个版本(`service_id` 反指),暴露规格(`service_slug` / `service_port` / `health_path`)快照在实例行上,建 Pod 只读它们:

| | `dev`(SSH + JupyterLab) | `service`(在线服务的版本) |
|---|---|---|
| `restartPolicy` | `Never`(容器退出即故障) | `Always`(kubelet 原地重启容器,Pod 不重建:重建会换名字,而全套 reconciler 都建立在「Pod 名 = 实例 uuid」上) |
| command / args | 不设,用镜像 ENTRYPOINT | 用户可覆盖(`container_command` / `container_args`) |
| 用户 env | 无 | `env_encrypted`(整包 AES-GCM,AAD 绑实例 uuid);密文项经 per-instance Secret 以 `secretKeyRef` 引用,明文不落 Pod spec |
| SSH NodePort Service | 恒建 | `with_ssh` 才建;为假时**不进端口池**(端口池 30000–32767 是全平台硬上限) |
| Jupyter Service + HTTPRoute | 恒建 | 不建 |
| 服务 Service + HTTPRoute | 无 | `<uuid>-svc` ClusterIP + 挂 `svc-https` listener 的 HTTPRoute |
| 探针 | 无(无探针时 ready ≡ 容器已启动) | `health_path` 非空时 startupProbe(失败阈值 90 × 10s = 15 分钟启动预算)+ readinessProbe |

在线服务的域名规则、鉴权链路、状态派生与 API Key 生命周期见 [services.md](./services.md)。

### 网关与接入

- **服务路由挂错 listener 是本形态最危险的单点**:`app-https` 上没有 `SecurityPolicy.extAuth`,把服务路由挂过去照样通、返回 200,只是**完全不鉴权**,且没有任何报错。两个 listener 名在 `core/k8s/base.py` 的 `GATEWAY_APP_LISTENER` / `GATEWAY_SVC_LISTENER` 钉死,离线用例 `tests/test_k8s_real_units.py::TestServiceWorkloadObjects` 逐条断言。
- **租户 HTTPRoute 的挂载契约**由 `core/k8s/base.py` 的三个常量钉死(`GATEWAY_NAMESPACE` / `GATEWAY_NAME` / `GATEWAY_APP_LISTENER`,须与 `deploy/app/k8s/04-gateway.yaml` 逐字一致;没有对应的 `SUPERDL_*` 配置项):路由建在**租户 ns**,`parentRefs` 指平台 ns 的 `Gateway superdl`,`sectionName` 钉死 `app-https`。跨 ns 挂载由该 listener 的 `allowedRoutes.namespaces.from: Selector` 授权,选择器就是 `ensure_namespace` 已经打在每个租户 ns 上的 `superdl.io/managed=true`(平台自身 ns 不带此标);**不需要 ReferenceGrant**(它只管 backendRef 跨 ns,而 backend 与路由同 ns)。三个名字任一写错都不报错:`create` 照样返 201,路由停在 `status.parents[].conditions` 的 `Accepted=False` / `NotAllowedByListeners`,用户侧只看到域名 404。不写 `sectionName` 则路由会挂到全部同端口 listener 上,把平台三个域一起拉进同一份路由表。
- **Jupyter 的 WebSocket 与 SSE 靠网关的 `streamIdleTimeout: 1h` 撑着**(`ClientTrafficPolicy superdl-gateway`)。Envoy Gateway 该项默认 5 分钟,不显式配就会把内核连接与终端按时掐断。调整网关策略时别把它落回默认。
- **路由规模是容量变量,不是常量**:一实例一条 HTTPRoute,全量经 xDS 下发进每个 Envoy,数据面与 EG 控制面的内存随路由数涨(5000 条量级时部分数据面到 1–2 GB)。light 档单机要么给足 `EnvoyProxy` 的 memory limit,要么对单机实例数设硬上限 —— OOMKill 掉的是全站入口。取值必须实机压过再定,见 `deploy/app/k8s/04-gateway.yaml` 注释。
- 孤儿端点清理按 `superdl.io/managed` 标签做全集群 LIST(Service 与 HTTPRoute 两类),HTTPRoute 无 typed model,增删查一律走 `CustomObjectsApi` 收发裸 dict,分页游标在 `metadata.continue`(不是 typed model 的 `_continue`)。
- SSH 仅密钥登录(公钥注入 authorized_keys),禁用密码;连接串形如 `ssh root@<实例域名> -p 3xxxx`:不设单独的 SSH 入口域名,主机名就是实例自己的域名(与 Jupyter 同名,`orchestrator/service.jupyter_host`),实例只靠 NodePort 区分。部署约束:泛域名解析到的地址必须同时转发 80/443 与 `ssh_port_range` 端口段(单节点即节点本身,多节点为转发该端口段的 LB/VIP)。
- SSH 依赖租户容器的三个 capability(`SYS_CHROOT` / `SETUID` / `SETGID`,见 [security.md](./security.md))与 entrypoint 起 sshd 前对 `/root` 的 `chmod g-w,o-w`(TopoLVM 把挂载点留成 2777,sshd StrictModes 会拒认证);缺任一条 SSH 都不可用。
- SSH host key 持久化在实例盘(`/root/.ssh/host_keys`),Pod 重建指纹不变;jupyter 由 entrypoint 守护循环拉起(不做 PID 1),连续秒退 5 次才让 Pod 失败收敛。
- JupyterLab 走每实例一条 HTTPRoute 按 host 路由到该实例的 Jupyter Service,主机名 = `SUPERDL_JUPYTER_HOST_PREFIX`(默认空)+ uuid + `.` + `SUPERDL_JUPYTER_DOMAIN_SUFFIX`,由 `orchestrator/service.jupyter_host` 单点拼接。与其它业务共用一级域时用前缀区分(如 `jupyter-<uuid>.<域>`),此时 Jupyter 与服务端点只能按端口分开,`SUPERDL_JUPYTER_URL_PORT`(默认 443)把端口带进入场 URL 与 `JUPYTER_ALLOW_ORIGIN`,**只影响 origin,不影响 HTTPRoute hostname 与 SSH 连接串**;两种分法见 [services.md](./services.md)「域名规则」。TLS 不在路由上出现,证书由 listener 的 `certificateRefs` 提供。
- Jupyter token 由控制面生成、AES-GCM 密文落库、注入 Pod env;access 端点签发一次性 bootstrap 票据(HMAC 密钥 = token 本体,单次、60s),镜像内 `/superdl-bootstrap` handler 核销后种第一方 cookie,token 不出现在 URL;`?token=` stock 登录为回落通道。**实例镜像须先于控制面发布**:镜像内的 bootstrap handler 是票据流的前提。
