# 切换节点池(SOP)

把一台节点在 `kata` / `hami` / `mig` 三个池之间换过去。场景:为整卡直通做实机验证、按库存需要调整档位配比、
验证失败后切回原池。`cpu` 池是无卡机的物理属性,不参与切换。

平台负责 K8s 标签与台账,主机侧改造(IOMMU、驱动、agent 配置)由节点上重跑装机脚本补齐。
端点与不变量见 [`docs/reference/nodes.md`](../../../docs/reference/nodes.md)。

## 前置

- 节点上**没有未释放实例**,含已关机 / 冻结 / 失败的。实例盘是节点本地 LV,开机会 pin 回原节点,
  换池后 `nodeSelector` 再也匹配不上。清空节点的做法见 [gpu-fault-sop.md](./gpu-fault-sop.md) 第 2–3 步。
- 目标池运行时已就绪:kata 看 `kubectl get runtimeclass kata-qemu`,hami 看 `hami-scheduler`,
  mig 看 gpu-operator。管理端集群页组件体检同判据。
- 切到 mig 池要求机型支持 MIG(A100 / A800 / A30 / H100 / H800 / H200 / H20 / B200 / GB200 系)。
- 准入策略已是允许 GPU operand 键的版本,否则 worker 改标签会被 Deny:

```bash
kubectl apply -f deploy/cluster/admission/tenant-restrictions.yaml
```

## 步骤

1. 管理端 节点与 GPU → 目标节点行「切换池」→ 选目标池 → 填原因 → 确认。
   受理后节点立即封锁,池标签与 GPU operand 标签经 outbox 收敛(秒级,60s 巡检兜底)。
2. 复制回执里的命令,在**该节点上**执行。命令带 `--force`(节点已完成加入,不带会被脚本的幂等入口直接退出)。
   切到 kata 时脚本会写 GRUB 的 `intel_iommu=on iommu=pt` 并**重启一次**,重启后 systemd oneshot 自动续跑。
3. 进度在管理端「待加入节点」卡里看;节点重新 Ready 且池标签匹配后登记转 `joined`。
4. 逐项核对(下节),全绿后在管理端「解封」。**平台不自动解封**,这是有意的:标签到位不等于能卖。

## 核对

```bash
# 池标签与 operand 标签(kata 池:vm-passthrough 在、deploy.device-plugin 不在)
kubectl get node <node> -L superdl.io/pool -L nvidia.com/gpu.workload.config \
  -L nvidia.com/gpu.deploy.device-plugin

# 组件落位:kata 池应有 kata-deploy / vfio-manager / sandbox-device-plugin,且没有 hami-device-plugin
kubectl -n kube-system get pod -o wide --field-selector spec.nodeName=<node>
kubectl -n gpu-operator get pod -o wide --field-selector spec.nodeName=<node>

# kata 池:卡已绑到 vfio-pci,且 IOMMU 每卡独立成组(同组多卡则整卡档不可在该节点售卖)
ssh <node> 'lspci -nnk -d 10de:; for g in /sys/kernel/iommu_groups/*/devices/*; do echo "$g"; done | grep -i nvidia'

# 回到 hami 池:HAMi device plugin 在、官方 device plugin 不在,节点重新注册出 nvidia.com/gpu
kubectl get node <node> -o jsonpath='{.status.allocatable}' | tr ',' '\n' | grep nvidia
```

真开一台目标档位的实例跑通,再解封。

## 坑

- **`node-label` 只在节点首次注册时生效**(k3s / RKE2 同),所以重跑脚本改不动已注册节点的标签,标签一律由平台改。
  脚本重写 `config.yaml` 是为了将来 Node 对象若被删除重建时,kubelet 带上来的标签与新池一致。
- 期望池(`node_specs.desired_pool`)**切完不清空**,它是平台认定的池;巡检以它为准纠偏,所以手工
  `kubectl label` 改池会在 60s 内被改回去。要改池走管理端。
- gpu-operator 按 `nvidia.com/gpu.workload.config` 派生 `nvidia.com/gpu.deploy.*` 且**已存在的值不覆盖**,
  所以切池必须把旧池残留键删掉——平台已整套下发,手工操作时别只改一半。
- 切池期间登记停在 `installing` 属正常:标签没收敛到位时对账器不判,等收敛后转 `joined`。
