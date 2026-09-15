# 切换节点池(SOP)

把一台节点在 `kata` / `hami` / `mig` 三个池之间换过去。场景:为整卡直通做实机验证、按库存需要调整档位配比、
验证失败后切回原池。`cpu` 池是无卡机的物理属性,不参与切换。

**不需要登录节点,也不重启。** 池是一个纯标签:池间差异的节点侧软件全部由 DaemonSet 按标签投送
(`kata-deploy` 认 `superdl.io/pool=kata`、HAMi device-plugin 认 `superdl.io/pool=hami`、
gpu-operator 的 vfio-manager 与 sandbox 插件认 `nvidia.com/gpu.deploy.*`),整卡直通的绑定与解绑
由 vfio-manager 在运行时做。IOMMU 是装机基线,不随池变。
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
2. 受理后节点立即封锁,池标签与 GPU operand 标签经 outbox 收敛(秒级,60s 巡检兜底)。
3. 逐项核对(下节)。**平台不自动解封**——标签到位不等于能卖,全绿后在管理端「解封」。

## 核对

按下面命令依次核对,不满足判据时保持封锁:

- kata 池:`nvidia.com/gpu.workload.config=vm-passthrough`,不保留 `nvidia.com/gpu.deploy.device-plugin`;有 kata-deploy、vfio-manager、sandbox-device-plugin,没有 hami-device-plugin。
- kata 池的 GPU 绑定到 `vfio-pci`,且每张卡独立一个 IOMMU 组;同组多卡时禁止在该节点售卖整卡档。
- 回到 hami 池:HAMi device plugin 在、官方 device plugin 不在,GPU 绑定回 `nvidia`,节点重新注册 `nvidia.com/gpu` 可分配资源。

```bash
kubectl get node <node> -L superdl.io/pool -L nvidia.com/gpu.workload.config \
  -L nvidia.com/gpu.deploy.device-plugin

kubectl -n kube-system get pod -o wide --field-selector spec.nodeName=<node>
kubectl -n gpu-operator get pod -o wide --field-selector spec.nodeName=<node>

ssh <node> 'lspci -nnk -d 10de:; for g in /sys/kernel/iommu_groups/*/devices/*; do echo "$g"; done | grep -i nvidia'

kubectl get node <node> -o jsonpath='{.status.allocatable}' | tr ',' '\n' | grep nvidia
```

真开一台目标档位的实例跑通,再解封。

## 坑

- **手工 `kubectl label` 改池会被顶回去**:期望池(`node_specs.desired_pool`)是事实源,巡检 C2 按它纠偏,
  而且「标签不符且节点仍可调度」会计 critical 指标 `NodePoolLabelMismatch` 并自动封锁该节点。要改池走管理端。
- **期望池切完不清空**,它是平台认定的池。Node 对象若被删除重建,kubelet 不带池标签回来,C2 按它补齐。
- gpu-operator 派生 `nvidia.com/gpu.deploy.*` 时**不覆盖已存在的值**,所以切池必须把旧池残留键删掉
  ——平台下发的是整套完备集(`core/gpu_adapter.pool_node_labels`),手工操作时别只改一半。
- `kata-deploy` 没有清理钩子(`command: kata-deploy install`,无 preStop),kata → 其他池会在节点上留下
  没人用的 containerd runtime handler。无害,不用管。
- MIG 模式开关需要 GPU reset,mig-manager 在运行时做,但部分驱动/机型组合仍要整机重启——这是唯一
  可能需要节点侧动作的场景,且只影响 mig 池。
