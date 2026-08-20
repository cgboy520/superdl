#!/usr/bin/env bash
# SuperDL GPU 节点一键加入脚本(WP23)。
# 用法(命令由管理端生成,token 走参数不进 URL):
#   curl -fsSL <API>/api/v1/node-enroll/script | sudo bash -s -- --token sdln_xxx
#   wget -qO node-join.sh <API>/api/v1/node-enroll/script && sudo bash node-join.sh --token sdln_xxx
#
# 设计:
# - 脚本本体零密钥;RKE2 server/join token 凭注册令牌 POST /bootstrap 换取。
# - 全幂等:每步落 marker(/var/lib/superdl-node-join/done.d/),可无限次重跑。
# - 需重启的步骤(nouveau/IOMMU/驱动)统一合并为一次重启,systemd oneshot 断点续跑;
#   最多 2 次重启,仍未就绪则上报 failed。
# - 各阶段回报进度(phase 与后端契约一致):precheck nouveau sysctl iommu driver
#   nvme_vg reboot registries rke2_config rke2_install rke2_start waiting_node
set -euo pipefail

API_BASE="__API_BASE__" # 服务端下发时替换;可用 --api-base 覆盖(测试用)
# 路径可经 env 覆盖仅为 bats 测试隔离;生产一律默认值
STATE_DIR="${SUPERDL_JOIN_STATE_DIR:-/var/lib/superdl-node-join}"
LOG_FILE="${SUPERDL_JOIN_LOG_FILE:-/var/log/superdl-node-join.log}"
ETC_DIR="${SUPERDL_JOIN_ETC_DIR:-/etc}"
RESUME_UNIT="superdl-node-join-resume"
TOKEN=""
CURRENT_PHASE="init"
NEED_REBOOT=0

# ---------- 参数 ----------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --token) TOKEN="$2"; shift 2 ;;
    --token-file) TOKEN="$(cat "$2")"; shift 2 ;;
    --api-base) API_BASE="$2"; shift 2 ;;
    *) echo "未知参数: $1" >&2; exit 2 ;;
  esac
done
[[ -n "$TOKEN" ]] || { echo "缺少 --token(在管理端「添加节点」生成)" >&2; exit 2; }
[[ "$API_BASE" != "__API_BASE__" ]] || { echo "脚本须经 API 下发(占位符未替换),或用 --api-base 指定" >&2; exit 2; }

mkdir -p "$STATE_DIR/done.d"
chmod 700 "$STATE_DIR"
exec > >(tee -a "$LOG_FILE") 2>&1
echo "==== $(date -Is) node-join 启动 (api=$API_BASE) ===="

# ---------- 基础函数 ----------
json_escape() { python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))'; }

report() { # report <phase> <state> [message]
  local phase="$1" state="$2" message="${3:-}"
  local msg_json
  msg_json="$(printf '%s' "$message" | json_escape)"
  curl -fsS -m 10 --retry 2 -X POST \
    -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d "{\"phase\":\"$phase\",\"state\":\"$state\",\"message\":$msg_json}" \
    "$API_BASE/api/v1/node-enroll/progress" >/dev/null || true
}

on_error() {
  local tail_log
  tail_log="$(tail -n 20 "$LOG_FILE" 2>/dev/null || true)"
  report "$CURRENT_PHASE" failed "step=$CURRENT_PHASE; log tail: $tail_log"
  echo "!! 失败于 $CURRENT_PHASE,详情见 $LOG_FILE;修复后可重跑同一条命令续跑" >&2
}
trap on_error ERR

marker() { [[ -f "$STATE_DIR/done.d/$1" ]]; }
mark_done() { touch "$STATE_DIR/done.d/$1"; }

run_step() { # run_step <phase> <fn>
  CURRENT_PHASE="$1"
  if marker "$1"; then echo "-- $1: 已完成,跳过"; return 0; fi
  echo "== $1 =="
  report "$1" running
  "$2"
  mark_done "$1"
  report "$1" ok
}

cfg_get() { python3 -c "import json,sys; v=json.load(open('$STATE_DIR/bootstrap.json')).get('$1',''); print(v if not isinstance(v,list) else ' '.join(v))"; }

# ---------- 步骤实现 ----------
step_bootstrap() {
  local hostname kernel arch os_release gpus payload
  hostname="$(hostname)"
  kernel="$(uname -r)"
  arch="$(uname -m)"
  # shellcheck disable=SC1091  # 运行期 source 目标机文件
  os_release="$(. /etc/os-release && echo "$PRETTY_NAME")"
  gpus="$(lspci 2>/dev/null | grep -i 'nvidia' | sed 's/.*: //' | head -8 | python3 -c 'import json,sys; print(json.dumps([l.strip() for l in sys.stdin if l.strip()]))')"
  payload="$(python3 - "$hostname" "$os_release" "$kernel" "$arch" <<'PYEOF'
import json, sys
print(json.dumps({"hostname": sys.argv[1],
                  "os_info": {"os_release": sys.argv[2], "kernel": sys.argv[3], "arch": sys.argv[4]},
                  "gpus": []}))
PYEOF
)"
  payload="$(python3 -c "import json,sys; p=json.loads(sys.argv[1]); p['gpus']=json.loads(sys.argv[2]); print(json.dumps(p))" "$payload" "$gpus")"
  curl -fsS -m 15 --retry 2 -X POST \
    -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d "$payload" "$API_BASE/api/v1/node-enroll/bootstrap" -o "$STATE_DIR/bootstrap.json"
  chmod 600 "$STATE_DIR/bootstrap.json"
  echo "-- bootstrap 完成: pool=$(cfg_get pool) rke2=$(cfg_get rke2_version)"
}

step_precheck() {
  [[ "$(id -u)" == "0" ]] || { echo "必须 root 执行"; return 1; }
  [[ "$(uname -m)" == "x86_64" ]] || { echo "仅支持 x86_64"; return 1; }
  command -v python3 >/dev/null || { echo "缺少 python3"; return 1; }
  command -v systemctl >/dev/null || { echo "需要 systemd"; return 1; }
  lspci 2>/dev/null | grep -qi nvidia || { echo "未检测到 NVIDIA GPU"; return 1; }
  local avail_kb
  avail_kb="$(df --output=avail -k / | tail -1 | tr -d ' ')"
  [[ "$avail_kb" -ge $((50 * 1024 * 1024)) ]] || { echo "/ 分区可用空间不足 50G"; return 1; }
  # server:9345 连通性(bash /dev/tcp,免装 nc)
  local server host port
  server="$(cfg_get rke2_server_url)"
  host="$(python3 -c "from urllib.parse import urlparse;u=urlparse('$server');print(u.hostname)")"
  port="$(python3 -c "from urllib.parse import urlparse;u=urlparse('$server');print(u.port or 9345)")"
  timeout 5 bash -c "</dev/tcp/$host/$port" || { echo "无法连通 $host:$port(检查内网路由/防火墙)"; return 1; }
}

step_nouveau() {
  cat > "$ETC_DIR"/modprobe.d/blacklist-nouveau.conf <<'EOF'
blacklist nouveau
options nouveau modeset=0
EOF
  update-initramfs -u
  if lsmod | grep -q '^nouveau'; then NEED_REBOOT=1; echo "-- nouveau 已加载,需重启卸载"; fi
}

step_sysctl() {
  echo "user.max_user_namespaces=65536" > "$ETC_DIR"/sysctl.d/99-superdl.conf
  sysctl --system >/dev/null
}

step_iommu() {
  [[ "$(cfg_get pool)" == "kata" ]] || { echo "-- 非 kata 池,跳过 IOMMU"; return 0; }
  if [[ ! -f "$ETC_DIR"/default/grub.d/99-superdl.cfg ]]; then
    mkdir -p "$ETC_DIR"/default/grub.d
    # shellcheck disable=SC2016  # 变量须由 GRUB 展开,单引号是预期
    echo 'GRUB_CMDLINE_LINUX_DEFAULT="$GRUB_CMDLINE_LINUX_DEFAULT intel_iommu=on iommu=pt"' \
      > "$ETC_DIR"/default/grub.d/99-superdl.cfg
    update-grub
  fi
  if [[ -z "$(ls -A /sys/kernel/iommu_groups 2>/dev/null)" ]]; then
    NEED_REBOOT=1
    echo "-- IOMMU 未生效,需重启(重启后仍未生效请检查 BIOS VT-d/AMD-Vi)"
  fi
}

step_driver() {
  local want
  want="$(cfg_get driver_version)"
  if nvidia-smi >/dev/null 2>&1; then
    echo "-- 驱动已就绪: $(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1)"
    return 0
  fi
  if dpkg -l "nvidia-driver-${want}-server" 2>/dev/null | grep -q '^ii'; then
    NEED_REBOOT=1
    echo "-- 驱动已安装但未加载,需重启"
    return 0
  fi
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq "nvidia-driver-${want}-server"
  NEED_REBOOT=1
}

step_nvme_vg() {
  local devices
  devices="$(cfg_get nvme_devices)"
  [[ -n "$devices" ]] || { echo "-- 未登记 NVMe 设备,跳过 VG"; return 0; }
  if vgs superdl-nvme >/dev/null 2>&1; then echo "-- VG 已存在,跳过"; return 0; fi
  # shellcheck disable=SC2086  # 设备列表按空格展开是预期行为
  pvcreate $devices
  # shellcheck disable=SC2086
  vgcreate superdl-nvme $devices
}

maybe_reboot() {
  [[ "$NEED_REBOOT" == "1" ]] || return 0
  CURRENT_PHASE="reboot"
  local count=0
  [[ -f "$STATE_DIR/reboot_count" ]] && count="$(cat "$STATE_DIR/reboot_count")"
  if [[ "$count" -ge 2 ]]; then
    report reboot failed "已重启 ${count} 次仍未就绪(nouveau/IOMMU/驱动),请人工检查 BIOS 与内核日志"
    exit 1
  fi
  echo $((count + 1)) > "$STATE_DIR/reboot_count"
  # 自持久化(管道执行时 $0 不是文件,从 API 重新拉取自身)
  if [[ -f "${BASH_SOURCE[0]:-/nonexistent}" ]]; then
    cp "${BASH_SOURCE[0]}" "$STATE_DIR/node-join.sh"
  else
    curl -fsSL "$API_BASE/api/v1/node-enroll/script" -o "$STATE_DIR/node-join.sh"
  fi
  chmod 700 "$STATE_DIR/node-join.sh"
  printf '%s' "$TOKEN" > "$STATE_DIR/token"
  chmod 600 "$STATE_DIR/token"
  cat > "$ETC_DIR/systemd/system/${RESUME_UNIT}.service" <<EOF
[Unit]
Description=SuperDL node join resume (WP23)
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
  report reboot rebooting "重启以生效 nouveau 卸载/IOMMU/驱动,oneshot 将自动续跑"
  echo "==== 重启续跑 ===="
  systemctl reboot
  exit 0
}

step_registries() {
  local content
  content="$(cfg_get registries_yaml)"
  [[ -n "$content" ]] || { echo "-- registries_yaml 为空,跳过(WP22 镜像缓存未配置)"; return 0; }
  mkdir -p "$ETC_DIR"/rancher/rke2
  printf '%s\n' "$content" > "$ETC_DIR"/rancher/rke2/registries.yaml
  chmod 644 "$ETC_DIR"/rancher/rke2/registries.yaml
}

step_rke2_config() {
  mkdir -p "$ETC_DIR"/rancher/rke2
  cat > "$ETC_DIR"/rancher/rke2/config.yaml <<EOF
server: $(cfg_get rke2_server_url)
token: $(cfg_get rke2_join_token)
node-label:
  - "superdl.io/pool=$(cfg_get pool)"
EOF
  chmod 600 "$ETC_DIR"/rancher/rke2/config.yaml
}

step_rke2_install() {
  local want
  want="$(cfg_get rke2_version)"
  if command -v rke2 >/dev/null 2>&1 && rke2 --version | grep -q "$want"; then
    echo "-- RKE2 $want 已安装,跳过"
    return 0
  fi
  curl -sfL https://get.rke2.io | INSTALL_RKE2_TYPE=agent INSTALL_RKE2_VERSION="$want" sh -
}

step_rke2_start() {
  systemctl enable --now rke2-agent.service
  local i
  for i in $(seq 1 60); do
    systemctl is-active --quiet rke2-agent.service && { echo "-- rke2-agent 已运行(第 ${i} 次探测)"; return 0; }
    sleep 5
  done
  echo "rke2-agent 300s 未进入 active,journalctl -u rke2-agent 查因"
  return 1
}

finalize() {
  CURRENT_PHASE="waiting_node"
  report waiting_node ok "rke2-agent 已启动,等待平台对账确认节点 Ready(管理端「待加入节点」可见进度)"
  if systemctl is-enabled --quiet "${RESUME_UNIT}.service" 2>/dev/null; then
    systemctl disable "${RESUME_UNIT}.service" || true
    rm -f "$ETC_DIR/systemd/system/${RESUME_UNIT}.service" "$STATE_DIR/token"
    systemctl daemon-reload
  fi
  echo "==== 完成:节点已启动 rke2-agent,加入结果以管理端为准 ===="
}

# ---------- 主流程 ----------
CURRENT_PHASE="bootstrap"
step_bootstrap # 每次执行都重新 bootstrap(幂等,服务端支持重跑;拿最新配置)
run_step precheck step_precheck
run_step nouveau step_nouveau
run_step sysctl step_sysctl
run_step iommu step_iommu
run_step driver step_driver
run_step nvme_vg step_nvme_vg
maybe_reboot
run_step registries step_registries
run_step rke2_config step_rke2_config
run_step rke2_install step_rke2_install
run_step rke2_start step_rke2_start
finalize
