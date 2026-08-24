# PostgreSQL 备份与恢复 Runbook

钱包余额与 `balance_ledger` 都在此库,恢复演练每季度一次。

## 备份分层

| 层 | 手段 | RPO |
|---|---|---|
| 逻辑备份 | `deploy/app/k8s/06-pg-backup.yaml` 每日 `pg_dump -Fc` → 对象存储(上传后 restore 冒烟校验要害表行数,失败即 Job Failed 告警) | 24h |
| 连续归档 | CloudNativePG barmanObjectStore S3 WAL 归档 + 每日基础备份(`values/cnpg-cluster.yaml`,cnpg 档);托管 PG 时用 RDS 自动备份 + PITR | 分钟级 |
| 集群元数据 | RKE2 etcd 快照(每 6h,留 12 份,`rke2/server-config.yaml`) | 6h |

对账兜底:`balance_ledger` 是追加式流水且每行带 `balance_after` 快照,恢复后可用
`GET /api/admin/v1/reconciliation` 与流水链校验资金一致性。

## 恢复步骤(逻辑备份)

```bash
# 1. 取最近备份
aws s3 ls s3://superdl-pg-backup/daily/ --endpoint-url $S3_ENDPOINT | tail -5
aws s3 cp s3://superdl-pg-backup/daily/superdl-<ts>.dump /tmp/ --endpoint-url $S3_ENDPOINT

# 2. 停写入(摘 api 流量 + 停全部 worker 组件),防止恢复期间产生分叉账
#    (P1-18 后 worker 共 5 个 Deployment:core/tenant-mgr/node-mgr/prewarm/disk-ops,
#     漏停任何一个,outbox 任务仍在写库)
kubectl -n superdl scale deploy superdl-api --replicas=0
kubectl -n superdl scale deploy superdl-worker superdl-worker-tenant-mgr \
  superdl-worker-node-mgr superdl-worker-prewarm superdl-worker-disk-ops --replicas=0

# 3. 恢复到新库(禁止原地覆盖),核验后再切换连接串
createdb superdl_restore
pg_restore -d superdl_restore --no-owner /tmp/superdl-<ts>.dump

# 4. 核验:行数量级、最新 ledger 时间、alembic 版本
psql superdl_restore -c "SELECT max(created_at) FROM balance_ledger"
psql superdl_restore -c "SELECT version_num FROM alembic_version"

# 5. 切换 SUPERDL_DATABASE_URL → superdl_restore,起 api(worker 组件后起)
kubectl -n superdl scale deploy superdl-api --replicas=2
# 冒烟通过后(worker 组件按 03-worker.yaml 的副本定义恢复)
kubectl -n superdl scale deploy superdl-worker superdl-worker-tenant-mgr --replicas=2
kubectl -n superdl scale deploy superdl-worker-node-mgr superdl-worker-prewarm \
  superdl-worker-disk-ops --replicas=1

# 6. 公告用户:恢复点之后的充值以渠道对账单为准,走管理端「补单」逐笔补入
```

## 恢复演练验收

- [ ] 从最近一次每日备份完整恢复到新库 < 30 分钟
- [ ] `alembic check` 通过;`/readyz` 就绪
- [ ] 抽 3 个用户核对 余额 = 流水链尾部 `balance_after`
- [ ] 管理端补单流程可把恢复点后的渠道已付订单补齐

## 季度演练清单(每季度一次,未演练过的备份视为不存在)

- [ ] 日常冒烟在线:近 7 日 `pg-backup-daily` Job 全部成功(含 restore 冒烟步骤),
      `PgBackupFailed` / `PgBackupStale` 告警静默期内无触发
- [ ] 完整恢复计时:从对象存储取最近一次备份,按「恢复步骤」恢复到隔离库并计时,
      结果填入下方 RTO 记录表
- [ ] 资金一致性:抽 3 个用户核对 余额 = 流水链尾部 `balance_after`;
      `alembic check` 通过
- [ ] PITR 抽检(连续归档层):从 WAL 归档恢复到指定时间点,验证可精确落在
      目标事务前后(cnpg 档用 recovery 模式集群演练;托管 PG 用控制台时间点恢复)
- [ ] 告警链路:手工 fail 一次备份(如临时改错 S3 凭据)确认 `PgBackupFailed`
      触达值班多渠道,随后恢复
- [ ] 记录归档:RTO 记录表更新 + 演练结论写入运维周报;耗时超标时扩资源或改方案

## RTO 记录表

目标 RTO:< 30 分钟(逻辑备份恢复到新库,不含业务切换公告)。

| 日期 | 备份类型(逻辑/CNPG basebackup/RDS) | 数据量 | 恢复耗时 | RTO 达标 | 演练人 | 备注 |
|---|---|---|---|---|---|---|
| | | | | | | |
