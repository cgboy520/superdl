# 集群部署:full / light 两条路径

选档:**full** = RKE2 多机生产,全池齐备(kata / mig / hami,外加可选的 cpu 池);
**light** = k3s 单机/小规模验证与轻量运营,组件集与 full 相同(CNI 同为 Cilium,kata / mig 池一样可用),差异只在 `values/light/` 的覆盖。
发行版由平台探测(管理端「集群」页可见),业务侧无需声明。

**cpu 池是无卡机池**,不承载 GPU 组件,只供纯 CPU 实例(`tier=cpu`)使用;没有无卡服务器时,CPU 规格也可挂 hami 池,每节点让出多少由策略 `gpu_node_cpu_instance_vcpu_cap` 封顶(0 = 不许)。详见 `docs/reference/nodes.md` 与 `docs/reference/catalog.md`。

chart 版本钉在 `helmfile.yaml.gotmpl`,K8s 版本钉在 `rke2/` 与 `k3s/` 的 server-config;升级走变更评审。
Gateway API 的 CRD 由 `gateway-api-crds.sh` 单点管,channel 首装即定,首装前先读「北向入口」一节。

## 前置检查(两档通用)

helm 不代建 Secret,先建好再 `./preflight.sh <full|light>`(只读,缺什么列全):

```bash
kubectl create ns monitoring --dry-run=client -o yaml | kubectl apply -f -
# SeaweedFS(自建 S3 后端,seaweedfs.enabled=true 时必需;用云 OSS 则跳过)
kubectl create ns seaweedfs --dry-run=client -o yaml | kubectl apply -f -
kubectl -n seaweedfs create secret generic superdl-seaweedfs-s3 \
  --from-literal=seaweedfs_s3_config='{"identities":[{"name":"superdl","credentials":[{"accessKey":"<ak>","secretKey":"<sk>"}],"actions":["Read","Write","List","Tagging","Admin"]}]}'
# JuiceFS(两档必需):六个键缺一不可,storage/bucket 按后端写(oss / s3 / minio …)
kubectl -n kube-system create secret generic superdl-juicefs-secret \
  --from-literal=name=superdl-data \
  --from-literal=metaurl=<postgres://juicefs:…@<pg-host>:5432/juicefs_meta?sslmode=require> \
  --from-literal=storage=<oss|s3|minio> --from-literal=bucket=<https://…> \
  --from-literal=access-key=<…> --from-literal=secret-key=<…>
# 配额 Job 另从 superdl-db 的 juicefs-metaurl 键取同一串(app/core/k8s/real.py)
kubectl -n monitoring create secret generic superdl-alert-token --from-literal=token=<与 SUPERDL_ALERTMANAGER_TOKEN 一致>
kubectl -n monitoring create secret generic superdl-smtp-password --from-literal=password=<SMTP 口令>
kubectl -n monitoring create secret generic grafana-admin \
  --from-literal=admin-user=admin --from-literal=admin-password=<口令>   # 仅 full;light 关 Grafana
```

full 档另需 `cert-manager/acme-dns-account`(DNS01 账户,见 `runbooks/acme-dns.md`);light 档不签发证书,手工把现成通配证书灌成 `superdl/superdl-jupyter-wildcard-tls` 与 `superdl/superdl-svc-wildcard-tls`。

## 北向入口:Envoy Gateway 与 Gateway API CRD(两档通用,首装前必读)

北向唯一入口是 Gateway API + Envoy Gateway。EG 控制面与 Envoy 数据面同住 `envoy-gateway-system`(未开 Gateway Namespace Mode);按 ns 名认入口的地方(NetworkPolicy 来源、`admission/tenant-restrictions.yaml` 的豁免名单)都写这个 ns。

**CRD 由 `./gateway-api-crds.sh` 单点管**(helmfile 侧 `crds.enabled=false`),脚本封的是 `helm template | kubectl apply --server-side`。首装不必手工执行:`./apply.sh` 经 envoy-gateway release 的 presync 钩子自动跑。

> **channel 只有一次机会。** 边缘限流用到 Gateway API 的 **experimental** channel;CRD 以 standard 装进后换不回来(唯一出路是删净 Gateway API CRD 重装,删 CRD 连带删掉全部 Gateway/HTTPRoute)。脚本自带前置闸门(channel 不符直接停手),`./preflight.sh` 复核 channel=experimental、bundle-version=v1.6.1。
> k3s 的 traefik 必须**装机即禁**(`k3s/server-config.yaml` 已写好),不能「先启用后禁用」。

**升级 Envoy Gateway**:`helmfile.yaml.gotmpl`、`gateway-api-crds.sh` 与 `scripts/check-gateway-manifests.py` 三处版本号一起改,再**先 `./gateway-api-crds.sh` 升 CRD,后 `./apply.sh <full|light> -l name=envoy-gateway` 升控制面**。

入口的**配置**在 `../app/k8s/04-gateway.yaml`(GatewayClass / 6 个 listener / 5 条路由 / 7 条策略;数据面 Envoy 的副本与资源在那里的 `EnvoyProxy`);本目录 `values/envoy-gateway.yaml` 只管**控制面**。

**light 档单机**:租户 Jupyter 一实例一条 HTTPRoute,给足 `EnvoyProxy` 的 memory limit 或对单机实例数设硬上限,取值实机压过再定。

## 路径 A:full(RKE2 生产)

1. **server 节点**(装机基线见 `../ansible/`):
   ```bash
   curl -sfL https://rancher-mirror.rancher.cn/rke2/install.sh | INSTALL_RKE2_MIRROR=cn INSTALL_RKE2_CHANNEL=latest sh -
   cp rke2/audit-policy.yaml /etc/rancher/rke2/audit-policy.yaml   # 缺失则 apiserver 起不来
   cp rke2/server-config.yaml /etc/rancher/rke2/config.yaml
   systemctl enable --now rke2-server
   ```
   **控制面 HA(公众生产强制)**:3 台 server 堆叠 etcd + 控制面 VIP(kube-vip/keepalived/SLB 任一)。ansible 在 `group_vars/servers.yml` 定义 `api_vip` + `server_ips`(奇数台 ≥3)即自动渲染 tls-san(模板内注释块保持不动);手工部署照 `rke2/server-config.yaml` 头注释取消 tls-san 注释并填真实值。
   第 2/3 台 server 加入:config.yaml 与首台同一渲染产物,另放 `rke2/server-join-config.yaml` 到 `/etc/rancher/rke2/config.yaml.d/50-join.yaml`(server 指 VIP:9345 + server token,首台严禁放)。
   VIP 就绪前可先单台上线,扩到 3 台前:tls-san 补齐 → 滚动重启全部 server → agent/cilium/netpol 统一切 VIP。
2. **平台接入**:管理端「平台配置 · 集群接入」录入 server 地址(HA 录 `https://<VIP>:9345`,单 server 录该机 IP)与 **agent token**(server-config.yaml 里 `agent-token` 的值);**禁止**录入 `/var/lib/rancher/rke2/server/node-token`(见「server token 与 agent token」)。
   GPU 节点的 registries.yaml 由平台按「平台配置 · 镜像仓库」自动生成;server 节点由 ansible 分发 `rke2/registries.yaml`。
3. **组件**:`./preflight.sh full && ./apply.sh full`(含 Loki/Alloy,见 `runbooks/loki-logging.md`;presync 先跑 `./gateway-api-crds.sh`)。
   准入策略不需要手工 apply:`admission/tenant-restrictions.yaml` 的七条 VAP 由 `apply.sh` 在 helmfile 之前下发并回读,七条全部 `Deny`、无 Audit 观察期;`preflight.sh` 与 `scripts/release.sh` 各再断言一次七个 Binding 存在且 `validationActions` 含 Deny。
4. **镜像仓库(Harbor)**:平台镜像与租户实例镜像的权威源,镜像引用一律 Harbor 全限定名。
   Harbor 侧:建平台项目(默认 `superdl`)、仅 Pull + List Repository 权限的机器人账户、(可选)Docker Hub 等代理缓存项目(设 public)。管理端「平台配置 · 镜像仓库」录入地址 / 项目 / 机器人 / 自签 CA / 代理映射并「测试连接」。拉取凭据不落节点:首装按 `../app/secrets.example.yaml` 手建 `superdl-registry-pull`,之后配置中心录入机器人后由 worker 按指纹覆写同名 Secret 并托管到各租户 ns;server 节点的 `registries.yaml` 由 ansible 分发。镜像发布与凭据轮换 SOP:`runbooks/image-prewarm.md`。
5. **GPU 节点**:管理端「节点 · 新增」生成一键命令,节点上执行即完成打标加入(池标签 + GPU Operator 落点标签 / 驱动 / registries 全自动)。
   **先装 gpu-operator 再加节点**;顺序颠倒时重打一次标签。
   MIG 切分是唯一还要手工打的标签:
   ```bash
   kubectl label node <mig池节点> nvidia.com/mig.config=all-1g.10gb --overwrite
   ```
6. 验证:`runbooks/cluster-validation.md`。

`apply.sh` 是本目录唯一的 apply 入口,两步:先 `kubectl apply -f admission/tenant-restrictions.yaml` 并回读七条 Policy 与 Binding(cluster-scoped,不进 `../app/k8s/kustomization.yaml`),再跑 `helmfile apply` 并固定两个必带开关:`HELM_DIFF_USE_UPGRADE_DRY_RUN=true`(helm-diff 走服务端 dry-run)与 `--skip-diff-on-install`。别绕过它直接跑 helmfile。单个 release:`./apply.sh light -l name=gpu-operator`。

## 平台组件落点标签

平台组件(api / 5 个 worker / 前端 / Envoy 数据面)的 `nodeSelector` 统一锚点是 `node-restriction.kubernetes.io/superdl-infra=true`,**由 `../ansible/site.yml` 在装机后用管理凭据打到控制面节点上**,不走发行版的 `node-label`。`node-restriction.kubernetes.io/` 前缀被 NodeRestriction 准入插件拉黑(`rke2/server-config.yaml` 与 `k3s/server-config.yaml` 的 `kube-apiserver-arg` 显式钉住),kubelet 打不上也改不掉;平台 SA 也无权改(准入策略③只放行 `superdl.io/*`)。

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
   cp rke2/audit-policy.yaml /etc/rancher/k3s/audit-policy.yaml   # 与 rke2 同规,缺失则 apiserver 起不来
   curl -sfL https://rancher-mirror.rancher.cn/k3s/k3s-install.sh | INSTALL_K3S_MIRROR=cn sh -s - server
   ```
   (config 已含 `disable: traefik`、`embedded-registry: true`=Spegel,以及 `flannel-backend: none` / `disable-network-policy: true` / `disable-kube-proxy: true`——CNI、NetworkPolicy、kube-proxy 全归 Cilium。
   这几项都必须**装机即设**:事后改要全集群重启 k3s 并重建全部 Pod,见下「给已有集群换 CNI」)
   再把 `values/light/cilium-light.yaml` 的 `CHANGE_ME_K3S_SERVER_IP` 换成 server 自己的 IP(单 server 没有 VIP;`preflight.sh` 会拦占位符)。
2. **平台接入**:同 full 第 2 步(k3s 同样配 `agent-token`,见 k3s/server-config.yaml;禁止用 `/var/lib/rancher/k3s/server/node-token`;server 地址 `https://<ip>:6443`)。
3. **组件**:`./preflight.sh light && ./apply.sh light`(presync 先装 Gateway API CRD;准入策略同 full 第 3 步)。

   light 与 full 装同一套组件,差异只在 `values/light/` 的覆盖:

   - HAMi 钉 k3s 版 scheduler 镜像(键 `kubeScheduler.image.tag`,升 k3s 时同步改)+ devicePlugin `runtimeClassName=nvidia`;chart 默认的 `nvidiaNodeSelector: {gpu: "on"}` 用 `null` 删键。
   - gpu-operator 关掉 toolkit(宿主 toolkit 由 node-join 装、k3s 自行探测生成 RuntimeClass nvidia)。`nvidia.com/gpu.count` 由 gpu-operator 自带的 GFD 提供。
   - kps / Loki 精简(盘紧可在 `environments/light.yaml` 关掉日志栈);开了 ServiceMonitor 的 release 必须 `needs: [monitoring/kube-prometheus-stack]`。
   - Envoy Gateway 控制面降到 1 副本并关掉 PDB。
   - Cilium 同装并接管 kube-proxy;CNI 路径按 k3s 的 containerd 改、`k8sServiceHost` 填 server 实 IP、北向 LoadBalancer 仍归 k3s ServiceLB(`values/light/cilium-light.yaml`)。
   - acme-dns 不装;租户 Jupyter 泛域名证书由现成通配证书灌成 `superdl/superdl-jupyter-wildcard-tls`。
   - **SeaweedFS 同装**(内网没有云 OSS):master/filer 各 1 副本、volume 3 副本两副本跨机,存储一律 `topolvm-provisioner`(准入策略④ 不许 seaweedfs ns 用 hostPath,而 chart 默认就是 hostPath);S3 只开 ClusterIP。占用实例盘 VG 约 1.6T。
   - **TopoLVM 必开**(VG `superdl-nvme` 由 node-join.sh 建出);**JuiceFS 必开**(数据盘),对象存储与元数据库见「前置检查」。
### 给已有集群换 CNI(flannel → Cilium)

装机时没设 `flannel-backend: none` 的老集群要补装 Cilium,是**全集群网络中断**的操作,不是滚动升级:k3s 的 flannel 开关是 server 端标志(agent 从 server 取节点配置,不必逐台改),但每个节点的 CNI 配置与全部 Pod 的网络都要重来。

1. 停租户侧入口(或挑无实例运行的窗口):切换期间跨节点 Pod 通信与 NodePort 全断。
2. server `/etc/rancher/k3s/config.yaml` 加 `flannel-backend: none`、`disable-network-policy: true`、`disable-kube-proxy: true`,`systemctl restart k3s`。此刻起 ClusterIP 无人处理,集群内服务发现全断,直到第 3 步 Cilium 起来。
3. `./apply.sh light -l name=cilium` 装上 Cilium;等 `cilium` DaemonSet 在**全部**节点 Ready(它跑 hostNetwork,没有 CNI 也能起来)。
4. 逐台 agent `systemctl restart k3s-agent`,让 kubelet 重读 CNI 配置;Cilium 的 `cni-exclusive` 会把旧的 `10-flannel.conflist` 挪走。
5. 重建全部非 hostNetwork 的 Pod(`kubectl delete pod -A --field-selector spec.nodeName=<node>` 逐台,或整机重启),旧 Pod 仍持有 flannel 的 IP 与路由。
6. 残留的 `cni0` / `flannel.1` 接口与 flannel / kube-proxy(`KUBE-*` 链)的 iptables 规则**重启节点才清干净**;不重启则手工 `ip link delete cni0`、`ip link delete flannel.1` 并清 `KUBE-*` 链,留着会和 Cilium 的 eBPF 数据面抢同一条流。
7. 回读:`kubectl -n kube-system exec ds/cilium -- cilium-dbg status`、全节点 Ready、租户 SSH 的 NodePort 能连、Envoy 的 LoadBalancer 外部 IP 未变。

**Cilium 与 kube-router 的两处语义差异必须靠 `cilium-policies.yaml` 补上**(随 cilium release 的 postsync 下发),否则表现是「组件都 Running 但平台连不上库、租户 SSH 不通」:

- **`ipBlock` 选不中节点**。节点在 Cilium 里是 `host` / `remote-node` 保留身份,与 IP 无关。`values/cilium.yaml` 的 `policyCIDRMatchMode: [nodes]` 只让 CIDR 选择器覆盖 `remote-node`,本机 `host`(平台库跑在节点宿主上)仍要按身份放行。
- **NodePort 的 SNAT 来源变了**。flannel 是 Pod 子网的 `.0/.1`,Cilium 换成入口节点的 `cilium_host`——该地址从子网池**动态分配**(实测 `.40` / `.59`),而且落在 `tenant-default` 的 `except 10.42.0.0/16` 里,按地址放行必然选不中。

换完必须实测:平台三域、租户 Jupyter、以及**从至少两台不同节点**连租户 SSH 的 NodePort。

4. **GPU 节点**:同 full 第 5 步。单机时 server 本机直接跑管理端生成的 node-join 命令:脚本检测到本机 `k3s.service` 在运行即走 server 路径(不装 agent、不改 server config,池标签经 `k3s kubectl` 打到节点;首次装 toolkit 后重启一次 k3s)。实例盘 VG `superdl-nvme` 不由 node-join 建时(令牌未登记 NVMe),须在 `./apply.sh light` 之前手工建好(空盘 `pvcreate`/`vgcreate`,或 loop 文件兜底)。
5. 能力边界:组件面不阉割(kata / mig 池同样可用),档位可用性看**池里有没有 Ready 节点**;单机只有一个池标签,选了 hami 就没有 kata/mig 池,专用整卡与共享·标准的 SKU 上架被硬校验拦下。纯 CPU 规格挂 hami 池即可在这台机上卖。管理端「集群」页常驻「轻量集群」黄条与组件体检。
