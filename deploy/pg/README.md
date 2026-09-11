# 自建单实例 PostgreSQL(控制面宿主机 Docker)

`deploy/README.md`「生产数据库要求」之外的第三种形态:PG 18 以 Docker 跑在控制面宿主机上,host 网络监听宿主机内网地址与 loopback。适用于没有托管 PG、又不把库放进 K8s(cnpg)的小规模集群。**单实例,无副本;宿主机即库的单点。**

## 文件

| 文件 | 落位 | 说明 |
|---|---|---|
| `compose.yaml` | `/etc/superdl/pg/compose.yaml` | 镜像按 digest 钉死;`archive_mode=on` + 本地 WAL 归档目录;`hba_file` 指向下面的文件 |
| `pg_hba.conf` | `/etc/superdl/pg/pg_hba.conf` | Unix socket `trust`(只有 `docker exec` 能到);TCP 一律 `hostssl` + `scram-sha-256`,`hostnossl` 全拒 |
| `roles.sql` | `psql -v app_password="'…'" -v ON_ERROR_STOP=1 -f roles.sql`(幂等)| 建应用角色 `superdl_app`(非 superuser、非 owner、只有 DML;`balance_ledger` 只读+追加,`audit_log` 不可 UPDATE)+ 默认权限,让后续迁移建的表自动授权 |
| `backup.sh` | `/etc/cron.daily/superdl-pg-backup` | 每日 `pg_dump -Fc` → gpg AES256 → 本机留 14 天 → rsync 到镜像机(`rrsync` 只写) → 恢复冒烟(恢复到临时库,数三张资金表) → 写 `.last-success` |
| `wal-sync.sh` | `/etc/cron.d/superdl-pg-wal-sync`(每 5 分钟) | WAL 归档目录 rsync 到镜像机;每周日做一份 `pg_basebackup`(gpg)同步过去;本机与镜像机各留 21 天 |

口令与密钥:`/etc/superdl/pg/pg.env`(`POSTGRES_PASSWORD` = owner `superdl`)、`/etc/superdl/pg/app.env`(`SUPERDL_APP_PASSWORD`)、`/etc/superdl/pg/backup-passphrase`(gpg 口令,**必须另存一份到密码管理器**,宿主机没了它就是唯一解密钥)、`/etc/superdl/pg/backup-ssh-key`(到镜像机 root 的 rsync 专用密钥,镜像机 `authorized_keys` 用 `restrict,command="/usr/bin/rrsync -wo <目录>"` 锁成只写)。全部 0600 root。

## 角色

- `superdl`:库 owner,只给迁移 Job(`deploy/app/k8s/10-migrate-job.yaml`,Secret `superdl-db-migrate`)与运维。
- `superdl_app`:api / worker(Secret `superdl-db`)。`roles.sql` 之后再跑迁移新建的表自动带 DML 授权(`ALTER DEFAULT PRIVILEGES FOR ROLE superdl`);新表若也该追加式,迁移里手工 `REVOKE UPDATE, DELETE`。
- 连接串一律 `sslmode=verify-full&sslrootcert=/etc/superdl/db-ca/ca.crt`:自签服务端证书本身就是 CA,灌成 ConfigMap `superdl/superdl-db-ca`(`kubectl -n superdl create configmap superdl-db-ca --from-file=ca.crt=/etc/superdl/pg/certs/server.crt`),各 Deployment 以 optional ConfigMap 卷挂到 `/etc/superdl/db-ca`。证书 SAN 必须含库的监听 IP。

## 备份与恢复

- RPO:逻辑备份 24h;WAL 归档 5 分钟(`archive_timeout=300` 强制切段)。镜像机上 `pg-mirror/dump/`(每日 dump)、`pg-mirror/base/`(每周 basebackup)、`pg-mirror/wal/`(WAL)。
- 从 dump 恢复:按 `deploy/cluster/runbooks/pg-backup-restore.md`「恢复步骤」,dump 先 `gpg --decrypt`。
- PITR:取最近一份 `base/*.tar.gz.gpg` 解密解包为新数据目录,建 `recovery.signal`,`postgresql.auto.conf` 写 `restore_command = 'cp /wal/%f %p'` 与 `recovery_target_time`,以同版本 PG 启动;WAL 目录来自 `wal/`。
- 告警:`PgBackupStale` / `PgBackupFailed` 看 K8s CronJob;本形态不跑 CronJob,由 `backup.sh` 把 `.last-success` 时间戳写进 node-exporter 的 textfile 目录(`/var/lib/node_exporter/textfile/superdl_pg_backup.prom`,指标 `superdl_pg_backup_last_success_timestamp_seconds`),kps 的 `PgBackupStandaloneStale` 据此告警。
- 演练:`backup.sh` 每天自带恢复冒烟;完整恢复每季度一次,记录进 runbook 的 RTO 表。

## 风险(与托管 PG / cnpg 相比)

- 宿主机故障 = 库停机,恢复靠镜像机上的 dump/WAL 重建,RTO 按 runbook 目标 30 分钟内。
- WAL 归档在本地目录,镜像机同步失败时本地继续累积:`wal-sync.sh` 失败会连续告警(`.last-success` 不更新),不会阻塞库写入。
