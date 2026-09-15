# PostgreSQL 备份与恢复 Runbook

恢复演练每季度一次。

> **上线前强制项(公众生产闸)**:分钟级 RPO 二选一:① 托管 PG:确认 PITR 与保留策略已开(preflight 以 `SUPERDL_MANAGED_PG_PITR_ACK=yes` 登记,full 档未登记判红);② 自建 cnpg 档:启用 `cnpg.enabled`(full 档默认开)且 preflight 全绿(S3 归档无占位符、ScheduledBackup 在跑)。
> 首次切流前按「恢复步骤」+「PITR 抽检」完整演练一次并填 RTO 记录表。

## 备份分层

| 层 | 手段 | RPO |
|---|---|---|
| 逻辑备份 | `deploy/app/k8s/06-pg-backup.yaml` 每日 `pg_dump -Fc` → **gpg AES256 客户端加密**(口令 = `superdl-pg-backup` 的 `BACKUP_ENCRYPT_KEY`)→ 对象存储;上传后 restore 冒烟(解密 + pg_restore + 要害表可查询),失败即 Job Failed 告警 | 24h |
| 连续归档 | CloudNativePG barmanObjectStore S3 WAL 归档 + 每日基础备份(`values/cnpg-cluster.yaml`,cnpg 档);托管 PG 时用 RDS 自动备份 + PITR | 分钟级 |
| 自建单实例(`deploy/pg/`) | `backup.sh` 每日 dump → gpg → 镜像机 + 恢复冒烟 + pg_hba 漂移比对;`wal-sync.sh` 每 5 分钟把 WAL 段 gpg 后同步(镜像机只有 `.gpg`)、每周日 `pg_basebackup`(gpg,密文 > 1 MiB 且已同步才算成功并建 `base/.done-<日期>`);指标 `superdl_pg_backup_last_success_timestamp_seconds` / `superdl_pg_wal_sync_last_success_timestamp_seconds` / `superdl_pg_basebackup_last_success_timestamp_seconds` / `superdl_pg_hba_drift`(node-exporter textfile),告警 `PgBackupStandaloneStale`(只看第一条) | dump 24h / WAL 5 分钟 |
| 集群元数据 | RKE2 etcd 快照(每 6h,留 12 份,`rke2/server-config.yaml`);k3s 内嵌 etcd 快照(每 6h,留 28 份,`k3s/server-config.yaml`)+ `k3s/state-backup.sh` 每 6h 把 token / cred / tls / datastore 加密到 PG 镜像机 `k3s/`(指标 `superdl_k3s_state_backup_last_success_timestamp_seconds`;恢复见 `deploy/cluster/README.md`「集群状态备份与恢复」) | 6h |

对账:`balance_ledger` 追加式且每行带 `balance_after`,恢复后用 `GET /api/admin/v1/reconciliation` 与流水链校验资金一致性。

## 恢复步骤(逻辑备份)

分段执行,不要把本节作为连续脚本运行。先下载最近的密文备份并解密,解密口令来自 `superdl-pg-backup` 的 `BACKUP_ENCRYPT_KEY`;恢复后立即清理明文。

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

确认 API 与全部 5 个 worker Deployment 已停止写入后,恢复到新库;**禁止原地覆盖生产库**。

```bash
createdb superdl_restore
pg_restore -d superdl_restore --no-owner /tmp/superdl-<ts>.dump
shred -u /tmp/superdl-<ts>.dump 2>/dev/null || rm -f /tmp/superdl-<ts>.dump

psql superdl_restore -c "SELECT max(created_at) FROM balance_ledger"
psql superdl_restore -c "SELECT version_num FROM alembic_version"

```

核验恢复库的行数量级、最新流水时间与 Alembic 版本。核验通过后,将应用连接配置 `SUPERDL_DATABASE_URL` 切换到 `superdl_restore`,确认 API 与各 worker 将使用新连接,再单独启动 API:

```bash
kubectl -n superdl scale deploy superdl-api --replicas=2
```

API `/readyz` 与业务冒烟通过后,按 `03-worker.yaml` 的副本定义恢复全部 worker:

```bash
kubectl -n superdl scale deploy superdl-worker superdl-worker-tenant-mgr --replicas=2
kubectl -n superdl scale deploy superdl-worker-node-mgr superdl-worker-prewarm \
  superdl-worker-disk-ops --replicas=1
```

公告用户恢复点;恢复点之后的充值以渠道对账单为准,走管理端「补单」逐笔补入。

## PITR(自建单实例)

材料全在镜像机 `pg-mirror/`:`base/base-<ts>.tar.gz.gpg`(取目标时间点之前最近一份)、`wal/*.gpg`(镜像机只有密文)、gpg 口令 `/etc/superdl/pg/backup-passphrase`(与备份机同一份,先复制到恢复机)。在隔离机器上、同版本 PG(镜像 digest 见 `deploy/pg/compose.yaml`;容器则把数据目录挂到镜像的 PGDATA `/var/lib/postgresql/18/docker`)执行:

```bash
mkdir -p /restore/data /restore/wal
gpg --batch --quiet --decrypt --passphrase-file /etc/superdl/pg/backup-passphrase base-<ts>.tar.gz.gpg | tar -xzf - -C /restore/data
rsync -a '<镜像机>:/var/lib/superdl/pg-mirror/wal/' /restore/wal/
chown -R 999:999 /restore/data /restore/wal && chmod 0700 /restore/data
touch /restore/data/recovery.signal
cat >> /restore/data/postgresql.auto.conf <<'EOF'
restore_command = 'gpg --batch --quiet --decrypt --passphrase-file /etc/superdl/pg/backup-passphrase -o %p /wal/%f.gpg'
recovery_target_time = '<YYYY-MM-DD HH:MM:SS+00>'
recovery_target_action = 'promote'
EOF
```

`restore_command` 在 PG 进程里跑 `gpg`,PG 所在环境须有 gpg 与口令文件(容器则把 `/wal` 与口令文件只读挂进去);没有 gpg 的环境先在宿主机整批解密(`for f in /restore/wal/*.gpg; do gpg --batch --quiet --decrypt --passphrase-file /etc/superdl/pg/backup-passphrase -o "${f%.gpg}" "$f"; done`)再用 `restore_command = 'cp /wal/%f %p'`。启动后看日志到 `recovery stopping before commit of transaction … / database system is ready`,按「恢复步骤」核验流水与 Alembic 版本后再切流。

## 恢复演练验收

- [ ] 从最近一次每日备份完整恢复到新库 < 30 分钟
- [ ] `alembic check` 通过;`/readyz` 就绪
- [ ] 抽 3 个用户核对 余额 = 流水链尾部 `balance_after`
- [ ] 管理端补单流程可把恢复点后的渠道已付订单补齐

## 季度演练清单(每季度一次)

- [ ] 日常冒烟在线:近 7 日 `pg-backup-daily` Job 全部成功(含 restore 冒烟),`PgBackupFailed` / `PgBackupStale` 无触发
- [ ] 完整恢复计时:从对象存储取最近一次备份,按「恢复步骤」恢复到隔离库并计时,结果填入下方 RTO 记录表
- [ ] 资金一致性:抽 3 个用户核对 余额 = 流水链尾部 `balance_after`;`alembic check` 通过
- [ ] PITR 抽检:从 WAL 归档恢复到指定时间点(cnpg 档用 recovery 模式集群;托管 PG 用控制台时间点恢复;自建单实例按上文「PITR(自建单实例)」用镜像机上的 base + `.gpg` wal)
- [ ] 自建单实例备份链在线:镜像机 `base/` 最新文件 > 1 MiB 且 mtime < 8 天;备份机 `base/.done-<最近周日>` 存在;`superdl_pg_wal_sync_last_success_timestamp_seconds` 距今 < 10 分钟、`superdl_pg_basebackup_last_success_timestamp_seconds` 距今 < 8 天;`superdl_pg_hba_drift == 0`
- [ ] 集群状态备份在线(k3s):镜像机 `k3s/` 最新文件 mtime < 12 小时,`superdl_k3s_state_backup_last_success_timestamp_seconds` 距今 < 12 小时;镜像机不是承载租户负载的节点
- [ ] 告警链路:手工 fail 一次备份(如临时改错 S3 凭据)确认 `PgBackupFailed` 触达值班,随后恢复
- [ ] 记录归档:RTO 记录表更新 + 演练结论写入运维周报

## RTO 记录表

目标 RTO:< 30 分钟(逻辑备份恢复到新库,不含业务切换公告)。

| 日期 | 备份类型(逻辑/CNPG basebackup/RDS) | 数据量 | 恢复耗时 | RTO 达标 | 演练人 | 备注 |
|---|---|---|---|---|---|---|
| | | | | | | |
