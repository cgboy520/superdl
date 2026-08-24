# ansible 装机基线

存量机器批量装机与**初始控制面**安装。新 GPU 节点加入首选管理端「添加节点」一键命令
(`../node-join/README.md`);本目录面向:第一台 server、存量 GPU 机的内核/驱动/存储基线。

## OS 基线

- **Ubuntu Server 22.04 / 24.04 LTS**(play 全部走 `apt` / `update-grub` / `update-initramfs`;
  其它发行版未验证)。x86_64;arm64 节点仅 toolkit 仓库随 apt `$(ARCH)` 自动适配,驱动/池未验证。
- 目标机:root 或 sudo(`become: true`)、python3、ssh 可达、可出公网
  (NVIDIA 仓库 `nvidia.github.io` 与 rancher 镜像 `rancher-mirror.rancher.cn`)。
- 内核:发行版默认 GA 内核即可;IOMMU/userns 由 play 下发(kata 池 `intel_iommu=on iommu=pt`
  + 重启生效;全 GPU 池 `user.max_user_namespaces=65536`)。

## inventory 分组

见 `inventory.ini.example`:`[servers]` 控制面(首台 server;多 server 逐台跑),
`[kata]`/`[hami]`/`[mig]` 三个 GPU 池(分池铁律:永不混布),
`[gpu_nodes:children]` 聚合三池。主机变量 `nvme_devices`(JSON 数组)登记本机 NVMe。

## 用法

```bash
cp inventory.ini.example inventory.ini   # 填真实地址/设备
# 控制面敏感值(不入 git):group_vars/servers.yml 或 -e
#   etcd_snapshot_bucket / etcd_s3_region / etcd_s3_endpoint /
#   etcd_s3_access_key / etcd_s3_secret_key(rke2 快照上传凭据,来自密管)
#   agent_token(openssl rand -hex 32;全 server 同值,录入管理端「平台配置·集群接入」)
ansible-playbook -i inventory.ini site.yml                          # 全量
ansible-playbook -i inventory.ini site.yml --limit servers          # 仅控制面
ansible-playbook -i inventory.ini site.yml -e cluster_distro=k3s --limit servers  # light 档控制面
ansible-playbook -i inventory.ini site.yml -e rke2_server_ip=<server-ip> --limit gpu_nodes
```

## 行为约定

- **控制面 play(`servers`)**:audit-policy.yaml → server config(仓库模板渲染,
  占位符无残留才落盘,0600)→ 安装 rke2/k3s server(幂等,已装跳过)→ enable+start。
  config 变更才 `restart-server`。装完按 `../cluster/README.md` 路径 A/B 继续
  (平台接入 → helmfile → 准入策略)。
- **GPU play(`gpu_nodes`)**:nouveau 黑名单、(kata)IOMMU、userns、NVIDIA 驱动、
  nvidia-container-toolkit(替代手工步;装完 try-restart rke2 让 containerd 探测出
  nvidia RuntimeClass)、TopoLVM VG、registries.yaml。
- **重启语义**:nouveau/IOMMU 变更 notify `reboot-after-kernel-change`(handler 链末位,
  grub/initramfs 先跑完);reboot 模块等待节点回归(900s 上限)后,play 末尾验证
  `nvidia-smi -L` 与 `nvidia-container-runtime --version`(5×10s 重试)。无变更则不重启,
  验证照常跑(幂等基线断言)。
- 驱动版本单一事实源是平台配置 `node_driver_version`(管理端·平台配置);
  site.yml 的 `nvidia_driver_version` 是存量机侧镜像,升版本先改平台再同步这里。
