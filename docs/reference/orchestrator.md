# 编排

实例状态机、outbox 编排、reconciler 对账、接入(SSH / JupyterLab)与 K8s 抽象。

## 数据模型

- `instances`:uuid、user_id、SKU 快照(sku_id + spec_snapshot jsonb + price_hourly)、gpu_count、status、k8s(namespace/node_name(253))、ssh_port?、jupyter_token(AES-GCM 密文)、image_ref、data_disk_id?、idempotency_key 唯一?(24h 窗口,窗外同键按新单)、version(乐观锁)
- `instance_events`:instance_id、from_status、to_status、reason、actor(user/system/admin)、metadata —— 追加式,计费主依据
- `port_allocations`:port 唯一(30000~32767)、instance_id nullable(部分唯一:一台实例至多一个端口)

状态机:creating→running/failed;running→stopping;stopping→stopped/releasing;stopped→starting/frozen/releasing;
starting→running/failed;frozen→stopped/releasing;failed→stopped/releasing;releasing→released。
running↔非 running 的边即计费边。failed→stopped 是故障恢复边(复用同一块实例盘重开机,
start 端点对 failed 放行);stopping→releasing 是悬挂放弃边(关机删不掉时允许直接释放)。

## 契约

| 端点 | 角色/鉴权 | 说明 |
|---|---|---|
| `POST /api/v1/instances` | user | Idempotency-Key;软准入(台账无货 409)→ 钱包行锁临界区(在途+新增余额校验、配额)→ 事务写 instances(creating)+event+outbox → 202 |
| `GET /api/v1/instances` `GET /api/v1/instances/{uuid}` | user | 列表(不分页)与详情 |
| `PATCH /api/v1/instances/{uuid}` | user | 改名等 |
| `POST /api/v1/instances/{uuid}/stop\|start\|restart` | user | 同构,均经 outbox;start 对 failed 放行(恢复边) |
| `DELETE /api/v1/instances/{uuid}` | user | 释放(stopped/frozen/failed/creating/stopping);幂等:releasing/released 重放回当前状态而非 400 |
| `GET /api/v1/instances/{uuid}/events` | user | 事件时间线,即计费依据;降序(最新在前)游标分页 `?cursor=&limit=` |
| `GET /api/v1/instances/{uuid}/access` | user | SSH 指令 + Jupyter 一次性 bootstrap 票据 URL(单次、60s;核销后种第一方 cookie,token 不进 URL);非 running 报错并说明 |
| `GET /api/v1/instances/{uuid}/logs` | user | 容器日志:**只读**;**owner 校验**(非属主 404 不暴露存在性);**限流 20/h/user**;**K8s 读 5s 超时**;仅 running/stopping(其余 409,已关机无 Pod 日志);`?tail_lines=` 默认 200、超 2000 截断,`?since_seconds=` 超 86400 截断;返回 `{lines, truncated}`;不记审计 |
| `POST /api/v1/instances/{uuid}/reset-jupyter-token` | user | 轮换 token(密文落库),旧票据与旧 URL 立即失效 |

## 规则与不变量

- K8s 访问收敛在 `app/core/k8s`:`K8sOrchestrator` 协议:ensure_namespace / create_instance / delete_instance / delete_instance_disk / get_status / read_instance_logs / list_instance_pods / wipe_disk / list_nodes / set_node_labels / prewarm_image / get_prewarm_status / delete_prewarm_job / probe_cluster / set_node_unschedulable。
- FakeOrchestrator(dev/test,内存态,可注入故障)与 RealOrchestrator(kubernetes 官方客户端)必须同步实现协议全部方法。
- gpu_adapter 按 tier 产出资源请求语法(HAMi `nvidia.com/gpu` + `gpucores`/`gpumem`;MIG profile;整卡)、按池选 RuntimeClass(kata-qemu / runc)、按 canonical 型号产出 `superdl.io/gpu-model` nodeSelector,以及 `annotations` 透传口(`nvidia.com/use-gputype`,开关 `SUPERDL_HAMI_USE_GPUTYPE` 默认关,仅混卡节点池需要)。
- RealOrchestrator 每租户:独立 namespace(PSA enforce=baseline + audit=restricted 标签)、ResourceQuota 兜底(对象数 + cpu/memory/ephemeral-storage 总量)、Egress 隔离 NetworkPolicy(私网黑名单 + 滥用端口黑名单,DNS 收敛到 CoreDNS Pod)、JuiceFS PVC;`disk.wipe` 为真实擦除 Job(幂等 + 退避)。ns/NetPol/Quota 已存在时 patch 收敛,加固覆盖存量租户;K8s list 调用一律分页(limit=500 + continue),同步调用走专属有界执行器。
- 状态迁移只能经 `orchestrator/service.py` 的 transition 函数(同事务写 `instance_events`),禁止直接 UPDATE status;非法迁移报 `INSTANCE_INVALID_TRANSITION`。
- 请求路径不许调 K8s:业务写入与 `outbox_tasks` 插入同一事务,K8s 动作一律由 worker 执行。唯一例外是日志端点的只读直读(实时性要求;owner/限流/超时三道闸兜住,见契约表)。
- reconciler(30s,advisory lock)是唯一收敛点:Pod Ready 而 DB creating/starting → running(开始计费);Pod 消失而 DB running → failed(停费并告警);Pod 存在而 DB 终态 → 强删(force);creating 超 5min → failed 并退款。每轮一次 `list_instance_pods` 即状态源(存在性/ready/phase/node_name/deleting,与 `get_status` 同形),不逐实例 `get_status`。
- stopping/releasing 悬挂两档超时(默认各 10min,`stopping_timeout_seconds`/`releasing_timeout_seconds`):一档经 outbox 重发删除任务,二档 force 强删后按正常边收敛(stopped 保留端口与实例盘;released 回收端口并销毁实例盘)。悬挂实例数见指标 `superdl_reconcile_stuck_instances`。
- 泄漏回收熔断:未知(DB 无记录)Pod 占比超 `leak_reclaim_abort_ratio`(默认 0.5)即中止本轮并计 `superdl_reconcile_leak_aborted_total`;在途删除(stopping/releasing)宽限同两档超时,其余一律 force 强删。
- 保留期 GC(reconciler 内):failed 超 `failed_retention_days`(默认 7 天)→ 通知并转 releasing;stopped 超 `stopped_retention_days`(默认 30 天)→ 转 releasing,提前 `stopped_retention_warn_days`(默认 7 天)预警。数据盘不受影响。
- 节点失联判定先看节点 Ready 状况(`list_nodes`):持续 not-ready 超 `running_unready_timeout_seconds`(默认 600s,须宽于 unreachable toleration 的 300s)且节点 NotReady/未知 → node_lost(通知用户);节点正常 → pod_unready(Pod 自身问题,不告警失联)。
- 端口从 `port_allocations` 池分配,释放必须回池;池耗尽时创建失败并给出明确错误。
- SSH 仅密钥登录(公钥注入 authorized_keys),禁用密码;连接串形如 `ssh root@ssh1.<域名> -p 3xxxx`。
- SSH host key 持久化在实例盘(`/root/.ssh/host_keys`),Pod 重建指纹不变;jupyter 由 entrypoint 守护循环拉起(不做 PID 1),连续秒退 5 次才让 Pod 失败收敛。
- JupyterLab 走 `<instance-uuid>.app.<域名>` 泛域名 Ingress 按 host 路由到实例 Service(IngressClass 由 `SUPERDL_INGRESS_CLASS_NAME` 指定,默认 `nginx`;未标 default 的 IngressClass 不会自动接管)。token 由控制面生成、AES-GCM 密文落库、注入 Pod env;access 端点签发一次性 bootstrap 票据(HMAC 密钥=token 本体,单次、60s),镜像内 `/superdl-bootstrap` handler 核销后种第一方 cookie,token 不出现在 URL;`?token=` stock 登录为回落通道。实例镜像须先于控制面发布:镜像内的 bootstrap handler 是票据流的前提。
- 镜像拉取凭据不落节点、不进 Pod spec 明文:outbox 建 Pod 前在 `ensure_namespace` 之后调 `core/registry.ensure_registry_pull_secret`,按生效 `registry_*` 把 `superdl-registry-pull` 托管到租户 ns(annotation 指纹相同跳过),Pod spec 以 `imagePullSecrets` 引用;未配机器人则 `image_pull_secret=None`。预热 Job 同一条链(平台 ns)。
- 型号 nodeSelector 由 spec 快照的 `gpu_model_selector` 键决定(未识别型号存 None,不钉型号)。
- shared 档 create/start/restart 三入口读集群能力缓存做 HAMi 门禁,未就绪直接报 `CLUSTER_NOT_READY`,见 [nodes.md](./nodes.md)。
- 每用户实例数与 GPU 数配额由 config 控制。
- 实例释放后触发擦盘任务;数据盘生命周期独立,见 [disks.md](./disks.md)。
