#!/bin/bash
# Off-host encrypted backup of the k3s cluster state: server/{token,agent-token,cred,tls} + the latest etcd snapshot + an online consistent SQLite copy → tar → gpg → rsync to the mirror host k3s/.
# Installed as /usr/local/sbin/superdl-k3s-state-backup (ansible site.yml), cron /etc/cron.d/superdl-k3s-state-backup every 6 hours;
# passphrase, mirror host and ssh key reuse /etc/superdl/pg/{backup-passphrase,backup.env,backup-ssh-key}.
# Metric: superdl_k3s_state_backup_last_success_timestamp_seconds in /var/lib/node_exporter/textfile/superdl_k3s_state_backup.prom.
set -euo pipefail
umask 077
SERVER=/var/lib/rancher/k3s/server
OUT=/var/lib/superdl/k3s-state
PASS=/etc/superdl/pg/backup-passphrase
KEY=/etc/superdl/pg/backup-ssh-key
TEXTFILE_DIR=/var/lib/node_exporter/textfile
PROM="$TEXTFILE_DIR/superdl_k3s_state_backup.prom"
RETAIN_DAYS=14
# shellcheck source=/dev/null
[[ -f /etc/superdl/pg/backup.env ]] && source /etc/superdl/pg/backup.env
MIRROR="${SUPERDL_PG_MIRROR:?}"
SSH="ssh -i $KEY -o BatchMode=yes -o StrictHostKeyChecking=accept-new"

log() { echo "k3s-state-backup: $*" >&2; }

[[ -f "$PASS" ]] || { log "missing $PASS"; exit 1; }
[[ -d "$SERVER/cred" && -d "$SERVER/tls" && -f "$SERVER/token" ]] || { log "$SERVER is not a k3s server data dir"; exit 1; }
[[ -f "$SERVER/db/state.db" ]] && chmod 0600 "$SERVER/db/state.db"
mkdir -p "$OUT"
exec 9>"$OUT/.lock"
if ! flock -n 9; then
  log "previous run still in progress, skipping"
  exit 0
fi

ts=$(date -u +%Y%m%dT%H%M%SZ)
host=$(hostname -s)
stage=$(mktemp -d "$OUT/.stage.XXXXXX")
trap 'rm -rf "$stage"' EXIT
mkdir -p "$stage/server/db"
cp -a "$SERVER/cred" "$SERVER/tls" "$SERVER/token" "$stage/server/"
[[ -f "$SERVER/agent-token" ]] && cp -a "$SERVER/agent-token" "$stage/server/"

if [[ -d "$SERVER/db/etcd" ]]; then
  snap=$(find "$SERVER/db/snapshots" -maxdepth 1 -type f -printf '%T@ %p\n' 2>/dev/null | sort -n | tail -n 1 | cut -d' ' -f2-)
  [[ -n "$snap" ]] || { log "etcd datastore but no snapshot under $SERVER/db/snapshots"; exit 1; }
  mkdir -p "$stage/server/db/snapshots"
  cp -a "$snap" "$stage/server/db/snapshots/"
  datastore="etcd snapshot $(basename "$snap")"
elif [[ -f "$SERVER/db/state.db" ]]; then
  command -v sqlite3 >/dev/null || { log "sqlite3 missing: apt-get install sqlite3"; exit 1; }
  sqlite3 "$SERVER/db/state.db" ".backup '$stage/server/db/state.db'"
  datastore="sqlite state.db"
else
  log "no datastore found under $SERVER/db"
  exit 1
fi

out="$OUT/k3s-state-$host-$ts.tar.gz.gpg"
tar -C "$stage" -czf - server \
  | gpg --batch --yes --symmetric --cipher-algo AES256 --passphrase-file "$PASS" -o "$out.tmp"
mv "$out.tmp" "$out"
rsync -a --timeout=600 -e "$SSH" "$out" "$MIRROR:k3s/"
find "$OUT" -maxdepth 1 -name 'k3s-state-*.tar.gz.gpg' -mtime +"$RETAIN_DAYS" -delete
now=$(date -u +%s)
echo "$now" > "$OUT/.last-success"
if [[ -d "$TEXTFILE_DIR" ]]; then
  chmod 0755 "$TEXTFILE_DIR"
  {
    printf '# HELP superdl_k3s_state_backup_last_success_timestamp_seconds Unix time of the last successful encrypted off-host backup of the k3s cluster state (token/cred/tls/datastore)\n'
    printf '# TYPE superdl_k3s_state_backup_last_success_timestamp_seconds gauge\n'
    printf 'superdl_k3s_state_backup_last_success_timestamp_seconds %s\n' "$now"
  } > "$PROM.tmp"
  chmod 0644 "$PROM.tmp"
  mv "$PROM.tmp" "$PROM"
fi
log "ok: $out ($datastore)"
