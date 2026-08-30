# 集群部署:full / light 两条路径

选档:**full** = RKE2 多机生产,全池齐备(kata / mig / hami,外加可选的 cpu 池);
**light** = k3s 单机/小规模验证与轻量运营,仅共享·经济档(hami 池)SKU。
发行版由平台探测(管理端「集群」页可见),业务侧无需声明。

**cpu 池是无卡机池**,不承载任何 GPU 组件:装机时整条 NVIDIA 链路跳过,GPU Operator 的 operand
也不会落到它上面(GFD 不给无卡机打 `nvidia.com/gpu.present`)。它只供纯 CPU 实例(`tier=cpu`)使用;
没有无卡服务器时,CPU 规格也可以挂 hami 池吃 GPU 机的空闲 CPU,每节点让出多少由策略
`gpu_node_cpu_instance_vcpu_cap` 封顶(0 = 不许)。详见 `docs/reference/nodes.md` 与 `docs/reference/catalog.md`。

chart 版本钉在 `helmfile.yaml.gotmpl`,K8s 版本钉在 `rke2/` 与 `k3s/` 的 server-config;升级走变更评审。
Gateway API 的 CRD 不跟 chart 走,由 `gateway-api-crds.sh` 单点管 —— 它有一个**首装即定、事后换不回去**的
选择,首装前先读下面「北向入口」一节。

## 前置检查(两档通用)

helm 不代建 Secret,先建好再 `./preflight.sh <full|light>`(只读,缺什么列全):

```bash
kubectl create ns monitoring --dry-run=client -o yaml | kubectl apply -f -
kubectl -n kube-system create secret generic superdl-juicefs-secret \
  --from-literal=metaurl=<postgres://juicefs:…@<pg-host>:5432/juicefs_meta?sslmode=require> --from-literal=access-key=<…> --from-literal=secret-key=<…>
kubectl -n monitoring create secret generic superdl-alert-token --from-literal=token=<与 SUPERDL_ALERTMANAGER_TOKEN 一致>
kubectl -n monitoring create secret generic superdl-smtp-password --from-literal=password=<SMTP 口令>
kubectl -n monitoring create secret generic grafana-admin \
  --from-literal=admin-user=admin --from-literal=admin-password=<口令>   # 仅 full;light 关 Grafana
```

full 档另需 `cert-manager/acme-dns-account`(DNS01 账户,见 `runbooks/acme-dns.md`);light 档不签发证书,
改为手工把现成通配证书灌成 `superdl/superdl-jupyter-wildcard-tls` 与 `superdl/superdl-svc-wildcard-tls`。

## 北向入口:Envoy Gateway 与 Gateway API CRD(两档通用,首装前必读)

北向唯一入口是 Gateway API + Envoy Gateway。EG 控制面与 Envoy 数据面同住 `envoy-gateway-system`
(未开 Gateway Namespace Mode):**凡是按 ns 名认入口的地方**(NetworkPolicy 来源、
`admission/tenant-restrictions.yaml` 的豁免名单)都写这个 ns,漏一处就是网关起不来。

**CRD 不跟 chart 走,由 `./gateway-api-crds.sh` 单点管**(helmfile 侧 `crds.enabled=false`):
gateway-helm 内置的 CRD 子 chart 落在 helm 的 `crds/` 目录,而该目录在 `helm upgrade` 时永不更新,
跟着 chart 装等于 CRD 永远停在首装那一版。脚本封的是 `helm template | kubectl apply --server-side`
(清单近 4 MB,客户端 apply 撞注解体积上限,报错只说 `metadata.annotations: Too long`)。
首装不必手工执行:`./apply.sh` 经 envoy-gateway release 的 presync 钩子自动跑一次。

> **channel 只有一次机会。** 平台用到的每源 IP 边缘限流落在 Gateway API 的 **experimental** channel。
> CRD 一旦以 standard 装进集群就换不回来:随 CRD 一起装的 safe-upgrades ValidatingAdmissionPolicy 用 CEL
> 拒绝「standard 之上装 experimental」;唯一出路是删净 Gateway API CRD 重装,而**删 CRD 会连带删掉集群内
> 全部 Gateway/HTTPRoute**(平台三个域名 + 全部租户 Jupyter 入口)。脚本自带前置闸门(channel 不符直接停手),
> `./preflight.sh` 另有一道复核(channel=experimental、bundle-version=v1.6.1)。
> k3s 的 traefik 同类风险:必须**装机即禁**(`k3s/server-config.yaml` 已写好),绝不能「先启用后禁用」——
> 部分版本上禁用 traefik 会连带删掉它自带的 Gateway API CRD。

**升级 Envoy Gateway**:先把 `helmfile.yaml.gotmpl`、`gateway-api-crds.sh` 与
`scripts/check-gateway-manifests.py` 三处版本号一起改(第三处是 CI 的清单校验闸门,不改就是拿旧 schema 校验新清单),
再**先 `./gateway-api-crds.sh` 升 CRD,后 `./apply.sh <full|light> -l name=envoy-gateway` 升控制面**。
chart 侧 `crds.enabled=false`,顺序反了就是「新控制面 + 旧 CRD」:控制器反复重启,或新字段被 apiserver
悄悄丢掉而清单看着一切正常。

入口的**配置**不在本目录,在 `../app/k8s/04-gateway.yaml`(GatewayClass / 6 个 listener / 4 条平台路由 /
5 条策略);数据面 Envoy 的副本与资源也在那里的 `EnvoyProxy`,本目录 `values/envoy-gateway.yaml` 只管**控制面**。
两处名字相近、键名也像,改错地方的表现是「值写了但完全没生效」,没有任何报错。

**light 档单机尤其注意**:租户 Jupyter 一实例一条 HTTPRoute,活跃实例多了就是几百上千条路由全量下发进每个 Envoy,
内存跟着涨。单机要么给足 `EnvoyProxy` 的 memory limit,要么对单机实例数设硬上限,**取值实机压过再定**。

## 路径 A:full(RKE2 生产)

1. **server 节点**(装机基线见 `../ansible/`):
   ```bash
   curl -sfL https://rancher-mirror.rancher.cn/rke2/install.sh | INSTALL_RKE2_MIRROR=cn INSTALL_RKE2_CHANNEL=latest sh -
   cp rke2/audit-policy.yaml /etc/rancher/rke2/audit-policy.yaml   # 缺失则 apiserver 起不来
   cp rke2/server-config.yaml /etc/rancher/rke2/config.yaml
   systemctl enable --now rke2-server
   ```
   **控制面 HA(公众生产强制,单 server 禁止对外开放)**:3 台 server 堆叠 etcd(奇数台法定人数)
   + 控制面 VIP(kube-vip/keepalived/SLB 任一)。ansible 在 `group_vars/servers.yml` 定义
   `api_vip` + `server_ips`(奇数台 ≥3)即自动渲染 tls-san(apiserver 证书覆盖 VIP 与全部 server IP,
   模板内注释块保持不动);手工部署照 `rke2/server-config.yaml` 头注释取消 tls-san 注释并填真实值。
   第 2/3 台 server 加入:config.yaml 与首台同一渲染产物,另放 `rke2/server-join-config.yaml` 到
   `/etc/rancher/rke2/config.yaml.d/50-join.yaml`(server 指 VIP:9345 + server token,首台严禁放)。
   VIP 就绪前可先单台上线,扩到 3 台前必须:tls-san 补齐 → 滚动重启全部 server → agent/cilium/netpol 统一切 VIP。
2. **平台接入**:管理端「平台配置 · 集群接入」录入 server 地址(HA 集群录 `https://<VIP>:9345`,单 server 录该机 IP)
   与 **agent token**(即 server-config.yaml 里 `agent-token` 的值,首装前生成,见该文件注释);
   **禁止**录入 `/var/lib/rancher/rke2/server/node-token`(server token 能拉 server 进 etcd 环;
   轮换与托管见下文「server token 与 agent token」)。
   GPU 节点的 registries.yaml 由平台按「平台配置 · 镜像仓库」自动生成;server 节点由 ansible 分发 `rke2/registries.yaml`。
3. **组件**:`./preflight.sh full && ./apply.sh full`(含 Loki/Alloy 日志栈,审计日志留存与查询见
   `runbooks/loki-logging.md`;presync 先跑 `./gateway-api-crds.sh` 按 experimental channel 装 Gateway API CRD)。
   再 apply 准入策略(preflight 强制校验两个 Binding 存在且 Deny):
   `kubectl apply -f admission/tenant-restrictions.yaml`
   (首次上线可先 [Audit] 观察一周再改回 [Deny],见该文件头注释;Audit 期间 preflight 该项会报缺)
4. **镜像仓库(Harbor)**:平台镜像与租户实例镜像的权威源,镜像引用一律 Harbor 全限定名。
   Harbor 侧:建平台项目(默认 `superdl`)、仅 Pull + List Repository 权限的机器人账户、
   (可选)Docker Hub 等代理缓存项目(设 public)。管理端「平台配置 · 镜像仓库」录入地址 / 项目 /
   机器人 / 自签 CA / 代理映射并「测试连接」。拉取凭据不落节点:首装按 `../app/secrets.example.yaml`
   手建 `superdl-registry-pull`(平台自身镜像的 `imagePullSecrets`),之后配置中心录入机器人后由 worker
   按指纹覆写同名 Secret 并托管到各租户 ns;server 节点的 `registries.yaml`(Spegel / 代理缓存 / CA)
   由 ansible 分发。镜像发布与凭据轮换 SOP:`runbooks/image-prewarm.md`。
5. **GPU 节点**:管理端「节点 · 新增」生成一键命令,节点上执行即完成打标加入
   (池标签 + GPU Operator 落点标签 / 驱动 / registries 全自动;无需再 SSH 回 server)。
   **先装 gpu-operator 再加节点**:operator 首次安装会给尚无 `nvidia.com/gpu.deploy.*` 标签的节点铺默认值,
   覆盖掉先打好的 `device-plugin=false`;顺序颠倒时重打一次标签即可。
   MIG 切分是唯一还要手工打的标签:
   ```bash
   kubectl label node <mig池节点> nvidia.com/mig.config=all-1g.10gb --overwrite
   ```
6. 验证:`runbooks/cluster-validation.md`。

`apply.sh` 是 `helmfile apply` 的薄包装,固定两个必带开关(漏一个 apply 会中途失败,报错不指向真正的原因):
`HELM_DIFF_USE_UPGRADE_DRY_RUN=true` 让 helm-diff 走服务端 dry-run(否则模板里的 `lookup` 恒空,
kata-deploy 的身份校验会误判成「无法确认上一次安装」而拒绝升级),`--skip-diff-on-install` 跳过首装时的 diff
(gpu-operator 首装时 ClusterPolicy CRD 还不存在)。单个 release:`./apply.sh light -l name=gpu-operator`。

## server token 与 agent token(轮换 + 快照托管)

- **职责分离**:server token(`/var/lib/rancher/<rke2|k3s>/server/node-token`)只允许留在 server 节点与
  本 runbook 约定的保险柜;agent token(server config 的 `agent-token` 值)录入平台库(AES-GCM 加密)
  并下发到 GPU 节点 agent config(0600 root):泄露 agent token 只能拉 agent,动不了 etcd。
- **agent token 轮换**:改全部 server 的 config → 滚动重启 server(逐一,等 etcd 健康再下一台)→
  更新平台「集群接入」配置。**在册节点不受影响**(join 后靠客户端证书双向认证,token 只在首次加入时使用);
  轮换窗口内新节点加入用新 token。
- **server token 轮换**:仅在怀疑泄露时做 —— RKE2/k3s 均支持 `token` 换新 + 滚动重启 server/agent;
  节点多时用「先加新 token 再撤旧 token」的两阶段法,参考发行版官方文档。
- **etcd 快照托管**:`secrets-encryption: true` 已开,但快照仍含全部集群状态与 token 材料:
  必须异地(对象存储/另一机房)加密保存,禁止只留 server 本机 `/var/lib/rancher`;
  访问快照的人等同于持有集群控制权,纳入审计。

## 路径 B:light(k3s 单机/小规模)

> **定位边界**:light 档控制面即单点(单 server,etcd 与业务同机),只适用于内网试点/演示/开发联调;
> **禁止作为公众生产对外开放** —— 公众生产一律走路径 A(3 server 堆叠 etcd + VIP)。
> 管理端「集群」页对 light 档常驻「轻量集群」黄条即是此提示。

1. **server(可兼跑业务)**:
   ```bash
   mkdir -p /etc/rancher/k3s && cp k3s/server-config.yaml /etc/rancher/k3s/config.yaml
   cp rke2/audit-policy.yaml /etc/rancher/k3s/audit-policy.yaml   # 与 rke2 同规,缺失则 apiserver 起不来
   curl -sfL https://rancher-mirror.rancher.cn/k3s/k3s-install.sh | INSTALL_K3S_MIRROR=cn sh -s - server
   ```
   (config 已含 `disable: traefik` 与 `embedded-registry: true`=Spegel)
2. **平台接入**:同 full 第 2 步(k3s 同样配 `agent-token`,见 k3s/server-config.yaml;
   禁止用 `/var/lib/rancher/k3s/server/node-token`;server 地址 `https://<ip>:6443`)。
3. **组件**:`./preflight.sh light && ./apply.sh light`(同样由 presync 先装 Gateway API CRD),
   再 apply 准入策略(preflight 强制校验两个 Binding 存在且 Deny):
   `kubectl apply -f admission/tenant-restrictions.yaml`

   light 与 full 装同一套组件,差异只在 `values/light/` 的覆盖:

   - HAMi 钉 k3s 版 scheduler 镜像(键是 `kubeScheduler.image.tag`,升 k3s 时必须同步改)
     + devicePlugin `runtimeClassName=nvidia`;chart 默认的 `nvidiaNodeSelector: {gpu: "on"}` 用 `null` 删键
     (helm 只在「用户 values 对 chart 默认值」这一层把 null 当删键)。
   - gpu-operator 关掉 toolkit(宿主 toolkit 由 node-join 装、k3s 自行探测生成 RuntimeClass nvidia;
     让 operator 再改一遍 k3s 的 containerd 配置会被下次启动覆盖回去)。
     `nvidia.com/gpu.count` 由 gpu-operator 自带的 GFD 提供,缺它 hami 池按 0 卡纳管。
   - kps / Loki 精简(盘紧可在 `environments/light.yaml` 关掉日志栈);
     开了 ServiceMonitor 的 release 必须 `needs: [monitoring/kube-prometheus-stack]`,否则新集群首装缺 CRD。
   - Envoy Gateway 控制面降到 1 副本并关掉 PDB(单副本配 `minAvailable: 1` 会让 `kubectl drain` 永远卡在这个 Pod 上)。
   - Cilium 不装(用 k3s 内置 flannel);acme-dns 不装(其 LoadBalancer 53 在 klipper-lb 上会占节点 hostPort 53
     并劫持节点自身 DNS),租户 Jupyter 泛域名证书由现成通配证书灌成 `superdl/superdl-jupyter-wildcard-tls`。
   - **TopoLVM 必开**(每个租户 Pod 都要挂实例盘;VG `superdl-nvme` 由 node-join.sh 建出);
     JuiceFS 可选(只有数据盘用),要数据盘时在 `environments/light.yaml` 打开。
4. **GPU 节点**:同 full 第 5 步。单机时 server 本机直接跑管理端生成的 node-join 命令:脚本检测到本机
   `k3s.service` 在运行即走 server 路径(不装 agent、不改 server config,池标签经 `k3s kubectl` 打到节点;
   首次装 toolkit 后会重启一次 k3s)。实例盘 VG `superdl-nvme` 不由 node-join 建时(令牌未登记 NVMe),
   须在 `./apply.sh light` 之前手工建好(空盘 `pvcreate`/`vgcreate`,或 loop 文件兜底),否则 TopoLVM lvmd 起不来。
5. 能力边界:组件面没有阉割(kata / mig 池同样可用),但档位可用性看的是**池里有没有 Ready 节点** ——
   单机只有一个池标签,选了 hami 就没有 kata/mig 池,专用整卡与共享·标准的 SKU 上架会被上架硬校验拦下。
   纯 CPU 规格是例外:挂 hami 池即可在这台机上卖,不需要单独的 cpu 池节点。
   管理端「集群」页常驻「轻量集群」黄条与组件体检(修复命令按实测发行版给出档位)。
