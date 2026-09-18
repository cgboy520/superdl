# Hardware notes

Reference: platform-specific facts that shaped node-join, pool switching and the cluster values. The
platform itself is hardware-neutral (any Debian-family x86_64 / aarch64 host with NVIDIA GPUs); this
page records what differs per platform so nobody re-discovers it on a live node.

## NVIDIA Grace superchips (GB10 / GB200)

- **No whole-GPU passthrough.** The integrated GPU is firmware-bound to a 1:1 IOMMU mapping and the
  kernel refuses to bind it to `vfio-pci`. `core/gpu_models.PASSTHROUGH_CAPABLE_FAMILIES` therefore
  excludes GB10 / GB200, the admin console greys out the `kata` pool for these nodes
  (`NodeOut.supports_passthrough=false`) and the API answers 409 to a forced switch.
- **GB10 (DGX Spark): unified memory that NVML does not report.** `nvidia-smi` shows the memory
  as N/A and the HAMi device plugin (pinned 2.9.0) skips such a device unless
  `devicePlugin.preConfiguredDeviceMemory` is set; `values/hami.yaml` carries it as a commented
  example. Set it per deployment to the share of the unified pool tenants may use (the running
  Spark cluster uses 86016 MiB of 121 GiB, leaving the host and Ceph their reservation) and keep
  `node_specs.vram_gb` in step; a value larger than the pool only hides the host's memory
  pressure. `rollout restart ds/hami-device-plugin` after changing it.
- **GB200: HBM reported normally.** NVML reports the GPU memory, HAMi auto-detects it; no
  pre-configured size is needed.
- **aarch64 kernel command line.** node-join writes the IOMMU argument to GRUB only on x86_64
  (`intel_iommu=on` / `amd_iommu=on` by CPU vendor); on aarch64 nothing is written, the SMMU must
  be enabled by firmware, and the script only checks that `/sys/kernel/iommu_groups` is populated
  after boot. When it stays empty after the reboot node-join retries with a reboot and asks the
  operator to check the firmware SMMU setting; there is no software fallback.

## x86_64 servers

- The IOMMU kernel argument follows the CPU vendor (`GenuineIntel` → `intel_iommu=on iommu=pt`,
  `AuthenticAMD` / `HygonGenuine` → `amd_iommu=on iommu=pt`); other vendors fail node-join closed
  until `SUPERDL_JOIN_IOMMU_ARGS` names the argument explicitly. VT-d / AMD-Vi must also be enabled
  in firmware or `/sys/kernel/iommu_groups` stays empty after the reboot.
- Whole-GPU passthrough (`kata` pool) needs one IOMMU group per GPU; boards that share a group with
  a sibling device cannot sell single-GPU instances on that node (see
  [cluster-validation.md](./cluster-validation.md) section B).

## Distribution baseline

- Debian family only (`ID` ubuntu / debian or `ID_LIKE` containing debian): node-join and the ansible
  playbook use apt, dpkg, update-initramfs and update-grub throughout. Other distributions fail the
  precheck instead of half-installing.

## Installer sources

- k3s / rke2 installers come from the official hosts by default (`get.k3s.io`, `get.rke2.io`); the
  mainland-China mirror `rancher-mirror.rancher.cn` is an explicit opt-in (`node_install_mirror=cn`,
  ansible `k8s_install_mirror=cn`). Both origins are pinned by sha256 in `node-join.sh` and
  `deploy/ansible/site.yml`.
