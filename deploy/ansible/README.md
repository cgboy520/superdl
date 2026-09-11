# ansible 初始控制面装机

只做 server 节点(HA 时含后续两台)的 rke2/k3s server 安装。GPU 节点不走 ansible,用管理端「添加节点」生成的一键命令(`../node-join/README.md`;脚本本体 `apps/api/app/modules/nodes/assets/node-join.sh`)。

## OS 基线

- **Ubuntu Server 22.04 / 24.04 LTS**,x86_64 或 aarch64。
- 目标机:root 或 sudo(`become: true`)、python3、ssh 可达、可出公网(从 `rancher-mirror.rancher.cn` 下载安装器)。

## inventory 分组

见 `inventory.ini.example`:只有 `[servers]`(控制面;多 server 逐台列出)。

## 用法

```bash
cp inventory.ini.example inventory.ini   # 填真实地址
# 控制面敏感值(不入 git):group_vars/servers.yml 或 -e
#   etcd_snapshot_bucket / etcd_s3_region / etcd_s3_endpoint /
#   etcd_s3_access_key / etcd_s3_secret_key(rke2 快照上传凭据)
#   agent_token(openssl rand -hex 32;全 server 同值,录入管理端「平台配置·集群接入」)
#   api_vip / server_ips(HA:奇数台 ≥3 的 server + VIP,见 site.yml vars 注释)
#   harbor_ca_pem(Harbor 自签/私有 CA 全文;公信证书留空。拉取凭据不经 ansible,见 deploy/cluster/README.md「镜像仓库」)
ansible-playbook -i inventory.ini site.yml                          # rke2(full 档)
ansible-playbook -i inventory.ini site.yml -e cluster_distro=k3s    # k3s(light 档)
```

## 行为约定

- 顺序:kubelet 配置 drop-in(`../cluster/rke2/kubelet-superdl.conf`,podPidsLimit)+ audit-policy.yaml + registries.yaml(+ 非空时的 harbor-ca.crt)→ server config(仓库模板渲染,占位符无残留才落盘,0600)→ 安装 rke2/k3s server(安装器先落盘、sha256 校验、再执行;幂等)→ enable+start → 等 kube-apiserver `/readyz` 就绪 → 给控制面节点打落点标签 `node-restriction.kubernetes.io/superdl-infra=true`(`--overwrite`,幂等)。config 变更才 `restart-server`。
- 落点标签必须在这里用管理凭据打,不能用发行版的 `node-label`;节点按 `node-role.kubernetes.io/control-plane` 角色选,不按主机名。口径见 `../cluster/README.md`「平台组件落点标签」。
- 含凭据的 task 一律 `no_log: true`(server config 渲染、tls-san 追加、落盘);报错不带内容,排障看目标机 `/etc/rancher/<distro>/config.yaml`。值一律经 `| to_json` 注入,不手工拼引号。
- HA 参数成对:`api_vip` 与 `server_ips` 必须同时给且奇数台 ≥3,否则 playbook 断言失败;单 server 集群 `api_vip` 留空。
- 装完按 `../cluster/README.md` 路径 A/B 继续(平台接入 → `preflight.sh` → `apply.sh`)。
- 驱动版本由平台配置 `node_driver_version`(管理端·平台配置)下发,node-join.sh 按下发版本安装;本目录不持有它。
