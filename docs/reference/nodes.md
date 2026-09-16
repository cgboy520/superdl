# 节点与集群

一键加节点、节点规格台账、集群能力探测。模块 `app/modules/nodes/`。

## 数据模型

- `node_enrollments`:token_hash(sha256 唯一)、progress_token_hash?(sha256 唯一,首次 bootstrap 换发)、pool(kata/hami/mig/cpu)、hostname?、note?、nvme_devices JSONB?、status、phase、error、node_name、reported_ip、os_info JSONB、gpu_info JSONB、expires_at(默认 24h,1~168h,绝对截止)、last_report_at、joined_at、created_by、idempotency_key(与 created_by 联合唯一)
- `node_specs`:node_name 唯一、pool_label?、unlabeled、gpu_model_raw?、gpu_model?(canonical)、label_synced、gpu_count、gpu_used、vram_gb、vcpu、mem_gb、disk_gb、driver_version?、cuda_version?、status(Ready/NotReady/Cordoned/Missing)、last_seen;两个期望态列由管理端写、handler 与巡检收敛:`desired_unschedulable`(cordon)、`desired_pool`(切池)
- `cluster_status`:单行 id=1,api_reachable、k8s_version?、distro?(rke2/k3s)、hami_ready、dcgm_present、kps_present、gpu_operator_present、kata_runtimeclass、nvidia_runtimeclass、gateway_ready、cert_manager_ready、nodes_ready、nodes_total、storage_classes JSONB?、pools JSONB?、pools_ready JSONB?(池→Ready 且可调度的节点数)、component_facts JSONB?(体检项 key → 状态 / 主数字 / 事实行 / 对象表)、error?、probed_at。布尔列是下发门禁的判据,`component_facts` 是集群页体检面板的数据源,两者互不替代

装机状态机:pending → installing → rebooting ⇆ installing → joining → joined,旁路终态 failed / expired / revoked(非终态均可因绝对过期落 expired)。终态 joined / failed / expired 另有一条通向 revoked 的边,**只有节点退役走得到**(手工 `revoke_enrollment` 对终态 409)。

## 契约

| 端点                                                    | 角色/鉴权               | 说明                                                                                                                                                                                                                                                                                                                                                                       |
| ------------------------------------------------------- | ----------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GET /api/v1/node-enroll/script`                        | 匿名+限流               | 静态脚本,仅替换 `__API_BASE__`,零密钥                                                                                                                                                                                                                                                                                                                                      |
| `POST /api/v1/node-enroll/bootstrap`                    | Bearer 注册令牌(一次性) | 上报 hostname/os/`gpu_details:[{name, memory_mib?}]` → 回 pool/发行版/agent 版本/server_url/join_token/驱动版本/nvme/registries_yaml/registry_ca_pem + `progress_token`(首跑换发) + `script_sha256`;首跑即消费注册令牌,之后 404;按 IP 限流                                                                                                                                 |
| `POST /api/v1/node-enroll/progress`                     | Bearer progress 令牌    | `{phase, state: running\|ok\|failed\|rebooting, message?, driver_version?, cuda_version?}` 推进 phase/status/error/心跳;版本字段并进 `os_info`;注册令牌不能上报;响应 204                                                                                                                                                                                                   |
| `GET /api/admin/v1/node-enrollments`                    | ops/readonly            | `?active=true` 排除 revoked、joined、超 7d 的 expired                                                                                                                                                                                                                                                                                                                      |
| `POST /api/admin/v1/node-enrollments`                   | ops                     | 响应含 token 明文与完整命令,仅此一次;Idempotency-Key 重放轮换该行 token(仅 pending/expired/failed,进行中 409);cluster 组未配 server_url/join_token → 409                                                                                                                                                                                                                   |
| `POST .../{enrollment_id}/regenerate`                   | ops                     | 仅 pending/expired/failed:换新 token 与有效期,状态回 pending                                                                                                                                                                                                                                                                                                               |
| `POST .../{enrollment_id}/revoke`                       | ops                     | reason 必填,非终态 → revoked                                                                                                                                                                                                                                                                                                                                               |
| `GET /api/admin/v1/nodes`                               | ops/readonly            | 台账;含 `gpu_model_raw / unlabeled / label_synced / last_seen / vram_gb`,含未打标与 Missing                                                                                                                                                                                                                                                                                |
| `POST /api/admin/v1/nodes/{node_name}/cordon\|uncordon` | ops                     | reason 必填,只 enqueue `node.cordon`                                                                                                                                                                                                                                                                                                                                       |
| `POST /api/admin/v1/nodes/{node_name}/switch-pool`      | ops                     | 切换节点池(kata/hami/mig 互切),reason 必填。前置:节点无未释放实例、机型与目标池匹配、目标池运行时就绪。同事务:停调度与期望池落台账 + enqueue `node.switch_pool`。**不需要任何节点侧动作**,不签发令牌;响应 `{node_name, from_pool, to_pool, queued}`                                                                                                                        |
| `POST /api/admin/v1/nodes/{node_name}/decommission`     | ops                     | **不可逆**,reason 必填。同事务:停调度期望态落台账 + 该主机名下全部登记置 revoked + enqueue `node.decommission`(worker 删 Node 对象);台账无此节点 404 `nodes.nodeNotFound`;节点上有未释放实例 409 `nodes.nodeHasInstances`,`?force=true` 跳过该闸。响应 `{node_name, revoked_enrollments, queued}`                                                                          |
| `GET /api/admin/v1/cluster/gpu-models`                  | ops/readonly            | 台账聚合 `[{gpu_model, gpu_model_raw, pool_label, node_count, gpu_total, ready_gpu_total, vram_gb}]`,canonical×pool 分组,未识别入 `unrecognized`                                                                                                                                                                                                                           |
| `GET /api/admin/v1/cluster/status`                      | ops/readonly            | 纯 DB:`{api_reachable, distro, k8s_version, probed_at, components:[{key, state, headline, facts, objects, fix_hint, diag_hint}], pools, pools_ready, config:{server_url_set, join_token_set, prometheus_url_set, grafana_url, registry_host, registry_project}}`。`state` 五态;`headline`/`facts` 的 `value` 是纯数据(计数 / 版本 / 对象名 / 地址),label 由前端按 key 映射 |
| `POST /api/admin/v1/cluster/test-connection`            | ops                     | 同步只读探测,upsert `cluster_status` 后返回;超时 5s → 502                                                                                                                                                                                                                                                                                                                  |
| `GET /api/admin/v1/cluster/components/{key}/probe`      | ops/readonly            | 实时深探(只读直连 K8s,不写库不记审计):`{key, probed_at, facts, pods, events}`。未知 key → 404;超时 5s / 探测失败 → 503;每管理员每小时 120 次                                                                                                                                                                                                                               |

## 规则与不变量

### 令牌

- 令牌 `sdln_` 前缀 256-bit 随机串,只存 sha256;无效/过期/吊销/终态一律 404。progress 令牌 `sdlp_` 前缀同规格。
- token 不进 URL、不进命令行参数:生成命令经 stdin 把 token 写入 `/run/superdl-join.token`(0600),脚本只认 `--token-file`;curl 一律 `--config` 注入 Authorization 头。集群 server URL 与 join token 不进脚本,由脚本凭 token `POST /bootstrap` 换取。
- 注册令牌一次性:首次 bootstrap(pending→installing)即消费并换发窄权限 progress 令牌(仅可 /progress;换发、吊销、轮换注册令牌时同步作废);脚本重跑与重启续跑只用盘上的 progress 令牌。
- 令牌绝对过期:expires_at 对一切非终态生效(progress 只刷新 last_report_at),过期即 404;落 expired 由对账器清扫。
- 签发令牌时必填期望主机名,bootstrap 上报主机名不符即置 failed 并 409。
- bootstrap 下发配置收窄到 9 键(cluster 组 server_url / join_token / agent_version / driver_version / install_mirror / registries_yaml + 镜像仓库组 registry_host / registry_ca_pem / registry_proxy_projects),全量生效配置不出注册链路。
- 一节点一令牌,不做批量可重用令牌;不做 drain。join token 落节点 agent config 文件(0600 root),轮换流程见 `deploy/cluster/README.md`「server token 与 agent token」。
- bootstrap 消费注册令牌在 `SELECT … FOR UPDATE` 行锁下做:同一令牌并发 bootstrap 只有一个能迁 installing,其余看到已消费态回 404。

### 台账与巡检

- 对账器(30s,advisory lock 1009)判定 joined:K8s 中该 node_name 出现且 Ready → 平台打上该池的整套标签 → 打成功才迁 `joined`(两阶段,事务内只收集待办,K8s 写在事务外逐节点独立 try;打失败留在原状态下一轮重试)。装机中的节点未打标,对账器按 `include_unlabeled` 取节点。2h 无心跳 → failed。读取走 `FOR UPDATE SKIP LOCKED`。
- 节点巡检(60s,advisory lock 1010)是节点事实源:阶段 A 纯 K8s 读 → B 单事务 DB 收敛 → B2 未登记隔离 → C 型号 label 收敛 → C2 池标签纠偏 → D cordon 期望态收敛(C / C2 / D 逐节点独立 try)。`enrollment.gpu_info` 只是装机一次性快照。
- **池标签键是 `node-restriction.kubernetes.io/superdl-pool`**(`core/k8s/base.POOL_NODE_LABEL`):该前缀受 NodeRestriction 准入插件保护,kubelet 用 `--node-label` 自打不上;只有平台 SA 经准入策略③的白名单能写。老键 `superdl.io/pool`(`LEGACY_POOL_NODE_LABEL`)在 `pool_node_labels` 的期望集里恒为删除,随对账 / 切池 / C2 收敛摘掉。
- **未登记隔离(阶段 B2)**:非 infra(不带 `node-restriction.kubernetes.io/superdl-infra` 或 `node-role.kubernetes.io/{control-plane,master,etcd}`)、`node_enrollments` 里没有任一状态的行、台账无 `desired_pool`、且未打池标签的节点 → 走 `service.request_cordon`(期望态落台账 + outbox,同管理端手工 cordon 一条路径,已请求不重复)并每轮打 `node_unenrolled` error 日志;常驻指标 `superdl_node_unenrolled` 每轮重置为此类节点数(消费方 `NodeUnenrolledJoined`)。带池标签但无登记行的节点不隔离,只打 `node_pool_label_without_enrollment` warning。核实来源后由运维退役或补登记。
- **池标签收敛(阶段 C2)的事实源是期望池,优先级 `node_specs.desired_pool` > 注册登记**;登记侧同时看 joined 与 failed 两态,同主机名多次登记取 id 最大的那行。规格快照(型号 / 显存 / 驱动 / CUDA)只认 joined。**未打标与标签不符都由 C2 补齐**(Node 对象被删重建后 kubelet 重注册不带池标签,即未打标)。
- **C2 下发的是该池的完整标签集**(`core/gpu_adapter.pool_node_labels`):池标签 + 新池的 GPU operand 标签 + **必须删掉旧池残留键**(gpu-operator 派生 `nvidia.com/gpu.deploy.*` 时不覆盖已存在的值)。平台 SA 的写权限由准入策略③ 的具名白名单给。
- 纠偏顺序**先 cordon 再改标签**;cordon 走 `service.request_cordon`(期望态落台账 + outbox),与管理端手工 cordon 同一条路径,阶段 D 按期望态复收敛。
- `superdl_node_pool_label_mismatch_total` 在**发现**时自增,不在纠正成功后。消费方 `NodePoolLabelMismatch`(critical)。判据是「标签不符**且**节点仍可调度」。未打标不计该指标。
- **切池**(`switch_node_pool`)只在 `kata` / `hami` / `mig` 三池之间;`cpu` 池是无卡机的物理属性,不参与。前置闸按序:节点在台账 → 目标池合法且不同于当前 → 当前池非 cpu,且 `gpu_count > 0` **或**当前已在 kata / hami / mig 之一(目标池组件起不来时观测卡数会归 0,得给节点留切回的路)→ 切 mig 须 `core/gpu_models.supports_mig`、切 kata 须 `core/gpu_models.supports_passthrough`(两张表都 fail-closed,未识别机型一律拒)→ **该节点上零未释放实例** → 目标池运行时就绪(`require_pool_runtime`)。同事务只做两件事:写 `desired_unschedulable=True` 与 `desired_pool`、enqueue `node.switch_pool`。
- **切池不需要任何节点侧动作,也不重启(kata 池除外,见下条)**:池间差异的节点侧软件全部由 DaemonSet 按标签投送(`kata-deploy` 认 `node-restriction.kubernetes.io/superdl-pool=kata`、HAMi device-plugin 认 `node-restriction.kubernetes.io/superdl-pool=hami`、gpu-operator 的 vfio-manager 与 sandbox 插件认它自己从 `workload.config` 派生的 `gpu.deploy.*`),整卡直通的绑定与解绑由 vfio-manager 在运行时做(启动 `vfio-manage bind --all`,preStop `vfio-manage unbind --all`)。**IOMMU 是装机基线,不随池变**(见「配置与装机」)。
- **kata 池要求宿主没有 NVIDIA 驱动**:gpu-operator 的 vfio-manager 见到预装驱动直接 `fatal: driver is pre-installed on host`,GPU 留在 `nvidia` 驱动上绑不到 `vfio-pci`,sandbox-validator 随之 CrashLoopBackOff,节点 `nvidia.com/gpu` 可分配数归 0。`node-join.sh` 对 kata 池跳过驱动与 container-toolkit,检出宿主驱动时 driver 阶段判失败;把已装驱动的在役节点切进 kata 要先手工卸载驱动并重启,步骤见 [node-pool-switch.md](../../deploy/cluster/runbooks/node-pool-switch.md)。**整卡直通的机型清单是 `core/gpu_models.PASSTHROUGH_CAPABLE_FAMILIES`**:Grace 超级芯片的集成 GPU(GB10 / GB200)固件强制 1:1 IOMMU 映射,内核拒绝把它绑到 vfio-pci,不在清单内。
- **未释放实例的口径是 `status != released`,含已关机 / 冻结 / 失败**(`orchestrator/queries.count_active_instances_on_node`);实例盘是节点本地 LV,开机 pin 回原节点。切池与退役共用这道闸,退役另有 `force` 旁路(机器已救不回来时用)。
- **`desired_pool` 非空即覆盖注册登记,且切完不清空**:Node 对象重建后由 C2 按它补齐。
- **池标签只由平台写入**:`node-join.sh` 不写任何池标签(agent config 不带 `node-label`);节点以未打标状态注册,对账器在判 Ready 时下发整套标签,打成功才迁 `joined`(失败不推进状态,下一轮重试)。
- **切池不自动解封**:handler 只到「标签收敛」,核对完组件落位由运维手工 uncordon。步骤见 [node-pool-switch.md](../../deploy/cluster/runbooks/node-pool-switch.md)。
- **节点退役**(`decommission_node`)三件事同一事务:停调度期望态落台账 → 该主机名下所有登记置 revoked → enqueue `node.decommission` 由 worker 删 Node 对象。两条平台管不到的边界交回运维(管理端退役确认框提示):删 Node 对象**不吊销 kubelet 证书**(kubelet 存活会重新注册,巡检按期望态再 cordon);join token 轮换与 kubelet 证书吊销是控制面动作。
- **能删哪些节点由准入层界定,不由 RBAC**:`deploy/cluster/admission/tenant-restrictions.yaml` 策略⑦,带控制面 / etcd 角色或 infra 落点标签的节点不可删。删掉控制面 Node 对象后 kubelet 重新注册,但 `node-restriction.kubernetes.io/superdl-infra` 不会跟着回来。
- 业务读台账,不实时调 K8s;节点消失先置 `Missing`,超保留期才删行(见 [limits.md](./limits.md));上架校验只认 Ready。
- 巡检在 worker 收敛环直连 K8s 并幂等重试;outbox 只管请求路径的业务事务。
- 型号归一化在 `core/gpu_models.py`:`canonical_gpu_model(raw)` 未识别返回 None,同名多容量家族(A100/A800/H100/H800/H200/V100)追加 `-{n}G`;`model_matches(sku, node)` 为相等或节点值前缀匹配。CMP 系列识别为 `CMP<数字>HX`:nvidia-smi 只报通用名时,bootstrap 上报名称回落 lspci 方括号内型号,显存仍取 nvidia-smi。
- `gpu_model` 参与调度,靠平台自有 label `superdl.io/gpu-model` 回写节点。hami 池的**物理卡数**依赖 GFD 标签 `nvidia.com/gpu.count`(缺标签按 0 纳管);两档的 GFD 由 gpu-operator 自带。
- 台账 `driver_version` / `cuda_version` 以 GFD 标签 `nvidia.com/cuda.{driver,runtime}-version.full`(缺则按 `.major/.minor/.revision` 拼)为准;装机上报的 `os_info` 只作无 GFD 时的回落。

### 集群能力

- 集群页组件体检十项:节点就绪 / HAMi / gpu-operator / DCGM / RuntimeClass nvidia / RuntimeClass kata-qemu / 存储类 / 实例入口(key `gateway`)/ 证书签发 / 监控栈。
- **体检五态**:`ok` 全就绪 / `degraded` 部分就绪 / `down` 缺位或全挂 / `disabled` 组件在位但该能力未开 / `unknown` 快照不可信。判定逻辑集中在 `app/core/k8s/health.py`,real 与 fake 共用同一份。
- **就绪判据看就绪数,不看对象存在**:`gpu_operator` 取 gpu-operator 所在 ns 下每个 operand DaemonSet 的 `numberReady == desiredNumberScheduled`,`dcgm` 取 dcgm-exporter DaemonSet 的同一比值,`monitoring` 取 Prometheus StatefulSet 的就绪副本数。
- **`unknown` 的窗口与下发门禁同源**:`probed_at` 超 `HAMI_GATE_MAX_AGE`(10 分钟)或 `api_reachable` 为假 → 十项全判 `unknown`,仍回上次事实供参考。从未探测过(无缓存行)时额外给安装命令;仅陈旧不给。
- **`kata_runtimeclass` 的池节点数只算 Ready 且可调度的**:RuntimeClass 在、池内没有 Ready 节点 → `disabled`。库存与可售性解读不进体检项,看 `pools_ready`。
- `storage` 按名核对 `topolvm-provisioner`(强制,缺它判 `down`)与 `superdl-cephfs`(可选,缺它只把数据盘那条事实标 warn)。
- `gateway` 逐 listener 单独判:整体 `Programmed=True` 但某个 listener 未就绪 → `degraded`,对象表给出端口 / 协议 / attachedRoutes / 条件 reason,与 `deploy/cluster/runbooks/cluster-validation.md` 的北向入口清单同判据。
- **组件文案全部由 key 映射到两端 locales**,后端只出事实数据;`fix_hint`(修复命令,仅 `down` / `degraded` / 从未探测时给)与 `diag_hint`(排障第一步,一直给)是命令,不随语言。
- Prometheus 侧事实(DCGM 样本新鲜度、抓取目标健康、触发中告警数)在巡检里经 `metering/service.py` 取并并入快照,集群页保持纯 DB 读;Prometheus 不可用只少几条事实,不改任何组件的状态位。
- **实时深探**回答快照答不了的「为什么不就绪」:匹配 Pod 的 phase / 节点 / 容器卡住原因(ImagePullBackOff 等,取自 containerStatuses 的 waiting/terminated)/ 重启次数,未就绪对象的 Warning 事件;`nodes` 项给非 Ready 的压力条件与污点,`cert_manager` 项另给证书 Ready 与到期日(CRD 未装按无证书处理)。是请求路径直连 K8s 的第二个只读例外,约束见 [decisions.md](../decisions.md);深探只补现场明细,不改组件状态位,失败时前端退化为只显示快照。
- **实例入口的判据是 `Gateway superdl` 对象 `status.conditions` 的 `Programmed=True`**,不是控制器 Deployment ready。CRD 未装或对象未下发都是 404,计「未就绪」不记 `error`。探测需 `gateway.networking.k8s.io/gateways` 的 get/list(`deploy/app/k8s/01-rbac.yaml` node-mgr 角色)。
- HAMi 门禁不做调度回落:shared 档能力未就绪直接 `CLUSTER_NOT_READY`,schedulerName 静态钉死。dedicated 档看 RuntimeClass `kata-qemu`(`require_kata_runtimeclass`)。门禁判据与 `build_gpu_request` 同源:**先看要不要卡,再看落哪个池**;`gpu_count == 0` 的实例不申请 `nvidia.com/*`、`schedulerName` 为空,只过 StorageClass。
- 发行版不设运行期配置,由平台探测 gitVersion(含 `+k3s`/`+rke2`)派生;两档装同一套组件,差异只在 `deploy/cluster/values/light/`。档位可用性看池里有没有 Ready 节点与运行时是否到位。
- k3s 只探测 nvidia 运行时、不设默认运行时,shared 档租户 Pod 显式 `runtimeClassName: nvidia`;RKE2 + gpu-operator 默认运行时已是 nvidia,保持 None。

### 配置与装机

- cluster 配置组键面:`cluster_server_url` / `cluster_join_token`(secret)/ `cluster_agent_version` / `node_driver_version` / `node_registries_yaml`(留空 = 平台生成;不得含凭据)/ `node_install_mirror`(`cn` | `official`,默认 `cn`)。
- **`cluster_join_token` 只许 agent token**(server config 的 `agent-token` 值):server node-token 形态 `K10<64hex>::server:<pw>` 会让 GPU 节点以 server 身份入群,配置中心(`SETTING_SPECS` 负前瞻)与 `node-join.sh` 写 agent config 前双侧拒绝。
- cluster 键不做启动 fail-fast:由 lifespan 在 DB 就绪后查生效配置打 error + 集群页红牌 + 创建注册命令 409。
- `render_registries_yaml`(`node_registries_yaml` 留空时的默认)按镜像仓库组生成:Spegel `"*"` + `registry_proxy_projects` 的每个上游 mirror + rewrite 到 Harbor 代理缓存项目 + `registry_ca_pem` 非空时 `configs.<host>.tls.ca_file`(占位 `__RANCHER_DIR__` 由 node-join 按发行版目录替换并落 `harbor-ca.crt` 0644);不含 auth。server 节点的同一份文件由 ansible 分发 `deploy/cluster/rke2/registries.yaml`。
- 池标签与 GPU Operator 的 operand 落点标签**由平台写,node-join 一律不碰**(agent `config.yaml` 只有 server 与 token)。完备期望集在 `core/gpu_adapter.pool_node_labels`:kata 池 `workload.config=vm-passthrough` 且删 `deploy.device-plugin`,hami 池 `deploy.device-plugin=false` 且删 `workload.config`,mig 与 cpu 池两个键都删。必须删掉旧池残留键;gpu-operator 派生 `gpu.deploy.*` 时不覆盖已存在的值。
- **IOMMU 在装机时开启,对全部带卡池都做,不按池分支**:x86_64 按 CPU 厂商写 GRUB(`/proc/cpuinfo` `vendor_id`:GenuineIntel → `intel_iommu=on iommu=pt`,AuthenticAMD / HygonGenuine → `amd_iommu=on iommu=pt`,其他厂商 fail-closed,可用 `SUPERDL_JOIN_IOMMU_ARGS` 显式指定),aarch64 不写;判据一律是 `/sys/kernel/iommu_groups` 非空,空则要求重启一次;cpu 池跳过。
- **kata 池装机跳过宿主驱动链路**:`node-join.sh` 的 driver 与 nvidia_toolkit 两阶段对 kata 池直接跳过(整卡直通由 vfio-pci 接管,宿主不跑 GPU 容器);宿主已装驱动则 driver 阶段判失败,不带病入群。nouveau 黑名单与 IOMMU 照常做。
- **cpu 池 = 无卡机**,承载纯 CPU 实例(`tier=cpu`,见 [catalog.md](./catalog.md))。装机时整条 NVIDIA 链路跳过:不做 NVIDIA 探测、不写 nouveau 黑名单、不装驱动与 container-toolkit、不上报驱动/CUDA 版本、不打 NVIDIA operand 标签。其余步骤与 GPU 节点相同。
- **发行版基线是 Debian 系**:precheck 读 `/etc/os-release`,`ID` 为 ubuntu / debian 或 `ID_LIKE` 含 debian 才放行(后续步骤全是 apt / dpkg / update-initramfs / update-grub),其他发行版 fail-closed。
- `node-join.sh` 随 API 镜像下发,步骤 marker 可无限重跑;需重启的场景用 systemd oneshot 断点续跑。phase 名:bootstrap/precheck/nouveau/sysctl/iommu/driver/nvidia_toolkit/nvme_vg/reboot/registries/agent_config/agent_install/agent_start/waiting_node。
- **server 本机跑 node-join(light 单机)**:判据是本机 `k3s.service` / `rke2-server.service` 在运行,此时不写 agent `config.yaml`、不装不起 agent,池标签经本机 kubectl(`k3s kubectl` / `/var/lib/rancher/rke2/bin/kubectl`)打到节点对象;toolkit 补装后重启 server 服务;`--uninstall` 不执行发行版卸载脚本、不删 server 的 config/registries。
