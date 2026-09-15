#!/bin/bash
# 每日:pg_dump -Fc → gpg → 本机留 14 天 → rsync 镜像机 dump/ → 恢复冒烟(临时库数三张资金表)→ 写 .last-success 与 textfile 指标。
# 顺带比对 pg_hba:容器内 /etc/pg/pg_hba.conf 对 /etc/superdl/pg/pg_hba.conf,后者再对 SUPERDL_PG_HBA_REF(backup.env 可选,仓库副本路径);不一致只告警并写 superdl_pg_hba_drift 1。
set -euo pipefail
B=/var/lib/superdl/pg/backup
COMPOSE=/etc/superdl/pg/compose.yaml
PASS=/etc/superdl/pg/backup-passphrase
KEY=/etc/superdl/pg/backup-ssh-key
HBA=/etc/superdl/pg/pg_hba.conf
# shellcheck source=/dev/null
[[ -f /etc/superdl/pg/backup.env ]] && source /etc/superdl/pg/backup.env
MIRROR="${SUPERDL_PG_MIRROR:?}"
HBA_REF="${SUPERDL_PG_HBA_REF:-}"
TEXTFILE_DIR=/var/lib/node_exporter/textfile
ts=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "$B"
umask 077
hba_drift=0
if ! docker compose -f "$COMPOSE" exec -T postgres cat /etc/pg/pg_hba.conf </dev/null | diff -q - "$HBA" >/dev/null; then
  hba_drift=1
  echo "warning: pg_hba drift: container /etc/pg/pg_hba.conf != $HBA (bind mount stale; restart postgres)" >&2
fi
if [[ -n "$HBA_REF" ]] && ! diff -q "$HBA_REF" "$HBA" >/dev/null; then
  hba_drift=1
  echo "warning: pg_hba drift: $HBA != $HBA_REF" >&2
fi
docker compose -f "$COMPOSE" exec -T postgres pg_dump -U superdl -Fc superdl > "$B/superdl-$ts.dump.tmp" </dev/null
gpg --batch --yes --symmetric --cipher-algo AES256 --passphrase-file "$PASS" \
  -o "$B/superdl-$ts.dump.gpg" "$B/superdl-$ts.dump.tmp"
rm -f "$B/superdl-$ts.dump.tmp"
find "$B" -name 'superdl-*.dump.gpg' -mtime +14 -delete
find "$B" -name 'superdl-*.dump' -delete
rsync -a --timeout=120 -e "ssh -i $KEY -o BatchMode=yes -o StrictHostKeyChecking=accept-new" \
  "$B/superdl-$ts.dump.gpg" "$MIRROR:dump/"
gpg --batch --yes --quiet --decrypt --passphrase-file "$PASS" -o "$B/.smoke.dump" "$B/superdl-$ts.dump.gpg"
docker compose -f "$COMPOSE" exec -T postgres psql -U superdl -d postgres -qc "DROP DATABASE IF EXISTS superdl_restore_smoke" </dev/null
docker compose -f "$COMPOSE" exec -T postgres psql -U superdl -d postgres -qc "CREATE DATABASE superdl_restore_smoke" </dev/null
docker compose -f "$COMPOSE" exec -T postgres pg_restore -U superdl -d superdl_restore_smoke --no-owner < "$B/.smoke.dump"
for t in wallets balance_ledger settlement_watermarks; do
  n=$(docker compose -f "$COMPOSE" exec -T postgres psql -U superdl -d superdl_restore_smoke -v ON_ERROR_STOP=1 -tAc "SELECT count(*) FROM $t" </dev/null)
  echo "smoke: $t rows=$n"
done
docker compose -f "$COMPOSE" exec -T postgres psql -U superdl -d postgres -qc "DROP DATABASE superdl_restore_smoke" </dev/null
rm -f "$B/.smoke.dump"
date -u +%s > "$B/.last-success"
if [[ -d "$TEXTFILE_DIR" ]]; then
  chmod 0755 "$TEXTFILE_DIR"
  {
    printf '# HELP superdl_pg_backup_last_success_timestamp_seconds 自建 PG 每日备份(含异地同步与恢复冒烟)最近一次成功的 Unix 时间\n'
    printf '# TYPE superdl_pg_backup_last_success_timestamp_seconds gauge\n'
    printf 'superdl_pg_backup_last_success_timestamp_seconds %s\n' "$(cat "$B/.last-success")"
    printf '# HELP superdl_pg_hba_drift 自建 PG 的 pg_hba.conf 容器内 / 宿主机 / 仓库副本不一致(1 = 不一致)\n'
    printf '# TYPE superdl_pg_hba_drift gauge\n'
    printf 'superdl_pg_hba_drift %s\n' "$hba_drift"
  } > "$TEXTFILE_DIR/superdl_pg_backup.prom.tmp"
  chmod 0644 "$TEXTFILE_DIR/superdl_pg_backup.prom.tmp"
  mv "$TEXTFILE_DIR/superdl_pg_backup.prom.tmp" "$TEXTFILE_DIR/superdl_pg_backup.prom"
fi
echo "backup ok: superdl-$ts.dump.gpg (hba_drift=$hba_drift)"
