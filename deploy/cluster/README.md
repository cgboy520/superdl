# 集群部署:full / light 两条路径

选档:**full** = RKE2 多机生产,全档位(dedicated/mig/shared);
**light** = k3s 单机/小规模验证与轻量运营,仅共享档(hami 池)SKU。
发行版由平台探测(管理端「集群」页可见),业务侧无需声明。

chart 版本钉在 `helmfile.yaml.gotmpl`,K8s 版本钉在 `rke2/` 与 `k3s/` 的 server-config;升级走变更评审。

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

## 路径 A:full(RKE2 生产)

1. **server 节点**(装机基线见 `../ansible/`):
   ```bash
   curl -sfL https://rancher-mirror.rancher.cn/rke2/install.sh | INSTALL_RKE2_MIRROR=cn INSTALL_RKE2_CHANNEL=latest sh -
   cp rke2/audit-policy.yaml /etc/rancher/rke2/audit-policy.yaml   # 缺失则 apiserver 起不来
   cp rke2/server-config.yaml /etc/rancher/rke2/config.yaml
   systemctl enable --now rke2-server
   ```
   **控制面 HA(公众生产强制,单 server 禁止对外开放)**:3 台 server 堆叠 etcd
   (奇数台法定人数) + 控制面 VIP(kube-vip/keepalived/SLB 任一)。ansible 在
   `group_vars/servers.yml` 定义 `api_vip` + `server_ips`(奇数台 ≥3)即自动渲染
   tls-san(apiserver 证书覆盖 VIP 与全部 server IP,模板内注释块保持不动);
   手工部署照 `rke2/server-config.yaml` 头注释取消 tls-san 注释并填真实值。
   第 2/3 台 server 加入:config.yaml 与首台同一渲染产物,另放
   `rke2/server-join-config.yaml` 到 `/etc/rancher/rke2/config.yaml.d/50-join.yaml`
   (server 指 VIP:9345 + server token,首台严禁放)。VIP 就绪前可先单台上线,
   扩到 3 台前必须:tls-san 补齐 → 滚动重启全部 server → agent/cilium/netpol 统一切 VIP。
2. **平台接入**:管理端「平台配置 · 集群接入」录入 server 地址(HA 集群录
   `https://<VIP>:9345`,单 server 录该机 IP)与 **agent token**——
   即 server-config.yaml 里 `agent-token` 的值(首装前生成,见该文件注释);
   **禁止**录入 `/var/lib/rancher/rke2/server/node-token`(server token 能拉 server 进 etcd 环;
   轮换与托管见下文「server token 与 agent token」)。
   GPU 节点的 registries.yaml 由平台按「平台配置 · 镜像仓库」自动生成;server 节点由 ansible 分发 `rke2/registries.yaml`。
3. **组件**:`./preflight.sh full && helmfile -e full apply`(含 Loki/Alloy 日志栈,
   审计日志留存与查询见 `runbooks/loki-logging.md`);再 apply 准入策略
   (preflight 强制校验两个 Binding 存在且 Deny):
   `kubectl apply -f admission/tenant-restrictions.yaml`
   (首次上线可先 [Audit] 观察一周再改回 [Deny],见该文件头注释;Audit 期间 preflight 该项会报缺)
4. **镜像仓库(Harbor)**:平台镜像与租户实例镜像的权威源,镜像引用一律 Harbor 全限定名。
   Harbor 侧:建平台项目(默认 `superdl`)、仅 Pull + List Repository 权限的机器人账户、
   (可选)Docker Hub 等代理缓存项目(设 public)。管理端「平台配置 · 镜像仓库」录入地址 / 项目 /
   机器人 / 自签 CA / 代理映射并「测试连接」。拉取凭据不落节点:首装按 `../app/secrets.example.yaml`
   手建 `superdl-registry-pull`(平台自身镜像的 `imagePullSecrets`),之后配置中心录入机器人后由 worker
   按指纹覆写同名 Secret 并托管到各租户 ns;server 节点的 `registries.yaml`(Spegel / 代理缓存 / CA)
   由 ansible 分发。镜像发布与凭据轮换 SOP:`runbooks/image-prewarm.md`。
5. **Kata**(dedicated 档):`kata/` 下 kata-deploy(仅 kata 池节点)+
   `kubectl apply -f kata/kata-runtimeclass.yaml`
6. **GPU 节点**:管理端「节点 · 新增」生成一键命令,节点上执行即完成打标加入
   (池标签/驱动/registries 全自动;无需再 SSH 回 server)。加入后按池补 GPU Operator
   工作负载标签(组件落点由节点标签决定,values 里的 nodeSelector 不生效;
   契约全文见 `values/gpu-operator.yaml` 头注释):
   ```bash
   kubectl label node <kata池节点> nvidia.com/gpu.workload.config=vm-passthrough
   kubectl label node <hami池节点> nvidia.com/gpu.deploy.device-plugin=false
   # mig 池无需标签;切分配置按需:kubectl label node <mig池节点> nvidia.com/mig.config=all-1g.10gb --overwrite
   ```
7. 验证:`runbooks/cluster-validation.md`。

## server token 与 agent token(轮换 + 快照托管)

- **职责分离**:server token(`/var/lib/rancher/<rke2|k3s>/server/node-token`)只允许留在
  server 节点与本 runbook 约定的保险柜;agent token(server config 的 `agent-token` 值)
  录入平台库(AES-GCM 加密)并下发到 GPU 节点 agent config(0600 root):泄露 agent token
  只能拉 agent,动不了 etcd。
- **agent token 轮换**:改全部 server 的 config → 滚动重启 server(逐一,等 etcd 健康再下一台)
  → 更新平台「集群接入」配置。**在册节点不受影响**(join 后靠客户端证书双向认证,token 只在
  首次加入时使用);轮换窗口内新节点加入用新 token。
- **server token 轮换**:仅在怀疑泄露时做——RKE2/k3s 均支持 `token` 换新 + 滚动重启
  server/agent;节点多时用「先加新 token 再撤旧 token」的两阶段法,参考发行版官方文档。
- **etcd 快照托管**:`secrets-encryption: true` 已开,但快照仍含全部集群状态与 token 材料:
  必须异地(对象存储/另一机房)加密保存,禁止只留 server 本机 `/var/lib/rancher`;
  访问快照的人等同于持有集群控制权,纳入审计。

## 路径 B:light(k3s 单机/小规模)

> **定位边界**:light 档控制面即单点(单 server,etcd 与业务同机),只适用于
> 内网试点/演示/开发联调;**禁止作为公众生产对外开放**——公众生产一律走路径 A
> (3 server 堆叠 etcd + VIP)。管理端「集群」页对 light 档常驻「轻量集群」黄条即是此提示。

1. **server(可兼跑业务)**:
   ```bash
   mkdir -p /etc/rancher/k3s && cp k3s/server-config.yaml /etc/rancher/k3s/config.yaml
   cp rke2/audit-policy.yaml /etc/rancher/k3s/audit-policy.yaml   # apiserver 审计策略,与 rke2 同规,缺失则起不来
   curl -sfL https://rancher-mirror.rancher.cn/k3s/k3s-install.sh | INSTALL_K3S_MIRROR=cn sh -s - server
   ```
   (config 已含 `disable: traefik` 与 `embedded-registry: true`=Spegel)
2. **平台接入**:同 full 第 2 步(k3s 同样配 `agent-token`,见 k3s/server-config.yaml;
   禁止用 `/var/lib/rancher/k3s/server/node-token`;server 地址 `https://<ip>:6443`)。
3. **组件**:`./preflight.sh light && helmfile -e light apply`;再 apply 准入策略
   (preflight 强制校验两个 Binding 存在且 Deny):
   `kubectl apply -f admission/tenant-restrictions.yaml`
   - light = HAMi(钉 k3s 版 scheduler 镜像 + devicePlugin runtimeClassName=nvidia,
     见 `values/light/hami-light.yaml`)+ kps 精简 + cert-manager + ingress-nginx
     + Loki/Alloy 日志栈(默认开,资源收紧见 `values/light/loki-light.yaml`;
     盘紧可在 `environments/light.yaml` 关);
     Cilium/gpu-operator 不装;**TopoLVM 必开**(每个租户 Pod 都要挂实例盘;
     VG `superdl-nvme` 由 node-join.sh 建出);JuiceFS 可选(只有数据盘用),
     要数据盘时在 `environments/light.yaml` 打开。
4. **GPU 节点**:同 full 第 6 步(单机时 server 本机跑 node-join 亦可)。
5. 能力边界:仅共享档 SKU;dedicated/mig 上架会被硬校验拦下;管理端「集群」页
   常驻「轻量集群」黄条与组件体检(含修复命令)。

