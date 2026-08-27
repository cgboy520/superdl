# 集群实机验证清单

CI 覆盖不到的检查项,每条为「做什么 + 通过判据」。

## A. 节点基线(每节点)

- [ ] `nvidia-smi` 正常,驱动版本与 GPU Operator 兼容矩阵一致
- [ ] `kubectl get node -o wide`:全部 Ready,K8s **v1.36.x**
- [ ] `kubectl get node -L superdl.io/pool`:池标签齐全,**kata 与 hami 无交集**
- [ ] `kubectl explain pod.spec.hostUsers` 存在;跑一个 `hostUsers: false` 测试 Pod,容器内 `readlink /proc/self/ns/user` 与宿主不同
- [ ] 内核 ≥6.3:`uname -r`

## B. Kata 整卡直通

1. IOMMU 分组检查(每台多卡节点):
   ```bash
   for g in /sys/kernel/iommu_groups/*/devices/*; do echo "$g"; done | grep -i nvidia
   ```
   判据:每张卡独立成组;多卡同组则整卡档不可在该节点售卖。
2. 8 卡节点同时开 2 个单卡 Kata 实例(两 Pod `runtimeClassName: kata-qemu` + `nvidia.com/gpu: 1`):
   判据:容器内 `nvidia-smi` 各只见 1 张卡,互相无感知。
3. 性能基准:
   ```bash
   python -c "import torch;print(torch.cuda.is_available())"
   # 跑 3 轮 resnet50 训练吞吐 vs 裸金属基线,记录差值
   ```
   判据:损耗 <5%。

## C. HAMi 共享池

- [ ] 同卡 2 实例(各 50% 算力 / 8G 显存):互相 `nvidia-smi` 只见配额显存
- [ ] 互扰压测:一实例满载,记录另一实例吞吐衰减 —— 超卖比率取值的数据依据
- [ ] 显存超限被拒:申请超过 gpumem 的分配应 OOM 在容器内,不影响邻居
- [ ] HAMi on k3s:`values/light/hami-light.yaml` 的 `kubeScheduler.image.tag` 与集群版本匹配、devicePlugin `runtimeClassName=nvidia` 生效、RuntimeClass `nvidia` 存在;渲染出的 hami-device-plugin DaemonSet nodeSelector 只有 `superdl.io/pool: hami`(chart 默认 `gpu: "on"` 已用 null 删除)
- [ ] k3s 上未装 HAMi 时下单共享档:报错明确指出缺件,不是超时或 500

## D. 存储

- [ ] JuiceFS:两 Pod 挂同一 subPath 读写一致;`juicefs bench` 记录基线;writeback 已关闭,fio 顺序写对比实测(开/关 writeback 各一轮)记录于此:____
- [ ] TopoLVM:PVC 创建/删除后 `lvs` 无残留;lvmd 容器 `/etc/lvm/lvm.conf` 已含 `issue_discards = 1`,大 LV(≥500Gi)`lvremove` 实测耗时记录于此:____
- [ ] 数据盘目录硬配额:建一块 1GB 测试盘,挂实例写超 1GB(`dd if=/dev/zero of=/root/data/fill bs=1M count=1200`)必须在配额处被拒(EDQUOT/No space);管理端死信页无 disk.quota 死信,Prometheus 查 `superdl_juicefs_quota_failed_total` 为 0;删盘后 `juicefs quota ls $METAURL` 无残留条目

## E. 监控与告警

- [ ] kube-prometheus-stack:DCGM 指标可查;导入 grafana.com **24450** 大盘
- [ ] 5 条 GPU 告警规则触发测试(人工触发 GPUHighTemperature 或用 amtool 注入)
- [ ] Alertmanager → 平台 webhook:`POST /api/v1/webhooks/alertmanager`(带 Bearer token)出现在管理端告警流
- [ ] 停 HAMi scheduler → 5 分钟内 HamiSchedulerDown 进管理端告警流
- [ ] `kubectl -n kube-system get svc hami-scheduler -o yaml`:monitor 端口名与端口(默认 31993/monitor)与 `values/kps.yaml` 的 additionalScrapeConfigs 一致;不一致改 values
- [ ] Prometheus 里查 `hami_container_device_utilization_ratio` / `hami_vgpu_memory_used_bytes`(HAMi 2.9 命名;容器维标签 `namespace`/`pod`/`container`):不一致只改 `apps/api/app/modules/metering/prom.py` 顶部常量与 HAMI_QUERIES;vGPUmonitor 容器不声明端口,kps 抓取按容器名 + podIP:9394 拼地址(`values/kps.yaml`)
- [ ] `DCGM_FI_DEV_GPU_UTIL` 的节点标签为小写 `hostname`(dcgm-exporter 4.x;3.x 为 `Hostname`)且值等于 K8s 节点名;不一致改 prom.py 的 DCGM_NODE_LABEL。注意 XID:DCGM 对不支持的卡型(如 CMP 系列)不产出 `DCGM_FI_DEV_XID_ERRORS`,节点页 XID 计数恒 0、XID 告警不触发
- [ ] 共享档实例跑负载:用户端详情页 GPU 利用率曲线出数,与 `nvidia-smi` 观测一致
- [ ] 管理端节点页热力格出真实 util/显存/温度;拔负载后 60s 内回落
- [ ] `helmfile -e light apply` 后 monitoring 命名空间全部 Pod Running,记录实测占用(目标 Prometheus RSS < 1Gi)
- [ ] Prometheus 停机(scale 0):用户端列表「监控暂不可用」、详情 503 文案、管理端热力格回落两态,全站无报错

## F. 节点一键加入

- [ ] kata / hami / mig 三池各跑通一次全流程,节点最终 Ready 且池标签正确
- [ ] kata 池重启断点:重启后 systemd oneshot 自动续跑至完成
- [ ] `registries.yaml` 已落到 `/etc/rancher/<rke2|k3s>/` 并生效(Harbor 自签时 `harbor-ca.crt` 同目录 0644,`configs.tls.ca_file` 指向它)
- [ ] 管理端 cordon/uncordon 落到真实节点(patch_node)
- [ ] server 侧 agent token(非 node-token)录入管理端的引导路径可走通
- [ ] GPU Operator 工作负载标签就位(node-join 随池标签自动打,契约见 `values/gpu-operator.yaml` 头注释):
      kata 池 `nvidia.com/gpu.workload.config=vm-passthrough`、hami 池 `nvidia.com/gpu.deploy.device-plugin=false`;
      kata 池注册 `nvidia.com/gpu` 的是 kata-sandbox-device-plugin,hami 池上无官方 device-plugin

## G. 镜像缓存与预热

- [ ] Spegel P2P:node-A `crictl pull` 某钉版本镜像后,node-B 拉同镜像秒级完成
- [ ] Harbor:管理端「平台配置 · 镜像仓库」测试连接绿;私有项目经引用 `superdl-registry-pull` 的 Pod(预热 Job / 租户实例)拉取成功,节点侧匿名 `crictl pull harbor.<域>/superdl/<镜像>` 被拒;public 代理缓存项目 `crictl pull docker.io/library/alpine:3.20` 经 mirror 命中 Harbor
- [ ] 轮换:配置中心保存新机器人 Secret → 新建实例 Pod 拉取成功 → 在 Harbor 撤销旧 Secret 后再建一台仍成功
- [ ] 预热 Job 在 kata/hami/mig 三池均可落(tolerations Exists)
- [ ] kubelet 镜像 GC 后,按 `prewarm_recheck_hours` 复检自动重拉
- [ ] 20GB 级镜像在 `activeDeadlineSeconds=1800` 内拉完

## H. 调度与 SKU

- [ ] `superdl.io/gpu-model` nodeSelector 在 dedicated/mig/shared 三档位真实命中(含混布池)
- [ ] 开启 `use-gputype` 后,以 raw 型号串注入的匹配语义符合预期
- [ ] 台账数据源优先级:型号 raw 走 nvidia-smi > GFD label > 存量;驱动/CUDA 版本反过来 GFD label 优先
      (`nvidia.com/cuda.{driver,runtime}-version.full`),装机快照只作无 GFD 时的回落

## I. 双档路径

- [ ] full / light 两条路径各按 `../README.md` 单页走通一次
- [ ] **light 档 gpu-operator(k3s)**:`toolkit.enabled=false` 下 operand 全部 Running,
      且 `kubectl get node -o json | jq '.items[].metadata.labels'` 里 `nvidia.com/gpu.count`
      与 `nvidia.com/cuda.driver-version.full` 仍在 —— 缺 `gpu.count` 时 hami 池按 0 卡纳管,
      在售 SKU 会当场无货(切换 GPU 栈后第一件要看的事)
- [ ] **light 档 kata-deploy(k3s)**:`kubectl get runtimeclass kata-qemu` 存在;kata 池有节点时
      `kata-deploy` DaemonSet Ready,节点上 `/var/lib/rancher/k3s/agent/etc/containerd/` 下有
      kata 的 drop-in,且真跑一个 `runtimeClassName: kata-qemu` 的 Pod(k3s 不自动探测 kata 运行时,
      containerd 配置全靠 chart 按 `k8sDistribution=k3s` 写对目录)
- [ ] 集群相关环境变量全部留空,仅经管理端「平台配置 · 集群接入」完成节点加入
- [ ] k3s kube-router NetworkPolicy 对租户 Egress 黑名单与 Cilium 等效
- [ ] RKE2 / k3s 的 cn 镜像源可用
- [ ] 集群能力探测(probe)所需 RBAC 在 RKE2 与 k3s 上均足够

## J. 发布检查单(每次上线)

- [ ] **CSP 与第三方 SDK 域核对**:staging 用真实 aliyun captcha provider 走通 注册/登录/找回密码 全链路(发码 → 弹窗验证 → 收码),浏览器控制台无 CSP 违规报告;若有新增域,先切 `Content-Security-Policy-Report-Only` 收敛清单再 enforce(见 `deploy/app/security-headers-web-csp.conf` 注释)
- [ ] admin 站响应头含 `X-Robots-Tag: noindex, nofollow`,web 站 CSP 含 `o.alicdn.com` 与 `*.captcha-open.aliyuncs.com`
- [ ] 资金库 PITR:托管 PG 书面确认已开(设 `SUPERDL_MANAGED_PG_PITR_ACK`)或 cnpg 档启用且预检全绿(见 `preflight.sh`)
