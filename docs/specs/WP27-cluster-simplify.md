# WP27 · 集群管理简化(能力探测 · 配置收敛 · full/light 双档)

方向决策经人工确认:
1. **「k3s vs k8s 两套体系」是伪命题**:主线 RKE2,k3s 是实机验证时加的旁路(6 个分叉点全在装机路径,集群侧零支持)。真问题是散装配置:9 处人工配置位置、55+ 待填值、server IP 填 5 遍、registries.yaml 3 份漂移副本、必须 SSH 上 server ≥2 次。
2. **砍 `k8s_distro` 运行期配置**(修订 WP23):一生选一次的事实不该是可切开关;发行版由平台探测(gitVersion 含 `+k3s`/`+rke2`),装机参数由探测值派生;cluster 键更名中性并兼容迁移。**探测优于声明**。
3. **k3s 升格为受支持的轻量档**(修订「仅本地验证」定位):限 hami 池 SKU(dedicated/mig 需 full 集群);`prod_forbidden` 硬禁改为集群页黄色警示——探测到的现实硬禁没有意义,讲清能力边界才是最佳实践。
4. **HAMi 门禁替代调度回落**:shared 档在 create/start/restart 三入口读能力缓存(HAMi 未就绪 → 清晰 AppError,替代 300s Pending);schedulerName 保持静态钉死——回落 default-scheduler 无收益(`nvidia.com/gpucores` 扩展资源照样 Pending 还丢报错)。修最尖锐 bug:k3s 路径 shared 档永久 Pending。
5. **能力条件下发 runtime_class**:已核实 k3s 只探测 nvidia 运行时不设默认 → k3s 上 shared 档租户 Pod 必须显式 `runtimeClassName: nvidia`(distro 启发式);RKE2+gpu-operator 默认运行时已 nvidia 保持 None。

## 目标

- **能力探测**:`cluster_status` 单行表,由 WP26 巡检并入探测(`probe_cluster()`:版本/发行版/HAMi 就绪/dcgm/kps/gpu-operator/kata RuntimeClass/StorageClass/池分布);`create_instance`/`start_instance`/restart handler 对 shared 档读缓存门禁(probed_at 超 10min 视为未知 → 拒绝并引导查 worker);新 ErrorCode `CLUSTER_NOT_READY`。
- **管理端「集群」页**(新路由):连接状态卡(server/版本/发行版徽标 + **测试连接**按钮,同步只读探测 upsert 后返回,对齐 SmsTestCard 先例)+ **组件体检卡**(每项 ✓/✗ + 可复制修复指引命令)+ 池分布汇总 + 未打标节点警示 + prod+k3s 黄色「轻量集群」警示 + 监控未接入引导(承接 WP25)。
- **配置面收敛**:删 k8s_distro 下拉;`rke2_server_url→cluster_server_url`、`rke2_version→cluster_agent_version`(明文直迁);**`rke2_join_token→cluster_join_token` 不能改行**——AES-GCM 且 AAD=行 key,直接 UPDATE 静默毁 token → `LEGACY_KEY_ALIASES` 读回落(旧 key 做 AAD)+ 写新删旧;Settings 用 AliasChoices 兼容旧 env;新键 `node_install_mirror`(choice ""|cn,默认 cn);`node_registries_yaml` 语义改「高级覆盖,留空=平台生成」(`render_registries_yaml`:server_url 解析 host + registry NodePort 30500 常量 + 模板);cluster 键不做启动 fail-fast(推荐路径是 DB 覆盖层,启动查 env 误报)→ lifespan DB 就绪后查 effective 配置打 error + 集群页红牌 + 既有 409 门禁。
- **部署面收敛**:helmfile `environments: {full, light}` + release condition + 单一集群变量(消灭 4 处 `<server-ip>` 手改);light = HAMi(**kubeScheduler.imageTag 钉 k3s 对应版本**、devicePlugin.runtimeClassName=nvidia,**替代并删除孤儿 gpu-device-plugin.yaml**)+ kps light(WP25 values)+ cert-manager/ingress-nginx(README 注明 k3s `--disable traefik`)+ juicefs/topolvm 可选;新增 `deploy/cluster/k3s/server-config.yaml`(embedded-registry: true = Spegel);4 个前置 Secret 入 README 前置检查节 + `preflight.sh` 只读检查;README 重写为 full/light 两条各一页。
- **node-join.sh**:RKE2 分支中国镜像(`rancher-mirror.rancher.cn/rke2/install.sh` + `INSTALL_RKE2_MIRROR=cn`,受 node_install_mirror 控制,k3s 分支同参对称);phase 改名 `agent_config/agent_install/agent_start`(服务端与前端新旧键双收兼容;完成语 :360 修发行版中性);bats 同步。
- 文档欠账 14 处修正(五键实为六键、ansible 与 containerd 表述、孤儿文件、k3s 章节缺失、helmfile 前置 Secret 等,spec/README 与实现对齐)。

## 契约

| 端点 | 角色 | 说明 |
|---|---|---|
| `GET /api/admin/v1/cluster/status` | ops/readonly | 纯 DB:`{api_reachable, distro, k8s_version, probed_at, components:[{key,label,ok,detail,fix_hint}], pools, config:{server_url_set, join_token_set, prometheus_url_set, grafana_url}}`;fix_hint 为可复制修复命令 |
| `POST /api/admin/v1/cluster/test-connection` | ops | 同步只读探测(probe+list_nodes),upsert cluster_status 后原样返回;超时 5s → 502 |
| platform-config cluster 组(改) | admin | 键面:cluster_server_url / cluster_join_token(secret) / cluster_agent_version / node_driver_version / node_registries_yaml(高级覆盖) / node_install_mirror;删 k8s_distro;SettingGroup 无新增(observability 归 WP25) |
| `BootstrapOut` | 兼容 | 字段形状不变;`k8s_distro` 取值改探测派生(兜底 agent_version 后缀解析,再兜底 rke2);registries_yaml 取覆盖值或平台生成 |

新 ErrorCode:`CLUSTER_NOT_READY`。openapi/api-client 再生成(SettingGroup 与新端点)。

## 数据变更

- 新表 `cluster_status`(迁移 `wp27_cluster_status`):单行 id=1,字段 `api_reachable / k8s_version? / distro? / hami_ready / dcgm_present / kps_present / gpu_operator_present / kata_runtimeclass / storage_classes JSONB? / pools JSONB? / detail JSONB? / error? / probed_at`。
- 数据迁移 `wp27_cluster_key_rename`:platform_settings 明文键改名(server_url/version);**join_token 行不动**(别名回落机制处理)。
- RBAC 增量(`deploy/app/k8s/01-rbac.yaml`):apps deployments/daemonsets/statefulsets get/list、storageclasses get/list。

## 分批(10 批)

能力表+probe_cluster(real/fake)→ 巡检并入+require_hami_ready → 三入口门禁+ErrorCode → 键迁移(AAD 别名回落)→ 砍 distro+镜像+phase 中性化+bats → registries 平台生成 → cluster/status+test-connection 契约 → 管理端集群页 → helmfile environments+k3s server-config+删孤儿+README/preflight → prometheus_url fail-fast 收尾+lifespan 告警+platform.tsx 键文案。

## 验收用例

1. 门禁:hami_ready=false 或 probed_at 陈旧 → shared 档 create/start 即 409 CLUSTER_NOT_READY(文案含引导);dedicated/mig 不受影响;hami_ready=true 放行。
2. 键迁移:旧 env(SUPERDL_RKE2_*)仍生效;DB 旧行 join_token 经别名回落可解密;管理端保存新键后旧行删除;显示为新键名。
3. registries 生成:cluster_server_url=`https://10.0.0.1:9345` → 生成 yaml 含 `http://10.0.0.1:30500`;node_registries_yaml 有值时优先。
4. distro 派生:fake 注入 gitVersion `v1.33.4+k3s1` → BootstrapOut.k8s_distro=k3s;无探测数据 → 按 agent_version 后缀;再兜底 rke2。
5. 测试连接:探测成功 upsert 并返回;fake 断连 → 502 且 cluster_status.error 记录原因。
6. bats:phase 新名走通;k3s/rke2 两 fixture 镜像参数正确;registries 路径按 distro 落位。
7. 实机(人工):新机 full/light 两条路径各按 README 单页走通;`.env` 集群键全空、仅经管理端配置完成加节点;k3s 上 HAMi 未装时下单即清晰报错。

## 实机验证项(人工事项)

HAMi on k3s(kubeScheduler.imageTag 匹配/devicePlugin runtimeClassName/RuntimeClass nvidia 存在性);k3s kube-router NetworkPolicy 对租户 Egress 黑名单等效性;RKE2 cn 镜像可用性;probe RBAC 增量在 RKE2/k3s 实测;kps light 单机占用(WP25 联动)。
