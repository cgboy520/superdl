# PostgreSQL backup and restore runbook

A restore drill runs once per quarter.

> **Mandatory before go-live (public production gate)**: minute-level RPO, one of two: ① managed PG: confirm PITR and the retention policy are on (recorded with `SUPERDL_MANAGED_PG_PITR_ACK=yes` in preflight; the full tier is red without it); ② self-hosted cnpg tier: enable `cnpg.enabled` (on by default in the full tier) with preflight all green (S3 archive without placeholders, ScheduledBackup running).
> Before the first traffic switch, run the "Restore steps" + "PITR spot check" once in full and fill in the RTO log.

## Backup layers

| Layer | Method | RPO |
|---|---|---|
| Logical backup | `deploy/app/k8s/06-pg-backup.yaml` daily `pg_dump -Fc` → **gpg AES256 client-side encryption** (passphrase = `BACKUP_ENCRYPT_KEY` of `superdl-pg-backup`) → object storage; a restore smoke after upload (decrypt + pg_restore + key tables queryable), failure means Job Failed and an alert | 24 h |
| Continuous archiving | CloudNativePG barmanObjectStore S3 WAL archiving + daily base backup (`values/cnpg-cluster.yaml`, cnpg tier); with managed PG the RDS automatic backups + PITR | minutes |
| Self-hosted single instance (`deploy/pg/`) | `backup.sh` daily dump → gpg → mirror host + restore smoke + pg_hba drift comparison; `wal-sync.sh` every 5 minutes gpg-encrypts WAL segments and syncs them (the mirror holds only `.gpg`), every Sunday `pg_basebackup` (gpg, counts as success only when the ciphertext is > 1 MiB and synced, then creates `base/.done-<date>`); metrics `superdl_pg_backup_last_success_timestamp_seconds` / `superdl_pg_wal_sync_last_success_timestamp_seconds` / `superdl_pg_basebackup_last_success_timestamp_seconds` / `superdl_pg_hba_drift` (node-exporter textfile), alert `PgBackupStandaloneStale` (watches the first one only) | dump 24 h / WAL 5 minutes |
| Cluster metadata | RKE2 etcd snapshots (every 6 h, 12 kept, `rke2/server-config.yaml`); k3s embedded etcd snapshots (every 6 h, 28 kept, `k3s/server-config.yaml`) + `k3s/state-backup.sh` every 6 h encrypting token / cred / tls / datastore to the PG mirror host's `k3s/` (metric `superdl_k3s_state_backup_last_success_timestamp_seconds`; restore in `deploy/cluster/README.md` "Cluster state backup and restore") | 6 h |

Reconciliation: `balance_ledger` is append-only and every row carries `balance_after`; after a restore verify fund consistency with `GET /api/admin/v1/reconciliation` and the ledger chain.

## Restore steps (logical backup)

Run in stages; do not treat this section as one continuous script. First download the latest encrypted backup and decrypt it with the passphrase from `BACKUP_ENCRYPT_KEY` of `superdl-pg-backup`; remove the plaintext right after the restore.

```bash
aws s3 ls s3://superdl-pg-backup/daily/ --endpoint-url $S3_ENDPOINT | tail -5
aws s3 cp s3://superdl-pg-backup/daily/superdl-<ts>.dump.gpg /tmp/ --endpoint-url $S3_ENDPOINT

gpg --batch --yes --decrypt \
  --passphrase <(kubectl -n superdl get secret superdl-pg-backup -o jsonpath='{.data.BACKUP_ENCRYPT_KEY}' | base64 -d) \
  -o /tmp/superdl-<ts>.dump /tmp/superdl-<ts>.dump.gpg

kubectl -n superdl scale deploy superdl-api --replicas=0
kubectl -n superdl scale deploy superdl-worker superdl-worker-tenant-mgr \
  superdl-worker-node-mgr superdl-worker-prewarm superdl-worker-disk-ops --replicas=0

```

Once the API and all 5 worker Deployments have stopped writing, restore into a new database; **never overwrite the production database in place**.

```bash
createdb superdl_restore
pg_restore -d superdl_restore --no-owner /tmp/superdl-<ts>.dump
shred -u /tmp/superdl-<ts>.dump 2>/dev/null || rm -f /tmp/superdl-<ts>.dump

psql superdl_restore -c "SELECT max(created_at) FROM balance_ledger"
psql superdl_restore -c "SELECT version_num FROM alembic_version"

```

Check the restored database's row counts, latest ledger time and Alembic version. Once they pass, switch the application connection `SUPERDL_DATABASE_URL` to `superdl_restore`, confirm the API and every worker will use the new connection, then start the API alone:

```bash
kubectl -n superdl scale deploy superdl-api --replicas=2
```

After the API `/readyz` and the business smoke pass, restore every worker to the replica counts defined in `03-worker.yaml`:

```bash
kubectl -n superdl scale deploy superdl-worker superdl-worker-tenant-mgr --replicas=2
kubectl -n superdl scale deploy superdl-worker-node-mgr superdl-worker-prewarm \
  superdl-worker-disk-ops --replicas=1
```

Announce the restore point to users; recharges after the restore point follow the channel statements and are credited one by one through the admin "Backfill".

## PITR (self-hosted single instance)

All material is on the mirror host under `pg-mirror/`: `base/base-<ts>.tar.gz.gpg` (take the latest one before the target time), `wal/*.gpg` (the mirror holds only ciphertext), the gpg passphrase `/etc/superdl/pg/backup-passphrase` (the same as on the backup host; copy it to the restore machine first). On an isolated machine with the same PG version (image digest in `deploy/pg/compose.yaml`; in a container mount the data directory at the image's PGDATA `/var/lib/postgresql/18/docker`):

```bash
mkdir -p /restore/data /restore/wal
gpg --batch --quiet --decrypt --passphrase-file /etc/superdl/pg/backup-passphrase base-<ts>.tar.gz.gpg | tar -xzf - -C /restore/data
rsync -a '<mirror host>:/var/lib/superdl/pg-mirror/wal/' /restore/wal/
chown -R 999:999 /restore/data /restore/wal && chmod 0700 /restore/data
touch /restore/data/recovery.signal
cat >> /restore/data/postgresql.auto.conf <<'EOF'
restore_command = 'gpg --batch --quiet --decrypt --passphrase-file /etc/superdl/pg/backup-passphrase -o %p /wal/%f.gpg'
recovery_target_time = '<YYYY-MM-DD HH:MM:SS+00>'
recovery_target_action = 'promote'
EOF
```

`restore_command` runs `gpg` inside the PG process, so PG's environment needs gpg and the passphrase file (in a container mount `/wal` and the passphrase file read-only); without gpg, decrypt the whole batch on the host first (`for f in /restore/wal/*.gpg; do gpg --batch --quiet --decrypt --passphrase-file /etc/superdl/pg/backup-passphrase -o "${f%.gpg}" "$f"; done`) and use `restore_command = 'cp /wal/%f %p'`. After starting, watch the log until `recovery stopping before commit of transaction … / database system is ready`, then verify the ledger and the Alembic version as in "Restore steps" before switching traffic.

## Restore drill acceptance

- [ ] Full restore of the latest daily backup into a new database < 30 minutes
- [ ] `alembic check` passes; `/readyz` ready
- [ ] Spot-check 3 users: balance = `balance_after` at the tail of the ledger chain
- [ ] The admin backfill flow can credit the channel-paid orders after the restore point

## Quarterly drill checklist (once per quarter)

- [ ] Daily smoke online: every `pg-backup-daily` Job of the last 7 days succeeded (restore smoke included), no `PgBackupFailed` / `PgBackupStale` fired
- [ ] Timed full restore: fetch the latest backup from object storage, restore it into an isolated database following "Restore steps" and time it; write the result into the RTO log below
- [ ] Fund consistency: spot-check 3 users, balance = `balance_after` at the tail of the ledger chain; `alembic check` passes
- [ ] PITR spot check: restore from the WAL archive to a given point in time (cnpg tier: a recovery-mode cluster; managed PG: the console's point-in-time restore; self-hosted single instance: "PITR (self-hosted single instance)" above with the base + `.gpg` WAL from the mirror host)
- [ ] Self-hosted single-instance backup chain online: the newest file in the mirror's `base/` is > 1 MiB with mtime < 8 days; `base/.done-<latest Sunday>` exists on the backup host; `superdl_pg_wal_sync_last_success_timestamp_seconds` < 10 minutes old, `superdl_pg_basebackup_last_success_timestamp_seconds` < 8 days old; `superdl_pg_hba_drift == 0`
- [ ] Cluster state backup online (k3s): the newest file in the mirror's `k3s/` has mtime < 12 hours, `superdl_k3s_state_backup_last_success_timestamp_seconds` < 12 hours old; the mirror host is not a node carrying tenant workloads
- [ ] Alert chain: fail one backup by hand (e.g. temporarily break the S3 credentials), confirm `PgBackupFailed` reaches on-call, then restore
- [ ] Records: update the RTO log + write the drill conclusion into the ops weekly report

## RTO log

Target RTO: < 30 minutes (logical backup restored into a new database, excluding the business switch announcement).

| Date | Backup type (logical / CNPG basebackup / RDS) | Data size | Restore time | RTO met | Operator | Notes |
|---|---|---|---|---|---|---|
| | | | | | | |
