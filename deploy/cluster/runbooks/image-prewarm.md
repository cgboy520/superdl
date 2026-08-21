# 镜像缓存与预热 Runbook

架构三层:
1. **Spegel P2P**(RKE2 `embedded-registry: true` + 全节点 `registries.yaml`)——任一节点已缓存的镜像,其余节点内网互拉,出口带宽 O(节点数)→O(1)。
2. **集群内 registry**(`registry/registry.yaml`,NodePort 30500)——`registry.superdl.local` 的真实落点,平台镜像权威源。
3. **平台预热**(管理端「镜像与预热」页 + worker 巡检)——每镜像×每节点拉取 Job,覆盖率实时可见。

## 平台镜像发布 SOP

```bash
# 1. 运维机 push(内网直达任一节点 IP 的 NodePort;skopeo 免本地 docker daemon)
skopeo copy --dest-tls-verify=false \
  docker://<上游镜像> docker://<任一节点IP>:30500/pytorch:2.9.0-cu128
# 2. 管理端「镜像与预热」新建条目,image_ref 填 registry.superdl.local/pytorch:2.9.0-cu128
# 3. 等巡检铺开(≤60s 发现节点),页面看每节点覆盖率;失败行有错误原因,可一键重试
```

规则:
- **镜像一律钉版本 tag,禁止 latest**(Spegel 不对 latest 做 P2P;latest 也无法审计)。
- image_ref 主机名统一 `registry.superdl.local`(节点侧 registries.yaml 已 mirror 到 NodePort,无需 DNS)。

## 部署与验证

```bash
kubectl apply -f ../registry/registry.yaml
# 节点侧:确认 /etc/rancher/<rke2|k3s>/registries.yaml 已分发(平台按 Server 地址生成;存量机器走 ansible -e rke2_server_ip=…,新节点由一键加入脚本落位)
# 验证 P2P:node-A crictl pull 某钉版本镜像 → node-B 拉同镜像应秒级(走内网 5001/9345)
curl http://<server-ip>:30500/v2/_catalog     # 仓库存活与内容
```

## 灾备与容量

- registry 数据在 TopoLVM 本地卷(500Gi):**丢了可全量重 push**(权威副本在构建产物/上游),不做跨节点冗余。
- 空间回收:管理端删除镜像条目只删平台目录;registry 侧按需 `registry garbage-collect`(进 Pod 执行,见 distribution 文档),或直接重建 PVC 后重 push。
- kubelet 磁盘压力会 GC 节点镜像缓存 → 平台巡检按 `prewarm_recheck_hours`(默认 24h)复检并自动重拉,覆盖率短暂下降属预期。

## 安全边界

- NodePort 30500 仅限集群内网,机房防火墙不得对外暴露;仓库无认证(匿名 pull / 内网 push)。
- TLS、认证、镜像扫描随 Harbor 后置项(development-plan §8.2)一并评估,届时本仓库 skopeo 直迁退役。
