# 镜像缓存与预热 Runbook

三层结构:

1. **Spegel P2P**(RKE2 `embedded-registry: true` + 全节点 `registries.yaml`)——任一节点已缓存的镜像,其余节点内网互拉。
2. **托管镜像仓**(阿里云 ACR 企业版优先,Harbor 备选)——`registry.superdl.local` 经节点 mirror 解析到托管仓 https,平台镜像权威源;集群内自建 registry 已退役(清单已从仓库删除)。
3. **平台预热**(管理端「镜像与预热」页 + worker 巡检)——每镜像×每节点拉取 Job,覆盖率实时可见。

## 托管仓迁移(一次性)

1. **开通托管仓**:ACR 企业版实例(选与集群同地域/专网可达)或 Harbor(helm 部署,自带 TLS)。
   建独立拉取凭据(ACR 访问凭据 / Harbor 机器人账户,仅 pull 权限)。
2. **配置 mirror**:`rke2/registries.yaml` 的 `CHANGE_ME_REGISTRY_HOST/USERNAME/PASSWORD`
   替换为真实值 → 分发全节点(管理端平台配置 `node_registries_yaml` 同步更新,节点侧由 node-join.sh 落位)
   → 滚动重启 agent。镜像引用主机名固定为 `registry.superdl.local`(逻辑名),换仓不改引用。
3. **验证**:任一节点 `crictl pull registry.superdl.local/pytorch:2.9.0-cu128` 成功;
   管理端预热页覆盖率正常;`./preflight.sh <env>` 托管仓段全绿。

## 平台镜像发布 SOP(托管仓)

```bash
# 1. 运维机 push(托管仓 https,凭据仅 push 权限子账号)
skopeo copy --dest-creds "<托管仓凭据>" \
  docker://<上游镜像> docker://<托管仓地址>/pytorch:2.9.0-cu128
# 2. 管理端「镜像与预热」新建条目,image_ref 填 registry.superdl.local/pytorch:2.9.0-cu128
# 3. 等巡检铺开(≤60s 发现节点),页面看每节点覆盖率;失败行有错误原因,可一键重试
```

规则:

- **镜像一律钉版本 tag,禁止 latest**:Spegel 不对 latest 做 P2P。
- image_ref 主机名统一 `registry.superdl.local`(逻辑名,节点侧 mirror 解析到托管仓,无需 DNS)。

## 灾备与容量

- 托管仓自带冗余与 GC;实例镜像全部可重 push,无需额外备份。
- kubelet 磁盘压力会 GC 节点镜像缓存 → 平台巡检按 `prewarm_recheck_hours`(默认 24h)复检并自动重拉,覆盖率短暂下降属预期。

## 安全边界

- 托管仓自带 TLS/认证/扫描;拉取凭据仅 pull 权限(ACR 独立访问凭据 / Harbor 机器人账户),按季度轮换。
- 租户实例无法触达平台镜像面:租户 Pod Egress NetworkPolicy 禁全部私网段
  (app/core/k8s/real.py PRIVATE_CIDRS);托管仓为公网/专网地址时,确认其不在租户 Egress 白名单内。
