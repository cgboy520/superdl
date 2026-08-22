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
  --from-literal=metaurl=<redis://…> --from-literal=access-key=<…> --from-literal=secret-key=<…>
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
2. **平台接入**:管理端「平台配置 · 集群接入」录入 server 地址与 **agent token**——
   即 server-config.yaml 里 `agent-token` 的值(首装前生成,见该文件注释);
   **禁止**录入 `/var/lib/rancher/rke2/server/node-token`(server token 能拉 server 进 etcd 环;
   轮换与托管见下文「server token 与 agent token」)。
   registries.yaml 平台自动生成,无需手改。
3. **组件**:`./preflight.sh full && helmfile -e full apply`
4. **内部镜像仓库**:`kubectl apply -f registry/registry.yaml`(已开 htpasswd 认证,
   apply 前先替换 htpasswd 口令与 NetworkPolicy ipBlock 两处占位;
   节点 pull 凭据与滚动顺序 SOP:`runbooks/image-prewarm.md`)
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

1. **server(可兼跑业务)**:
   ```bash
   mkdir -p /etc/rancher/k3s && cp k3s/server-config.yaml /etc/rancher/k3s/config.yaml
   curl -sfL https://rancher-mirror.rancher.cn/k3s/k3s-install.sh | INSTALL_K3S_MIRROR=cn sh -s - server
   ```
   (config 已含 `disable: traefik` 与 `embedded-registry: true`=Spegel)
2. **平台接入**:同 full 第 2 步(k3s 同样配 `agent-token`,见 k3s/server-config.yaml;
   禁止用 `/var/lib/rancher/k3s/server/node-token`;server 地址 `https://<ip>:6443`)。
3. **组件**:`./preflight.sh light && helmfile -e light apply`
   - light = HAMi(钉 k3s 版 scheduler 镜像 + devicePlugin runtimeClassName=nvidia,
     见 `values/light/hami-light.yaml`)+ kps 精简 + cert-manager + ingress-nginx;
     Cilium/gpu-operator 不装;**TopoLVM 必开**(每个租户 Pod 都要挂实例盘;
     需先由 ansible 基线建出 VG `superdl-nvme`);JuiceFS 可选(只有数据盘用),
     要数据盘时在 `environments/light.yaml` 打开。
4. **GPU 节点**:同 full 第 6 步(单机时 server 本机跑 node-join 亦可)。
5. 能力边界:仅共享档 SKU;dedicated/mig 上架会被硬校验拦下;管理端「集群」页
   常驻「轻量集群」黄条与组件体检(含修复命令)。

