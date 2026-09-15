# ansible 初始控制面装机

只做 server 节点(HA 时含后续两台)的 rke2/k3s server 安装。GPU 节点不走 ansible,用管理端「添加节点」生成的一键命令(`../node-join/README.md`;脚本本体 `apps/api/app/modules/nodes/assets/node-join.sh`)。

## OS 基线

- **Ubuntu Server 22.04 / 24.04 LTS**,x86_64 或 aarch64。
- 目标机:root 或 sudo(`become: true`)、python3、ssh 可达、可出公网(从 `rancher-mirror.rancher.cn` 下载安装器)。

## inventory 分组

见 `inventory.ini.example`:只有 `[servers]`(控制面;多 server 逐台列出)。

## 用法

在本目录执行,先把 `inventory.ini` 中的地址换成目标 server。两条 playbook 命令二选一:默认 RKE2(full),`cluster_distro=k3s` 为 light。
控制面参数放在不入 git 的 `group_vars/servers.yml`,敏感值不要放命令行:

- RKE2 快照:`etcd_snapshot_bucket`、`etcd_s3_region`、`etcd_s3_endpoint`、`etcd_s3_access_key`、`etcd_s3_secret_key`。
- `agent_token`:全体 server 使用同一份独立随机凭据(至少 32 字符),录入管理端「平台配置 · 集群接入」,禁止使用 server node-token。
- HA:`api_vip` 与 `server_ips`(奇数台 ≥3);可选 `server_hostnames` 一并加入证书 SAN。
- 私有 Harbor CA:`harbor_ca_pem` 填 PEM 全文,公信证书留空;同时在 `../cluster/rke2/registries.yaml` 配置对应 Harbor 的 `configs` → 主机名 → `tls.ca_file`,指向目标机 `/etc/rancher/<distro>/harbor-ca.crt`。拉取凭据按 `../cluster/README.md`「镜像仓库」托管为 Secret。

```bash
cp inventory.ini.example inventory.ini
ansible-playbook -i inventory.ini site.yml
ansible-playbook -i inventory.ini site.yml -e cluster_distro=k3s
```

## 行为约定

- 顺序:kubelet 配置 drop-in(`../cluster/rke2/kubelet-superdl.conf`,podPidsLimit)+ audit-policy.yaml + registries.yaml(+ 非空时的 harbor-ca.crt)→ server config(仓库模板渲染,占位符无残留才落盘,0600)→ 安装 rke2/k3s server(安装器先落盘、sha256 校验、再执行;幂等)→ enable+start → 等 kube-apiserver `/readyz` 就绪 → 给控制面节点打落点标签 `node-restriction.kubernetes.io/superdl-infra=true`(`--overwrite`,幂等)。config 变更才 `restart-server`。
- 落点标签必须在这里用管理凭据打,不能用发行版的 `node-label`;节点按 `node-role.kubernetes.io/control-plane` 角色选,不按主机名。口径见 `../cluster/README.md`「平台组件落点标签」。
- 含凭据的 task 一律 `no_log: true`(server config 渲染、tls-san 追加、落盘);报错不带内容,排障看目标机 `/etc/rancher/<distro>/config.yaml`。值一律经 `| to_json` 注入,不手工拼引号。
- HA 参数成对:`api_vip` 与 `server_ips` 必须同时给且奇数台 ≥3,否则 playbook 断言失败;单 server 集群 `api_vip` 留空。
- 装完按 `../cluster/README.md` 路径 A/B 继续(平台接入 → `preflight.sh` → `apply.sh`)。
- 驱动版本由平台配置 `node_driver_version`(管理端·平台配置)下发,node-join.sh 按下发版本安装;本目录不持有它。
