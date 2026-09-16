#!/usr/bin/env bash
set -eEuo pipefail
umask 077

API_BASE="__API_BASE__"
STATE_DIR="${SUPERDL_JOIN_STATE_DIR:-/var/lib/superdl-node-join}"
LOG_FILE="${SUPERDL_JOIN_LOG_FILE:-/var/log/superdl-node-join.log}"
ETC_DIR="${SUPERDL_JOIN_ETC_DIR:-/etc}"
RANCHER_STATE_DIR="${SUPERDL_JOIN_RANCHER_STATE_DIR:-/var/lib/rancher}"
LVM_IMG_DIR="${SUPERDL_JOIN_LVM_DIR:-/var/lib/superdl-lvm}"
IOMMU_GROUPS_DIR="${SUPERDL_JOIN_IOMMU_DIR:-/sys/kernel/iommu_groups}"
OS_RELEASE_FILE="${SUPERDL_JOIN_OS_RELEASE:-/etc/os-release}"
CPUINFO_FILE="${SUPERDL_JOIN_CPUINFO:-/proc/cpuinfo}"
# Kernel IOMMU arguments for x86_64; empty = derived from the CPU vendor (unknown vendors fail closed).
IOMMU_ARGS="${SUPERDL_JOIN_IOMMU_ARGS:-}"
RESUME_UNIT="superdl-node-join-resume"
TOKEN=""
TOKEN_FILE=""
FORCE=0
UNINSTALL=0
CURRENT_PHASE="init"
NEED_REBOOT=0
DRIVER_VERSION=""
CUDA_VERSION=""

PIN_K3S_OFFICIAL="${SUPERDL_JOIN_PIN_K3S_OFFICIAL:-ed01f89fd977bf20ac1516bbebf8370bf3ddbaa55dac8aba610956a4c78cc00b}"
PIN_K3S_CN="${SUPERDL_JOIN_PIN_K3S_CN:-3944aa467eb945b5ff2151a8e4f8d4a5f3a210d31ab39aec81f37606936d0863}"
PIN_RKE2_OFFICIAL="${SUPERDL_JOIN_PIN_RKE2_OFFICIAL:-42983c86d1da64a92061d83afb57630cedd69241989f1b0673f3db6c3d92ee6b}"
PIN_RKE2_CN="${SUPERDL_JOIN_PIN_RKE2_CN:-5541410b86d4d19d927d820be85156e787be3fdb24be72507af52932a1d12de1}"

NVCTK_MIN_VERSION="${SUPERDL_JOIN_NVCTK_MIN_VERSION:-1.17.8}"
POD_PIDS_LIMIT="${SUPERDL_JOIN_POD_PIDS_LIMIT:-4096}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --token-file) TOKEN_FILE="$2"; shift 2 ;;
    --api-base) API_BASE="$2"; shift 2 ;;
    --force) FORCE=1; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[[ "$(id -u)" == "0" ]] || { echo "must run as root (sudo bash ...)" >&2; exit 2; }

if [[ "$UNINSTALL" == "1" ]]; then
  echo "==== $(date -Is) node-join --uninstall ===="
  DISTRO_NAME=""
  for d in rke2 k3s; do
    if [[ -d "$ETC_DIR/rancher/$d" ]] || command -v "$d" >/dev/null 2>&1; then DISTRO_NAME="$d"; fi
  done
  SERVER_HERE=0
  if [[ "$DISTRO_NAME" == "k3s" ]] && systemctl is-active --quiet k3s.service 2>/dev/null; then SERVER_HERE=1; fi
  if [[ "$DISTRO_NAME" == "rke2" ]] && systemctl is-active --quiet rke2-server.service 2>/dev/null; then SERVER_HERE=1; fi
  AGENT="${DISTRO_NAME:+${DISTRO_NAME}-agent.service}"
  if [[ -n "$AGENT" && "$SERVER_HERE" == "0" ]]; then
    systemctl disable --now "$AGENT" 2>/dev/null || true
  fi
  if [[ -n "$DISTRO_NAME" && "$SERVER_HERE" == "0" ]]; then
    for us in "/usr/local/bin/${DISTRO_NAME}-agent-uninstall.sh" "/usr/local/bin/${DISTRO_NAME}-uninstall.sh"; do
      [[ -x "$us" ]] && { echo "-- running $us"; "$us"; }
    done
  elif [[ "$SERVER_HERE" == "1" ]]; then
    echo "-- this host is the ${DISTRO_NAME} server: distribution and server config left untouched; remove the pool label on the platform side (kubectl label node ... node-restriction.kubernetes.io/superdl-pool-)"
  fi
  systemctl disable "${RESUME_UNIT}.service" 2>/dev/null || true
  rm -f "$ETC_DIR/systemd/system/${RESUME_UNIT}.service"
  systemctl disable superdl-nvme-loop.service 2>/dev/null || true
  rm -f "$ETC_DIR/systemd/system/superdl-nvme-loop.service"
  systemctl daemon-reload || true
  rm -f "$ETC_DIR/sysctl.d/99-superdl.conf"
  sysctl --system >/dev/null || true
  if [[ -f "$ETC_DIR/default/grub.d/99-superdl.cfg" ]]; then
    rm -f "$ETC_DIR/default/grub.d/99-superdl.cfg"
    update-grub
  fi
  if [[ -f "$ETC_DIR/modprobe.d/blacklist-nouveau.conf" ]]; then
    rm -f "$ETC_DIR/modprobe.d/blacklist-nouveau.conf"
    update-initramfs -u
  fi
  if [[ -n "$DISTRO_NAME" && "$SERVER_HERE" == "0" ]]; then
    rm -f "$ETC_DIR/rancher/$DISTRO_NAME/config.yaml" "$ETC_DIR/rancher/$DISTRO_NAME/registries.yaml" \
      "$ETC_DIR/rancher/$DISTRO_NAME/harbor-ca.crt" \
      "$RANCHER_STATE_DIR/$DISTRO_NAME/agent/etc/kubelet.conf.d/50-superdl.conf"
  fi
  rm -rf "$STATE_DIR"
  echo "==== uninstall complete: agent removed; delete the node from the cluster on the platform side (kubectl delete node) ===="
  echo "-- note: the superdl-nvme VG and loop image are business data and were left in place; NVIDIA driver / container-toolkit untouched"
  exit 0
fi

[[ -n "$TOKEN_FILE" ]] || { echo "missing --token-file (generate the command under Nodes > Add node in the admin console; the token never goes on the command line)" >&2; exit 2; }
[[ -f "$TOKEN_FILE" ]] || { echo "token file not found: $TOKEN_FILE" >&2; exit 2; }
[[ "$API_BASE" != "__API_BASE__" ]] || { echo "the script must be served by the API (placeholder not replaced) or run with --api-base" >&2; exit 2; }

mkdir -p "$STATE_DIR/done.d"
chmod 700 "$STATE_DIR"
touch "$LOG_FILE"
chmod 644 "$LOG_FILE"
exec > >(tee -a "$LOG_FILE") 2>&1
echo "==== $(date -Is) node-join start (api=$API_BASE) ===="

json_escape() { python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))'; }

use_token_file() {
  TOKEN="$(cat "$1")"
  printf 'header = "Authorization: Bearer %s"\n' "$TOKEN" > "$STATE_DIR/curl.conf"
  chmod 600 "$STATE_DIR/curl.conf"
}

report() {
  local phase="$1" state="$2" message="${3:-}"
  local msg_json extra=""
  msg_json="$(printf '%s' "$message" | json_escape || true)"
  if [[ -n "$DRIVER_VERSION" ]]; then extra+=",\"driver_version\":\"$DRIVER_VERSION\""; fi
  if [[ -n "$CUDA_VERSION" ]]; then extra+=",\"cuda_version\":\"$CUDA_VERSION\""; fi
  curl -fsS -m 10 --retry 2 --config "$STATE_DIR/curl.conf" \
    -H "Content-Type: application/json" \
    -d "{\"phase\":\"$phase\",\"state\":\"$state\",\"message\":$msg_json$extra}" \
    "$API_BASE/api/v1/node-enroll/progress" >/dev/null || true
}

collect_driver_versions() {
  if is_cpu_pool; then return 0; fi
  DRIVER_VERSION="$({ nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null || true; } | head -1 | tr -cd '0-9.')"
  CUDA_VERSION="$({ nvidia-smi 2>/dev/null || true; } | sed -n 's/.*CUDA[^:]*Version: \([0-9.]*\).*/\1/p' | head -1)"
}

on_error() {
  local tail_log
  tail_log="$(tail -n 20 "$LOG_FILE" 2>/dev/null || true)"
  report "$CURRENT_PHASE" failed "step=$CURRENT_PHASE; log tail: $tail_log"
  echo "!! failed at $CURRENT_PHASE; see $LOG_FILE. Fix the cause and rerun the same command to resume" >&2
}
trap on_error ERR

is_cpu_pool() { [[ "$(cfg_get pool)" == "cpu" ]]; }
is_kata_pool() { [[ "$(cfg_get pool)" == "kata" ]]; }

marker() { [[ -f "$STATE_DIR/done.d/$1" ]]; }
mark_done() { touch "$STATE_DIR/done.d/$1"; }

run_step() {
  CURRENT_PHASE="$1"
  if marker "$1"; then echo "-- $1: already done, skipping"; return 0; fi
  echo "== $1 =="
  report "$1" running
  "$2"
  mark_done "$1"
  report "$1" ok
}

cfg_get() { python3 -c "import json,sys; v=json.load(open('$STATE_DIR/bootstrap.json')).get('$1',''); print(v if not isinstance(v,list) else ' '.join(v))"; }

load_distro() {
  DISTRO="$(cfg_get k8s_distro)"
  RANCHER_DIR="$ETC_DIR/rancher/$DISTRO"
  AGENT_UNIT="${DISTRO}-agent.service"
  SERVER_UNIT="$([[ "$DISTRO" == "k3s" ]] && echo k3s.service || echo rke2-server.service)"
}

is_server_node() { systemctl is-active --quiet "$SERVER_UNIT" 2>/dev/null; }

server_kubectl() {
  if [[ "$DISTRO" == "k3s" ]]; then
    k3s kubectl "$@"
  else
    KUBECONFIG="$RANCHER_DIR/rke2.yaml" "${RKE2_BIN_DIR:-/var/lib/rancher/rke2/bin}/kubectl" "$@"
  fi
}

step_bootstrap() {
  local hostname kernel arch os_release gpu_details payload
  hostname="$(hostname)"
  kernel="$(uname -r)"
  arch="$(uname -m)"
  # shellcheck source=/dev/null
  os_release="$(. "$OS_RELEASE_FILE" && echo "$PRETTY_NAME")"
  gpu_details="$({ nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits 2>/dev/null || true; } | head -8 | python3 -c '
import json, sys
out = []
for line in sys.stdin:
    parts = [p.strip() for p in line.strip().split(",")]
    if not parts or not parts[0]:
        continue
    entry = {"name": parts[0]}
    if len(parts) > 1 and parts[1].isdigit():
        entry["memory_mib"] = int(parts[1])
    out.append(entry)
print(json.dumps(out))')"
  gpu_details="$(python3 - "$gpu_details" "$({ lspci 2>/dev/null | grep -i 'nvidia' || true; } | sed 's/.*: //' | head -8)" <<'PYEOF'
import json, re, sys
smi = json.loads(sys.argv[1])
pci = [l.strip() for l in sys.argv[2].splitlines() if l.strip()]
if not smi:
    smi = [{"name": l} for l in pci]
elif pci:
    generic = re.compile(r"^NVIDIA\s+Graphics\s+Device$", re.I)
    for i, entry in enumerate(smi):
        if generic.match(entry.get("name", "")):
            raw = pci[i] if i < len(pci) else pci[0]
            m = re.search(r"\[([^\]]+)\]", raw)
            entry["name"] = m.group(1) if m else raw
print(json.dumps(smi))
PYEOF
)"
  payload="$(python3 - "$hostname" "$os_release" "$kernel" "$arch" "$gpu_details" <<'PYEOF'
import json, sys
print(json.dumps({"hostname": sys.argv[1],
                  "os_info": {"os_release": sys.argv[2], "kernel": sys.argv[3], "arch": sys.argv[4]},
                  "gpu_details": json.loads(sys.argv[5])}))
PYEOF
)"
  curl -fsS -m 15 --retry 2 --config "$STATE_DIR/curl.conf" \
    -H "Content-Type: application/json" \
    -d "$payload" "$API_BASE/api/v1/node-enroll/bootstrap" -o "$STATE_DIR/bootstrap.json"
  chmod 600 "$STATE_DIR/bootstrap.json"
  printf '%s' "$(cfg_get progress_token)" > "$STATE_DIR/token"
  chmod 600 "$STATE_DIR/token"
  use_token_file "$STATE_DIR/token"
  echo "-- bootstrap done: pool=$(cfg_get pool) agent=$(cfg_get cluster_agent_version)"
}

# Every later step is apt / dpkg / update-initramfs / update-grub: only the Debian family is supported.
check_distro() {
  local id id_like pretty
  # shellcheck source=/dev/null
  id="$(. "$OS_RELEASE_FILE" 2>/dev/null && echo "${ID:-}")"
  # shellcheck source=/dev/null
  id_like="$(. "$OS_RELEASE_FILE" 2>/dev/null && echo "${ID_LIKE:-}")"
  # shellcheck source=/dev/null
  pretty="$(. "$OS_RELEASE_FILE" 2>/dev/null && echo "${PRETTY_NAME:-unknown}")"
  case " $id $id_like " in
    *" ubuntu "* | *" debian "*) echo "-- distribution: $pretty (Debian family)"; return 0 ;;
  esac
  echo "unsupported distribution: $pretty (ID=$id ID_LIKE=$id_like); a Debian/Ubuntu family system is required (apt, dpkg, update-initramfs, update-grub)"
  return 1
}

# GRUB kernel arguments that enable the IOMMU for this CPU vendor (x86_64 only).
iommu_kernel_args() {
  if [[ -n "$IOMMU_ARGS" ]]; then echo "$IOMMU_ARGS"; return 0; fi
  local vendor
  vendor="$(awk -F': *' '/^vendor_id/ {print $2; exit}' "$CPUINFO_FILE" 2>/dev/null || true)"
  case "$vendor" in
    GenuineIntel) echo "intel_iommu=on iommu=pt" ;;
    AuthenticAMD | HygonGenuine) echo "amd_iommu=on iommu=pt" ;;
    *)
      echo "!! unknown CPU vendor '${vendor:-?}' (from $CPUINFO_FILE): cannot pick the IOMMU kernel argument; set SUPERDL_JOIN_IOMMU_ARGS explicitly" >&2
      return 1
      ;;
  esac
}

step_precheck() {
  case "$(uname -m)" in x86_64 | aarch64) ;; *) echo "only x86_64 / aarch64 are supported (this host: $(uname -m))"; return 1 ;; esac
  check_distro || return 1
  command -v python3 >/dev/null || { echo "python3 is missing"; return 1; }
  command -v systemctl >/dev/null || { echo "systemd is required"; return 1; }
  if is_cpu_pool; then
    echo "-- cpu pool: skipping NVIDIA GPU detection"
  else
    lspci 2>/dev/null | grep -i nvidia >/dev/null || { echo "no NVIDIA GPU detected"; return 1; }
  fi
  local avail_kb
  avail_kb="$(df --output=avail -k / | tail -1 | tr -d ' ')"
  [[ "$avail_kb" -ge $((50 * 1024 * 1024)) ]] || { echo "less than 50G free on /"; return 1; }
  local server host port default_port=9345
  [[ "$DISTRO" == "k3s" ]] && default_port=6443
  server="$(cfg_get cluster_server_url)"
  host="$(python3 -c "from urllib.parse import urlparse;u=urlparse('$server');print(u.hostname)")"
  port="$(python3 -c "from urllib.parse import urlparse;u=urlparse('$server');print(u.port or $default_port)")"
  timeout 5 bash -c "</dev/tcp/$host/$port" || { echo "cannot reach $host:$port (check routing / firewall)"; return 1; }
}

step_nouveau() {
  if is_cpu_pool; then echo "-- cpu pool: no NVIDIA GPU, skipping the nouveau blacklist"; return 0; fi
  cat > "$ETC_DIR"/modprobe.d/blacklist-nouveau.conf <<'EOF'
blacklist nouveau
options nouveau modeset=0
EOF
  update-initramfs -u
  if lsmod | grep -q '^nouveau'; then NEED_REBOOT=1; echo "-- nouveau is loaded; a reboot is needed to unload it"; fi
}

step_sysctl() {
  printf 'user.max_user_namespaces=65536\nfs.inotify.max_user_instances=8192\nfs.inotify.max_user_watches=1048576\n' \
    > "$ETC_DIR"/sysctl.d/99-superdl.conf
  sysctl --system >/dev/null
}

step_iommu() {
  if is_cpu_pool; then echo "-- cpu pool: no GPU, skipping IOMMU"; return 0; fi
  if [[ "$(uname -m)" == "x86_64" && ! -f "$ETC_DIR"/default/grub.d/99-superdl.cfg ]]; then
    local args
    args="$(iommu_kernel_args)" || return 1
    mkdir -p "$ETC_DIR"/default/grub.d
    # shellcheck disable=SC2016
    echo "GRUB_CMDLINE_LINUX_DEFAULT=\"\$GRUB_CMDLINE_LINUX_DEFAULT $args\"" \
      > "$ETC_DIR"/default/grub.d/99-superdl.cfg
    echo "-- GRUB: $args"
    update-grub
  fi
  if [[ -z "$(ls -A "$IOMMU_GROUPS_DIR" 2>/dev/null)" ]]; then
    NEED_REBOOT=1
    echo "-- IOMMU not active; reboot required (if still inactive afterwards, check BIOS VT-d/AMD-Vi or firmware SMMU settings)"
  else
    echo "-- IOMMU active (${IOMMU_GROUPS_DIR} has groups)"
  fi
}

step_driver() {
  if is_cpu_pool; then echo "-- cpu pool: skipping NVIDIA driver install"; return 0; fi
  if is_kata_pool; then
    if nvidia-smi > /dev/null 2>&1 || lsmod | grep -q '^nvidia' \
      || dpkg -l 'nvidia-driver-*' 2> /dev/null | grep -q '^ii'; then
      echo "!! kata pool requires a host without the NVIDIA driver: vfio-manager fails on a pre-installed driver and the GPU never binds to vfio-pci" >&2
      echo "   uninstall the driver and rerun this command (steps in runbook node-pool-switch.md):" >&2
      echo "   systemctl disable --now nvidia-persistenced; apt-get purge -y 'nvidia-driver-*'; update-initramfs -u; reboot" >&2
      return 1
    fi
    echo "-- kata pool: whole-GPU passthrough via vfio-pci, skipping NVIDIA driver install"
    return 0
  fi
  local want
  want="$(cfg_get driver_version)"
  if nvidia-smi >/dev/null 2>&1; then
    echo "-- driver ready: $(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1)"
    return 0
  fi
  if dpkg -l "nvidia-driver-${want}-server" 2>/dev/null | grep -q '^ii'; then
    NEED_REBOOT=1
    echo "-- driver installed but not loaded; reboot required"
    return 0
  fi
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq "nvidia-driver-${want}-server"
  NEED_REBOOT=1
}

step_nvidia_toolkit() {
  if is_cpu_pool; then echo "-- cpu pool: skipping nvidia-container-toolkit"; return 0; fi
  if is_kata_pool; then echo "-- kata pool: the host runs no GPU containers, skipping nvidia-container-toolkit"; return 0; fi
  local installed
  installed="$(dpkg-query -W -f='${Version}' nvidia-container-toolkit 2>/dev/null || true)"
  if [[ -n "$installed" ]] && dpkg --compare-versions "$installed" ge "$NVCTK_MIN_VERSION"; then
    echo "-- nvidia-container-toolkit $installed >= $NVCTK_MIN_VERSION, skipping"
  else
    if [[ -n "$installed" ]]; then
      echo "-- nvidia-container-toolkit $installed is below the minimum $NVCTK_MIN_VERSION, upgrading"
    fi
    local key="$ETC_DIR/apt/keyrings/nvidia-container-toolkit-keyring.gpg"
    mkdir -p "$ETC_DIR/apt/keyrings" "$ETC_DIR/apt/sources.list.d"
    curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
      | gpg --batch --yes --dearmor -o "$key"
    curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
      | sed "s#deb https://#deb [signed-by=$key] https://#g" \
      > "$ETC_DIR/apt/sources.list.d/nvidia-container-toolkit.list"
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq nvidia-container-toolkit
    installed="$(dpkg-query -W -f='${Version}' nvidia-container-toolkit 2>/dev/null || true)"
    if [[ -z "$installed" ]] || dpkg --compare-versions "$installed" lt "$NVCTK_MIN_VERSION"; then
      echo "!! nvidia-container-toolkit version ${installed:-missing} is below the security minimum $NVCTK_MIN_VERSION" >&2
      return 1
    fi
  fi
  if systemctl is-active --quiet "$AGENT_UNIT" 2>/dev/null; then
    echo "-- $AGENT_UNIT is running; restarting it to detect the nvidia runtime"
    systemctl restart "$AGENT_UNIT"
  elif is_server_node; then
    echo "-- this host is $SERVER_UNIT; restarting it to detect the nvidia runtime"
    systemctl restart "$SERVER_UNIT"
  fi
}

step_nvme_vg() {
  local devices
  devices="$(cfg_get nvme_devices)"
  if [[ -z "$devices" ]]; then
    echo "!! no NVMe devices registered: the superdl-nvme VG is not created, nothing guessed." \
         "This node has no local instance-disk capability; for TopoLVM local disks, issue a new" \
         "enrollment token with NVMe devices registered and rerun with --force."
    report nvme_vg running \
      "no NVMe devices registered: skipping the instance-disk VG (superdl-nvme), nothing guessed. No TopoLVM local instance disks on this node yet; issue a token with NVMe registered and rerun with --force to add them."
    return 0
  fi
  step_lvm_discards
  if vgs superdl-nvme >/dev/null 2>&1; then echo "-- VG already exists, skipping"; return 0; fi
  local dev size pvs=()
  for dev in $devices; do
    if [[ "$dev" == loop:* ]]; then
      size="${dev#loop:}"; size="${size%[Gg]}"
      mkdir -p "$LVM_IMG_DIR"
      [[ -f "$LVM_IMG_DIR/superdl-nvme.img" ]] || truncate -s "${size}G" "$LVM_IMG_DIR/superdl-nvme.img"
      pvs+=("$(losetup --find --show "$LVM_IMG_DIR/superdl-nvme.img")")
      _write_loop_unit
      echo "-- per registration: loop file as the instance disk (${size}G; testing only, not dedicated-disk performance)"
    else
      if [[ ! -b "$dev" ]]; then
        echo "!! NVMe device not found: $dev (wrong registration? check the node's NVMe registration in the admin console)" >&2
        return 1
      fi
      if wipefs -n "$dev" 2>/dev/null | grep -q .; then
        echo "!! $dev carries existing signatures (not an empty disk); refusing pvcreate:" >&2
        wipefs -n "$dev" >&2
        echo "!! once confirmed empty, run wipefs -a $dev and rerun; if a data disk was registered by mistake, fix the registration" >&2
        return 1
      fi
      pvs+=("$dev")
    fi
  done
  pvcreate -f "${pvs[@]}"
  vgcreate superdl-nvme "${pvs[@]}"
}

step_lvm_discards() {
  local conf="$ETC_DIR/lvm/lvm.conf"
  if grep -q "^[[:space:]]*issue_discards[[:space:]]*=" "$conf" 2>/dev/null; then
    echo "-- lvm.conf already sets issue_discards, skipping"
    return 0
  fi
  mkdir -p "$ETC_DIR/lvm"
  printf '\n# superdl: TRIM NVMe on instance-disk destruction (cross-tenant data remanence protection)\ndevices {\n    issue_discards = 1\n}\n' >> "$conf"
}

_write_loop_unit() {
  cat > "$ETC_DIR/systemd/system/superdl-nvme-loop.service" <<EOF
[Unit]
Description=SuperDL TopoLVM loop-backed VG (superdl-nvme)
DefaultDependencies=no
After=local-fs.target
Before=${AGENT_UNIT}
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/sbin/losetup --find $LVM_IMG_DIR/superdl-nvme.img
[Install]
WantedBy=local-fs.target
EOF
  systemctl daemon-reload
  systemctl enable superdl-nvme-loop.service
}

maybe_reboot() {
  [[ "$NEED_REBOOT" == "1" ]] || return 0
  CURRENT_PHASE="reboot"
  local count=0
  [[ -f "$STATE_DIR/reboot_count" ]] && count="$(cat "$STATE_DIR/reboot_count")"
  if [[ "$count" -ge 2 ]]; then
    report reboot failed "rebooted ${count} times and still not ready (nouveau/IOMMU/driver); check BIOS settings and kernel logs"
    exit 1
  fi
  echo $((count + 1)) > "$STATE_DIR/reboot_count"
  if [[ -f "${BASH_SOURCE[0]:-/nonexistent}" ]]; then
    cp "${BASH_SOURCE[0]}" "$STATE_DIR/node-join.sh"
  else
    curl -fsSL "$API_BASE/api/v1/node-enroll/script" -o "$STATE_DIR/node-join.sh"
    local want actual
    want="$(cfg_get script_sha256)"
    actual="$(sha256sum "$STATE_DIR/node-join.sh" | awk '{print $1}')"
    if [[ -z "$want" ]]; then
      report reboot failed "bootstrap did not provide script_sha256; refusing to run an unverifiable re-downloaded script"
      echo "!! bootstrap did not provide script_sha256; aborting (refusing to run an unverifiable re-downloaded script)" >&2
      exit 1
    fi
    if [[ "$actual" != "$want" ]]; then
      report reboot failed "re-downloaded script checksum mismatch (expected $want, got $actual); check the API path"
      echo "!! re-downloaded script checksum mismatch; aborting (expected $want, got $actual)" >&2
      exit 1
    fi
  fi
  chmod 700 "$STATE_DIR/node-join.sh"
  cat > "$ETC_DIR/systemd/system/${RESUME_UNIT}.service" <<EOF
[Unit]
Description=SuperDL node join resume
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/bin/bash $STATE_DIR/node-join.sh --token-file $STATE_DIR/token --api-base $API_BASE

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable "${RESUME_UNIT}.service"
  report reboot rebooting "rebooting to apply nouveau removal / IOMMU / driver; the oneshot unit resumes automatically"
  echo "==== rebooting to resume ===="
  systemctl reboot
  exit 0
}

step_registries() {
  local content ca
  content="$(cfg_get registries_yaml)"
  [[ -n "$content" ]] || { echo "-- registries_yaml is empty, skipping (no image cache configured)"; return 0; }
  mkdir -p "$RANCHER_DIR"
  ca="$(cfg_get registry_ca_pem)"
  if [[ -n "$ca" ]]; then
    printf '%s\n' "$ca" > "$RANCHER_DIR"/harbor-ca.crt
    chmod 644 "$RANCHER_DIR"/harbor-ca.crt
  else
    rm -f "$RANCHER_DIR"/harbor-ca.crt
  fi
  printf '%s\n' "${content//__RANCHER_DIR__/$RANCHER_DIR}" > "$RANCHER_DIR"/registries.yaml
  chmod 600 "$RANCHER_DIR"/registries.yaml
}

step_agent_config() {
  if is_server_node; then
    echo "-- this host is $SERVER_UNIT: no agent config written; the platform labels node $(hostname) with its pool"
    collect_driver_versions
    report agent_config running "server host: driver versions reported, waiting for the platform to label the pool"
    return 0
  fi
  mkdir -p "$RANCHER_DIR"
  local join_token server_url
  join_token="$(cfg_get cluster_join_token)"
  server_url="$(cfg_get cluster_server_url)"
  if [[ -z "$join_token" ]]; then
    echo "!! cluster_join_token from bootstrap is empty: refusing to write the agent config." \
         "Check Platform config > Cluster access in the admin console and the ansible agent_token (site.yml asserts before rendering)" >&2
    return 1
  fi
  if [[ ! "$join_token" =~ ^[A-Za-z0-9:._~+/=-]{16,512}$ ]]; then
    echo "!! cluster_join_token contains illegal characters (allowed: [A-Za-z0-9:._~+/=-]): refusing to write the agent config" >&2
    return 1
  fi
  # a server node-token (K10<64hex>::server:<pw>) would join this host as a server: only agent tokens are accepted
  if [[ "$join_token" =~ ^K10[0-9A-Fa-f]{64}::server: ]]; then
    echo "!! cluster_join_token is a server node-token (K10...::server:...); only an agent token is allowed: refusing to write the agent config." \
         "Put the server config's agent-token value under Platform config > Cluster access in the admin console" >&2
    return 1
  fi
  if [[ ! "$server_url" =~ ^https://[][0-9A-Za-z.:-]+:[0-9]{1,5}$ ]]; then
    echo "!! cluster_server_url is malformed: refusing to write the agent config" >&2
    return 1
  fi
  {
    echo "server: \"$server_url\""
    echo "token: \"$join_token\""
  } > "$RANCHER_DIR"/config.yaml
  chmod 600 "$RANCHER_DIR"/config.yaml
  local dropin_dir="$RANCHER_STATE_DIR/$DISTRO/agent/etc/kubelet.conf.d"
  mkdir -p "$dropin_dir"
  printf 'apiVersion: kubelet.config.k8s.io/v1beta1\nkind: KubeletConfiguration\npodPidsLimit: %s\n' \
    "$POD_PIDS_LIMIT" > "$dropin_dir/50-superdl.conf"
  chmod 644 "$dropin_dir/50-superdl.conf"
}

step_agent_install() {
  local want mirror url pin
  if is_server_node; then
    echo "-- this host is $SERVER_UNIT (already in the cluster), skipping agent install"
    return 0
  fi
  want="$(cfg_get cluster_agent_version)"
  mirror="$(cfg_get install_mirror)"
  if command -v "$DISTRO" >/dev/null 2>&1 && "$DISTRO" --version | grep -q "$want"; then
    echo "-- $DISTRO $want already installed, skipping"
    return 0
  fi
  if [[ "$DISTRO" == "k3s" ]]; then
    if [[ "$mirror" == "official" ]]; then
      url="https://get.k3s.io"; pin="$PIN_K3S_OFFICIAL"
    else
      url="https://rancher-mirror.rancher.cn/k3s/k3s-install.sh"; pin="$PIN_K3S_CN"
    fi
  else
    if [[ "$mirror" == "official" ]]; then
      url="https://get.rke2.io"; pin="$PIN_RKE2_OFFICIAL"
    else
      url="https://rancher-mirror.rancher.cn/rke2/install.sh"; pin="$PIN_RKE2_CN"
    fi
  fi
  local installer
  installer="$(mktemp "$STATE_DIR/installer.XXXXXX")"
  curl -fsSL "$url" -o "$installer"
  chmod 700 "$installer"
  if ! echo "$pin  $installer" | sha256sum -c - >/dev/null 2>&1; then
    echo "!! $DISTRO installer checksum mismatch ($url): upstream changed or the download was tampered with;" \
         "verify upstream, update the pin built into this script and rerun" >&2
    rm -f "$installer"
    return 1
  fi
  if [[ "$DISTRO" == "k3s" ]]; then
    if [[ "$mirror" == "official" ]]; then
      INSTALL_K3S_EXEC=agent INSTALL_K3S_VERSION="$want" sh "$installer"
    else
      INSTALL_K3S_MIRROR=cn INSTALL_K3S_EXEC=agent INSTALL_K3S_VERSION="$want" sh "$installer"
    fi
  else
    if [[ "$mirror" == "official" ]]; then
      INSTALL_RKE2_TYPE=agent INSTALL_RKE2_VERSION="$want" sh "$installer"
    else
      INSTALL_RKE2_MIRROR=cn INSTALL_RKE2_TYPE=agent INSTALL_RKE2_VERSION="$want" sh "$installer"
    fi
  fi
  rm -f "$installer"
}

step_agent_start() {
  if is_server_node; then
    echo "-- this host is $SERVER_UNIT; no agent to start"
    return 0
  fi
  systemctl enable --now "$AGENT_UNIT"
  echo "-- ${AGENT_UNIT%.service} is running"
}

finalize() {
  CURRENT_PHASE="waiting_node"
  collect_driver_versions
  local unit="$AGENT_UNIT"
  is_server_node && unit="$SERVER_UNIT"
  report waiting_node ok "${unit%.service} started; waiting for the platform reconciler to confirm the node is Ready (progress under Pending nodes in the admin console)"
  if systemctl is-enabled --quiet "${RESUME_UNIT}.service" 2>/dev/null; then
    systemctl disable "${RESUME_UNIT}.service" || true
  fi
  rm -f "$ETC_DIR/systemd/system/${RESUME_UNIT}.service"
  systemctl daemon-reload
  rm -f "$STATE_DIR/bootstrap.json" "$STATE_DIR/token" "$STATE_DIR/curl.conf"
  mark_done completed
  echo "==== done: ${unit%.service} started; the admin console is the source of truth for the join result ===="
}

if [[ "$FORCE" == "1" ]]; then
  echo "-- --force: clearing local markers / delivered config / old token and reinstalling from scratch (requires a newly issued token from the admin console)"
  rm -rf "$STATE_DIR/done.d" "$STATE_DIR/bootstrap.json" "$STATE_DIR/token" \
    "$STATE_DIR/curl.conf" "$STATE_DIR/reboot_count"
  mkdir -p "$STATE_DIR/done.d"
elif marker completed; then
  echo "this node has already joined; nothing to do. To reinstall from scratch: --force with a newly issued token from the admin console"
  exit 0
fi
if marker bootstrap; then
  use_token_file "$STATE_DIR/token"
else
  use_token_file "$TOKEN_FILE"
fi

if marker bootstrap; then
  echo "-- bootstrap: already done, skipping (reusing the delivered config; enrollment tokens are single-use, bootstrap is not repeated)"
else
  CURRENT_PHASE="bootstrap"
  step_bootstrap
  mark_done bootstrap
  report bootstrap ok
fi
load_distro
run_step precheck step_precheck
run_step nouveau step_nouveau
run_step sysctl step_sysctl
run_step iommu step_iommu
run_step driver step_driver
run_step nvidia_toolkit step_nvidia_toolkit
run_step nvme_vg step_nvme_vg
maybe_reboot
run_step registries step_registries
run_step agent_config step_agent_config
run_step agent_install step_agent_install
run_step agent_start step_agent_start
finalize
