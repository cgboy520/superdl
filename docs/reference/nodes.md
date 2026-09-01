# 节点与集群

一键加节点、节点规格台账、集群能力探测。模块 `app/modules/nodes/`。

## 数据模型

- `node_enrollments`:token_hash(sha256 唯一)、progress_token_hash?(sha256 唯一,首次 bootstrap 换发;NULL=尚未 bootstrap 或注册令牌已轮换)、pool(kata/hami/mig/cpu)、hostname?、note?、nvme_devices JSONB?、status、phase、error、node_name、reported_ip、os_info JSONB、gpu_info JSONB、expires_at(默认 24h,1~168h 可调,绝对截止)、last_report_at、joined_at、created_by、idempotency_key(与 created_by 联合唯一)
- `node_specs`:node_name 唯一、pool_label?、unlabeled、gpu_model_raw?、gpu_model?(canonical)、label_synced、gpu_count、gpu_used、vram_gb、vcpu、mem_gb、disk_gb、driver_version?、cuda_version?、status(Ready/NotReady/Cordoned/Missing)、last_seen
- `cluster_status`:单行 id=1,api_reachable、k8s_version?、distro?(rke2/k3s)、hami_ready、dcgm_present、kps_present、gpu_operator_present、kata_runtimeclass、nvidia_runtimeclass、gateway_ready、cert_manager_ready、nodes_ready、nodes_total、storage_classes JSONB?、pools JSONB?、error?、probed_at

装机状态机:pending → installing → rebooting ⇆ installing → joining → joined,旁路终态 failed / expired / revoked(非终态均可因绝对过期落 expired)。终态 joined / failed / expired 另有一条通向 revoked 的边,**只有节点退役走得到**(管理端手工吊销 `revoke_enrollment` 对终态仍一律 409)。

## 契约

| 端点 | 角色/鉴权 | 说明 |
|---|---|---|
| `GET /api/v1/node-enroll/script` | 匿名+限流 | 静态脚本,仅替换 `__API_BASE__`,内容零密钥 |
| `POST /api/v1/node-enroll/bootstrap` | Bearer 注册令牌(一次性) | 上报 hostname/os/`gpu_details:[{name, memory_mib?}]`(nvidia-smi 不可用时为 lspci 名称,无显存) → 回 pool/发行版/agent 版本/server_url/join_token/驱动版本/nvme/registries_yaml/registry_ca_pem + `progress_token`(首跑换发) + `script_sha256`(重拉脚本指纹);首跑即消费注册令牌,之后任何令牌 bootstrap 均 404;按 IP 限流 |
| `POST /api/v1/node-enroll/progress` | Bearer progress 令牌 | `{phase, state: running\|ok\|failed\|rebooting, message?, driver_version?, cuda_version?}` 推进 phase/status/error/心跳;版本字段并进登记快照 `os_info`;注册令牌不能上报;响应 204 无体(脚本不读响应) |
| `GET /api/admin/v1/node-enrollments` | ops/readonly | `?active=true` 排除 revoked、joined(已进正式节点列表)、超 7d 的 expired |
| `POST /api/admin/v1/node-enrollments` | ops | 响应含 token 明文与完整命令,仅此一次;Idempotency-Key 重放轮换该行 token 而不建新行(仅 pending/expired/failed,进行中 409,同 regenerate 守卫);cluster 组未配 server_url/join_token → 409 |
| `POST .../{enrollment_id}/regenerate` | ops | 仅 pending/expired/failed:换新 token 与有效期,状态回 pending |
| `POST .../{enrollment_id}/revoke` | ops | reason 必填,非终态 → revoked |
| `GET /api/admin/v1/nodes` | ops/readonly | 数据源为台账;含 `gpu_model_raw / unlabeled / label_synced / last_seen / vram_gb`,含未打标与 Missing |
| `POST /api/admin/v1/nodes/{node_name}/cordon\|uncordon` | ops | reason 必填,只 enqueue `node.cordon`,请求路径不动 K8s |
| `POST /api/admin/v1/nodes/{node_name}/decommission` | ops | **不可逆**,reason 必填。同事务:停调度期望态落台账 + 该主机名下全部注册登记置 revoked + enqueue `node.decommission`(worker 删 Node 对象),请求路径不动 K8s;台账无此节点报 404 `nodes.nodeNotFound`。响应 `{node_name, revoked_enrollments, queued}` |
| `GET /api/admin/v1/cluster/gpu-models` | ops/readonly | 台账聚合 `[{gpu_model, gpu_model_raw, pool_label, node_count, gpu_total, ready_gpu_total, vram_gb}]`,canonical×pool 分组,未识别入 `unrecognized` 桶 |
| `GET /api/admin/v1/cluster/status` | ops/readonly | 纯 DB:`{api_reachable, distro, k8s_version, probed_at, components:[{key,label,ok,detail,fix_hint}], pools, config:{server_url_set, join_token_set, prometheus_url_set, grafana_url, registry_host, registry_project}}`(registry 两项非密,供镜像页新建表单默认前缀),fix_hint 为可复制修复命令 |
| `POST /api/admin/v1/cluster/test-connection` | ops | 同步只读探测,upsert `cluster_status` 后原样返回;超时 5s → 502 |

## 规则与不变量

### 令牌

- 令牌只走 `sdln_` 前缀 256-bit 随机串,只存 sha256;无效/过期/吊销/终态一律返 404,不区分原因。progress 令牌 `sdlp_` 前缀同规格。
- token 不进 URL、不进命令行参数:生成命令经 stdin 把 token 写入 `/run/superdl-join.token`(0600),脚本只认 `--token-file`;curl 一律 `--config` 注入 Authorization 头。集群 server URL 与 join token 不进脚本,由脚本凭 token `POST /bootstrap` 换取。
- 注册令牌一次性:首次 bootstrap(pending→installing)即消费并换发窄权限 progress 令牌(仅可 /progress;换发、吊销、轮换注册令牌时同步作废);重复 bootstrap 一律 404;脚本重跑与重启续跑只用盘上的 progress 令牌上报。
- 令牌绝对过期:expires_at 对一切非终态生效(progress 只刷新 last_report_at,不延长截止),过期即 404,请求路径不迁移状态;落 expired 由对账器清扫。
- 签发令牌时必填期望主机名,bootstrap 上报主机名不符即置 failed 并 409(被盗令牌不能在别的机器换出 join token)。
- bootstrap 下发配置收窄到 9 键(cluster 组 server_url / join_token / agent_version / driver_version / install_mirror / registries_yaml + 镜像仓库组 registry_host / registry_ca_pem / registry_proxy_projects,后三者只用于渲染 registries.yaml 与落 CA),全量生效配置(含解密后的支付私钥、Harbor 机器人 Secret)不出注册链路。
- 一节点一令牌,不做批量可重用令牌;不做 drain。join token 最终必然落节点 agent config 文件(0600 root),轮换走发行版自带的 token rotate。

### 台账与巡检

- 对账器(30s,advisory lock 1009)判定 joined 的唯一依据是 K8s 中该 node_name 出现且 Ready 且池标签匹配;池标签不符 → failed;2h 无心跳 → failed。读取走 `FOR UPDATE SKIP LOCKED`,与请求路径并发吊销/上报不互相覆盖。
- 节点巡检(60s,advisory lock 1010)是节点事实源:阶段 A 纯 K8s 读 → 阶段 B 单事务 DB 收敛 → 阶段 C 型号 label 收敛 → 阶段 C2 池标签纠偏 → 阶段 D cordon 期望态收敛(C / C2 / D 逐节点独立 try,失败下轮自愈)。`enrollment.gpu_info` 只是装机一次性快照,不得当事实源。
- **池标签纠偏(阶段 C2)的事实源是注册登记,且必须同时看 joined 与 failed 两态**:kubelet `--node-labels` 是节点自声明,持 join token 的机器可自称 kata 池去吸 VM 隔离负载;而入网对账器恰恰把「自声明与登记不符」的行判成 failed —— 只读 joined 等于对冒名节点永不纠偏,最需要生效的就是那一行。同主机名多次登记取 id 最大的那行(运维最后一次授权的池)。规格快照(型号 / 显存 / 驱动 / CUDA)仍只认 joined,那是「装机成功那一次」的快照。
- 纠偏顺序是**先 cordon 再改标签**:改标签存在窗口(kubelet 重启会再次自声明),而调度器只看标签;只纠标签不停调度等于承认「纠正成功之前落上去的负载」可接受,而那正是这条防线要挡的事(别人的 VM 隔离负载落到攻击者持 root 的宿主上)。cordon 走 `service.request_cordon`(期望态落台账 + outbox),与管理端手工 cordon 同一条路径,阶段 D 会照期望态复收敛。
- `superdl_node_pool_label_mismatch_total` 在**发现**时自增,不在纠正成功后:纠正是 K8s 写、可能连轮失败,这条计的是「出现了冒名节点」这件事本身,不是纠正动作的成败。消费方是 `NodePoolLabelMismatch`(critical):平台已自动纠偏并停调度,但「谁在冒名」要人去查——正常装机不会触发,登记与标签同源。
- **节点退役**(`decommission_node`)三件事同一事务,少一件这台机器就还留着一条回来的路:停调度期望态落台账(阶段 D 把重新冒头的它再压住)→ 该主机名下所有注册登记置 revoked(令牌永不可复用,重装也换不出 join token)→ enqueue `node.decommission` 由 worker 删 Node 对象。两条**平台管不到的边界**必须交回运维(回执文案 `nodes.decommissionDone` 就写着):删 Node 对象只摘掉节点在集群里的身份,**不吊销 kubelet 证书** —— kubelet 还活着就会自行重新注册,巡检按期望态把它再 cordon 一次;集群 join token 轮换与 kubelet 证书吊销都是控制面动作,平台不执行。
- **能删哪些节点由准入层界定,不由 RBAC**:`nodes` 是集群级资源,`resourceNames` 对 delete 不适用(节点名运营时才产生),字段级策略③又只匹配 UPDATE。收窄在 `deploy/cluster/admission/tenant-restrictions.yaml` 的策略⑦:带控制面 / etcd 角色或任一 infra 落点标签的节点一律不可删。删掉一个控制面 Node 对象后 kubelet 会重新注册,但 `node-restriction.kubernetes.io/superdl-infra` **不会**跟着回来(NodeRestriction 正是禁止 kubelet 自打该前缀),现象是 api / 5 个 worker / 前端 / Envoy 数据面一起 Pending,而集群表面上只是「少了一个标签」。
- 业务读台账,不实时调 K8s;节点消失先置 `Missing`,超保留期才删行(见 [limits.md](./limits.md));上架校验只认 Ready。
- 巡检在 worker 收敛环直连 K8s 并以幂等重试保证收敛,outbox 只管请求路径的业务事务。
- 型号归一化在 `core/gpu_models.py`:`canonical_gpu_model(raw)` 未识别返回 None,同名多容量家族(A100/A800/H100/H800/H200/V100)追加 `-{n}G`;`model_matches(sku, node)` 为相等或节点值前缀匹配(SKU `A100` 匹配台账 `A100-80G`)。CMP 系列识别为 `CMP<数字>HX`:nvidia-smi 只报通用名(`NVIDIA Graphics Device`)时,bootstrap 上报的名称回落 lspci 方括号内型号,显存仍取 nvidia-smi。
- `gpu_model` 必须参与调度,靠平台自有 label `superdl.io/gpu-model` 回写节点(不依赖 GFD、发行版无关)。但 hami 池的**物理卡数**依赖 GFD 标签 `nvidia.com/gpu.count`(HAMi 把 allocatable 放大为物理 × 切分数,缺标签按 0 纳管防超卖);两档的 GFD 都由 gpu-operator 自带。
- 台账 `driver_version` / `cuda_version` 以 GFD 标签 `nvidia.com/cuda.{driver,runtime}-version.full`(缺则按 `.major/.minor/.revision` 拼)为准;装机上报的 `os_info` 只作无 GFD 时的回落,不自愈的两种情况(驱动升级后快照不更新、收尾上报撞上对账器判 joined 而被 404 丢弃)都靠标签兜住。

### 集群能力

- 集群页组件体检十项按用户可见链路排:节点就绪 / HAMi / gpu-operator / DCGM / RuntimeClass nvidia / RuntimeClass kata-qemu / 存储类 / 实例入口(组件 key `gateway`)/ 证书签发 / 监控栈。`storage` 按名核对 `topolvm-provisioner`(强制,缺它判红)与 `superdl-juicefs`(可选,缺它只提示数据盘不可售);`kata_runtimeclass` 绿灯时另报 kata 池节点数(RuntimeClass 在但池里没节点,独享档一样没库存)。
- **实例入口的判据是 `Gateway superdl` 对象 `status.conditions` 的 `Programmed=True`,不是「入口控制器 Deployment 活着」**:listener 的证书 Secret 缺失、hostname 撞车或端口被占时 `Programmed` 为 False,而 envoy-gateway 控制面仍活着、流量一条都进不来。CRD 未装或对象未下发都是 404,同样计「未就绪」但不记 `error`。探测需 `gateway.networking.k8s.io/gateways` 的 get/list 权限(`deploy/app/k8s/01-rbac.yaml` 的 node-mgr 角色)。
- HAMi 门禁不做调度回落:shared 档能力未就绪直接报 `CLUSTER_NOT_READY`,schedulerName 静态钉死。dedicated 档同款门禁看 RuntimeClass `kata-qemu`(`require_kata_runtimeclass`):缺它下发的 Pod 会被 kubelet 直接拒,用户侧只能看到开机后转 failed。门禁判据与 `build_gpu_request` 同源:**先看要不要卡,再看落哪个池** —— `gpu_count == 0` 的实例不申请任何 `nvidia.com/*`、`schedulerName` 为空走默认调度器,因此只过 StorageClass,**即使它挂在 hami 池上**;要卡的才按池过 HAMi / Kata 门禁。
- 发行版不设运行期配置,由平台探测 gitVersion(含 `+k3s`/`+rke2`)派生;两档装同一套组件(gpu-operator + HAMi + kata-deploy),差异只在 k3s 侧的 values 覆盖(见 `deploy/cluster/values/light/`)。档位可用性看池里有没有 Ready 节点与运行时是否到位,不看发行版。
- k3s 只探测 nvidia 运行时、不设默认运行时,shared 档租户 Pod 必须显式 `runtimeClassName: nvidia`;RKE2 + gpu-operator 默认运行时已是 nvidia,保持 None。

### 配置与装机

- cluster 配置组键面:`cluster_server_url` / `cluster_join_token`(secret)/ `cluster_agent_version` / `node_driver_version` / `node_registries_yaml`(留空 = 平台生成;高级覆盖明文落库,不得含凭据)/ `node_install_mirror`(`cn` | `official`,默认 `cn`),无 k8s_distro 键。
- cluster 键不做启动 fail-fast(配置路径是 DB 覆盖层):由 lifespan 在 DB 就绪后查生效配置打 error + 集群页红牌 + 创建注册命令 409。
- `render_registries_yaml`(`node_registries_yaml` 留空时的平台默认)按镜像仓库组生成:Spegel `"*"` + `registry_proxy_projects` 的每个上游 mirror + rewrite 到 Harbor 代理缓存项目(拉不到回落上游)+ `registry_ca_pem` 非空时 `configs.<host>.tls.ca_file`(占位 `__RANCHER_DIR__` 由 node-join 按发行版目录替换并落 `harbor-ca.crt` 0644);不含 auth,拉取凭据经 imagePullSecrets 托管(见 [images.md](./images.md))。server 节点的同一份文件由 ansible 分发 `deploy/cluster/rke2/registries.yaml`。
- 池标签与 GPU Operator 的 operand 落点标签由 node-join 同时落下(agent 节点写进 `node-label`,server 节点经本机 kubectl):hami 池 `nvidia.com/gpu.deploy.device-plugin=false`(排斥官方 device-plugin,否则与 HAMi 抢注 `nvidia.com/gpu`),kata 池 `nvidia.com/gpu.workload.config=vm-passthrough`(换来 vfio-manager + kata 沙箱插件),mig 与 cpu 池无需额外标签。ClusterPolicy 不认各组件的 nodeSelector,落点只认节点标签。
- **cpu 池 = 无卡机**,承载纯 CPU 实例(`tier=cpu`,见 [catalog.md](./catalog.md))。装机时整条 NVIDIA 链路跳过:precheck 不做 NVIDIA 探测、不写 nouveau 黑名单、不装驱动与 container-toolkit、收尾不上报驱动/CUDA 版本(台账这两列留空),也不打任何 NVIDIA operand 标签。其余步骤(NVMe VG、registries、上报、对账判 joined)与 GPU 节点相同。
- `node-join.sh` 随 API 镜像下发,步骤 marker 可无限重跑;需重启的场景(kata 池 IOMMU 等)用 systemd oneshot 断点续跑。phase 名发行版中性:bootstrap/precheck/nouveau/sysctl/iommu/driver/nvidia_toolkit/nvme_vg/reboot/registries/agent_config/agent_install/agent_start/waiting_node。
- **server 本机跑 node-join(light 单机,server 兼跑 GPU 负载)**:脚本以「本机 `k3s.service` / `rke2-server.service` 在运行」为判据,此时不写 agent 的 `config.yaml`、不装不起 agent,池标签经本机 kubectl(`k3s kubectl` / `/var/lib/rancher/rke2/bin/kubectl`)直接打到节点对象;toolkit 补装后重启的是 server 服务;`--uninstall` 不执行发行版卸载脚本、不删 server 的 config/registries。其余步骤与 agent 节点相同,对账器判 joined 的依据不变。
