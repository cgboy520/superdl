# 节点与集群

一键加节点、节点规格台账、集群能力探测。模块 `app/modules/nodes/`。

## 数据模型

- `node_enrollments`:token_hash(sha256 唯一)、pool(kata/hami/mig)、hostname?、note?、nvme_devices JSONB?、status、phase、error、node_name、reported_ip、os_info JSONB、gpu_info JSONB、expires_at(默认 24h,1~168h 可调)、last_report_at、joined_at、created_by、idempotency_key(与 created_by 联合唯一)
- `node_specs`:node_name 唯一、pool_label?、unlabeled、gpu_model_raw?、gpu_model?(canonical)、label_synced、gpu_count、gpu_used、vram_gb、vcpu、mem_gb、disk_gb、driver_version?、cuda_version?、status(Ready/NotReady/Cordoned/Missing)、last_seen
- `cluster_status`:单行 id=1,api_reachable、k8s_version?、distro?(rke2/k3s)、hami_ready、dcgm_present、kps_present、gpu_operator_present、kata_runtimeclass、storage_classes JSONB?、pools JSONB?、detail JSONB?、error?、probed_at

装机状态机:pending → installing → rebooting ⇆ installing → joining → joined,旁路终态 failed / expired / revoked。

## 契约

| 端点 | 角色/鉴权 | 说明 |
|---|---|---|
| `GET /api/v1/node-enroll/script` | 匿名+限流 | 静态脚本,仅替换 `__API_BASE__`,内容零密钥 |
| `POST /api/v1/node-enroll/bootstrap` | Bearer token | 上报 hostname/os/`gpu_details:[{name, memory_mib}]` → 回 pool/发行版/agent 版本/server_url/join_token/驱动版本/nvme/registries_yaml;仅 pending/installing/rebooting 放行,其余统一 404;按 IP 限流 |
| `POST /api/v1/node-enroll/progress` | Bearer token | `{phase, state: running\|ok\|failed\|rebooting, message?}` 推进 phase/status/error/心跳 |
| `GET /api/admin/v1/node-enrollments` | ops/readonly | `?active=true` 排除 revoked、超 24h 的 joined、超 7d 的 expired |
| `POST /api/admin/v1/node-enrollments` | ops | 响应含 token 明文与完整命令,仅此一次;Idempotency-Key 重放轮换该行 token 而不建新行;cluster 组未配 server_url/join_token → 409 |
| `POST .../{enrollment_id}/regenerate` | ops | 仅 pending/expired/failed:换新 token 与有效期,状态回 pending |
| `POST .../{enrollment_id}/revoke` | ops | reason 必填,非终态 → revoked |
| `GET /api/admin/v1/nodes` | ops/readonly | 数据源为台账;含 `gpu_model_raw / unlabeled / label_synced / last_seen / vram_gb`,含未打标与 Missing |
| `POST /api/admin/v1/nodes/{node_name}/cordon\|uncordon` | ops | reason 必填,只 enqueue `node.cordon`,请求路径不动 K8s |
| `GET /api/admin/v1/cluster/gpu-models` | ops/readonly | 台账聚合 `[{gpu_model, gpu_model_raw, pool_label, node_count, gpu_total, ready_gpu_total, vram_gb}]`,canonical×pool 分组,未识别入 `unrecognized` 桶 |
| `GET /api/admin/v1/cluster/status` | ops/readonly | 纯 DB:`{api_reachable, distro, k8s_version, probed_at, components:[{key,label,ok,detail,fix_hint}], pools, config:{...}}`,fix_hint 为可复制修复命令 |
| `POST /api/admin/v1/cluster/test-connection` | ops | 同步只读探测,upsert `cluster_status` 后原样返回;超时 5s → 502 |

## 规则与不变量

- 令牌只走 `sdln_` 前缀 256-bit 随机串,只存 sha256;无效/过期/吊销/终态一律返 404,不区分原因以防探测。
- token 只经命令行参数传入,不进 URL(否则落 access log 与代理日志);集群 server URL 与 join token 不进脚本,由脚本凭 token `POST /bootstrap` 换取。
- bootstrap 上报的 hostname 与预填不符即置 failed,防令牌串用。
- 对账器(30s,advisory lock 1009)判定 joined 的唯一依据是 K8s 中该 node_name 出现且 Ready 且池标签匹配;池标签不符 → failed;pending 过 expires_at → expired;2h 无心跳 → failed。
- 节点巡检(60s,advisory lock 1010)是节点事实源:阶段 A 纯 K8s 读 → 阶段 B 单事务 DB 收敛 → 阶段 C 逐节点 label patch(失败下轮自愈)。`enrollment.gpu_info` 只是装机一次性快照,不得当事实源。
- 业务读台账,不实时调 K8s;节点消失先置 `Missing`,超 7 天才删行;上架校验只认 Ready。
- 巡检在 worker 收敛环直连 K8s 并以幂等重试保证收敛,outbox 只管请求路径的业务事务。
- 型号归一化在 `core/gpu_models.py`:`canonical_gpu_model(raw)` 未识别返回 None,同名多容量家族(A100/A800/H100/H800/H200/V100)追加 `-{n}G`;`model_matches(sku, node)` 为相等或节点值前缀匹配(SKU `A100` 匹配台账 `A100-80G`)。
- `gpu_model` 必须参与调度,靠平台自有 label `superdl.io/gpu-model` 回写节点(不依赖 GFD、发行版无关)。
- HAMi 门禁不做调度回落:shared 档能力未就绪直接报 `CLUSTER_NOT_READY`,schedulerName 静态钉死(回落 default-scheduler 后 `nvidia.com/gpucores` 照样 Pending)。
- 发行版不设运行期配置,由平台探测 gitVersion(含 `+k3s`/`+rke2`)派生;k3s 为受支持的轻量档,仅限 hami 池 SKU,dedicated/mig 需 full 集群。
- k3s 只探测 nvidia 运行时、不设默认运行时,故 k3s 上 shared 档租户 Pod 必须显式 `runtimeClassName: nvidia`;RKE2 + gpu-operator 默认运行时已是 nvidia,保持 None。
- cluster 配置组键面:`cluster_server_url / cluster_join_token(secret)/ cluster_agent_version / node_driver_version / node_registries_yaml(留空=平台生成)/ node_install_mirror(""|cn,默认 cn)`,无 k8s_distro 键。
- `render_registries_yaml` 由 server_url 解析 host + registry NodePort 30500 常量 + 模板生成。
- cluster 键不做启动 fail-fast(推荐配置路径是 DB 覆盖层,启动只查 env 会误报);改由 lifespan 在 DB 就绪后查生效配置打 error + 集群页红牌 + 创建注册命令 409。
- `node-join.sh` 随 API 镜像下发,步骤 marker 可无限重跑;需重启的场景(kata 池 IOMMU 等)用 systemd oneshot 断点续跑。phase 名发行版中性:bootstrap/precheck/nouveau/sysctl/iommu/driver/nvidia_toolkit/nvme_vg/reboot/registries/agent_config/agent_install/agent_start/waiting_node。
- 一节点一令牌,不做批量可重用令牌;不做 drain(牵扯计费与迁移策略)。
- join token 最终必然落节点 agent config 文件(0600 root),轮换走发行版自带的 token rotate。
