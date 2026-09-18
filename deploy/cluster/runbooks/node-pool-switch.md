# Switching a node's pool (SOP)

Move a node between the `kata` / `hami` / `mig` pools. Scenarios: real-hardware validation of whole-card passthrough, rebalancing tiers to stock needs,
switching back after a failed validation. The `cpu` pool is the physical property of GPU-less machines and does not take part.

**No login to the node, no reboot.** A pool is a pure label: every node-side software difference between pools is delivered by DaemonSets keyed on labels
(`kata-deploy` follows `node-restriction.kubernetes.io/superdl-pool=kata`, the HAMi device plugin follows
`node-restriction.kubernetes.io/superdl-pool=hami`, gpu-operator's vfio-manager and sandbox plugin follow
`nvidia.com/gpu.deploy.*`); binding and unbinding for whole-card passthrough is done at runtime by vfio-manager. IOMMU is part of the install baseline and does not change with the pool.
The pool label key carries the `node-restriction.kubernetes.io/` prefix: the NodeRestriction admission plugin forbids kubelets to set or change it, only the platform SA
(admission policy ③ allows exactly this key) may write it; the `superdl-infra` placement key shares the prefix but even the platform SA cannot touch it.
Endpoints and invariants are in [`docs/reference/nodes.md`](../../../docs/reference/nodes.md).

## Prerequisites

- The node has **no unreleased instances**, stopped / frozen / failed included. Emptying a node is steps 2–3 of [gpu-fault-sop.md](./gpu-fault-sop.md).
- The target pool's runtime is ready: kata checks `kubectl get runtimeclass kata-qemu`, hami checks `hami-scheduler`,
  mig checks gpu-operator. The admin cluster page's component health uses the same criteria.
- Switching to the mig pool requires a MIG-capable model (A100 / A800 / A30 / H100 / H800 / H200 / H20 / B200 / GB200 families).
- Switching to the kata pool requires a model that can be passed through whole (`core/gpu_models.PASSTHROUGH_CAPABLE_FAMILIES`: discrete PCIe / SXM boards).
  The integrated GPU of Grace superchips (GB10 / GB200) has a firmware-enforced 1:1 IOMMU mapping, the kernel refuses to bind it to `vfio-pci`, and the platform answers 409 directly (hardware facts in [hardware-notes.md](./hardware-notes.md)).
- Switching to the kata pool also requires **no NVIDIA driver on the node host**; do the next section first. Both model gates appear in the admin console as a greyed target pool.
- The admission policy is the version that allows the GPU operand keys, otherwise the worker's label change is denied:

```bash
kubectl apply -f deploy/cluster/admission/tenant-restrictions.yaml
```

## Before switching to kata: remove the host NVIDIA driver

gpu-operator's vfio-manager fails at once with `fatal: driver is pre-installed on host` when it finds a host driver,
the GPU stays on the `nvidia` driver and cannot bind to `vfio-pci`, and the node's allocatable `nvidia.com/gpu` drops to 0.
Nodes assigned to the kata pool at install time skip the driver and container-toolkit in `node-join.sh`, so this section does not apply to them;
it is needed only when moving an in-service hami / mig node into kata, and the order is **remove the driver first, switch the pool second**.

**A modprobe blacklist alone is not enough**: the driver package is still installed, so both the kata gate in `node-join.sh` (`dpkg -l 'nvidia-driver-*'`) and
gpu-operator's pre-installed driver check still trigger; the package really has to be purged.

```bash
# 1. The node is empty (the same gate as the pool switch) and cordoned
kubectl cordon <node>

# 2. Dry run first to see the packages removed along (especially the CUDA toolchain and container-toolkit dependencies)
ssh <node> 'apt-get -s purge "nvidia-driver-*"'

# 3. When it looks right, run it and reboot
ssh <node> 'systemctl disable --now nvidia-persistenced || true
  DEBIAN_FRONTEND=noninteractive apt-get purge -y "nvidia-driver-*"
  update-initramfs -u && reboot'

# 4. After the reboot all three must hold
ssh <node> '! test -e /proc/driver/nvidia \
  && ! lsmod | grep -q "^nvidia" \
  && ! dpkg -l "nvidia-driver-*" 2>/dev/null | grep -q "^ii" \
  && echo "no host driver"'
```

Switching back to hami / mig goes the other way: reinstall the driver package (`apt-get install -y nvidia-driver-<version>-server`, the version from the platform configuration
`node_driver_version`) → reboot → switch the pool only once `nvidia-smi` shows the cards, otherwise the node returns to the hami pool without recognising them.

## Steps

1. Admin Nodes and GPUs → the target node's row "Switch pool" → pick the target pool → enter a reason → confirm.
2. Once accepted the node is cordoned at once; the pool label and the GPU operand labels converge through the outbox (seconds, the 60 s patrol as fallback).
3. Verify item by item (next section). **The platform never uncordons automatically**; uncordon in the admin console once everything is green.

## Verification

Check with the commands below in order and keep the node cordoned while any criterion fails:

- kata pool: `nvidia.com/gpu.workload.config=vm-passthrough` and no leftover `nvidia.com/gpu.deploy.device-plugin`; kata-deploy, vfio-manager and sandbox-device-plugin present, hami-device-plugin absent.
- The kata pool's GPUs are bound to `vfio-pci`, each card in its own IOMMU group; with several cards in one group, selling the whole-card tier on that node is forbidden.
- Back in the hami pool: the HAMi device plugin present, the official device plugin absent, the GPUs bound back to `nvidia`, the node registers allocatable `nvidia.com/gpu` again.

```bash
kubectl get node <node> -L node-restriction.kubernetes.io/superdl-pool -L nvidia.com/gpu.workload.config \
  -L nvidia.com/gpu.deploy.device-plugin

kubectl -n kube-system get pod -o wide --field-selector spec.nodeName=<node>
kubectl -n gpu-operator get pod -o wide --field-selector spec.nodeName=<node>

ssh <node> 'lspci -nnk -d 10de:; for g in /sys/kernel/iommu_groups/*/devices/*; do echo "$g"; done | grep -i nvidia'

kubectl get node <node> -o jsonpath='{.status.allocatable}' | tr ',' '\n' | grep nvidia
```

Start one real instance of the target tier end to end, then uncordon.

## Pool label key migration (`superdl.io/pool` → `node-restriction.kubernetes.io/superdl-pool`, one-off)

Nodes of existing clusters carry the old key `superdl.io/pool`; the `hami` / `kata-deploy` nodeSelectors accept only the new key. The order is fixed, finish each step before the next:

1. Ship the new admission policy (the old policy ③ does not allow the new key, the platform's label write would be denied): `kubectl apply -f deploy/cluster/admission/tenant-restrictions.yaml`.
2. Release the api / worker with the new key (`scripts/release.sh`): the node patrol applies the new key to every node from `node_specs.desired_pool` and removes the old key `superdl.io/pool`; no manual label. Check: `kubectl get nodes -L node-restriction.kubernetes.io/superdl-pool -L superdl.io/pool`, the new column complete, the old column empty.
3. `./apply.sh <full|light> -l name=hami` and `./apply.sh <full|light> -l name=kata-deploy`: the DaemonSets re-place by the new key. `./preflight.sh` fails while any node still carries the old key.

Between removing the old key in step 2 and the re-placement in step 3, the hami-device-plugin / kata-deploy Pods leave the nodes: running instances are unaffected (the device plugin only takes part in new allocations); do not start new shared-tier / whole-card instances inside that window.

## Pitfalls

- **A manual `kubectl label` pool change is reverted**: the desired pool (`node_specs.desired_pool`) is the source of truth, patrol C2 corrects drift,
  and "label mismatch while the node is still schedulable" counts the critical metric `NodePoolLabelMismatch` and cordons the node automatically. Change pools through the admin console.
- **The desired pool is not cleared after the switch.** If the Node object is deleted and recreated, the kubelet comes back without the pool label and C2 fills it in.
- gpu-operator **does not overwrite existing values** when deriving `nvidia.com/gpu.deploy.*`; a pool switch must delete the leftover keys of the old pool.
  The platform ships the complete set (`core/gpu_adapter.pool_node_labels`); when operating by hand, do not change only half.
- `kata-deploy` has no cleanup hook (`command: kata-deploy install`, no preStop); kata → another pool leaves an unused
  containerd runtime handler on the node, harmless, no cleanup needed.
- Toggling MIG mode needs a GPU reset, done at runtime by mig-manager; some driver / model combinations still need a full reboot, mig pool only.
