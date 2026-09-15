#!/bin/bash
# 每 5 分钟:wal_archive/ 的段 gpg 到 wal_enc/<段>.gpg,wal_enc/ rsync 到镜像机 wal/(镜像机只有密文);每周日一份 pg_basebackup(gpg)到 base/ 与镜像机 base/。
# 指标(node-exporter textfile,两条 gauge 同一文件):superdl_pg_wal_sync_last_success_timestamp_seconds、superdl_pg_basebackup_last_success_timestamp_seconds;失败保留上次值。
# basebackup 成功 = 密文 > 1 MiB 且已同步到镜像机,成功才建 base/.done-<日期>;失败退出非零但 WAL 同步照常执行,同日已有本地归档则只补同步。
set -euo pipefail
DATA=/var/lib/superdl/pg/data
WAL="$DATA/wal_archive"
WAL_ENC=/var/lib/superdl/pg/wal_enc
BASE=/var/lib/superdl/pg/base
COMPOSE=/etc/superdl/pg/compose.yaml
PASS=/etc/superdl/pg/backup-passphrase
KEY=/etc/superdl/pg/backup-ssh-key
TEXTFILE_DIR=/var/lib/node_exporter/textfile
PROM="$TEXTFILE_DIR/superdl_pg_wal_sync.prom"
RETAIN_DAYS=21
BASE_MIN_BYTES=$((1024 * 1024))
# shellcheck source=/dev/null
[[ -f /etc/superdl/pg/backup.env ]] && source /etc/superdl/pg/backup.env
MIRROR="${SUPERDL_PG_MIRROR:?}"
SSH="ssh -i $KEY -o BatchMode=yes -o StrictHostKeyChecking=accept-new"
umask 077
mkdir -p "$WAL" "$WAL_ENC" "$BASE"
chown 999:999 "$WAL"
exec 9>/var/lib/superdl/pg/.wal-sync.lock
if ! flock -n 9; then
  echo "wal-sync: previous run still in progress, skipping" >&2
  exit 0
fi

log() { echo "wal-sync: $*" >&2; }

prom_get() {
  [[ -f "$PROM" ]] || return 0
  awk -v m="$1" '$1 == m { print $2 }' "$PROM"
}

write_prom() {
  [[ -d "$TEXTFILE_DIR" ]] || return 0
  chmod 0755 "$TEXTFILE_DIR"
  {
    printf '# HELP superdl_pg_wal_sync_last_success_timestamp_seconds 自建 PG WAL 归档加密并同步到镜像机最近一次成功的 Unix 时间\n'
    printf '# TYPE superdl_pg_wal_sync_last_success_timestamp_seconds gauge\n'
    if [[ -n "$1" ]]; then printf 'superdl_pg_wal_sync_last_success_timestamp_seconds %s\n' "$1"; fi
    printf '# HELP superdl_pg_basebackup_last_success_timestamp_seconds 自建 PG 每周 pg_basebackup(gpg 且已同步到镜像机)最近一次成功的 Unix 时间\n'
    printf '# TYPE superdl_pg_basebackup_last_success_timestamp_seconds gauge\n'
    if [[ -n "$2" ]]; then printf 'superdl_pg_basebackup_last_success_timestamp_seconds %s\n' "$2"; fi
  } > "$PROM.tmp"
  chmod 0644 "$PROM.tmp"
  mv "$PROM.tmp" "$PROM"
}

make_basebackup() {
  local ts tmp out size
  ts=$(date -u +%Y%m%dT%H%M%SZ)
  out="$BASE/base-$ts.tar.gz.gpg"
  tmp="$out.tmp"
  if ! docker compose -f "$COMPOSE" exec -T postgres pg_basebackup -U superdl -D - -Ft -X none -z </dev/null \
    | gpg --batch --yes --symmetric --cipher-algo AES256 --passphrase-file "$PASS" -o "$tmp"; then
    rm -f "$tmp"
    log "pg_basebackup failed"
    return 1
  fi
  size=$(stat -c %s "$tmp" 2>/dev/null || echo 0)
  if (( size < BASE_MIN_BYTES )); then
    rm -f "$tmp"
    log "basebackup archive too small (${size} bytes), discarded"
    return 1
  fi
  mv "$tmp" "$out"
  echo "$out"
}

basebackup() {
  local today out
  today=$(date -u +%F)
  out=$(find "$BASE" -maxdepth 1 -name "base-${today//-/}T*.tar.gz.gpg" -size +1M | sort | tail -n 1)
  if [[ -z "$out" ]]; then
    out=$(make_basebackup) || return 1
  fi
  if ! rsync -a --timeout=600 -e "$SSH" "$out" "$MIRROR:base/"; then
    log "basebackup rsync failed: $out"
    return 1
  fi
  touch "$BASE/.done-$today"
  find "$BASE" -name 'base-*.tar.gz.gpg' -mtime +"$RETAIN_DAYS" -delete
  find "$BASE" -name '.done-*' -mtime +"$RETAIN_DAYS" -delete
  log "basebackup ok: $out"
}

wal_sync() {
  local f name
  for f in "$WAL"/*; do
    [[ -f "$f" ]] || continue
    name=$(basename "$f")
    if [[ -e "$WAL_ENC/$name.gpg" ]]; then continue; fi
    if ! gpg --batch --yes --symmetric --cipher-algo AES256 --passphrase-file "$PASS" -o "$WAL_ENC/$name.gpg.tmp" "$f"; then
      rm -f "$WAL_ENC/$name.gpg.tmp"
      log "wal encrypt failed: $name"
      return 1
    fi
    mv "$WAL_ENC/$name.gpg.tmp" "$WAL_ENC/$name.gpg"
  done
  if ! rsync -a --timeout=300 --exclude '*.tmp' -e "$SSH" "$WAL_ENC/" "$MIRROR:wal/"; then
    log "wal rsync failed"
    return 1
  fi
  find "$WAL" -type f -mmin +$((RETAIN_DAYS * 24 * 60)) -delete
  find "$WAL_ENC" -type f -mmin +$((RETAIN_DAYS * 24 * 60)) -delete
}

rc=0
base_ts=$(prom_get superdl_pg_basebackup_last_success_timestamp_seconds)
wal_ts=$(prom_get superdl_pg_wal_sync_last_success_timestamp_seconds)
if [[ "$(date -u +%u)" == "7" && ! -e "$BASE/.done-$(date -u +%F)" ]]; then
  if basebackup; then
    base_ts=$(date -u +%s)
    echo "$base_ts" > "$BASE/.last-success"
  else
    rc=1
  fi
fi
if wal_sync; then
  wal_ts=$(date -u +%s)
  echo "$wal_ts" > "$DATA/.wal-sync-last-success"
else
  rc=1
fi
write_prom "$wal_ts" "$base_ts"
exit "$rc"
