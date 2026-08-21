# PostgreSQL 备份与恢复 Runbook

钱包余额与 `balance_ledger` 都在此库,恢复演练每季度一次。

## 备份分层

| 层 | 手段 | RPO |
|---|---|---|
| 逻辑备份 | `deploy/app/k8s/06-pg-backup.yaml` 每日 `pg_dump -Fc` → 对象存储 | 24h |
| 连续归档 | pgBackRest / WAL-G(PITR)或云 RDS 自动备份 | 分钟级 |
| 集群元数据 | RKE2 etcd 快照(每 6h,留 12 份,`rke2/server-config.yaml`) | 6h |

对账兜底:`balance_ledger` 是追加式流水且每行带 `balance_after` 快照,恢复后可用
`GET /api/admin/v1/reconciliation` 与流水链校验资金一致性。

## 恢复步骤(逻辑备份)

```bash
# 1. 取最近备份
aws s3 ls s3://superdl-pg-backup/daily/ --endpoint-url $S3_ENDPOINT | tail -5
aws s3 cp s3://superdl-pg-backup/daily/superdl-<ts>.dump /tmp/ --endpoint-url $S3_ENDPOINT

# 2. 停写入(摘 api 流量 + 停 worker,防止恢复期间产生分叉账)
kubectl -n superdl scale deploy superdl-api superdl-worker --replicas=0

# 3. 恢复到新库(禁止原地覆盖),核验后再切换连接串
createdb superdl_restore
pg_restore -d superdl_restore --no-owner /tmp/superdl-<ts>.dump

# 4. 核验:行数量级、最新 ledger 时间、alembic 版本
psql superdl_restore -c "SELECT max(created_at) FROM balance_ledger"
psql superdl_restore -c "SELECT version_num FROM alembic_version"

# 5. 切换 SUPERDL_DATABASE_URL → superdl_restore,起 api(worker 后起)
kubectl -n superdl scale deploy superdl-api --replicas=2
# 冒烟通过后
kubectl -n superdl scale deploy superdl-worker --replicas=1

# 6. 公告用户:恢复点之后的充值以渠道对账单为准,走管理端「补单」逐笔补入
```

## 恢复演练验收

- [ ] 从最近一次每日备份完整恢复到新库 < 30 分钟
- [ ] `alembic check` 通过;`/readyz` 就绪
- [ ] 抽 3 个用户核对 余额 = 流水链尾部 `balance_after`
- [ ] 管理端补单流程可把恢复点后的渠道已付订单补齐
