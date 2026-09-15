# 集群实机验证清单

CI 覆盖不到的检查项,每条为「做什么 + 通过判据」。

## A. 节点基线(每节点)

- [ ] `nvidia-smi` 正常,驱动版本与 GPU Operator 兼容矩阵一致
- [ ] `kubectl get node -o wide`:全部 Ready,K8s **v1.36.x**
- [ ] `kubectl get node -L node-restriction.kubernetes.io/superdl-pool`:池标签齐全,**kata 与 hami 无交集**;`-L superdl.io/pool` 一列全空(旧键已由平台摘除,迁移见 [node-pool-switch.md](./node-pool-switch.md)「池标签键迁移」)
- [ ] `kubectl explain pod.spec.hostUsers` 存在;跑一个 `hostUsers: false` 测试 Pod,容器内 `readlink /proc/self/ns/user` 与宿主不同
- [ ] 内核 ≥6.3:`uname -r`
- [ ] 平台组件落点标签只在控制面节点上:`kubectl get nodes -l node-restriction.kubernetes.io/superdl-infra=true` 至少一台,且**没有一台带 `node-restriction.kubernetes.io/superdl-pool`**;`preflight.sh` 同款正反两查。api / worker / 前端 / Envoy 的 2 副本按 hostname `DoNotSchedule` 分布:**infra 节点 ≥2 台才有冗余**(一台时两副本同机),两台时 `kubectl -n superdl get pods -o wide` 每个 Deployment 的两副本落在不同节点;拔掉一台 infra 节点后替补副本保持 Pending 直到节点恢复,这是预期信号
- [ ] **全局 Pod 兜底策略是 `Deny`**(`superdl-global-pod-guard`,`admission/tenant-restrictions.yaml`):`kubectl debug node/<node>` 默认落 `default` ns 会被拒,排障加 `--namespace kube-system`(豁免 ns)

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
   ```
   跑 3 轮 ResNet-50 训练,记录吞吐与同硬件裸金属基线的差值;判据:损耗 <5%。

## C. HAMi 共享池

- [ ] 同卡 2 实例(各 50% 算力 / 8G 显存):互相 `nvidia-smi` 只见配额显存
- [ ] 互扰压测:一实例满载,记录另一实例吞吐衰减(定超卖比率)
- [ ] 显存超限被拒:申请超过 gpumem 的分配应 OOM 在容器内,不影响邻居
- [ ] HAMi on k3s:`values/light/hami-light.yaml` 的 `kubeScheduler.image.tag` 与集群版本匹配、devicePlugin `runtimeClassName=nvidia` 生效、RuntimeClass `nvidia` 存在;渲染出的 hami-device-plugin DaemonSet nodeSelector 只有 `node-restriction.kubernetes.io/superdl-pool: hami`(chart 默认 `gpu: "on"` 已用 null 删除)
- [ ] k3s 上未装 HAMi 时下单共享档:报错明确指出缺件,不是超时或 500

## D. 存储

- [ ] CephFS:两 Pod(均 `hostUsers: false`)跨节点挂同一 PVC 读写一致;`ceph -s` HEALTH_OK;fio 顺序写基线记录于此:____
- [ ] TopoLVM:PVC 创建/删除后 `lvs` 无残留;lvmd 容器 `/etc/lvm/lvm.conf` 已含 `issue_discards = 1`,大 LV(≥500Gi)`lvremove` 实测耗时记录于此:____
- [ ] **数据盘 StorageClass `superdl-cephfs` 的 `reclaimPolicy` 为 `Delete`**(代码与文档按删盘即删子卷假设)。存量集群一次性迁移:① SC 字段不可改,`kubectl delete sc superdl-cephfs`(不影响已绑定的 PV/PVC)后 `./apply.sh <full|light> -l name=rook-ceph-cluster` 重建;② 存量 PV 各自带 `persistentVolumeReclaimPolicy`,逐只 `kubectl patch pv <pv> -p '{"spec":{"persistentVolumeReclaimPolicy":"Delete"}}'`(`kubectl get pv -o custom-columns=NAME:.metadata.name,SC:.spec.storageClassName,RECLAIM:.spec.persistentVolumeReclaimPolicy,PHASE:.status.phase | grep superdl-cephfs` 列全);③ `Released` 的 PV 是已删租户盘的残留,`kubectl delete pv <pv>` 让 CSI 删掉子卷。`preflight.sh` 对 SC 与 PV 各断言一次
- [ ] 数据盘硬配额:建一块 1GB 测试盘,挂实例写超 1GB(`dd if=/dev/zero of=/root/data/fill bs=1M count=1200`)必须被拒(No space);管理端死信页无 disk.provision 死信,Prometheus 查 `superdl_disk_provision_failed_total` 为 0;删盘后 `kubectl -n tenant-<id> get pvc` 无残留

## E. 监控与告警

- [ ] kube-prometheus-stack:DCGM 指标可查;导入 grafana.com **24450** 大盘
- [ ] `superdl.gpu` 规则组 7 条告警各触发一次(人工触发 GPUHighTemperature 或用 amtool 注入);停 dcgm-exporter 30 分钟 → `GpuTelemetryMissing` 进告警流
- [ ] **监控栈拿不到平台 Secret**:`kubectl auth can-i --as=system:serviceaccount:monitoring:alloy get secrets -n superdl` 与 operator / prometheus / loki 同款均 `no`;`kubectl -n superdl get servicemonitor,podmonitor` 为空,两个 monitor 在 `monitoring` ns(`08-monitoring.yaml`),Prometheus `up{namespace="superdl"}` 两个抓取池都在(Bearer 来自 `monitoring/superdl-metrics-token`);`preflight.sh`「监控栈 SA 不得读 Secret」全绿
- [ ] Alertmanager → 平台 webhook:`POST /api/v1/webhooks/alertmanager`(带 Bearer token)出现在管理端告警流
- [ ] 停 HAMi scheduler → 5 分钟内 HamiSchedulerDown 进管理端告警流
- [ ] `kubectl -n kube-system get svc hami-scheduler -o yaml`:存在名为 `monitor` 的端口(`values/kps.yaml` 的 additionalScrapeConfigs 按**端口名**保留目标);名字对不上改 values
- [ ] Prometheus 里查 `hami_container_device_utilization_ratio` / `hami_vgpu_memory_used_bytes`(容器维标签 `namespace`/`pod`/`container`):不一致只改 `apps/api/app/modules/metering/prom.py` 顶部常量与 HAMI_QUERIES;vGPUmonitor 容器不声明端口,kps 按容器名 + podIP:9394 抓取(`values/kps.yaml`)
- [ ] `DCGM_FI_DEV_GPU_UTIL` 的节点标签为小写 `hostname`(dcgm-exporter 4.x;3.x 为 `Hostname`)且值等于 K8s 节点名;不一致改 prom.py 的 DCGM_NODE_LABEL。DCGM 对不支持的卡型(如 CMP 系列)不产出 `DCGM_FI_DEV_XID_ERRORS`
- [ ] 共享档实例跑负载:用户端详情页 GPU 利用率曲线出数,与 `nvidia-smi` 观测一致
- [ ] 管理端节点页热力格出真实 util/显存/温度;拔负载后 60s 内回落
- [ ] `helmfile -e light apply` 后 monitoring 命名空间全部 Pod Running,记录实测占用(目标 Prometheus RSS < 1Gi)
- [ ] Prometheus 停机(scale 0):用户端列表「监控暂不可用」、详情 503 文案、管理端热力格回落两态,全站无报错

## F. 节点一键加入

- [ ] kata / hami / mig 三池各跑通一次全流程,节点最终 Ready 且池标签正确
- [ ] **切池(无节点侧动作)**:空节点经管理端 hami → kata 再切回,全程不登录节点、不重启;两次都核对池标签与 operand 标签整套收敛(旧池残留键已删)、组件落位正确、卡在 kata 侧绑到 `vfio-pci` 而切回后回到 `nvidia`、目标档位实例真能开机;步骤与核对清单见 [node-pool-switch.md](./node-pool-switch.md)
- [ ] kata 池重启断点:重启后 systemd oneshot 自动续跑至完成
- [ ] `registries.yaml` 已落到 `/etc/rancher/<rke2|k3s>/` 并生效(Harbor 自签时 `harbor-ca.crt` 同目录 0644,`configs.tls.ca_file` 指向它)
- [ ] 管理端 cordon/uncordon 落到真实节点(patch_node)
- [ ] server 侧 agent token(非 node-token)录入管理端的引导路径可走通
- [ ] 装机顺序:gpu-operator 先于节点加入(顺序颠倒时重打一次标签)
- [ ] GPU Operator 工作负载标签就位(由平台在入网对账时打,契约见 [node-pool-switch.md](./node-pool-switch.md)「核对」):kata 池 `nvidia.com/gpu.workload.config=vm-passthrough`、hami 池 `nvidia.com/gpu.deploy.device-plugin=false`;kata 池注册 `nvidia.com/gpu` 的是 kata-sandbox-device-plugin
- [ ] **GPU 可见性伪造防线**:① 应用层:管理端/ API 建服务型实例显式传 `NVIDIA_*` env 必须 422;② 准入层:`kubectl -n tenant-<uuid> apply` 一个带 `env: [{name: NVIDIA_VISIBLE_DEVICES, value: all}]` 的 Pod 必须被 `superdl-tenant-pod-baseline` 拒绝;③ 运行时纵深(测试集群验证 CDI 注入生效后才上生产):`values/gpu-operator.yaml` 的 toolkit 段加 `ACCEPT_NVIDIA_VISIBLE_DEVICES_ENVVAR_WHEN_UNPRIVILEGED=false`。验证矩阵:kata / mig / hami 三池各建一台实例,容器内 `nvidia-smi` 只见分配到的卡,hami 池显存超限仍在容器内被拒

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
- [ ] 台账数据源优先级:型号 raw 走 nvidia-smi > GFD label > 存量;驱动/CUDA 版本 GFD label 优先(`nvidia.com/cuda.{driver,runtime}-version.full`),装机快照只作回落

## I. 双档路径

- [ ] full / light 两条路径各按 `../README.md` 单页走通一次
- [ ] **light 档 gpu-operator(k3s)**:`toolkit.enabled=false` 下 operand 全部 Running,且 `kubectl get node -o json | jq '.items[].metadata.labels'` 里 `nvidia.com/gpu.count` 与 `nvidia.com/cuda.driver-version.full` 仍在
- [ ] **light 档 kata-deploy(k3s)**:`kubectl get runtimeclass kata-qemu` 存在;kata 池有节点时 `kata-deploy` DaemonSet Ready,节点上 `/var/lib/rancher/k3s/agent/etc/containerd/` 下有 kata 的 drop-in,且真跑一个 `runtimeClassName: kata-qemu` 的 Pod
- [ ] 集群相关环境变量全部留空,仅经管理端「平台配置 · 集群接入」完成节点加入
- [ ] light 档 Cilium NetworkPolicy 对租户 Egress 黑名单生效(与 full 档同一份策略)
- [ ] RKE2 / k3s 的 cn 镜像源可用
- [ ] 集群能力探测(probe)所需 RBAC 在 RKE2 与 k3s 上均足够

## J. 北向入口(Gateway API + Envoy Gateway)

- [ ] `kubectl get crd gateways.gateway.networking.k8s.io -o jsonpath='{.metadata.annotations}'`:`gateway.networking.k8s.io/channel` = **experimental**、`bundle-version` = **v1.6.1**。不符即停手(channel 事后换不回去);`preflight.sh` 同款检查
- [ ] `kubectl -n superdl get gateway superdl -o yaml`:`Programmed=True`,6 个 listener(`http` / `api-https` / `console-https` / `admin-https` / `app-https` / `svc-https`)各自 `Programmed=True`,`attachedRoutes` 与预期条数一致(管理端「集群」页「实例入口(网关)」同判据)
- [ ] **策略已挂上**:`kubectl -n superdl describe securitypolicy superdl-admin-allowlist` / `securitypolicy superdl-svc-extauth` / `backendtrafficpolicy superdl-api-ratelimit` / `backendtrafficpolicy superdl-api-webhooks` / `backendtrafficpolicy superdl-console-api-ratelimit` / `backendtrafficpolicy superdl-admin-ratelimit` / `backendtrafficpolicy superdl-svc-ratelimit` / `backendtrafficpolicy superdl-app-ratelimit` / `clienttrafficpolicy superdl-gateway`,九者 `status.ancestors[].conditions` 均 `Accepted=True`(`sectionName` 写错不报错,只在这里可见)
- [ ] **console 域的 `/api/v1` 走 Envoy 直达 API**:`curl -sI https://console.<域>/api/v1/catalog/skus` 响应头带 `x-request-id` 且 `superdl-web` 的 nginx 访问日志里没有这条;`curl -sI https://console.<域>/docs` 与 `/api/admin/v1/…` 为 404(`superdl-console-edge-deny`);同一客户端连打 console 域 `/api/v1` 30 次出现 429,管理端域同款(30/s)
- [ ] **前置层都登记了**:经真实公网链路(CDN → 前置反代)打 `/api/v1/…` 后,`audit_log.ip` 与 API 日志 `client_ip` 是用户真实出口 IP,不是 CDN / 反代地址;不对时核对 `ClientTrafficPolicy` 的 `numTrustedHops` 与 ConfigMap `FORWARDED_ALLOW_IPS` 是否列全每一跳
- [ ] 三个平台域各 `curl -I https://<域>` 证书链正确;`curl -I http://<域>` 返回 301
- [ ] **源 IP 传到了 Envoy**:白名单网段外的机器访问 `admin.<域>` 应 403,网段内正常。失败查 `kubectl -n superdl get envoyproxy superdl-proxy -o jsonpath='{.spec.provider.kubernetes.envoyService.externalTrafficPolicy}'` 是否仍是 `Local`
- [ ] 每源 IP 限流生效:同一客户端 `for i in $(seq 30); do curl -s -o /dev/null -w '%{http_code} ' https://<api域>/readyz; done` 出现 429;**换第二台机器同时打不受影响**。本地限流按 Envoy 实例计数,2 副本时全局上限约为配置值 × 副本数
- [ ] **Jupyter 长连接熬过 5 分钟**:开一个实例的 JupyterLab,跑一段 >6 分钟无输出的 cell,期间不操作页面,内核不得断连(`streamIdleTimeout` 未配时 EG 默认 5 分钟掐 WebSocket/SSE)
- [ ] 租户路由跨 ns 挂载:`kubectl -n tenant-<uuid> get httproute <实例uuid> -o yaml` 的 `status.parents[].conditions` 为 `Accepted=True`;删实例后该路由随之消失(`kubectl get httproute -A -l superdl.io/managed=true` 无孤儿)
- [ ] **路由规模与内存**:按目标单机实例数造出等量 HTTPRoute,记录 Envoy 数据面与 envoy-gateway 控制面 RSS,据此定 `EnvoyProxy` 的 memory limit 或单机实例数硬上限。实测记录于此:____
- [ ] 数据面滚动不断流:重启 `envoy-gateway-system` 下 EG 生成的 Envoy Deployment,期间外部 `/readyz` 轮询无 5xx(2 副本 + `envoyPDB.minAvailable: 1` + `shutdown.drainTimeout: 60s`)
- [ ] Envoy Pod 落在带 `node-restriction.kubernetes.io/superdl-infra=true` 的节点上,且未被准入策略拦下(`admission/tenant-restrictions.yaml` 的豁免名单含 `envoy-gateway-system`;拒绝信息只在 EG 控制器日志里)

### J-1. 服务型实例端点(`svc-https` listener)

- [ ] **服务端泛域名证书已签发**:`kubectl -n superdl get certificate superdl-svc-wildcard` 为 `Ready=True`。长期 False 查 `*.svc.<域>` 的 acme-dns 前置(新账户 + `_acme-challenge.svc.<域>` CNAME 委托 + `acme-dns-account` 的 acmedns.json 含 `svc.<域>` 键,见 `05-cert-manager.yaml` 与 `runbooks/acme-dns.md`)
- [ ] **合法 Key 通**:`curl -H 'Authorization: Bearer <明文 Key>' https://svc-<slug>.svc.<域>/<容器自己的路径>` 返回容器的真实响应;`-H 'X-API-Key: <明文 Key>'` 再打一遍也必须通过(只有 Bearer 通即 `headersToExtAuth` 漏了 `x-api-key`)
- [ ] **非法 / 已吊销 / 跨用户 Key 一律 401**,响应体是平台统一错误体;鉴权服务 4xx 响应原样透传公网,确认响应里没有栈、内网主机名与 `Set-Cookie`
- [ ] **不带 Key 必须 401**,`require_api_key=false` 的端点不带 Key 应 200(各造一个端点各打一次)
- [ ] **控制面挂了返回 503 而非 403**:临时 `kubectl -n superdl scale deploy/superdl-api --replicas=0`(验完立刻恢复)打端点应 **503**;403 即 `statusOnError` 漏配
- [ ] **鉴权回调未被边缘收口挡掉**:上条恢复后端点立刻恢复 200;仍 503 查 `superdl-api` 日志里 `/api/internal/v1/endpoint-auth` 是否 404(`headersToExtAuth` 含 `x-forwarded-for` 会触发 `app/core/edge_guard.py` 的 404 收口)
- [ ] **平台注入头不可伪造**:客户端自带 `-H 'x-superdl-endpoint: forged' -H 'x-superdl-key-id: 999'` 打端点,容器侧收到的必须是鉴权服务给的真值(`headersToBackend` 覆盖语义)
- [ ] **端点级限流生效且互不牵连**:对同一端点 `for i in $(seq 40); do curl -s -o /dev/null -w '%{http_code} ' -H 'Authorization: Bearer <Key>' https://svc-<slug>.svc.<域>/; done` 出现 429;**同时打另一个端点不受影响**。本地限流按 Envoy 实例计数,2 副本时单端点上限约 20/s × 2
- [ ] **Jupyter 域未被顺带鉴权**:`app-https` 上的实例 Jupyter 仍按 token 可访问(`superdl-svc-extauth` 误挂到 `app-https` 即全部 Jupyter 403/503)
- [ ] 服务端点路由跨 ns 挂载:`kubectl -n tenant-<uuid> get httproute -o yaml` 中服务端点那条的 `status.parents[].conditions` 为 `Accepted=True`

## K. 发布检查单(每次上线)

- [ ] **CSP 与第三方 SDK 域核对**:用真实 aliyun captcha provider 走通注册/登录/找回密码全链路,浏览器控制台无 CSP 违规;新增域先切 `Content-Security-Policy-Report-Only` 收敛再 enforce(见 `deploy/app/security-headers-web-csp.conf`)
- [ ] admin 站响应头含 `X-Robots-Tag: noindex, nofollow`,web 站 CSP 含 `o.alicdn.com` 与 `*.captcha-open.aliyuncs.com`
- [ ] 资金库 PITR:托管 PG 确认已开(设 `SUPERDL_MANAGED_PG_PITR_ACK`)或 cnpg 档启用且预检全绿(见 `preflight.sh`)
- [ ] **切换 server-side apply 后一次性清理**(`scripts/release.sh` 已改为 `kubectl apply --server-side --force-conflicts`):五个 TLS Secret 若曾用客户端 apply 灌入,其 `last-applied-configuration` 注解里存着整份证书与私钥的副本,逐个摘掉:`for s in superdl-api-tls superdl-frontends-tls superdl-admin-tls superdl-jupyter-wildcard-tls superdl-svc-wildcard-tls; do kubectl -n superdl annotate secret "$s" kubectl.kubernetes.io/last-applied-configuration-; done`(`kubectl -n superdl get secret <name> -o jsonpath='{.metadata.annotations}'` 回读为空)
