#!/bin/bash
# WAL 归档异地同步(落位 /etc/cron.d/superdl-pg-wal-sync,每 5 分钟)+ 每周日 pg_basebackup。
# 本机 WAL 归档目录 = 容器 /var/lib/postgresql/wal_archive(compose 的 archive_command);本机与镜像机各留 21 天。
set -euo pipefail
DATA=/var/lib/superdl/pg/data
WAL="$DATA/wal_archive"
BASE=/var/lib/superdl/pg/base
COMPOSE=/etc/superdl/pg/compose.yaml
PASS=/etc/superdl/pg/backup-passphrase
KEY=/etc/superdl/pg/backup-ssh-key
# 镜像机地址等落 /etc/superdl/pg/backup.env(SUPERDL_PG_MIRROR=user@host)
[[ -f /etc/superdl/pg/backup.env ]] && source /etc/superdl/pg/backup.env
MIRROR="${SUPERDL_PG_MIRROR:?}"
SSH="ssh -i $KEY -o BatchMode=yes -o StrictHostKeyChecking=accept-new"
mkdir -p "$WAL" "$BASE"
chown 999:999 "$WAL"   # 容器内 postgres uid
umask 077
if [[ "$(date -u +%u)" == "7" && ! -e "$BASE/.done-$(date -u +%F)" ]]; then
  ts=$(date -u +%Y%m%dT%H%M%SZ)
  docker compose -f "$COMPOSE" exec -T postgres pg_basebackup -U superdl -D - -Ft -X none -z </dev/null \
    | gpg --batch --yes --symmetric --cipher-algo AES256 --passphrase-file "$PASS" -o "$BASE/base-$ts.tar.gz.gpg"
  rsync -a --timeout=600 -e "$SSH" "$BASE/base-$ts.tar.gz.gpg" "$MIRROR:base/"
  touch "$BASE/.done-$(date -u +%F)"
  find "$BASE" -name 'base-*.tar.gz.gpg' -mtime +21 -delete
  find "$BASE" -name '.done-*' -mtime +21 -delete
fi
rsync -a --timeout=300 -e "$SSH" "$WAL/" "$MIRROR:wal/"
find "$WAL" -type f -mmin +$((21 * 24 * 60)) -delete
date -u +%s > "$WAL/../.wal-sync-last-success"
