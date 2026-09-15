# 自建单实例 PostgreSQL(控制面宿主机 Docker)

`deploy/README.md`「生产数据库要求」之外的第三种形态:PG 18 以 Docker 跑在控制面宿主机上,host 网络监听宿主机内网地址与 loopback。适用于没有托管 PG、又不把库放进 K8s(cnpg)的小规模集群。**单实例,无副本;宿主机即库的单点。**

## 文件

| 文件 | 落位 | 说明 |
|---|---|---|
| `compose.yaml` | `/etc/superdl/pg/compose.yaml` | 镜像按 digest 钉死;`archive_mode=on` + 本地 WAL 归档目录;`hba_file` 指向容器内 `/etc/pg/pg_hba.conf`(bind 自下面的文件) |
| `pg_hba.conf` | `/etc/superdl/pg/pg_hba.conf` | Unix socket `trust`(`replication` 单独一行,`all` 不含它,供 `pg_basebackup`);TCP 一律 `hostssl` + `scram-sha-256`,`hostnossl` 全拒。生产文件须与仓库逐字一致,`backup.sh` 每日比对并写 `superdl_pg_hba_drift` |
| `roles.sql` | `psql -U postgres -d superdl -v app_password="'…'" -v ON_ERROR_STOP=1 -f roles.sql`(幂等,每次 `alembic upgrade head` 之后重跑)| 建应用角色 `superdl_app`(非 superuser、非 owner、只有 DML;`balance_ledger` / `audit_log` / `instance_events` 只读+追加,`alembic_version` 只读)+ 默认权限,让后续迁移建的表自动授权;`audit_log_prune(integer)`(迁移建的 SECURITY DEFINER 函数,days ≥ 30)收回 PUBLIC 后只授 `superdl_app` EXECUTE,worker 的审计留存清理只经它 |
| `backup.sh` | `/etc/cron.daily/superdl-pg-backup` | 每日 `pg_dump -Fc` → gpg AES256 → 本机留 14 天 → rsync 到镜像机 `dump/`(`rrsync` 只写) → 恢复冒烟(恢复到临时库,数三张资金表) → pg_hba 漂移比对 → 写 `.last-success` 与 `superdl_pg_backup.prom` |
| `wal-sync.sh` | `/etc/cron.d/superdl-pg-wal-sync`(每 5 分钟,flock 防重入) | `wal_archive/` 每个段 gpg 到 `/var/lib/superdl/pg/wal_enc/<段>.gpg`,`wal_enc/` rsync 到镜像机 `wal/`(镜像机只有密文);每周日一份 `pg_basebackup`(gpg)到 `base/` 与镜像机 `base/`;本机两目录各留 21 天;写 `superdl_pg_wal_sync.prom` |

口令与密钥:`/etc/superdl/pg/pg.env`(`POSTGRES_USER=postgres` + `POSTGRES_PASSWORD`,引导 superuser)、`/etc/superdl/pg/app.env`(`SUPERDL_APP_PASSWORD`)、`/etc/superdl/pg/backup-passphrase`(gpg 口令,**必须另存一份到密码管理器**,dump / basebackup / WAL / k3s 状态四条链共用)、`/etc/superdl/pg/backup-ssh-key`(到镜像机 root 的 rsync 专用密钥,镜像机 `authorized_keys` 用 `restrict,command="/usr/bin/rrsync -wo <目录>"` 锁成只写)、`/etc/superdl/pg/backup.env`(`SUPERDL_PG_MIRROR=<user@host:pg-mirror>`;可选 `SUPERDL_PG_HBA_REF=<仓库副本路径>` 给漂移比对)。全部 0600 root。

**镜像机不得是承载租户负载的节点**(租户可触达的机器失陷即三条备份链失陷);镜像机 rrsync 目录下预建 `dump/ base/ wal/ k3s/`。

## 角色

- `postgres`:引导 superuser(镜像 `POSTGRES_USER`),只用于 `roles.sql` 与宿主机运维,不进任何 K8s Secret。
- `superdl`:库 owner,`LOGIN NOSUPERUSER NOCREATEROLE CREATEDB REPLICATION`(CREATEDB 供 `backup.sh` 建冒烟库,REPLICATION 供 `pg_basebackup`,两者只经 socket),只给迁移 Job(`deploy/app/k8s/10-migrate-job.yaml`,Secret `superdl-db-migrate`)与运维。
- `superdl_app`:api / worker(Secret `superdl-db`)。追加式表 `balance_ledger` / `audit_log` / `instance_events` 无 UPDATE / DELETE,`alembic_version` 无写权限,审计留存清理只经 `audit_log_prune(integer)`(EXECUTE 只授它)。`roles.sql` 之后再跑迁移新建的表自动带 DML 授权(`ALTER DEFAULT PRIVILEGES FOR ROLE superdl`);新表若也该追加式,迁移里手工 `REVOKE UPDATE, DELETE`。函数由迁移创建,`roles.sql` 须在 `alembic upgrade head` 之后重跑,GRANT EXECUTE 才落地。
- 连接串一律 `sslmode=verify-full&sslrootcert=/etc/superdl/db-ca/ca.crt`:自签服务端证书本身就是 CA,灌成 ConfigMap `superdl/superdl-db-ca`(`kubectl -n superdl create configmap superdl-db-ca --from-file=ca.crt=/etc/superdl/pg/certs/server.crt`),各 Deployment 以 optional ConfigMap 卷挂到 `/etc/superdl/db-ca`。证书 SAN 必须含库的监听 IP。

新装(`pg.env` 的 `POSTGRES_USER=postgres`,首次启动后以 `postgres` 执行):

```sql
CREATE ROLE superdl LOGIN NOSUPERUSER NOCREATEROLE CREATEDB REPLICATION PASSWORD '…';
CREATE DATABASE superdl OWNER superdl;
```

已有实例(`superdl` 是引导 superuser):以 `superdl` 执行下面两句,再把 `pg.env` 的 `POSTGRES_USER` 改成 `postgres`(只影响文件与实际一致,已初始化的库不会因此改变):

```sql
CREATE ROLE postgres LOGIN SUPERUSER PASSWORD '…';
ALTER ROLE superdl NOSUPERUSER NOCREATEROLE CREATEDB REPLICATION;
```

## 备份与恢复

- RPO:逻辑备份 24h;WAL 归档 5 分钟(`archive_timeout=300` 强制切段)。镜像机上 `pg-mirror/dump/`(每日 dump)、`pg-mirror/base/`(每周 basebackup)、`pg-mirror/wal/`(WAL,只有 `.gpg`)、`pg-mirror/k3s/`(k3s 集群状态,见 `deploy/cluster/README.md`「集群状态备份与恢复」)。
- 从 dump 恢复:按 `deploy/cluster/runbooks/pg-backup-restore.md`「恢复步骤」,dump 先 `gpg --decrypt`。
- PITR:取最近一份 `base/*.tar.gz.gpg` 解密解包为新数据目录,建 `recovery.signal`,`postgresql.auto.conf` 写 `recovery_target_time` 与解密式 `restore_command = 'gpg --batch --quiet --decrypt --passphrase-file /etc/superdl/pg/backup-passphrase -o %p /wal/%f.gpg'`(WAL 目录来自镜像机 `wal/`),以同版本 PG 启动;步骤见 runbook「PITR(自建单实例)」。
- 指标(node-exporter textfile `/var/lib/node_exporter/textfile/`,目录不存在则跳过):
  - `superdl_pg_backup.prom`:`superdl_pg_backup_last_success_timestamp_seconds`(每日 dump + 同步 + 冒烟)、`superdl_pg_hba_drift`(1 = 容器内 / 宿主机 / 仓库副本三份 pg_hba 不一致;漂移不阻断备份);
  - `superdl_pg_wal_sync.prom`:`superdl_pg_wal_sync_last_success_timestamp_seconds`(每 5 分钟)、`superdl_pg_basebackup_last_success_timestamp_seconds`(每周;失败保留上次值)。
  - kps 现有规则只对第一条告警(`PgBackupStandaloneStale`);其余三条的阈值口径:WAL > 30 分钟、basebackup > 8 天、drift == 1。
- `base/.done-<日期>`:只在当天 basebackup 密文 > 1 MiB 且已同步到镜像机后创建;没有它的周日每 5 分钟重试(同日已有 > 1 MiB 的本地归档则只补同步)。`base/` 里小于 1 MiB 的文件是失败残留,本机与镜像机一并删除;镜像机 `wal/` 里无 `.gpg` 后缀的旧明文段随镜像机保留期清掉。
- 演练:`backup.sh` 每天自带恢复冒烟;完整恢复每季度一次,记录进 runbook 的 RTO 表,季度清单含 `base/` 最新文件 > 1 MiB 且 mtime < 8 天、`.done-*` 存在、两条 wal-sync 指标新鲜。

## 风险

- 宿主机故障 = 库停机,恢复靠镜像机上的 dump / base / WAL 重建,RTO 按 runbook 目标 30 分钟内。
- WAL 归档在本地目录,镜像机同步失败时本地继续累积:`wal-sync.sh` 失败会连续告警(`superdl_pg_wal_sync_last_success_timestamp_seconds` 不更新),不会阻塞库写入。
- 明文 WAL 只在本机 `wal_archive/`;gpg 口令丢失 = dump / base / WAL / k3s 状态四条链全部不可恢复。
