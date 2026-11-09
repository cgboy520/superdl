# Data disks

Data disks are CephFS subvolumes of the external Ceph cluster, one per disk, with their own lifecycle, quota, expansion and daily settlement. Every disk of a user mounts on all of that user's instances at its own mount path. Tables and code live in the orchestrator module (`disks.py`, `storage.py`, `storage_patrol.py`); the Ceph side is described in [orchestrator.md](./orchestrator.md#storage).

## Data model

- `data_disks`: uuid, user_id, name, size_gb, mount_path (default `/data`), status (active / grace / frozen / deleting / deleted), provisioned, ceph_path (the subvolume path once created), bytes_used / usage_checked_at (refreshed by the storage patrol)
- Unique per user among non-deleted disks: `mount_path` (partial unique index `uq_data_disks_user_mount_path`)
- Subvolume `u<user id>-disk-<uuid>`, cephx id `<ceph_auth_prefix>u<user id>-disk-<uuid>`, static PV `superdl-<subvolume>` + PVC `disk-<uuid>` + node-stage Secret `ceph-<subvolume>` in the tenant namespace; names are derived, not stored
- `instances.data_disk_ids`: the disks an instance's Pod mounts, snapshotted when it is created, started or restarted

## Contract

| Endpoint                      | Role / auth | Notes                                                                                                                                                                                                                           |
| ----------------------------- | ----------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `POST /api/v1/disks`          | user        | `{name, size_gb, mount_path?}` (default `/data`); balance (in-flight + the new daily fee), count and capacity quotas checked under the wallet row lock, then the mount path (`disks.mountPathInvalid` / `disks.mountPathTaken`) |
| `GET /api/v1/disks`           | user        | List with `mount_path`, `bytes_used`, `usage_checked_at` and `mounted_by` (instances whose Pod mounts the disk)                                                                                                                 |
| `PATCH /api/v1/disks/{uuid}`  | user        | `{size_gb?, mount_path?}`: expand (never shrink, `DISK_SHRINK_FORBIDDEN`) and / or move the mount point                                                                                                                         |
| `DELETE /api/v1/disks/{uuid}` | user        | `DISK_IN_USE` (`disks.inUseDelete` with the instance names) while a Pod mounts it (creating / starting / running / stopping / releasing); stopped, frozen and failed instances do not block                                     |

## Rules and invariants

- Mount paths: `/data` plus a suffix (`/data`, `/data2`, `/datasets`, up to 37 characters), `/workspace` plus a suffix, or `/mnt/<name>` except `/mnt/org` (tenant storage lives there); letters, digits and `. _ -`, no nesting. The same rule is mirrored in the console (`apps/web/src/lib/disks.ts`).
- Auto-mount: an instance mounts every `active` disk of its owner read-write and every `grace` disk read-only; `frozen` and `deleted` disks are left out. The set and the paths are snapshotted into `instances.data_disk_ids` at create / start / restart, so a new disk, a moved mount point or a disk leaving grace reaches a running instance at its next start; an expansion applies at once (the subvolume quota changes under the live mount).
- The backend must support idmapped mounts (tenant Pods always run `hostUsers: false`): CephFS with the kernel client does.
- Arrears (available balance ≤ 0, the same criterion as the instance arrears patrol, see [billing.md](./billing.md)) → grace → frozen → purge; the day counts are policy parameters, see [limits.md](./limits.md). Grace stops billing (the days already accrued are settled on entering grace); the grace clock `grace_started_at` starts at the first arrears and is not reset by a payment; the frozen-deletion clock `frozen_started_at` is not reset by a payment either (`grace_ended_at` is recorded) — a later freeze continues the old clock, and only staying in good standing for more than `disk_frozen_days` after paying resets it. "Freeze → top up on day 29 → fall into arrears again" therefore still purges on the original deadline.
- Billed per civil day of the billing time zone (`SUPERDL_BILLING_TIMEZONE`; the day boundary is `billing_day_floor` in `app/core/timeutil.py`, days are stepped with `billing_day_shift`, DST transition days are 23 / 25 hours); a partial day counts as a whole day; stopped instances still pay. Daily settlement runs at 00:10 in the billing zone (advisory lock), settles the previous day, is idempotent through `bills_daily_disk` UNIQUE(disk_id, day), and missed days are caught up through `settlement_watermarks`.
- Before deletion or expansion the days not yet billed are settled at the pre-change size, with the watermark as the lower bound.
- `grace` / `frozen` disks are not billed; deleting a frozen disk does not back-bill.
- `size_gb` is both the billing basis and the **real hard limit**: it is the subvolume quota (`--size`), effective at creation. Creation and expansion enqueue `disk.provision` in the same transaction; the tenant-mgr worker creates the subvolume (or resizes it), authorizes the disk's own cephx key, records `ceph_path` and creates the Secret / PV / PVC in the tenant namespace, then sets `provisioned=true`. A disk an instance needs before the task ran is provisioned inline when the Pod spec is built. After retries are exhausted the dead letter is re-dispatched periodically by the reconciler (failures count in `superdl_disk_provision_failed_total`).
- Deletion enqueues `disk.deprovision`: the PVC, PV and Secret are deleted (PVC protection holds the PVC until the last Pod using it is gone, the task retries meanwhile), then the key's clients are evicted, the key is revoked and the subvolume removed — the data is gone with it, there is no separate wipe job.
- The per-user count limit follows the chain "user override → policy `max_disks_per_user` → env"; the total capacity limit is the policy `max_disk_gb_per_user` (sum of `size_gb` over non-deleted disks, checked on creation and expansion, `disks.capacityQuota` when exceeded); values in [limits.md](./limits.md). Creation only checks the balance; nothing is charged that day.
- Usage (`bytes_used`) comes from `fs subvolume info`, refreshed by the storage patrol in batches of 100 per minute, oldest first; it is shown to the user and never billed.
- The multi-step deletion safeguards live in the frontend; the backend validates the mount state.
- Idempotency-key window 24 h: a replay inside the window returns the existing disk, the same key outside it is a new order; concurrent replays converge on the unique constraint.
