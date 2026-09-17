# 集群部署:full / light 两条路径

选档:**full** = RKE2 多机生产,全池齐备(kata / mig / hami,外加可选的 cpu 池);
**light** = k3s 单机/小规模验证与轻量运营,组件集与 full 相同(CNI 同为 Cilium,kata / mig 池一样可用),差异只在 `values/light/` 的覆盖。
发行版由平台探测(管理端「集群」页可见),业务侧无需声明。

**cpu 池是无卡机池**,不承载 GPU 组件,只供纯 CPU 实例(`tier=cpu`)使用;没有无卡服务器时,CPU 规格也可挂 hami 池,每节点让出多少由策略 `gpu_node_cpu_instance_vcpu_cap` 封顶(0 = 不许)。详见 `docs/reference/nodes.md` 与 `docs/reference/catalog.md`。

chart 版本钉在 `helmfile.yaml.gotmpl`,K8s 版本由安装器 channel 决定(ansible `rke2_channel`,默认 `latest`);实机验证清单核对为 v1.36.x;升级走变更评审。
Gateway API 的 CRD 由 `gateway-api-crds.sh` 单点管,channel 首装即定,首装前先读「北向入口」一节。

## 前置检查(两档通用)

helm 不代建 Secret,先建好再 `./preflight.sh <full|light>`(只读,缺什么列全):

```bash
kubectl create ns monitoring --dry-run=client -o yaml | kubectl apply -f -
kubectl -n monitoring create secret generic superdl-alert-token --from-literal=token=<与 SUPERDL_ALERTMANAGER_TOKEN 一致>
kubectl -n monitoring create secret generic superdl-smtp-password --from-literal=password=<SMTP 口令>
kubectl -n monitoring create secret generic superdl-metrics-token --from-literal=token=<与 SUPERDL_METRICS_TOKEN 一致>
kubectl -n monitoring create secret generic grafana-admin \
  --from-literal=admin-user=admin --from-literal=admin-password=<口令>
```

full 档另需 `cert-manager/acme-dns-account`(DNS01 账户,见 `runbooks/acme-dns.md`);light 档不签发证书,手工把现成通配证书灌成 `superdl/superdl-jupyter-wildcard-tls` 与 `superdl/superdl-svc-wildcard-tls`。`grafana-admin` 仅 full 档需要,light 关闭 Grafana。

启用 cnpg 时,还需在 `superdl` 命名空间预建 `cnpg-backup-s3`,键为 `ACCESS_KEY_ID` 与 `ACCESS_SECRET_KEY`,并替换 `values/cnpg-cluster.yaml` 中的对象存储占位符。凭据使用受限权限文件供给,不放在命令行。

监控栈的 RBAC 收窄在两处:`values/`(alloy `rbac.rules` 只留 pods / pods/log / namespaces / services / endpoints / nodes 读;loki 关 ruler sidecar 且不挂 token;kps `global.rbac.create=false` + kube-state-metrics 去掉 secrets 采集器)与 `monitoring-rbac.yaml`(prometheus-operator / Prometheus / admission Job 的 RBAC 手写版,operator 的 configmaps / secrets 只在 `monitoring` ns 的 Role;随 kube-prometheus-stack release 的 presync 下发)。引用凭据的 ServiceMonitor / PodMonitor 一律放 `monitoring` ns(`../app/k8s/08-monitoring.yaml`,Bearer 取上面的 `superdl-metrics-token`)。不变量「monitoring 之外的 SA 没有 secrets 读权」由 CI `monitoring-rbac` job(helm 渲染断言)、kind 冒烟的 `auth can-i` 与 `./preflight.sh` 各守一道。

存储与监控变更前核对:

- `values/rook-ceph-cluster.yaml` 的三个 OSD 必须落在三台不同机器上(`failureDomain: host`)。
- 修改 Prometheus 存储参数前,先以 `--cascade=orphan` 删除 `monitoring` 中的 StatefulSet `prometheus-kube-prometheus-stack-prometheus`,保留 Pod/PVC,再通过 `./apply.sh <full|light>` 重建 StatefulSet。
- critical 告警默认双通道:平台 webhook + 外部 SMTP。可选第三通道在 `values/kps.yaml` 以注释示例给出(Slack incoming webhook / PagerDuty Events v2 / 钉钉群机器人),启用时取消对应 route、receiver 与 `alertmanagerSpec.secrets` 的注释并建好 Secret;钉钉还须在 `alertmanager.alertmanagerSpec.containers` 配置 `prometheus-webhook-dingtalk` sidecar,固定镜像版本,profile 为 `oncall`,机器人凭据引用 `monitoring/superdl-dingtalk-token` 的 `token` 键。启用机器人加签时,同时配置转换器支持的签名参数与 Secret。

## 北向入口:Envoy Gateway 与 Gateway API CRD(两档通用,首装前必读)

北向唯一入口是 Gateway API + Envoy Gateway。EG 控制面与 Envoy 数据面同住 `envoy-gateway-system`(未开 Gateway Namespace Mode);按 ns 名认入口的地方(NetworkPolicy 来源、`admission/tenant-restrictions.yaml` 的豁免名单)都写这个 ns。

**CRD 由 `./gateway-api-crds.sh` 单点管**(helmfile 侧 `crds.enabled=false`),脚本封的是 `helm template | kubectl apply --server-side`。首装不必手工执行:`./apply.sh` 经 envoy-gateway release 的 presync 钩子自动跑。

> **channel 只有一次机会。** Gateway API CRD 必须装 **experimental** channel;CRD 以 standard 装进后换不回来(唯一出路是删净 Gateway API CRD 重装,删 CRD 连带删掉全部 Gateway/HTTPRoute)。脚本自带前置闸门(channel 不符直接停手),`./preflight.sh` 复核 channel=experimental、bundle-version=v1.6.1。
> k3s 的 traefik 必须**装机即禁**(`k3s/server-config.yaml` 已写好),不能「先启用后禁用」。

**升级 Envoy Gateway**:`helmfile.yaml.gotmpl`、`gateway-api-crds.sh` 与 `scripts/check-gateway-manifests.py` 三处版本号一起改,再**先 `./gateway-api-crds.sh` 升 CRD,后 `./apply.sh <full|light> -l name=envoy-gateway` 升控制面**。

入口的**配置**在 `../app/k8s/04-gateway.yaml`(GatewayClass / 6 个 listener / 8 条路由 / 9 条策略;数据面 Envoy 的副本与资源在那里的 `EnvoyProxy`);本目录 `values/envoy-gateway.yaml` 只管**控制面**。公网真实入口是 console 域(CDN → 前置反代 → `console-https`),`/api/v1` 由 HTTPRoute `superdl-console-api` 直达 API;每一跳前置地址同时登记进 `ClientTrafficPolicy` 的 `numTrustedHops` 与 ConfigMap `FORWARDED_ALLOW_IPS`,见 `docs/architecture.md`「公网真实链路」。

**light 档单机**:租户 Jupyter 一实例一条 HTTPRoute,给足 `EnvoyProxy` 的 memory limit 或对单机实例数设硬上限,取值实机压过再定。

## 路径 A:full(RKE2 生产)

1. **server 节点**(装机基线见 `../ansible/`):
   ```bash
   curl -sfL https://get.rke2.io | INSTALL_RKE2_CHANNEL=latest sh -
   # Mainland China: curl -sfL https://rancher-mirror.rancher.cn/rke2/install.sh | INSTALL_RKE2_MIRROR=cn INSTALL_RKE2_CHANNEL=latest sh -
   cp rke2/audit-policy.yaml /etc/rancher/rke2/audit-policy.yaml
   cp rke2/server-config.yaml /etc/rancher/rke2/config.yaml
   systemctl enable --now rke2-server
   ```
   **控制面 HA(公众生产强制)**:3 台 server 堆叠 etcd + 控制面 VIP(kube-vip/keepalived/SLB 任一)。ansible 在 `group_vars/servers.yml` 定义 `api_vip` + `server_ips`(奇数台 ≥3)即自动追加 `tls-san`。手工部署时在每台 server 的 config.yaml 添加同一份 `tls-san` 列表,包含 VIP、全部 server IP,以及需要用于访问 API 的 server 主机名;单 server 可省略。
   第 2/3 台 server 加入:config.yaml 与首台同一渲染产物,另放 `rke2/server-join-config.yaml` 到 `/etc/rancher/rke2/config.yaml.d/50-join.yaml`(server 指 VIP:9345 + server token,首台严禁放)。
   VIP 就绪前可先单台上线,扩到 3 台前:tls-san 补齐 → 滚动重启全部 server → agent/cilium/netpol 统一切 VIP。
2. **平台接入**:管理端「平台配置 · 集群接入」录入 server 地址(HA 录 `https://<VIP>:9345`,单 server 录该机 IP)与 **agent token**(server-config.yaml 里 `agent-token` 的值);**禁止**录入 `/var/lib/rancher/rke2/server/node-token`(见「server token 与 agent token」)。
   GPU 节点的 registries.yaml 由平台按「平台配置 · 镜像仓库」自动生成;server 节点由 ansible 分发 `rke2/registries.yaml`。
3. **组件**:`./preflight.sh full && ./apply.sh full`(含 Loki/Alloy,见 `runbooks/loki-logging.md`;presync 先跑 `./gateway-api-crds.sh`)。
   准入策略不需要手工 apply:`admission/tenant-restrictions.yaml` 的七条 VAP 由 `apply.sh` 在 helmfile 之前下发并回读,七条全部 `Deny`、无 Audit 观察期;`preflight.sh` 与 `scripts/release.sh` 各再断言一次七个 Binding 存在且 `validationActions` 含 Deny。
4. **镜像仓库(Harbor)**:平台镜像与租户实例镜像的权威源,镜像引用一律 Harbor 全限定名。
   Harbor 侧:建平台项目(默认 `superdl`)、仅 Pull + List Repository 权限的机器人账户、(可选)Docker Hub 等代理缓存项目(设 public)。管理端「平台配置 · 镜像仓库」录入地址 / 项目 / 机器人 / 自签 CA / 代理映射并「测试连接」。拉取凭据不落节点:首装按 `../README.md`「生产发布流程」手建 `superdl-registry-pull`,之后配置中心录入机器人后由 worker 按指纹覆写同名 Secret 并托管到各租户 ns;server 节点的 `registries.yaml` 由 ansible 分发。镜像发布与凭据轮换 SOP:`runbooks/image-prewarm.md`。
5. **GPU 节点**:管理端「节点 · 新增」生成一键命令,节点上执行即完成打标加入(池标签 + GPU Operator 落点标签 / 驱动 / registries 全自动)。
   **先装 gpu-operator 再加节点**;顺序颠倒时重打一次标签。
   MIG 切分是唯一还要手工打的标签:
   ```bash
   kubectl label node <mig池节点> nvidia.com/mig.config=all-1g.10gb --overwrite
   ```
6. 验证:`runbooks/cluster-validation.md`。

`apply.sh` 是本目录唯一的 apply 入口,两步:先 `kubectl apply -f admission/tenant-restrictions.yaml` 并回读七条 Policy 与 Binding(cluster-scoped,不进 `../app/k8s/kustomization.yaml`),再跑 `helmfile apply` 并固定两个必带开关:`HELM_DIFF_USE_UPGRADE_DRY_RUN=true`(helm-diff 走服务端 dry-run)与 `--skip-diff-on-install`。别绕过它直接跑 helmfile。单个 release:`./apply.sh light -l name=gpu-operator`。

## 平台组件落点标签

平台组件(api / 5 个 worker / 前端 / Envoy 数据面)的 `nodeSelector` 统一锚点是 `node-restriction.kubernetes.io/superdl-infra=true`,**由 `../ansible/site.yml` 在装机后用管理凭据打到控制面节点上**,不走发行版的 `node-label`。`node-restriction.kubernetes.io/` 前缀被 NodeRestriction 准入插件拉黑(`rke2/server-config.yaml` 与 `k3s/server-config.yaml` 的 `kube-apiserver-arg` 显式钉住),kubelet 打不上也改不掉;平台 SA 也无权改——准入策略③ 对 Node labels 只放行 `superdl.io/*`、池标签键 `node-restriction.kubernetes.io/superdl-pool`(同前缀,kubelet 打不上,只有平台写;hami / kata-deploy 的 nodeSelector 认它)与两个具名的 GPU operand 键(`nvidia.com/gpu.workload.config`、`nvidia.com/gpu.deploy.device-plugin`,管理端切池要随池标签一起收敛,见 [`runbooks/node-pool-switch.md`](./runbooks/node-pool-switch.md))。白名单保持具名,不放宽成 `nvidia.com/*` 前缀。

**infra 落点节点 ≥2 台才有冗余**:api / worker(core、tenant-mgr)/ web / admin / Envoy 数据面各 2 副本,按 `kubernetes.io/hostname` 的 topologySpread 是 `DoNotSchedule`(zone 维仍 `ScheduleAnyway`,节点可能没有 zone 标签)。只有一台 infra 节点时两副本同机、可调度但无冗余;两台时副本必分两机;其中一台失联后替补副本保持 Pending 直到该节点恢复或被删 —— Pending 就是可见信号(`kubectl -n superdl get pods | grep Pending`),不要为此放宽约束。单副本的 node-mgr / prewarm / disk-ops 不受影响。

`preflight.sh` 三项复核:NodeRestriction 已启用、至少一台节点带该标签、**GPU 池节点严禁带该标签**。手工补标:

```bash
kubectl label nodes -l node-role.kubernetes.io/control-plane \
  node-restriction.kubernetes.io/superdl-infra=true --overwrite
```

## server token 与 agent token(轮换 + 快照托管)

- **职责分离**:server token(`/var/lib/rancher/<rke2|k3s>/server/node-token`)只留在 server 节点与保险柜;agent token(server config 的 `agent-token` 值)录入平台库(AES-GCM 加密)并下发到 GPU 节点 agent config(0600 root)。
- **两份 server-config 模板里 `agent-token` 是取消注释的 `CHANGE_ME_AGENT_TOKEN` 占位行**,由 ansible 渲染(值经 `group_vars/servers.yml` 或 `-e` 注入)。**不许把它注释回去**(注释掉即静默回落用 server token 认证)。
- `preflight.sh` 两道校验:模板侧该行原样存在且值仍是 `CHANGE_ME`;集群侧 `agent-token` ≠ `/var/lib/rancher/<distro>/server/node-token` 且长度 ≥32。两个文件只在 server 节点上,别处跑 preflight 人工核对后以 `SUPERDL_AGENT_TOKEN_ACK=yes` 登记(与 `SUPERDL_MANAGED_PG_PITR_ACK` / `SUPERDL_LIGHT_INTERNAL_ACK` 同款;脚本不回显 token 值)。
- **agent token 轮换**:改全部 server 的 config → 滚动重启 server(逐一,等 etcd 健康再下一台)→ 更新平台「集群接入」配置。在册节点不受影响(join 后靠客户端证书认证)。
- **server token 轮换**:仅在怀疑泄露时做;节点多时用「先加新 token 再撤旧 token」两阶段法,参考发行版官方文档。
- **etcd 快照托管**:`secrets-encryption: true` 已开,快照仍含全部集群状态与 token 材料:异地加密保存,禁止只留 server 本机 `/var/lib/rancher`;访问快照纳入审计。

## 路径 B:light(k3s 单机/小规模)

> **定位边界**:light 档控制面即单点(单 server,etcd 与业务同机),只适用于内网试点/演示/开发联调;**禁止作为公众生产对外开放**,公众生产走路径 A。管理端「集群」页对 light 档常驻「轻量集群」黄条。

1. **server(可兼跑业务)**:
   ```bash
   mkdir -p /etc/rancher/k3s && cp k3s/server-config.yaml /etc/rancher/k3s/config.yaml
   cp rke2/audit-policy.yaml /etc/rancher/k3s/audit-policy.yaml
   curl -sfL https://get.k3s.io | sh -s - server
   # Mainland China: curl -sfL https://rancher-mirror.rancher.cn/k3s/k3s-install.sh | INSTALL_K3S_MIRROR=cn sh -s - server
   ```
   (config 已含 `disable: traefik`、`embedded-registry: true`=Spegel,以及 `flannel-backend: none` / `disable-network-policy: true` / `disable-kube-proxy: true`——CNI、NetworkPolicy、kube-proxy 全归 Cilium。
   这几项都必须**装机即设**:事后改要全集群重启 k3s 并重建全部 Pod,见下「给已有集群换 CNI」)
   再把 `values/light/cilium-light.yaml` 的 `CHANGE_ME_K3S_SERVER_IP` 换成 server 自己的 IP(`preflight.sh` 会拦占位符)。
2. **平台接入**:同 full 第 2 步(k3s 同样配 `agent-token`,见 k3s/server-config.yaml;禁止用 `/var/lib/rancher/k3s/server/node-token`;server 地址 `https://<ip>:6443`)。
3. **组件**:`./preflight.sh light && ./apply.sh light`(presync 先装 Gateway API CRD;准入策略同 full 第 3 步)。

   light 与 full 装同一套组件,差异只在 `values/light/` 的覆盖:

   - HAMi 钉 k3s 版 scheduler 镜像(键 `kubeScheduler.image.tag`,升 k3s 时同步改)+ devicePlugin `runtimeClassName=nvidia`;chart 默认的 `nvidiaNodeSelector: {gpu: "on"}` 用 `null` 删键。
   - gpu-operator 关掉 toolkit(宿主 toolkit 由 node-join 装、k3s 自行探测生成 RuntimeClass nvidia)。`nvidia.com/gpu.count` 由 gpu-operator 自带的 GFD 提供。
   - kps / Loki 精简(盘紧可在 `environments/light.yaml` 关掉日志栈);开了 ServiceMonitor 的 release 必须 `needs: [monitoring/kube-prometheus-stack]`。
   - Envoy Gateway 控制面降到 1 副本并关掉 PDB。
   - Cilium 同装并接管 kube-proxy;`values/light/cilium-light.yaml` 只覆盖 `k8sServiceHost`(server 实 IP)并关闭 `l2announcements`,北向 LoadBalancer 仍归 k3s ServiceLB。
   - acme-dns 不装;租户 Jupyter 泛域名证书由现成通配证书灌成 `superdl/superdl-jupyter-wildcard-tls`。
   - **TopoLVM 必开**(VG `superdl-nvme` 由 node-join.sh 建出);**Rook-Ceph 必开**(数据盘 CephFS,OSD 落 TopoLVM 的 Block PVC,`values/rook-ceph-cluster.yaml`)。
### 给已有集群换 CNI(flannel → Cilium)

装机时没设 `flannel-backend: none` 的老集群要补装 Cilium,是**全集群网络中断**的操作,不是滚动升级:k3s 的 flannel 开关是 server 端标志(agent 不必逐台改),但每个节点的 CNI 配置与全部 Pod 的网络都要重来。

1. 停租户侧入口(或挑无实例运行的窗口):切换期间跨节点 Pod 通信与 NodePort 全断。
2. server `/etc/rancher/k3s/config.yaml` 加 `flannel-backend: none`、`disable-network-policy: true`、`disable-kube-proxy: true`,`systemctl restart k3s`。此刻起 ClusterIP 无人处理,集群内服务发现全断,直到第 3 步 Cilium 起来。
3. `./apply.sh light -l name=cilium` 装上 Cilium;等 `cilium` DaemonSet 在**全部**节点 Ready。
4. 逐台 agent `systemctl restart k3s-agent`,让 kubelet 重读 CNI 配置;Cilium 的 `cni-exclusive` 会把旧的 `10-flannel.conflist` 挪走。
5. 重建全部非 hostNetwork 的 Pod(`kubectl delete pod -A --field-selector spec.nodeName=<node>` 逐台,或整机重启)。
6. 残留的 `cni0` / `flannel.1` 接口与 flannel / kube-proxy(`KUBE-*` 链)的 iptables 规则**重启节点才清干净**;不重启则手工 `ip link delete cni0`、`ip link delete flannel.1` 并清 `KUBE-*` 链。
7. 回读:`kubectl -n kube-system exec ds/cilium -- cilium-dbg status`、全节点 Ready、租户 SSH 的 NodePort 能连、Envoy 的 LoadBalancer 外部 IP 未变。

**Cilium 的两处策略语义必须靠 `cilium-policies.yaml` 补上**(随 cilium release 的 postsync 下发);缺失时的现场是「组件都 Running 但平台连不上库、租户 SSH 不通」:

- **`ipBlock` 选不中节点**。节点在 Cilium 里是 `host` / `remote-node` 保留身份,与 IP 无关。`values/cilium.yaml` 的 `policyCIDRMatchMode: [nodes]` 只让 CIDR 选择器覆盖 `remote-node`,本机 `host`(平台库跑在节点宿主上)仍要按身份放行。
- **NodePort 的 SNAT 来源是入口节点的 `cilium_host`**。该地址从 Pod CIDR **动态分配**,且落在 `tenant-default` 的 `except 10.42.0.0/16` 里,按地址放行选不中;`cilium-policies.yaml` 按身份放行。

换完必须实测:平台三域、租户 Jupyter、以及**从至少两台不同节点**连租户 SSH 的 NodePort。

4. **GPU 节点**:同 full 第 5 步。单机时 server 本机直接跑管理端生成的 node-join 命令:脚本检测到本机 `k3s.service` 在运行即走 server 路径(不装 agent、不改 server config,池标签经 `k3s kubectl` 打到节点;首次装 toolkit 后重启一次 k3s)。实例盘 VG `superdl-nvme` 不由 node-join 建时(令牌未登记 NVMe),须在 `./apply.sh light` 之前手工建好(空盘 `pvcreate`/`vgcreate`,或 loop 文件兜底)。
5. 能力边界:组件面不阉割(kata / mig 池同样可用),档位可用性看**池里有没有 Ready 节点**;单机只有一个池标签,选了 hami 就没有 kata/mig 池,专用整卡与共享·标准的 SKU 上架被硬校验拦下。纯 CPU 规格挂 hami 池即可在这台机上卖。管理端「集群」页常驻「轻量集群」黄条与组件体检。

## 集群状态备份与恢复(light / k3s)

`k3s/server-config.yaml` 的 `cluster-init: true` 让单 server 也用内嵌 etcd(已有 SQLite 库的 server 带此项重启即自动迁入;迁移前先手动跑一次 `superdl-k3s-state-backup`),每 6 小时落一份 etcd 快照到 `/var/lib/rancher/k3s/server/db/snapshots`(留 28 份,只在本机)。异机由 `k3s/state-backup.sh` 承担(`../ansible/site.yml` 装成 `/usr/local/sbin/superdl-k3s-state-backup`,cron `/etc/cron.d/superdl-k3s-state-backup` 每 6 小时;手工安装时同样这两处):

- 打包 `server/token`、`server/agent-token`、`server/cred`(含 secrets-encryption 密钥)、`server/tls`(CA)与最新 etcd 快照;仍是 SQLite(`server/db/state.db` 存在且无 `server/db/etcd/`)时用 `sqlite3 .backup` 做在线一致副本(需 `sqlite3`,缺则报错退出);顺带把 `state.db` 收成 0600。
- gpg AES256(口令 `/etc/superdl/pg/backup-passphrase`)→ `/var/lib/superdl/k3s-state/k3s-state-<主机>-<ts>.tar.gz.gpg`(本机留 14 天)→ rsync 到 PG 镜像机 `k3s/`(`/etc/superdl/pg/backup.env` 的 `SUPERDL_PG_MIRROR`、密钥 `backup-ssh-key`;镜像机 `rrsync -wo` 目录下须预建 `k3s/`)。**镜像机不得是承载租户负载的节点。**
- 成功写 `/var/lib/node_exporter/textfile/superdl_k3s_state_backup.prom` 的 `superdl_k3s_state_backup_last_success_timestamp_seconds`;季度演练清单在 `runbooks/pg-backup-restore.md`。

没有这份备份时 server 机器损毁 = 全部 Secret(含 `superdl-crypto` 的配置主密钥:平台库里的密文与实名摘要随之作废)、CA 与 token 全丢,agent 无法重新接入,只能重建集群并按 `../app/secrets.example.yaml` 重灌 Secret,租户实例与数据盘对象全部重建。

恢复到新 server(同版本 k3s、同一份 `/etc/rancher/k3s/config.yaml` 与 audit-policy,沿用原 IP;换 IP 要同步改 agent config 的 `server:`、`values/light/cilium-light.yaml` 的 `k8sServiceHost`、DNS 与 `/etc/hosts`):

```bash
mkdir -p /tmp/k3s-state && gpg --batch --decrypt --passphrase-file /etc/superdl/pg/backup-passphrase k3s-state-<主机>-<ts>.tar.gz.gpg | tar -xzf - -C /tmp/k3s-state
curl -sfL https://get.k3s.io | INSTALL_K3S_SKIP_START=true sh -s - server   # mainland China: rancher-mirror.rancher.cn/k3s/k3s-install.sh with INSTALL_K3S_MIRROR=cn
mkdir -p /var/lib/rancher/k3s/server/db && cp -a /tmp/k3s-state/server/{token,agent-token,cred,tls} /var/lib/rancher/k3s/server/
# etcd:用快照重置(token 必须是备份里那份,快照内引导数据靠它解密);命令结束后再 start
k3s server --cluster-reset --cluster-reset-restore-path=/tmp/k3s-state/server/db/snapshots/<快照文件>
# SQLite(备份里是 state.db 而非快照):放回数据库文件即可
cp -a /tmp/k3s-state/server/db/state.db /var/lib/rancher/k3s/server/db/
systemctl start k3s
rm -rf /tmp/k3s-state
```

起来后:`kubectl get nodes` 里 agent 自动回连(证书与 token 未变);`kubectl delete node <旧 server 名>`(主机名变了才有);`./preflight.sh light` 全绿;租户实例是无 ownerReference 的裸 Pod,已丢的按管理端实例详情逐台重建。
