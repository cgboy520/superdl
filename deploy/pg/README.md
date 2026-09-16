# Self-hosted single-instance PostgreSQL (Docker on the control-plane host)

The third form beyond `deploy/README.md` "Production database requirements": PG 18 runs in Docker on the control-plane host with host networking, listening on the host's internal address and loopback. For small clusters without managed PG that also do not put the database inside K8s (cnpg). **Single instance, no replica; the host is the database's single point of failure.**

## Files

| File | Location | Notes |
|---|---|---|
| `compose.yaml` | `/etc/superdl/pg/compose.yaml` | Image pinned by digest; `archive_mode=on` + local WAL archive directory; `hba_file` points at `/etc/pg/pg_hba.conf` inside the container (bind-mounted from the file below) |
| `pg_hba.conf` | `/etc/superdl/pg/pg_hba.conf` | Unix socket `trust` (`replication` on its own line, `all` excludes it, for `pg_basebackup`); TCP always `hostssl` + `scram-sha-256`, `hostnossl` fully refused. The production file must match the repository byte for byte; `backup.sh` compares daily and writes `superdl_pg_hba_drift` |
| `roles.sql` | `psql -U postgres -d superdl -v app_password="'…'" -v ON_ERROR_STOP=1 -f roles.sql` (idempotent, re-run after every `alembic upgrade head`) | Creates the application role `superdl_app` (not superuser, not owner, DML only; `balance_ledger` / `audit_log` / `instance_events` read + append only, `alembic_version` read-only) + default privileges so tables created by later migrations are granted automatically; `audit_log_prune(integer)` (a SECURITY DEFINER function created by a migration, days ≥ 30) is revoked from PUBLIC and granted EXECUTE to `superdl_app` only, the worker's audit retention cleanup goes only through it |
| `backup.sh` | `/etc/cron.daily/superdl-pg-backup` | Daily `pg_dump -Fc` → gpg AES256 → 14 days kept locally → rsync to the mirror's `dump/` (`rrsync` write-only) → restore smoke (restore into a temporary database, count three fund tables) → pg_hba drift comparison → write `.last-success` and `superdl_pg_backup.prom` |
| `wal-sync.sh` | `/etc/cron.d/superdl-pg-wal-sync` (every 5 minutes, flock against re-entry) | Every segment in `wal_archive/` is gpg-encrypted to `/var/lib/superdl/pg/wal_enc/<segment>.gpg`, `wal_enc/` is rsynced to the mirror's `wal/` (the mirror holds only ciphertext); every Sunday one `pg_basebackup` (gpg) into `base/` and the mirror's `base/`; both local directories keep 21 days; writes `superdl_pg_wal_sync.prom` |

Passwords and keys: `/etc/superdl/pg/pg.env` (`POSTGRES_USER=postgres` + `POSTGRES_PASSWORD`, the bootstrap superuser), `/etc/superdl/pg/app.env` (`SUPERDL_APP_PASSWORD`), `/etc/superdl/pg/backup-passphrase` (gpg passphrase, **a second copy must live in the password manager**, shared by the dump / basebackup / WAL / k3s state chains), `/etc/superdl/pg/backup-ssh-key` (the rsync-only key to the mirror host's root; the mirror's `authorized_keys` locks it to write-only with `restrict,command="/usr/bin/rrsync -wo <directory>"`), `/etc/superdl/pg/backup.env` (`SUPERDL_PG_MIRROR=<user@host:pg-mirror>`; optional `SUPERDL_PG_HBA_REF=<path of the repository copy>` for the drift comparison). All 0600 root.

**The mirror host must not be a node that carries tenant workloads** (a machine tenants can reach compromises all three backup chains once breached); pre-create `dump/ base/ wal/ k3s/` under the mirror's rrsync directory.

## Roles

- `postgres`: the bootstrap superuser (image `POSTGRES_USER`), used only for `roles.sql` and host operations, never in any K8s Secret.
- `superdl`: the database owner, `LOGIN NOSUPERUSER NOCREATEROLE CREATEDB REPLICATION` (CREATEDB lets `backup.sh` create the smoke database, REPLICATION serves `pg_basebackup`, both over the socket only), given only to the migration Job (`deploy/app/k8s/10-migrate-job.yaml`, Secret `superdl-db-migrate`) and operations.
- `superdl_app`: api / worker (Secret `superdl-db`). The append-only tables `balance_ledger` / `audit_log` / `instance_events` have no UPDATE / DELETE, `alembic_version` no write; audit retention cleanup only through `audit_log_prune(integer)` (EXECUTE granted to it alone). Tables created by migrations after `roles.sql` carry the DML grants automatically (`ALTER DEFAULT PRIVILEGES FOR ROLE superdl`); a new table that should also be append-only gets a manual `REVOKE UPDATE, DELETE` in its migration. The function is created by a migration, so `roles.sql` must be re-run after `alembic upgrade head` for the GRANT EXECUTE to land.
- Connection strings always use `sslmode=verify-full&sslrootcert=/etc/superdl/db-ca/ca.crt`: the self-signed server certificate is its own CA, loaded as the ConfigMap `superdl/superdl-db-ca` (`kubectl -n superdl create configmap superdl-db-ca --from-file=ca.crt=/etc/superdl/pg/certs/server.crt`), mounted by every Deployment as an optional ConfigMap volume at `/etc/superdl/db-ca`. The certificate SAN must contain the database's listening IP.

Fresh install (`POSTGRES_USER=postgres` in `pg.env`, run as `postgres` after the first start):

```sql
CREATE ROLE superdl LOGIN NOSUPERUSER NOCREATEROLE CREATEDB REPLICATION PASSWORD '…';
CREATE DATABASE superdl OWNER superdl;
```

Existing instance (`superdl` is the bootstrap superuser): run the two statements below as `superdl`, then change `POSTGRES_USER` in `pg.env` to `postgres` (only makes the file match reality; an initialised database does not change because of it):

```sql
CREATE ROLE postgres LOGIN SUPERUSER PASSWORD '…';
ALTER ROLE superdl NOSUPERUSER NOCREATEROLE CREATEDB REPLICATION;
```

## Backup and restore

- RPO: logical backup 24 h; WAL archiving 5 minutes (`archive_timeout=300` forces a segment switch). On the mirror host: `pg-mirror/dump/` (daily dumps), `pg-mirror/base/` (weekly basebackups), `pg-mirror/wal/` (WAL, `.gpg` only), `pg-mirror/k3s/` (k3s cluster state, see `deploy/cluster/README.md` "Cluster state backup and restore").
- Restore from a dump: follow `deploy/cluster/runbooks/pg-backup-restore.md` "Restore steps", `gpg --decrypt` the dump first.
- PITR: take the latest `base/*.tar.gz.gpg`, decrypt and unpack it as the new data directory, create `recovery.signal`, write `recovery_target_time` and the decrypting `restore_command = 'gpg --batch --quiet --decrypt --passphrase-file /etc/superdl/pg/backup-passphrase -o %p /wal/%f.gpg'` into `postgresql.auto.conf` (the WAL directory comes from the mirror's `wal/`), start with the same PG version; steps in the runbook "PITR (self-hosted single instance)".
- Metrics (node-exporter textfile `/var/lib/node_exporter/textfile/`, skipped when the directory is missing):
  - `superdl_pg_backup.prom`: `superdl_pg_backup_last_success_timestamp_seconds` (daily dump + sync + smoke), `superdl_pg_hba_drift` (1 = the three pg_hba copies in the container / on the host / in the repository differ; drift does not block the backup);
  - `superdl_pg_wal_sync.prom`: `superdl_pg_wal_sync_last_success_timestamp_seconds` (every 5 minutes), `superdl_pg_basebackup_last_success_timestamp_seconds` (weekly; keeps the previous value on failure).
  - The existing kps rule alerts only on the first (`PgBackupStandaloneStale`); thresholds for the other three: WAL > 30 minutes, basebackup > 8 days, drift == 1.
- `base/.done-<date>`: created only once the day's basebackup ciphertext is > 1 MiB and synced to the mirror; a Sunday without it retries every 5 minutes (with a local archive > 1 MiB already present that day, only the sync is retried). Files under `base/` smaller than 1 MiB are failure leftovers, delete them locally and on the mirror; old plaintext segments without the `.gpg` suffix in the mirror's `wal/` are cleared by the mirror's retention.
- Drills: `backup.sh` carries its own restore smoke every day; a full restore once per quarter, recorded in the runbook's RTO log; the quarterly checklist includes the newest `base/` file > 1 MiB with mtime < 8 days, `.done-*` present, and both wal-sync metrics fresh.

## Risks

- Host failure = database outage; recovery rebuilds from the dump / base / WAL on the mirror, RTO within the runbook's 30-minute target.
- The WAL archive is a local directory; when the mirror sync fails it keeps growing locally: a failing `wal-sync.sh` alerts continuously (`superdl_pg_wal_sync_last_success_timestamp_seconds` stops updating) without blocking database writes.
- Plaintext WAL exists only in the local `wal_archive/`; losing the gpg passphrase = the dump / base / WAL / k3s state chains all become unrecoverable.
