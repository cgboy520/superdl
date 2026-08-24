# 镜像缓存与预热 Runbook

三层结构:

1. **Spegel P2P**(RKE2 `embedded-registry: true` + 全节点 `registries.yaml`)——任一节点已缓存的镜像,其余节点内网互拉。
2. **集群内 registry**(`registry/registry.yaml`,ClusterIP 5000)——`registry.superdl.local` 的真实落点,平台镜像权威源,htpasswd 认证。
3. **平台预热**(管理端「镜像与预热」页 + worker 巡检)——每镜像×每节点拉取 Job,覆盖率实时可见。

## 首次启用认证(或轮换口令)

```bash
# 1. 生成 htpasswd 行(任意有 htpasswd 的机器;-B = bcrypt,registry 只认 bcrypt)
htpasswd -nbB ops '<强口令>' > htpasswd
# 2. 凭据不落 git,手工建 Secret 后 apply(原占位口令 CHANGE_ME 已泄漏,禁止写回清单)
kubectl -n registry create secret generic registry-htpasswd --from-file=htpasswd && rm -f htpasswd
kubectl apply -f ../registry/registry.yaml
# 3. 节点侧:registries.yaml 去掉 configs 块注释并填同口令(模板见 ../rke2/registries.yaml),
#    重启 rke2-agent/k3s-agent;存量节点走 ansible 分发,新节点模板见该文件末尾说明
# 4. 轮换口令:重建 Secret → rollout restart registry → 同步更新全部节点 configs.auth
```

滚动顺序铁律:**先铺节点凭据,后开仓库认证**(反向则节点 pull 全断)。

## 平台镜像发布 SOP

```bash
# 1. 运维机 push(ClusterIP 只在集群内可达,先经 kubeconfig 端口转发;skopeo 免本地 docker daemon)
kubectl -n registry port-forward svc/registry 5000:5000 &
skopeo copy --dest-tls-verify=false --dest-creds ops:'<强口令>' \
  docker://<上游镜像> docker://127.0.0.1:5000/pytorch:2.9.0-cu128
# 2. 管理端「镜像与预热」新建条目,image_ref 填 registry.superdl.local/pytorch:2.9.0-cu128
# 3. 等巡检铺开(≤60s 发现节点),页面看每节点覆盖率;失败行有错误原因,可一键重试
```

规则:

- **镜像一律钉版本 tag,禁止 latest**:Spegel 不对 latest 做 P2P。
- image_ref 主机名统一 `registry.superdl.local`(节点侧 registries.yaml 已 mirror 到 registry ClusterIP,无需 DNS)。

## 部署与验证

```bash
kubectl apply -f ../registry/registry.yaml   # 先手工建 registry-htpasswd Secret;ipBlock 示例段按真实网段启用
# 节点侧:确认 /etc/rancher/<rke2|k3s>/registries.yaml 已分发(endpoint 为 registry ClusterIP;存量机器走 ansible,新节点由一键加入脚本落位)
# 验证 P2P:node-A crictl pull 某钉版本镜像 → node-B 拉同镜像应秒级(走内网 5001/9345)
kubectl -n registry port-forward svc/registry 5000:5000 &
curl -u ops:'<强口令>' http://127.0.0.1:5000/v2/_catalog          # 仓库存活与内容(匿名 401 为预期)
```

## 灾备与容量

- registry 数据在 TopoLVM 本地卷(500Gi,占 GPU 节点 NVMe;迁移取舍见 registry.yaml 的 PVC 注释),
  不做跨节点冗余,丢了全量重 push。
- 空间回收:管理端删除镜像条目只删平台目录;registry 侧 API DELETE 已关
  (REGISTRY_STORAGE_DELETE_ENABLED=false),回收进 Pod 执行
  `registry garbage-collect /etc/registry/config.yml -m`(删未引用 blob 与 untagged manifest,
  不受 API 开关影响),或直接重建 PVC 后重 push。
- kubelet 磁盘压力会 GC 节点镜像缓存 → 平台巡检按 `prewarm_recheck_hours`(默认 24h)复检并自动重拉,覆盖率短暂下降属预期。

## 安全边界

- htpasswd 认证(匿名 push/pull 一律 401)+ registry namespace 默认拒绝 NetworkPolicy
  (仅放行节点/运维网段);light 档 k3s 默认自带 netpol 控制器(内嵌 kube-router)
  会执行 NetworkPolicy,机房防火墙作为第二道边界。
- registry 收编为 ClusterIP(P0-07),仅集群内可达;机房防火墙不得把集群网段对外暴露;
  TLS 升级路径见 registry.yaml 末尾 Service 注释,未上 TLS 前口令内网明文传输,按季度轮换。
- 租户实例无法触达 registry:租户 Pod Egress NetworkPolicy 禁全部私网段
  (app/core/k8s/real.py PRIVATE_CIDRS),ClusterIP 段与 Pod 网段均在其内。
