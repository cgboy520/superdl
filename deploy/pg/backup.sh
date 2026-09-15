#!/bin/bash
set -euo pipefail
B=/var/lib/superdl/pg/backup
COMPOSE=/etc/superdl/pg/compose.yaml
PASS=/etc/superdl/pg/backup-passphrase
KEY=/etc/superdl/pg/backup-ssh-key
[[ -f /etc/superdl/pg/backup.env ]] && source /etc/superdl/pg/backup.env
MIRROR="${SUPERDL_PG_MIRROR:?}"
TEXTFILE_DIR=/var/lib/node_exporter/textfile
ts=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "$B"
umask 077
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
  printf '# HELP superdl_pg_backup_last_success_timestamp_seconds 自建 PG 每日备份(含异地同步与恢复冒烟)最近一次成功的 Unix 时间\n# TYPE superdl_pg_backup_last_success_timestamp_seconds gauge\nsuperdl_pg_backup_last_success_timestamp_seconds %s\n' "$(cat "$B/.last-success")" \
    > "$TEXTFILE_DIR/superdl_pg_backup.prom.tmp"
  chmod 0644 "$TEXTFILE_DIR/superdl_pg_backup.prom.tmp"
  mv "$TEXTFILE_DIR/superdl_pg_backup.prom.tmp" "$TEXTFILE_DIR/superdl_pg_backup.prom"
fi
echo "backup ok: superdl-$ts.dump.gpg"
