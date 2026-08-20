# 节点一键加入

脚本本体在 **`apps/api/app/modules/nodes/assets/node-join.sh`**(随 API 镜像打包,
`GET /api/v1/node-enroll/script` 直接下发,服务端仅替换 `__API_BASE__` 占位符)。
本目录只放测试与说明,避免两份拷贝漂移。

## 使用(运维视角)

1. 超管在管理端「平台配置 · 集群接入」录入一次:RKE2 Server 地址(`https://<server-ip>:9345`)、
   join token(server 节点 `cat /var/lib/rancher/rke2/server/node-token`)、RKE2 版本、驱动版本、
   registries.yaml 内容(镜像缓存 mirror,可留空)。
2. ops 在管理端「节点与 GPU · 添加节点」选池生成一次性命令(默认 24h 有效,只显示一次):
   ```bash
   curl -fsSL https://<api>/api/v1/node-enroll/script | sudo bash -s -- --token sdln_xxx
   ```
3. 新服务器上粘贴执行;管理端「待加入节点」实时看阶段进度。kata 池含一次自动重启
   (IOMMU/驱动生效,systemd oneshot 断点续跑)。失败可修复环境后重跑同一条命令(全幂等),
   或在管理端重新生成令牌。

安全模型:脚本本体零密钥;RKE2 server/join token 凭注册令牌 `POST /bootstrap` 换取(Bearer,
令牌 256-bit 只存哈希、过期/一次性、统一 404 防探测)。token 走命令行参数不进 URL。
join token 轮换:server 侧 `rke2 token rotate` 后在管理端更新一处即可。

## 测试

```bash
shellcheck apps/api/app/modules/nodes/assets/node-join.sh
bats deploy/node-join/tests          # PATH shim 伪造系统命令,不碰真实系统
```
覆盖:参数错误 / 全流程免重启 / 幂等重跑 / 重启断点(oneshot+token 0600) / kata GRUB / 重启循环保护。

实机验证(CI 不可覆盖,见 `docs/specs/WP23-node-join.md`):三池全流程、kata 重启续跑、真实 join。
