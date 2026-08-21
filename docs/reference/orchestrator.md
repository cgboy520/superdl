# 编排

实例状态机、outbox 编排、reconciler 对账、接入(SSH / JupyterLab)与 K8s 抽象。

## 数据模型

- `instances`:uuid、user_id、SKU 快照(sku_id + spec_snapshot jsonb + price_hourly)、gpu_count、status、k8s(namespace/pod_name/node_name)、ssh_port?、jupyter_token、image_ref、data_disk_id?、idempotency_key 唯一?、version(乐观锁)
- `instance_events`:instance_id、from_status、to_status、reason、actor(user/system/admin)、metadata —— 追加式,计费主依据
- `port_allocations`:port 唯一(30000~32767)、instance_id nullable

状态机:creating→running/failed;running→stopping;stopping→stopped;stopped→starting/frozen/releasing;
starting→running/failed;frozen→stopped/releasing;releasing→released。running↔非 running 的边即计费边。

## 契约

| 端点 | 角色/鉴权 | 说明 |
|---|---|---|
| `POST /api/v1/instances` | user | Idempotency-Key;校验余额 → 事务写 instances(creating)+event+outbox → 202 |
| `GET /api/v1/instances` `GET /api/v1/instances/{uuid}` | user | 列表(不分页)与详情 |
| `PATCH /api/v1/instances/{uuid}` | user | 改名等 |
| `POST /api/v1/instances/{uuid}/stop\|start\|restart` | user | 同构,均经 outbox |
| `DELETE /api/v1/instances/{uuid}` | user | 释放 |
| `GET /api/v1/instances/{uuid}/events` | user | 事件时间线,即计费依据 |
| `GET /api/v1/instances/{uuid}/access` | user | SSH 指令 + Jupyter URL(含 token);非 running 报错并说明 |
| `POST /api/v1/instances/{uuid}/reset-jupyter-token` | user | 重置后旧 URL 立即失效 |

## 规则与不变量

- K8s 访问收敛在 `app/core/k8s`:`K8sOrchestrator` 协议:ensure_namespace / create_instance / delete_instance / delete_instance_disk / get_status / list_instance_pods / wipe_disk / available_gpus / list_nodes / set_node_labels / prewarm_image / get_prewarm_status / delete_prewarm_job / probe_cluster / set_node_unschedulable。
- FakeOrchestrator(dev/test,内存态,可注入故障)与 RealOrchestrator(kubernetes 官方客户端)必须同步实现协议全部方法。
- gpu_adapter 按 tier 产出资源请求语法(HAMi `nvidia.com/gpu` + `gpucores`/`gpumem`;MIG profile;整卡)、按池选 RuntimeClass(kata-qemu / runc)、按 canonical 型号产出 `superdl.io/gpu-model` nodeSelector,以及 `annotations` 透传口(`nvidia.com/use-gputype` 开关默认关)。
- RealOrchestrator 每租户:独立 namespace、ResourceQuota 兜底、Egress 隔离 NetworkPolicy、JuiceFS PVC;`disk.wipe` 为真实擦除 Job(幂等 + 退避)。
- 状态迁移只能经 `orchestrator/service.py` 的 transition 函数(同事务写 `instance_events`),禁止直接 UPDATE status;非法迁移报 `INSTANCE_INVALID_TRANSITION`。
- 请求路径不许调 K8s:业务写入与 `outbox_tasks` 插入同一事务,K8s 动作一律由 worker 执行。
- reconciler(30s,advisory lock)是唯一收敛点:Pod Ready 而 DB creating/starting → running(开始计费);Pod 消失而 DB running → failed(停费并告警);Pod 存在而 DB released → 强删;creating 超 5min → failed 并退款。
- 端口从 `port_allocations` 池分配,释放必须回池;池耗尽时创建失败并给出明确错误。
- SSH 仅密钥登录(公钥注入 authorized_keys),禁用密码;连接串形如 `ssh root@ssh1.<域名> -p 3xxxx`。
- JupyterLab 走 `<instance-uuid>.app.<域名>` 泛域名 Ingress 按 host 路由到实例 Service,token 由控制面生成并注入。
- 型号 nodeSelector 由 spec 快照的 `gpu_model_selector` 键决定:快照无此键的存量实例 start 时不带该 selector。
- shared 档 create/start/restart 三入口读集群能力缓存做 HAMi 门禁,未就绪直接报 `CLUSTER_NOT_READY`,见 [nodes.md](./nodes.md)。
- 每用户实例数与 GPU 数配额由 config 控制。
- 实例释放后触发擦盘任务;数据盘生命周期独立,见 [disks.md](./disks.md)。
