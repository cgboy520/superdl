# 镜像缓存与预热 Runbook

三层结构:

1. **Spegel P2P**(RKE2 `embedded-registry: true` + 全节点 `registries.yaml` 的 `mirrors "*"`)——任一节点已缓存的镜像,其余节点内网互拉。
2. **Harbor**——平台镜像与租户实例镜像的权威源;镜像引用一律 Harbor 全限定名 `<host>/<项目>/<名>:<tag>`。
   接入参数(地址 / 项目 / 机器人账户与 Secret / 自签 CA / 代理缓存映射)在管理端「平台配置 · 镜像仓库」,保存后「测试连接」。
3. **平台预热**(管理端「镜像与预热」页 + worker 巡检)——每镜像 × 每节点拉取 Job,覆盖率实时可见。

## 拉取凭据(不落节点)

- 平台在建实例 Pod / 预热 Job 之前,按生效配置把机器人凭据写成 `superdl-registry-pull`(`kubernetes.io/dockerconfigjson`)
  托管到平台 ns 与该租户 ns(annotation 指纹相同跳过),Pod / Job 以 `imagePullSecrets` 引用;项目为 public 时不生成、不引用。
- 平台自身镜像(api / web / admin):首装按 `deploy/app/secrets.example.yaml` 手建同名 Secret;配置中心录入机器人后 worker 会按指纹覆写它。
- **轮换**:Harbor 生成新 Secret → 管理端「镜像仓库」保存 → 新建一台实例确认拉取成功 → 在 Harbor 撤销旧 Secret;全程不碰节点。
- 节点 `registries.yaml`(GPU 节点由 node-join.sh 按平台配置生成;server 节点由 ansible 分发 `deploy/cluster/rke2/registries.yaml`)
  只承担 Spegel / 代理缓存 mirror / 自签 CA,永远不含 auth。

## Harbor 侧一次性准备

1. 项目:`superdl`(平台镜像;私有)。可选代理缓存项目:`dockerhub`(上游 Docker Hub)、`ghcr`(上游 GHCR)等,设为 **public**
   (mirror 拉取不带凭据),并在「镜像仓库」的代理缓存映射里逐行填 `docker.io=dockerhub`、`ghcr.io=ghcr`。
2. 机器人账户:项目级 `robot$superdl+pull`,权限仅 Pull Repository + List Repository(平台探测与拉取都够用);
   CI 推送另建 `robot$superdl+push`(Push + Pull),只放 GitHub secrets,不进配置中心。
3. 证书:公信证书无需配置;自签/私有 CA 把 PEM 粘进「镜像仓库 · CA 证书」(新节点自动落 `harbor-ca.crt`),
   server 节点经 ansible `harbor_ca_pem` 变量落盘。

## 平台镜像发布 SOP

```bash
# 1. 运维机 push(push 权限机器人)
docker login harbor.<域> -u 'robot$superdl+push'
skopeo copy --dest-creds 'robot$superdl+push:<secret>' \
  docker://<上游镜像> docker://harbor.<域>/superdl/pytorch:2.13.0-cu132-py313
# 2. 管理端「镜像与预热」新建条目:image_ref 默认前缀已按配置填好,补 pytorch:2.13.0-cu132-py313
# 3. 等巡检铺开(≤60s 发现节点),页面看每节点覆盖率;失败行有错误原因,可一键重试
```

规则:

- **镜像一律钉版本 tag,禁止 latest**:Spegel 不对 latest 做 P2P。
- 换 Harbor 域名:SQL 批量改 `images.image_ref`(实例快照是历史值,不改)+ 重新预热。

## 灾备与容量

- Harbor 自带冗余与 GC;实例镜像全部可重 push,无需额外备份。
- kubelet 磁盘压力会 GC 节点镜像缓存 → 平台巡检按 `prewarm_recheck_hours`(默认 24h)复检并自动重拉,覆盖率短暂下降属预期。

## 安全边界

- 拉取机器人仅 Pull + List 权限,按季度轮换(见上文,不碰节点);推送机器人只在 CI。
- 租户实例无法触达 Harbor 管理面:租户 Pod Egress NetworkPolicy 禁全部私网段(app/core/k8s/real.py PRIVATE_CIDRS);
  Harbor 为公网地址时,确认其不在租户 Egress 白名单内(镜像拉取由 kubelet 发起,不经租户 Pod 网络策略)。
