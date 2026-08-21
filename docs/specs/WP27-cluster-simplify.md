# WP27 · 集群管理简化(能力探测 · 配置收敛 · full/light 双档)

决策:
1. 主线 RKE2;k3s 为受支持的轻量档,限 hami 池 SKU(dedicated/mig 需 full 集群)。prod+k3s 不硬禁,集群页黄色「轻量集群」警示讲清能力边界。
2. 探测优于声明:发行版不设运行期配置,由平台探测(gitVersion 含 `+k3s`/`+rke2`),装机参数由探测值派生。
3. HAMi 门禁,不做调度回落:shared 档在 create/start/restart 三入口读能力缓存,HAMi 未就绪直接给清晰 AppError,而非 Pending 到超时;schedulerName 静态钉死——回落 default-scheduler 无收益(`nvidia.com/gpucores` 扩展资源照样 Pending,还丢失报错)。
4. k3s 只探测 nvidia 运行时、不设默认运行时 → k3s 上 shared 档租户 Pod 必须显式 `runtimeClassName: nvidia`(按发行版启发式);RKE2+gpu-operator 默认运行时已是 nvidia,保持 None。

## 目标

- 能力探测:`cluster_status` 单行表,节点巡检并入探测(`probe_cluster()`:版本/发行版/HAMi 就绪/dcgm/kps/gpu-operator/kata RuntimeClass/StorageClass/池分布);`create_instance`/`start_instance`/restart handler 对 shared 档读缓存门禁(probed_at 超 10min 视为未知 → 拒绝并引导查 worker);ErrorCode `CLUSTER_NOT_READY`。
- 管理端「集群」页:连接状态卡(server/版本/发行版徽标 + 测试连接按钮,同步只读探测 upsert 后返回)+ 组件体检卡(逐项就绪/缺失 + 可复制修复指引命令)+ 池分布汇总 + 未打标节点警示 + prod+k3s 黄色警示 + 监控未接入引导。
- 配置面:cluster 组键面 `cluster_server_url / cluster_join_token(secret)/ cluster_agent_version / node_driver_version / node_registries_yaml(高级覆盖,留空=平台生成)/ node_install_mirror(""|cn,默认 cn)`,无 k8s_distro。`cluster_join_token` 读取带 `LEGACY_KEY_ALIASES` 回落:AES-GCM 的 AAD 绑定行 key,直接改行键名会静默毁掉密文,故旧行以旧 key 做 AAD 解密、写新删旧;Settings 用 AliasChoices 兼容旧 env(SUPERDL_RKE2_*)。`render_registries_yaml`:server_url 解析 host + registry NodePort 30500 常量 + 模板。cluster 键不做启动 fail-fast(推荐配置路径是 DB 覆盖层,启动只查 env 会误报)→ lifespan 在 DB 就绪后查 effective 配置打 error + 集群页红牌 + 创建注册命令 409 门禁。
- 部署面:helmfile `environments: {full, light}` + release condition + 单一集群变量;light = HAMi(kubeScheduler.imageTag 钉 k3s 对应版本、devicePlugin.runtimeClassName=nvidia)+ kps light(见 WP25)+ cert-manager/ingress-nginx(k3s 需 `--disable traefik`)+ juicefs/topolvm 可选;`deploy/cluster/k3s/server-config.yaml`(embedded-registry: true = Spegel);前置 Secret 列入 README 前置检查节 + `preflight.sh` 只读检查;README 为 full/light 两条各一页。
- node-join.sh:安装支持中国镜像(`rancher-mirror.rancher.cn` + `INSTALL_RKE2_MIRROR=cn`,k3s 分支同参对称,受 node_install_mirror 控制);phase 名发行版中性(`agent_config/agent_install/agent_start`)。

## 契约

| 端点 | 角色 | 说明 |
|---|---|---|
| `GET /api/admin/v1/cluster/status` | ops/readonly | 纯 DB:`{api_reachable, distro, k8s_version, probed_at, components:[{key,label,ok,detail,fix_hint}], pools, config:{server_url_set, join_token_set, prometheus_url_set, grafana_url}}`;fix_hint 为可复制修复命令 |
| `POST /api/admin/v1/cluster/test-connection` | ops | 同步只读探测(probe+list_nodes),upsert cluster_status 后原样返回;超时 5s → 502 |
| platform-config `cluster` 组 | admin | 键面见「目标 · 配置面」;无 k8s_distro |
| `BootstrapOut` | 兼容 | 字段形状兼容;`k8s_distro` 由探测派生(兜底 agent_version 后缀解析,再兜底 rke2);registries_yaml 取覆盖值或平台生成 |

ErrorCode:`CLUSTER_NOT_READY`。

## 数据变更

- 表 `cluster_status`(迁移 `wp27_cluster_status`):单行 id=1,字段 `api_reachable / k8s_version? / distro? / hami_ready / dcgm_present / kps_present / gpu_operator_present / kata_runtimeclass / storage_classes JSONB? / pools JSONB? / detail JSONB? / error? / probed_at`。
- 数据迁移 `wp27_cluster_key_rename`:platform_settings 明文键改名(server_url/version);join_token 行不动(别名回落机制处理)。
- RBAC(`deploy/app/k8s/01-rbac.yaml`):apps deployments/daemonsets/statefulsets get/list、storageclasses get/list。

## 验收用例

1. 门禁:hami_ready=false 或 probed_at 陈旧 → shared 档 create/start 即 409 CLUSTER_NOT_READY(文案含引导);dedicated/mig 不受影响;hami_ready=true 放行。
2. 键兼容:旧 env(SUPERDL_RKE2_*)仍生效;DB 旧行 join_token 经别名回落可解密;管理端保存新键后旧行删除;显示为新键名。
3. registries 生成:cluster_server_url=`https://10.0.0.1:9345` → 生成 yaml 含 `http://10.0.0.1:30500`;node_registries_yaml 有值时优先。
4. distro 派生:fake 注入 gitVersion `v1.33.4+k3s1` → BootstrapOut.k8s_distro=k3s;无探测数据 → 按 agent_version 后缀;再兜底 rke2。
5. 测试连接:探测成功 upsert 并返回;fake 断连 → 502 且 cluster_status.error 记录原因。
6. bats:phase 名走通;k3s/rke2 两 fixture 镜像参数正确;registries 路径按 distro 落位。

## 实机验证项(人工事项)

新机 full/light 两条路径各按 README 单页走通;`.env` 集群键全空、仅经管理端配置完成加节点;k3s 上 HAMi 未装时下单即清晰报错;HAMi on k3s(kubeScheduler.imageTag 匹配/devicePlugin runtimeClassName/RuntimeClass nvidia 存在性);k3s kube-router NetworkPolicy 对租户 Egress 黑名单等效性;RKE2 cn 镜像可用性;probe RBAC 增量在 RKE2/k3s 实测;kps light 单机占用。
