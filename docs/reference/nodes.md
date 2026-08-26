# 节点与集群

一键加节点、节点规格台账、集群能力探测。模块 `app/modules/nodes/`。

## 数据模型

- `node_enrollments`:token_hash(sha256 唯一)、progress_token_hash?(sha256 唯一,首次 bootstrap 换发;NULL=尚未 bootstrap 或注册令牌已轮换)、pool(kata/hami/mig)、hostname?、note?、nvme_devices JSONB?、status、phase、error、node_name、reported_ip、os_info JSONB、gpu_info JSONB、expires_at(默认 24h,1~168h 可调,绝对截止)、last_report_at、joined_at、created_by、idempotency_key(与 created_by 联合唯一)
- `node_specs`:node_name 唯一、pool_label?、unlabeled、gpu_model_raw?、gpu_model?(canonical)、label_synced、gpu_count、gpu_used、vram_gb、vcpu、mem_gb、disk_gb、driver_version?、cuda_version?、status(Ready/NotReady/Cordoned/Missing)、last_seen
- `cluster_status`:单行 id=1,api_reachable、k8s_version?、distro?(rke2/k3s)、hami_ready、dcgm_present、kps_present、gpu_operator_present、kata_runtimeclass、storage_classes JSONB?、pools JSONB?、error?、probed_at

装机状态机:pending → installing → rebooting ⇆ installing → joining → joined,旁路终态 failed / expired / revoked(非终态均可因绝对过期落 expired)。

## 契约

| 端点 | 角色/鉴权 | 说明 |
|---|---|---|
| `GET /api/v1/node-enroll/script` | 匿名+限流 | 静态脚本,仅替换 `__API_BASE__`,内容零密钥 |
| `POST /api/v1/node-enroll/bootstrap` | Bearer 注册令牌(一次性) | 上报 hostname/os/`gpu_details:[{name, memory_mib?}]`(nvidia-smi 不可用时为 lspci 名称,无显存) → 回 pool/发行版/agent 版本/server_url/join_token/驱动版本/nvme/registries_yaml + `progress_token`(首跑换发) + `script_sha256`(重拉脚本指纹);首跑即消费注册令牌,之后任何令牌 bootstrap 均 404;按 IP 限流 |
| `POST /api/v1/node-enroll/progress` | Bearer progress 令牌 | `{phase, state: running\|ok\|failed\|rebooting, message?, driver_version?, cuda_version?}` 推进 phase/status/error/心跳;版本字段并进登记快照 `os_info`,巡检据此填台账 driver/cuda 列(脚本在驱动已加载的收尾上报 `waiting_node` 附带:首装要经重启,bootstrap 时采不到);注册令牌不能上报;响应 204 无体(脚本不读响应) |
| `GET /api/admin/v1/node-enrollments` | ops/readonly | `?active=true` 排除 revoked、joined(已进正式节点列表)、超 7d 的 expired |
| `POST /api/admin/v1/node-enrollments` | ops | 响应含 token 明文与完整命令,仅此一次;Idempotency-Key 重放轮换该行 token 而不建新行(仅 pending/expired/failed,进行中 409,同 regenerate 守卫);cluster 组未配 server_url/join_token → 409 |
| `POST .../{enrollment_id}/regenerate` | ops | 仅 pending/expired/failed:换新 token 与有效期,状态回 pending |
| `POST .../{enrollment_id}/revoke` | ops | reason 必填,非终态 → revoked |
| `GET /api/admin/v1/nodes` | ops/readonly | 数据源为台账;含 `gpu_model_raw / unlabeled / label_synced / last_seen / vram_gb`,含未打标与 Missing |
| `POST /api/admin/v1/nodes/{node_name}/cordon\|uncordon` | ops | reason 必填,只 enqueue `node.cordon`,请求路径不动 K8s |
| `GET /api/admin/v1/cluster/gpu-models` | ops/readonly | 台账聚合 `[{gpu_model, gpu_model_raw, pool_label, node_count, gpu_total, ready_gpu_total, vram_gb}]`,canonical×pool 分组,未识别入 `unrecognized` 桶 |
| `GET /api/admin/v1/cluster/status` | ops/readonly | 纯 DB:`{api_reachable, distro, k8s_version, probed_at, components:[{key,label,ok,detail,fix_hint}], pools, config:{server_url_set, join_token_set, prometheus_url_set, grafana_url, registry_host, registry_project}}`(registry 两项非密,供镜像页新建表单默认前缀),fix_hint 为可复制修复命令 |
| `POST /api/admin/v1/cluster/test-connection` | ops | 同步只读探测,upsert `cluster_status` 后原样返回;超时 5s → 502 |

## 规则与不变量

- 令牌只走 `sdln_` 前缀 256-bit 随机串,只存 sha256;无效/过期/吊销/终态一律返 404,不区分原因。progress 令牌 `sdlp_` 前缀同规格。
- token 不进 URL、不进命令行参数:生成命令经 stdin 把 token 写入 `/run/superdl-join.token`(0600),脚本只认 `--token-file`;curl 一律 `--config` 注入 Authorization 头。集群 server URL 与 join token 不进脚本,由脚本凭 token `POST /bootstrap` 换取。
- 注册令牌一次性:首次 bootstrap(pending→installing)即消费并换发窄权限 progress 令牌(仅可 /progress,换发/吊销/轮换注册令牌时同步作废);重复 bootstrap 一律 404;脚本重跑/重启续跑只用盘上的 progress 令牌上报。
- 令牌绝对过期:expires_at 对一切非终态生效(progress 只刷新 last_report_at,不延长截止),过期即 404,请求路径不迁移状态;落 expired 由对账器(30s)清扫。
- bootstrap 下发配置收窄到 cluster 组 6 键(server_url/join_token/agent_version/driver_version/install_mirror/registries_yaml),全量生效配置(含解密后的支付私钥等)不出注册链路。
- 签发令牌时必填期望主机名,bootstrap 上报主机名不符即置 failed 并 409(被盗令牌不能在别的机器换出 join token)。
- 对账器(30s,advisory lock 1009)判定 joined 的唯一依据是 K8s 中该 node_name 出现且 Ready 且池标签匹配;池标签不符 → failed;2h 无心跳 → failed。读取走 `FOR UPDATE SKIP LOCKED`,与请求路径并发吊销/上报不互相覆盖。
- 节点巡检(60s,advisory lock 1010)是节点事实源:阶段 A 纯 K8s 读 → 阶段 B 单事务 DB 收敛 → 阶段 C 逐节点 label patch(失败下轮自愈)。`enrollment.gpu_info` 只是装机一次性快照,不得当事实源。
- 业务读台账,不实时调 K8s;节点消失先置 `Missing`,超 7 天才删行;上架校验只认 Ready。
- 巡检在 worker 收敛环直连 K8s 并以幂等重试保证收敛,outbox 只管请求路径的业务事务。
- 型号归一化在 `core/gpu_models.py`:`canonical_gpu_model(raw)` 未识别返回 None,同名多容量家族(A100/A800/H100/H800/H200/V100)追加 `-{n}G`;`model_matches(sku, node)` 为相等或节点值前缀匹配(SKU `A100` 匹配台账 `A100-80G`)。
- `gpu_model` 必须参与调度,靠平台自有 label `superdl.io/gpu-model` 回写节点(不依赖 GFD、发行版无关)。
- HAMi 门禁不做调度回落:shared 档能力未就绪直接报 `CLUSTER_NOT_READY`,schedulerName 静态钉死。
- 发行版不设运行期配置,由平台探测 gitVersion(含 `+k3s`/`+rke2`)派生;k3s 为受支持的轻量档,仅限 hami 池 SKU,dedicated/mig 需 full 集群。
- k3s 只探测 nvidia 运行时、不设默认运行时,shared 档租户 Pod 必须显式 `runtimeClassName: nvidia`;RKE2 + gpu-operator 默认运行时已是 nvidia,保持 None。
- cluster 配置组键面:`cluster_server_url / cluster_join_token(secret)/ cluster_agent_version / node_driver_version / node_registries_yaml(留空=平台生成)/ node_install_mirror(""|cn,默认 cn)`,无 k8s_distro 键。
- `render_registries_yaml`(`node_registries_yaml` 留空时的平台默认)由 server_url 解析 host + NodePort 30500 常量生成,指向的是过渡期集群内 registry;已迁托管仓的集群必须填 `node_registries_yaml`(以 `deploy/cluster/rke2/registries.yaml` 为模板)覆盖,否则新节点拉不到平台镜像。
- cluster 键不做启动 fail-fast(配置路径是 DB 覆盖层):改由 lifespan 在 DB 就绪后查生效配置打 error + 集群页红牌 + 创建注册命令 409。
- `node-join.sh` 随 API 镜像下发,步骤 marker 可无限重跑;需重启的场景(kata 池 IOMMU 等)用 systemd oneshot 断点续跑。phase 名发行版中性:bootstrap/precheck/nouveau/sysctl/iommu/driver/nvidia_toolkit/nvme_vg/reboot/registries/agent_config/agent_install/agent_start/waiting_node。
- 一节点一令牌,不做批量可重用令牌;不做 drain。
- join token 最终必然落节点 agent config 文件(0600 root),轮换走发行版自带的 token rotate。
