# ansible 初始控制面装机

只做一件事:第一台(HA 时含后续两台)server 的 rke2/k3s server 安装。GPU 节点不走 ansible,
一律用管理端「添加节点」生成的一键命令(`../node-join/README.md`;脚本本体
`apps/api/app/modules/nodes/assets/node-join.sh` 覆盖内核参数、NVIDIA 驱动与 container toolkit、
NVMe VG、registries.yaml 与 agent 加入,并有 bats 测试)。

## OS 基线

- **Ubuntu Server 22.04 / 24.04 LTS**(其它发行版未验证),x86_64。
- 目标机:root 或 sudo(`become: true`)、python3、ssh 可达、可出公网
  (从 rancher 镜像 `rancher-mirror.rancher.cn` 下载安装器)。

## inventory 分组

见 `inventory.ini.example`:只有 `[servers]`(控制面;多 server 逐台列出)。

## 用法

```bash
cp inventory.ini.example inventory.ini   # 填真实地址
# 控制面敏感值(不入 git):group_vars/servers.yml 或 -e
#   etcd_snapshot_bucket / etcd_s3_region / etcd_s3_endpoint /
#   etcd_s3_access_key / etcd_s3_secret_key(rke2 快照上传凭据,来自密管)
#   agent_token(openssl rand -hex 32;全 server 同值,录入管理端「平台配置·集群接入」)
#   api_vip / server_ips(HA:奇数台 ≥3 的 server + VIP,见 site.yml vars 注释)
#   harbor_ca_pem(Harbor 自签/私有 CA 全文;公信证书留空。拉取凭据不经 ansible,见 deploy/cluster/README.md「镜像仓库」)
ansible-playbook -i inventory.ini site.yml                          # rke2(full 档)
ansible-playbook -i inventory.ini site.yml -e cluster_distro=k3s    # k3s(light 档)
```

## 行为约定

- audit-policy.yaml → server config(仓库模板渲染,占位符无残留才落盘,0600)→
  安装 rke2/k3s server(安装器先落盘、sha256 校验、再执行;幂等,已装跳过)→ enable+start。
  config 变更才 `restart-server`。装完按 `../cluster/README.md` 路径 A/B 继续
  (平台接入 → helmfile → 准入策略)。
- 驱动版本单一事实源是平台配置 `node_driver_version`(管理端·平台配置),由 node-join.sh
  按服务端下发的版本安装;本目录不持有它的镜像值。
