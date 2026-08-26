# 节点一键加入

脚本本体在 **`apps/api/app/modules/nodes/assets/node-join.sh`**(随 API 镜像打包,
`GET /api/v1/node-enroll/script` 直接下发,服务端仅替换 `__API_BASE__` 占位符)。
本目录只放测试与说明,不留第二份脚本拷贝。

## 使用(运维视角)

1. 超管在管理端「平台配置 · 集群接入」录入一次:server 地址(HA 集群填控制面 VIP:
   RKE2 `https://<vip>:9345`;k3s 单 server 填 `https://<server-ip>:6443`)、join token
   (专用 **agent token**:server config 的 `agent-token` 值;**禁止**填
   `/var/lib/rancher/<rke2|k3s>/server/node-token`——server token 能再拉 server 进 etcd 环,
   落到 GPU 节点等于交出控制面,见 deploy/cluster/README.md「server token 与 agent token」)、
   agent 版本、驱动版本;registries.yaml 由平台按「平台配置 · 镜像仓库」自动生成(Spegel + Harbor 代理缓存 + 自签 CA,不含凭据;高级覆盖可手填)。
2. ops 在管理端「节点与 GPU · 添加节点」选池生成一次性命令(默认 24h 有效,只显示一次):
   ```bash
   echo 'sdln_xxx' | sudo sh -c 'umask 077; cat > /run/superdl-join.token; curl -fsSL https://<api>/api/v1/node-enroll/script | bash -s -- --token-file /run/superdl-join.token; s=$?; rm -f /run/superdl-join.token; exit $s'
   ```
   token 经 stdin 落入 `/run` 下的 0600 文件(tmpfs,重启即消),脚本一律从文件读 token,
   不出现在任何进程的 argv 里,命令执行完即删。
3. 新服务器上粘贴执行;管理端「待加入节点」实时看阶段进度。kata 池含一次自动重启
   (IOMMU/驱动生效,systemd oneshot 断点续跑)。失败可修复环境后重跑同一条命令
   (断点续跑,已完成步骤跳过);节点加入完成后重跑直接退出,从头重装须
   `--force` + 管理端新签发的令牌。

安全模型:脚本本体零密钥;server 地址与 join token 凭注册令牌 `POST /bootstrap` 换取
(Bearer,令牌 256-bit 只存哈希、绝对过期、无效一律 404)。注册令牌**一次性**:
首次 bootstrap 即被服务端消费,换发窄权限 progress 令牌(仅可上报进度,不可再拉配置),
落 `/var/lib/superdl-node-join/token`(0600)供断点续跑,装机完成即连同 bootstrap.json
一起删除。k3s/rke2 安装器不裸 `curl|sh`:固定 URL 下载后校验脚本内置 sha256 pin 再执行;
管道执行时重启前从 API 重拉脚本本体,并校验 bootstrap 下发的脚本指纹(script_sha256)。
join token 轮换:server 侧 `rke2 token rotate` 后在管理端更新一处即可。

落盘权限:`umask 077` 前置(生成文件先窄后宽,无 0644→chmod 窗口),日志显式 0644。
卸载:`sudo bash node-join.sh --uninstall` 逆向拆除(agent、本脚本写入的 sysctl/GRUB/黑名单/
集群配置、状态目录),不碰 superdl-nvme VG 与驱动;节点清退仍需平台侧 `kubectl delete node`。

## 测试

```bash
shellcheck apps/api/app/modules/nodes/assets/node-join.sh
bats deploy/node-join/tests          # PATH shim 伪造系统命令,不碰真实系统
```

覆盖:参数错误 / 全流程免重启 / 令牌不进 argv 与完成后落盘清理 / 完成后重跑直退 /
--force 重装 / 断点续跑(令牌切换)/ 旧服务端无 progress_token 兼容 / 重启断点
(oneshot + token 0600)/ 管道执行重拉脚本指纹校验与不符中止 / 安装器 pin 不符拒执行 /
重启循环保护 / kata GRUB IOMMU / k3s 模式落位 / 安装源 cn 与 official /
NVMe 未登记不兜底 / loop 显式登记。
实机验证项见 `deploy/cluster/runbooks/cluster-validation.md`;节点域说明见 `docs/reference/nodes.md`。
