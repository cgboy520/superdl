# Data disks

CephFS data disks: one PVC per disk, with their own lifecycle, quota, expansion and daily settlement. Tables and code live in the orchestrator module.

## Data model

- `data_disks`: uuid, user_id, name, size_gb, status (active / grace / frozen / deleting / deleted), mounted_instance_id?, provisioned
- The PVC name is not stored; it is derived from the disk uuid (`core/k8s/base.data_disk_pvc_name` → `disk-<uuid>`) and created in the tenant namespace

## Contract

| Endpoint                      | Role / auth | Notes                                                                                                                                                                             |
| ----------------------------- | ----------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `POST /api/v1/disks`          | user        | `{name, size_gb}`; balance (in-flight + the new daily fee) and count quota checked under the wallet row lock                                                                      |
| `GET /api/v1/disks`           | user        | List                                                                                                                                                                              |
| `PATCH /api/v1/disks/{uuid}`  | user        | Expand only, never shrink; shrinking returns `DISK_SHRINK_FORBIDDEN`                                                                                                              |
| `DELETE /api/v1/disks/{uuid}` | user        | `DISK_IN_USE` while mounted on a running / starting / creating / stopping / releasing instance; allowed and auto-detached when the mounting instance is stopped / frozen / failed |

## Rules and invariants

- The data-disk backend must support idmapped mounts (tenant Pods always run `hostUsers: false`); the current backend is CephFS (Rook), one PVC per disk.
- Disks are decoupled from instances: releasing an instance leaves the disk alone, and the same disk can be mounted by different instances over time (mount point `/root/data`, RWX, any node).
- Arrears (available balance ≤ 0, the same criterion as the instance arrears patrol, see [billing.md](./billing.md)) → grace → frozen → purge; the day counts are policy parameters, see [limits.md](./limits.md). Grace stops billing (the days already accrued are settled on entering grace); the grace clock `grace_started_at` starts at the first arrears and is not reset by a payment; the frozen-deletion clock `frozen_started_at` is not reset by a payment either (`grace_ended_at` is recorded) — a later freeze continues the old clock, and only staying in good standing for more than `disk_frozen_days` after paying resets it. "Freeze → top up on day 29 → fall into arrears again" therefore still purges on the original deadline.
- Billed per civil day of the billing time zone (`SUPERDL_BILLING_TIMEZONE`; the day boundary is `billing_day_floor` in `app/core/timeutil.py`, days are stepped with `billing_day_shift`, DST transition days are 23 / 25 hours); a partial day counts as a whole day; stopped instances still pay. Daily settlement runs at 00:10 in the billing zone (advisory lock), settles the previous day, is idempotent through `bills_daily_disk` UNIQUE(disk_id, day), and missed days are caught up through `settlement_watermarks`.
- Before deletion or expansion the days not yet billed are settled at the pre-change size, with the watermark as the lower bound.
- `grace` / `frozen` disks are not billed; deleting a frozen disk does not back-bill.
- `size_gb` is both the billing basis and the **real hard limit**: it is the PVC's requested capacity, the CephFS CSI creates a quota-bearing subvolume, and the quota is effective at creation — there is no "quota pending" window. Creation and expansion enqueue `disk.provision` in the same transaction; the worker (disk-ops) creates or patches the PVC in the tenant namespace (grow only) and sets `provisioned=true` on success; a disk with `provisioned=false` cannot be mounted (`disks.notProvisioned`). After retries are exhausted the dead letter is re-dispatched periodically by the reconciler (failures count in `superdl_disk_provision_failed_total`). Deletion enqueues `disk.deprovision`, which deletes the PVC; the StorageClass's `reclaimPolicy=Delete` makes the CSI destroy the subvolume — there is no separate wipe job.
- The per-user count limit follows the chain "user override → policy `max_disks_per_user` → env"; the total capacity limit is the policy `max_disk_gb_per_user` (sum of `size_gb` over non-deleted disks, checked on creation and expansion, `disks.capacityQuota` when exceeded); values in [limits.md](./limits.md). Creation only checks the balance; nothing is charged that day.
- The multi-step deletion safeguards live in the frontend; the backend validates the mount state.
- Idempotency-key window 24 h: a replay inside the window returns the existing disk, the same key outside it is a new order; concurrent replays converge on the unique constraint.
