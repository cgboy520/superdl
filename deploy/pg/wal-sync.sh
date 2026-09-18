#!/bin/bash
# Every 5 minutes: segments in wal_archive/ are gpg-encrypted to wal_enc/<segment>.gpg and wal_enc/ is rsynced to the mirror host wal/ (the mirror holds only ciphertext); every Sunday one pg_basebackup (gpg) into base/ and the mirror host base/.
# Metrics (node-exporter textfile, two gauges in one file): superdl_pg_wal_sync_last_success_timestamp_seconds, superdl_pg_basebackup_last_success_timestamp_seconds; the previous value is kept on failure.
# basebackup success = ciphertext > 1 MiB and synced to the mirror; only then is base/.done-<date> created; on failure the exit is non-zero but the WAL sync still runs, and with a local archive already present that day only the sync is retried.
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
    printf '# HELP superdl_pg_wal_sync_last_success_timestamp_seconds Unix time of the last successful encrypted WAL archive sync to the mirror host (self-hosted PG)\n'
    printf '# TYPE superdl_pg_wal_sync_last_success_timestamp_seconds gauge\n'
    if [[ -n "$1" ]]; then printf 'superdl_pg_wal_sync_last_success_timestamp_seconds %s\n' "$1"; fi
    printf '# HELP superdl_pg_basebackup_last_success_timestamp_seconds Unix time of the last successful weekly pg_basebackup (gpg, synced to the mirror host; self-hosted PG)\n'
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
