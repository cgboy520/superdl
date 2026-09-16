# Hardware notes

Reference: platform-specific facts that shaped node-join, pool switching and the cluster values. The
platform itself is hardware-neutral (any Debian-family x86_64 / aarch64 host with NVIDIA GPUs); this
page records what differs per platform so nobody re-discovers it on a live node.

## NVIDIA Grace superchips (GB10 / GB200)

- **No whole-GPU passthrough.** The integrated GPU is firmware-bound to a 1:1 IOMMU mapping and the
  kernel refuses to bind it to `vfio-pci`. `core/gpu_models.PASSTHROUGH_CAPABLE_FAMILIES` therefore
  excludes GB10 / GB200, the admin console greys out the `kata` pool for these nodes
  (`NodeOut.supports_passthrough=false`) and the API answers 409 to a forced switch.
- **Unified CPU/GPU memory.** `nvidia-smi` reports the shared pool as GPU memory. HAMi's
  `preConfiguredDeviceMemory` is deliberately **not** set in `values/hami.yaml`: the plugin reads the
  reported size, and pinning a smaller number would only hide memory from tenants. Size disk-backed
  swap and instance limits with the shared pool in mind.
- **aarch64 kernel command line.** node-join writes the IOMMU argument to GRUB only on x86_64
  (`intel_iommu=on` / `amd_iommu=on` by CPU vendor); on aarch64 the SMMU is enabled by firmware and
  the script only checks that `/sys/kernel/iommu_groups` is populated after boot.

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
