# Ceph storage (SOP + incident response)

Tenant instance disks, data disks, tenant storage mounts and the platform's upload staging volume live on an external Ceph cluster. The platform holds one restricted admin key for a single subvolume group: it creates, resizes and removes subvolumes, issues one cephx key per subvolume and reads usage; it can do nothing else on the cluster. Mechanics: `docs/reference/orchestrator.md#storage`, `docs/reference/disks.md`.

## 1. Prerequisites (once per cluster)

1. **Ceph side** (done by the Ceph administrators): a CephFS file system, a subvolume group for this platform with a quota, a data pool (pool layout) and a restricted key that may manage subvolumes in that group and authorize ids under one prefix (for example `client.cloud-admin` with prefix `cloud.`). Ask for: fsid, `mon_host`, file system name, group, pool layout, whether subvolumes must use an isolated RADOS namespace, the key id, its secret and the id prefix.
2. **Network**: every node (the kernel mounts tenant volumes) and the `tenant-mgr` worker Pods (the `ceph` CLI) reach the Ceph public network: monitors TCP 3300 (msgr2) / 6789, OSDs and MDS TCP 6800-7300. Replace `CHANGE_ME_CEPH_PUBLIC_CIDR` in `deploy/app/k8s/09-networkpolicy.yaml`. Check from a node: `nc -zv <mon> 3300`.
3. **Nodes**: the `ceph` kernel module must exist (`modinfo ceph`; node-join refuses a node without it). Ubuntu ships it in `linux-modules-extra-$(uname -r)` on some kernels.
4. **Platform component volumes (TopoLVM)**: on the host that carries Prometheus / Loki / acme-dns (the server in the light tier), create the volume group and label the node:

   ```bash
   wipefs -n /dev/<empty disk>            # must print nothing
   pvcreate /dev/<empty disk> && vgcreate superdl-infra /dev/<empty disk>
   kubectl label node <node> superdl.io/infra-storage=true
   ```

   An existing `superdl-nvme` VG can be renamed in place: `vgrename superdl-nvme superdl-infra`.
5. **ceph-csi**: put the fsid and the msgr2 monitor addresses into `deploy/cluster/values/ceph-csi-cephfs.yaml`, then `./apply.sh <full|light> -l name=ceph-csi-cephfs`. `./preflight.sh` checks the values, the CSIDriver and the infra-storage label.
6. **Platform configuration** (admin console › Platform configuration › Storage): `ceph_fsid`, `ceph_mon_host` (the `mon_host` line of ceph.conf), `ceph_fs_name`, `ceph_subvolume_group`, `ceph_pool_layout`, `ceph_namespace_isolated`, `ceph_admin_id` (without `client.`), `ceph_admin_key`, `ceph_auth_prefix` (the allowed id prefix, ending with a dot), `ceph_mount_options` (`ms_mode=prefer-crc` for msgr2). The Cluster page's storage item turns green once the storage patrol reaches the cluster (within a minute).
7. **Upload staging volume** (needed before the first release; the API and the image-ops worker mount it): run on a machine with the `ceph` CLI and the admin keyring:

   ```bash
   CEPH_ARGS="-n client.<admin id>" CEPH_FS_NAME=<fs> CEPH_SUBVOLUME_GROUP=<group> \
   CEPH_POOL_LAYOUT=<pool> CEPH_NAMESPACE_ISOLATED=true CEPH_AUTH_PREFIX=<prefix> \
     deploy/cluster/ceph/static-volume.sh --name superdl-uploads --size-gib 1024 \
       --namespace superdl --uid 10001 --gid 10001 --mode 700 --fsid <fsid> | kubectl apply -f -
   ```

   Size it for the largest archives being uploaded at the same time (per-user cap `custom_image_max_upload_gb`). The PVC `superdl-uploads` ships with `deploy/app/k8s/03-worker.yaml`.

## 2. Cut-over from node-local storage

The platform does not migrate instance disks or data disks from TopoLVM / Rook. The database migration refuses to run while any instance is not released or any data disk is not deleted.

1. Announce the window; users copy what they need off the platform.
2. Release every instance and delete every data disk (admin console, or let users do it). Verify: `select count(*) from instances where status <> 'released'` and `select count(*) from data_disks where status <> 'deleted'` both return 0.
3. Complete section 1, then release the new version (`scripts/release.sh`); the migration runs between stop and start.
4. Remove the old stack: `helm -n rook-ceph uninstall rook-ceph-cluster ceph-csi-drivers rook-ceph`, delete the leftover `superdl-cephfs` StorageClass and the `rook-ceph` namespace, wipe the OSD volumes; on GPU nodes delete the instance-disk VG (`vgremove superdl-nvme`) and the loop unit `superdl-nvme-loop.service` if one exists. Keep the infra host's VG (renamed to `superdl-infra`).

## Unreachable

Alert `CephUnreachable` (the storage patrol failed for 5 minutes) or `DiskProvisionFailed`; instance create / start / restart answer `nodes.storageNotReady`.

1. Admin console › Cluster › Storage: the `cephError` fact carries the last CLI error.
2. From a `tenant-mgr` Pod's node and from a GPU node: `nc -zv <mon> 3300`; then the NetworkPolicy `tenant-mgr-ceph-egress` and the node routes.
3. Authentication errors (`EACCES`, `-13`): the admin key or id in the platform configuration changed, or its caps on the Ceph side did.
4. Running instances keep working as long as their nodes reach Ceph; only new Pods and disk changes wait for the platform's probe. Dead-lettered volume tasks are re-dispatched by the reconciler once Ceph answers.

Acceptance: the storage item is green, a test instance starts with a data disk mounted (`df -h /root /data`).

## Capacity

Alert `CephGroupNearlyFull` (subvolume group usage above 85 % of its quota). At the quota every tenant volume stops accepting writes.

1. Cluster › Storage shows `cephUsedGib` / `cephQuotaGib`; the largest consumers are visible per disk in the database (`data_disks.bytes_used`, `instances.system_disk_bytes_used`).
2. Ask the Ceph administrators to raise the group quota (the platform key cannot), or lower `disk_max_gb` / `max_disk_gb_per_user` until it is raised.
3. Abandoned uploads are removed by the image patrol after 24 hours; failed imports after 30 days.

## Tenant storage mounts

A tenant's mount stays `failed` with `last_error`: the path does not exist, the tenant's key lacks caps on it (read for read-only mounts, read-write otherwise), or the id is wrong. The tenant fixes it on their Storage tab and re-checks; the platform never uses its own key for tenant mounts.
