# ansible 初始控制面装机与宿主机加固

`site.yml` 只做 server 节点(HA 时含后续两台)的 rke2/k3s server 安装(k3s 时另装集群状态备份);`harden.yml` 对全部主机(servers + agents)做 sshd / 防火墙 / 账号 / sysctl 加固。GPU 节点入池不走 ansible,用管理端「添加节点」生成的一键命令(`../node-join/README.md`;脚本本体 `apps/api/app/modules/nodes/assets/node-join.sh`)。

## OS 基线

- **Debian 系**(Ubuntu Server 22.04 / 24.04 LTS 为验证基线,Debian 12 同族),x86_64 或 aarch64;playbook 全程 apt。
- 目标机:root 或 sudo(`become: true`)、python3、ssh 可达、可出公网:默认从官方源 `get.rke2.io` / `get.k3s.io` 下载安装器(`k8s_install_mirror: official`),中国大陆机房可 `-e k8s_install_mirror=cn` 改走 `rancher-mirror.rancher.cn`;其他取值 playbook 断言失败。

## inventory 分组

见 `inventory.ini.example`:`[servers]`(控制面;多 server 逐台列出)与 `[agents]`(GPU 节点,只被 `harden.yml` 使用)。

## site.yml

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
ansible-playbook -i inventory.ini site.yml -e k8s_install_mirror=cn   # mainland-China mirror
```

### 行为约定

- 顺序:kubelet 配置 drop-in(`../cluster/rke2/kubelet-superdl.conf`,podPidsLimit)+ audit-policy.yaml + registries.yaml(+ 非空时的 harbor-ca.crt)→ server config(仓库模板渲染,占位符无残留才落盘,0600)→ 安装 rke2/k3s server(安装器由 `get_url` 按 `checksum: sha256:…` 校验后才落到 root 私有目录 `/root/.cache/superdl/`,`force: true` 覆盖旧文件,再执行;幂等)→ enable+start → 等 kube-apiserver `/readyz` 就绪 → 给控制面节点打落点标签 `node-restriction.kubernetes.io/superdl-infra=true`(`--overwrite`,幂等)→ k3s 时装集群状态备份。config 变更才 `restart-server`。
- 安装器 sha256 在 `site.yml` 的 `rke2_installer_sha256_official` / `_cn` 与 `k3s_installer_sha256_official` / `_cn`,按 `k8s_install_mirror` 选用;与 `node-join.sh` 内置 pin 同值(bats 交叉校验),上游或镜像站更新安装器时两处同步改。
- 落点标签必须在这里用管理凭据打,不能用发行版的 `node-label`;节点按 `node-role.kubernetes.io/control-plane` 角色选,不按主机名。口径见 `../cluster/README.md`「平台组件落点标签」。
- 含凭据的 task 一律 `no_log: true`(server config 渲染、tls-san 追加、落盘);报错不带内容,排障看目标机 `/etc/rancher/<distro>/config.yaml`。值一律经 `| to_json` 注入,不手工拼引号。
- HA 参数成对:`api_vip` 与 `server_ips` 必须同时给且奇数台 ≥3,否则 playbook 断言失败;单 server 集群 `api_vip` 留空。
- k3s 集群状态备份:`../cluster/k3s/state-backup.sh` 装为 `/usr/local/sbin/superdl-k3s-state-backup`(0700 root),cron `/etc/cron.d/superdl-k3s-state-backup` 每 6 小时(`23 */6 * * *`);依赖包 `sqlite3 gnupg rsync`;镜像机、口令与 ssh 密钥沿用 `/etc/superdl/pg/backup.env`、`backup-passphrase`、`backup-ssh-key`(见 `../pg/README.md`),这三份文件不在 server 上时 cron 报错。`k3s/server-config.yaml` 的 `cluster-init: true` 让已有 SQLite 单 server 在 config 变更触发的重启时迁入内嵌 etcd:先手动跑一次 `superdl-k3s-state-backup` 再执行。恢复见 `../cluster/README.md`「集群状态备份与恢复」。
- 装完按 `../cluster/README.md` 路径 A/B 继续(平台接入 → `preflight.sh` → `apply.sh`)。
- 驱动版本由平台配置 `node_driver_version`(管理端·平台配置)下发,node-join.sh 按下发版本安装;本目录不持有它。

## harden.yml

在 `site.yml`(server)与 node-join(GPU 节点)之后跑,对 `[servers]` + `[agents]` 全部主机,可反复执行:

```bash
ansible-playbook -i inventory.ini harden.yml -e '{"cluster_cidrs": ["10.0.0.0/24"]}'
```

做的事:

- sshd drop-in `/etc/ssh/sshd_config.d/10-superdl.conf`:`PasswordAuthentication no`、`KbdInteractiveAuthentication no`、`ChallengeResponseAuthentication no`、`PermitEmptyPasswords no`、`PermitRootLogin prohibit-password`、`UsePAM yes`、`MaxAuthTries 3`、`LoginGraceTime 20`、`X11Forwarding no`;`ssh_allow_users` 非空时加 `AllowUsers`。TCP 转发保留(运维 port-forward / 隧道)。`10-` 前缀让它排在 Ubuntu cloud-init 的 `50-cloud-init.conf`(`PasswordAuthentication yes`)之前。落盘后 `sshd -t`,不过则撤回 drop-in 并中止;通过才 reload ssh。
- **锁死前置**:关口令登录前断言目标机至少有一条可用公钥(`ssh_allow_users` 非空只数这些用户,否则数 root 与 uid ≥ 1000 用户的 `authorized_keys`),没有则失败不改任何东西。`ssh_allow_users` 里不含当前连接用户时,跑完你的下一次连接会被拒。
- `remove_users`(默认空,**opt-in**):`state: absent`、保留 home。确认 `test` 账号没有进程 / cron / sudoers 依赖后设 `remove_users: ["test"]`;含当前连接用户时断言失败。
- nftables(`firewall_enabled: true`,可按主机 host_vars 关):`/etc/nftables.conf` 只声明 `table inet superdl`,`input` 默认 drop:放行 lo、established/related、ICMP/ICMPv6、DHCP 客户端、SSH(`ssh_allow_cidrs`,默认 `0.0.0.0/0`,**按办公网 / 跳板机 / VPN 网段收窄**)、`pod_cidr` / `service_cidr` 来源、`cluster_cidrs` 来源的集群端口(`fw_cluster_tcp_ports` / `fw_cluster_udp_ports`,见 playbook 顶部注释表)、NodePort 30000-32767 对任意来源、`[servers]` 另开 80/443。模板落盘前 `nft -c -f` 预检,通过才 `nft -f` 生效;不 `flush ruleset`,并把 `nftables.service` 的 `ExecStop` 改成只删本表(发行版默认 `flush ruleset` 会清掉 Docker / Cilium / kubelet 的 iptables-nft 规则)。`ufw` 须保持 inactive。`ssh_port` 只改防火墙放行,不改 sshd 端口。
- sysctl `/etc/sysctl.d/60-superdl-harden.conf`:`kernel.dmesg_restrict=1`、`fs.protected_regular=2`、`fs.protected_fifos=2`、`fs.protected_hardlinks=1`、`fs.protected_symlinks=1`;不碰 `rp_filter`(Cilium 自管)。
- 不装 fail2ban:口令登录已关,SSH 来源由 `ssh_allow_cidrs` 收窄。

变量放 `group_vars/all.yml`(不入 git):`cluster_cidrs`(必填)、`ssh_allow_cidrs`、`ssh_allow_users`、`remove_users`、`firewall_enabled`、`pod_cidr` / `service_cidr`(k3s 默认 `10.42.0.0/16` / `10.43.0.0/16`;rke2 也是同一默认值)、端口列表四个。

跑完核对:另开一个终端确认还能 ssh 进来;`nft list table inet superdl`;从另一节点 `kubectl get nodes` 全 Ready、租户 NodePort SSH 可连、Envoy 三域可达。
