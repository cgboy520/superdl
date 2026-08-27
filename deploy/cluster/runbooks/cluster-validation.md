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
- [ ] **先装 gpu-operator 再加节点**:operator 首次安装时会给尚无 `nvidia.com/gpu.deploy.*`
      标签的节点铺一套默认值(container 负载 → device-plugin=true),把先打好的
      `device-plugin=false` 覆盖掉(单机 light 上 server 节点先于组件装机时实测撞到)。
      顺序颠倒时重打一次即可,之后 operator 不再改动已有值
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

- [ ] `superdl.io/gpu-model` nodeSelector 在 kata / mig / hami 三个池真实命中(含混布池)
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

## J. 北向入口(Gateway API + Envoy Gateway)

- [ ] `kubectl get crd gateways.gateway.networking.k8s.io -o jsonpath='{.metadata.annotations}'`:
      `gateway.networking.k8s.io/channel` = **experimental**、`bundle-version` = **v1.6.1**。不符即停手 ——
      channel 事后换不回去(safe-upgrades 策略拒绝 standard→experimental),唯一出路是删净 CRD 重装,
      而那会连带删掉集群内全部 Gateway/HTTPRoute(平台三域名 + 全部租户 Jupyter 入口)。`preflight.sh` 同款检查
- [ ] `kubectl -n superdl get gateway superdl -o yaml`:`Programmed=True`,5 个 listener
      (`http` / `api-https` / `console-https` / `admin-https` / `app-https`)各自 `Programmed=True`,
      `attachedRoutes` 与预期条数一致。这也是管理端「集群」页「实例入口(网关)」那一格的判据
- [ ] **策略真的挂上了**:`kubectl -n superdl describe securitypolicy superdl-admin-allowlist` /
      `backendtrafficpolicy superdl-api-ratelimit` / `clienttrafficpolicy superdl-gateway`,
      三者 `status.ancestors[].conditions` 均 `Accepted=True`。listener 的 `sectionName` 写错**不报错**、
      apply 照样成功,只是策略静默失效(白名单没了、限流没了),线上看不出异常 —— 这里是唯一线索
- [ ] 三个平台域各 `curl -I https://<域>` 证书链正确;`curl -I http://<域>` 返回 301
- [ ] **源 IP 真的传到了 Envoy**:白名单网段外的机器访问 `admin.<域>` 应 403,网段内正常。失败先查
      `kubectl -n superdl get envoyproxy superdl-proxy -o jsonpath='{.spec.provider.kubernetes.envoyService.externalTrafficPolicy}'`
      是否仍是 `Local` —— 改成 `Cluster` 后 Envoy 看到的源 IP 全变成节点 IP,白名单与每 IP 限流同时失效且不报错
- [ ] 每源 IP 限流生效:同一客户端 `for i in $(seq 30); do curl -s -o /dev/null -w '%{http_code} ' https://<api域>/readyz; done`
      出现 429;**换第二台机器同时打不受影响**(`sourceCIDR.type` 写成 `Exact` 时第二台会被一起限死)。
      注意本地限流是每 Envoy 实例计数,2 副本时全局上限约为配置值 × 副本数
- [ ] **Jupyter 长连接熬过 5 分钟**:开一个实例的 JupyterLab,跑一段 >6 分钟无输出的 cell,期间不操作页面,
      内核不得断连。`streamIdleTimeout` 未配时 EG 默认 5 分钟掐 WebSocket/SSE,症状(「用着用着内核断了 /
      页面反复重连」)极像 token 过期或网络抖动,不实测就发现不了
- [ ] 租户路由跨 ns 挂载:`kubectl -n tenant-<uuid> get httproute <实例uuid> -o yaml` 的
      `status.parents[].conditions` 为 `Accepted=True`(不是 `NotAllowedByListeners`);删实例后该路由随之消失
      (`kubectl get httproute -A -l superdl.io/managed=true` 无孤儿)
- [ ] **路由规模与内存**:按目标单机实例数造出等量 HTTPRoute,记录 Envoy 数据面与 envoy-gateway 控制面 RSS,
      据此定 `EnvoyProxy` 的 memory limit 或单机实例数硬上限。实测记录于此:____
      (一实例一条路由,几百上千条是常态;这里 OOMKill 掉的是全站入口,不是单个租户)
- [ ] 数据面滚动不断流:重启 `envoy-gateway-system` 下 EG 生成的 Envoy Deployment,期间外部 `/readyz` 轮询无 5xx
      (2 副本 + `envoyPDB.minAvailable: 1` + `shutdown.drainTimeout: 60s`)
- [ ] Envoy Pod 落在 infra 节点且未被准入策略拦下(`admission/tenant-restrictions.yaml` 的豁免名单含
      `envoy-gateway-system`)。漏改名单时数据面 Deployment 是 EG 动态生成的、仓库里改不到,
      现象只是「Gateway 一直不 Ready」,拒绝信息只在 EG 控制器日志里

## K. 发布检查单(每次上线)

- [ ] **CSP 与第三方 SDK 域核对**:staging 用真实 aliyun captcha provider 走通 注册/登录/找回密码 全链路(发码 → 弹窗验证 → 收码),浏览器控制台无 CSP 违规报告;若有新增域,先切 `Content-Security-Policy-Report-Only` 收敛清单再 enforce(见 `deploy/app/security-headers-web-csp.conf` 注释)
- [ ] admin 站响应头含 `X-Robots-Tag: noindex, nofollow`,web 站 CSP 含 `o.alicdn.com` 与 `*.captcha-open.aliyuncs.com`
- [ ] 资金库 PITR:托管 PG 书面确认已开(设 `SUPERDL_MANAGED_PG_PITR_ACK`)或 cnpg 档启用且预检全绿(见 `preflight.sh`)
