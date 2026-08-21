# 集群部署:full / light 两条路径

版本锁定:RKE2/k3s **v1.36** · Cilium 1.20 · GPU Operator v26.3 ·
HAMi v2.9 · kube-prometheus-stack 88.x · JuiceFS CSI(1.4.x LTS)· TopoLVM 17.x · Kata 4.0。
所有 chart 钉版本,升级走变更评审。

选档:**full** = RKE2 多机生产,全档位(dedicated/mig/shared);
**light** = k3s 单机/小规模验证与轻量运营,仅共享档(hami 池)SKU。
发行版由平台探测(管理端「集群」页可见),业务侧无需声明。

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
2. **平台接入**:管理端「平台配置 · 集群接入」录入 server 地址与 join token
   (`cat /var/lib/rancher/rke2/server/node-token`);registries.yaml 平台自动生成,无需手改。
3. **组件**:`./preflight.sh full && helmfile -e full apply`
4. **内部镜像仓库**:`kubectl apply -f registry/registry.yaml`(SOP:`runbooks/image-prewarm.md`)
5. **Kata 4.0**(dedicated 档):`kata/` 下 kata-deploy(仅 kata 池节点)+
   `kubectl apply -f kata/kata-runtimeclass.yaml`
6. **GPU 节点**:管理端「节点 · 新增」生成一键命令,节点上执行即完成打标加入
   (池标签/驱动/registries 全自动;无需再 SSH 回 server)。
7. 验证:`runbooks/cluster-validation.md`。

## 路径 B:light(k3s 单机/小规模)

1. **server(可兼跑业务)**:
   ```bash
   mkdir -p /etc/rancher/k3s && cp k3s/server-config.yaml /etc/rancher/k3s/config.yaml
   curl -sfL https://rancher-mirror.rancher.cn/k3s/k3s-install.sh | INSTALL_K3S_MIRROR=cn sh -s - server
   ```
   (config 已含 `disable: traefik` 与 `embedded-registry: true`=Spegel)
2. **平台接入**:同 full 第 2 步(k3s token 在 `/var/lib/rancher/k3s/server/node-token`;
   server 地址 `https://<ip>:6443`)。
3. **组件**:`./preflight.sh light && helmfile -e light apply`
   - light = HAMi(钉 k3s 版 scheduler 镜像 + devicePlugin runtimeClassName=nvidia,
     见 `values/light/hami-light.yaml`)+ kps 精简 + cert-manager + ingress-nginx;
     Cilium/gpu-operator 不装;**TopoLVM 必开**(实例盘的强制依赖,每个租户 Pod 都要挂,
     关掉则一个实例都开不出来,需先由 ansible 基线建出 VG `superdl-nvme`);
     JuiceFS 才是可选项(只有数据盘用),要数据盘时在 `environments/light.yaml` 打开。
4. **GPU 节点**:同 full 第 6 步(单机时 server 本机跑 node-join 亦可)。
5. 能力边界:仅共享档 SKU;dedicated/mig 上架会被硬校验拦下;管理端「集群」页
   常驻「轻量集群」黄条与组件体检(含修复命令)。

