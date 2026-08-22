#!/usr/bin/env bash
# SuperDL GPU 节点一键加入脚本。
# 用法(命令由管理端生成;token 经 stdin 落 0600 文件,不进 URL 也不进任何进程 argv):
#   echo 'sdln_xxx' | sudo sh -c 'umask 077; cat > /run/superdl-join.token; curl -fsSL <API>/api/v1/node-enroll/script | bash -s -- --token-file /run/superdl-join.token; s=$?; rm -f /run/superdl-join.token; exit $s'
#   wget -qO node-join.sh <API>/api/v1/node-enroll/script && echo 'sdln_xxx' | sudo sh -c 'umask 077; cat > /run/superdl-join.token; bash node-join.sh --token-file /run/superdl-join.token; s=$?; rm -f /run/superdl-join.token; exit $s'
#
# 约束:
# - 脚本本体零密钥;server 地址与 join token 凭注册令牌 POST /bootstrap 换取。
# - 注册令牌一次性:首次 bootstrap 即被服务端消费,换发窄权限 progress 令牌
#   (仅可上报进度),落 $STATE_DIR/token(0600);重跑/重启续跑只用它。
# - k8s_distro=k3s 时装 k3s agent,取值由服务端下发。
# - 全幂等:每步落 marker($STATE_DIR/done.d/);已完成的节点重跑直接退出,
#   从头重装须 --force + 管理端新签发的令牌。
# - 需重启的步骤(nouveau/IOMMU/驱动)合并为一次重启,systemd oneshot 断点续跑;
#   最多 2 次重启,仍未就绪则上报 failed。管道执行时重启前从 API 重拉自身,
#   并校验 bootstrap 下发的脚本指纹(script_sha256),防中途被替换。
# - k3s/rke2 安装器不裸 curl|sh:先落临时文件,校验脚本内置 sha256 pin 再执行。
# - phase 取值与后端契约一致:bootstrap precheck nouveau sysctl iommu driver
#   nvidia_toolkit nvme_vg reboot registries agent_config agent_install agent_start waiting_node
set -eEuo pipefail

API_BASE="__API_BASE__" # 服务端下发时替换;可用 --api-base 覆盖(测试用)
# 路径可经 env 覆盖仅为 bats 测试隔离;生产一律默认值
STATE_DIR="${SUPERDL_JOIN_STATE_DIR:-/var/lib/superdl-node-join}"
LOG_FILE="${SUPERDL_JOIN_LOG_FILE:-/var/log/superdl-node-join.log}"
ETC_DIR="${SUPERDL_JOIN_ETC_DIR:-/etc}"
LVM_IMG_DIR="${SUPERDL_JOIN_LVM_DIR:-/var/lib/superdl-lvm}"  # loop 兜底镜像目录(仅显式选择时用)
RESUME_UNIT="superdl-node-join-resume"
TOKEN=""
TOKEN_FILE=""
FORCE=0
CURRENT_PHASE="init"
NEED_REBOOT=0

# k3s/rke2 安装器 sha256 pin(固定 URL + 校验后执行,替代裸 curl|sh;与 NVIDIA 源 GPG
# 验证同一信任模型)。上游安装器更新会校验失败并按 failed 上报,需核对上游后更新 pin。
# SUPERDL_JOIN_PIN_* 仅为 bats 测试与应急处置留的覆盖口(需本机 root,不削弱威胁模型)。
PIN_K3S_OFFICIAL="${SUPERDL_JOIN_PIN_K3S_OFFICIAL:-ed01f89fd977bf20ac1516bbebf8370bf3ddbaa55dac8aba610956a4c78cc00b}"
PIN_K3S_CN="${SUPERDL_JOIN_PIN_K3S_CN:-3944aa467eb945b5ff2151a8e4f8d4a5f3a210d31ab39aec81f37606936d0863}"
PIN_RKE2_OFFICIAL="${SUPERDL_JOIN_PIN_RKE2_OFFICIAL:-42983c86d1da64a92061d83afb57630cedd69241989f1b0673f3db6c3d92ee6b}"
PIN_RKE2_CN="${SUPERDL_JOIN_PIN_RKE2_CN:-5541410b86d4d19d927d820be85156e787be3fdb24be72507af52932a1d12de1}"

# ---------- 参数 ----------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --token-file) TOKEN_FILE="$2"; shift 2 ;;
    --api-base) API_BASE="$2"; shift 2 ;;
    --force) FORCE=1; shift ;;
    *) echo "未知参数: $1" >&2; exit 2 ;;
  esac
done
[[ -n "$TOKEN_FILE" ]] || { echo "缺少 --token-file(在管理端「添加节点」生成命令,token 不落命令行)" >&2; exit 2; }
[[ -f "$TOKEN_FILE" ]] || { echo "token 文件不存在: $TOKEN_FILE" >&2; exit 2; }
[[ "$API_BASE" != "__API_BASE__" ]] || { echo "脚本须经 API 下发(占位符未替换),或用 --api-base 指定" >&2; exit 2; }
[[ "$(id -u)" == "0" ]] || { echo "必须 root 执行(sudo bash ...)" >&2; exit 2; }

mkdir -p "$STATE_DIR/done.d"
chmod 700 "$STATE_DIR"
exec > >(tee -a "$LOG_FILE") 2>&1
echo "==== $(date -Is) node-join 启动 (api=$API_BASE) ===="

# ---------- 基础函数 ----------
json_escape() { python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))'; }

# 令牌经 curl --config 注入 Authorization 头:不进 curl 进程 argv(节点本地用户 ps 不可见)
use_token_file() { # use_token_file <path>
  TOKEN="$(cat "$1")"
  printf 'header = "Authorization: Bearer %s"\n' "$TOKEN" > "$STATE_DIR/curl.conf"
  chmod 600 "$STATE_DIR/curl.conf"
}

report() { # report <phase> <state> [message]
  local phase="$1" state="$2" message="${3:-}"
  local msg_json
  # json_escape 失败(python3 缺失等)不得让 ERR trap 在 on_error 里递归
  msg_json="$(printf '%s' "$message" | json_escape || true)"
  curl -fsS -m 10 --retry 2 --config "$STATE_DIR/curl.conf" \
    -H "Content-Type: application/json" \
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

# 装载发行版参数(k8s_distro 由服务端必发:rke2 / k3s)
load_distro() {
  DISTRO="$(cfg_get k8s_distro)"
  RANCHER_DIR="$ETC_DIR/rancher/$DISTRO"
  AGENT_UNIT="${DISTRO}-agent.service"
}

# ---------- 步骤实现 ----------
step_bootstrap() {
  local hostname kernel arch os_release gpus gpu_details driver cuda payload
  hostname="$(hostname)"
  kernel="$(uname -r)"
  arch="$(uname -m)"
  # shellcheck disable=SC1091  # 运行期 source 目标机文件
  os_release="$(. /etc/os-release && echo "$PRETTY_NAME")"
  # 型号优先取 nvidia-smi,取不到退回 lspci。nvidia-smi 无驱动时返回非零,
  # 必须由 { ... || true; } 兜住,否则 pipefail 会中断整段采集。
  gpus="$({ nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null || true; } | head -8 | python3 -c 'import json,sys; print(json.dumps([l.strip() for l in sys.stdin if l.strip()]))')"
  [[ "$gpus" == "[]" ]] && gpus="$(lspci 2>/dev/null | grep -i 'nvidia' | sed 's/.*: //' | head -8 | python3 -c 'import json,sys; print(json.dumps([l.strip() for l in sys.stdin if l.strip()]))')"
  # 全卡清单(名称+显存 MiB):台账显存口径;nvidia-smi 不可用时为 [],服务端回落默认表
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
  driver="$({ nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null || true; } | head -1)"
  # 兼容 "CUDA Version: 12.8" 与新驱动的 "CUDA UMD Version: 13.3"
  cuda="$({ nvidia-smi 2>/dev/null || true; } | sed -n 's/.*CUDA[^:]*Version: \([0-9.]*\).*/\1/p' | head -1)"
  payload="$(python3 - "$hostname" "$os_release" "$kernel" "$arch" "$driver" "$cuda" "$gpus" "$gpu_details" <<'PYEOF'
import json, sys
print(json.dumps({"hostname": sys.argv[1],
                  "os_info": {"os_release": sys.argv[2], "kernel": sys.argv[3], "arch": sys.argv[4],
                              "driver_version": sys.argv[5], "cuda_version": sys.argv[6]},
                  "gpus": json.loads(sys.argv[7]),
                  "gpu_details": json.loads(sys.argv[8])}))
PYEOF
)"
  curl -fsS -m 15 --retry 2 --config "$STATE_DIR/curl.conf" \
    -H "Content-Type: application/json" \
    -d "$payload" "$API_BASE/api/v1/node-enroll/bootstrap" -o "$STATE_DIR/bootstrap.json"
  chmod 600 "$STATE_DIR/bootstrap.json"
  # 注册令牌一次性:服务端已消费并换发 progress 令牌,此后上报/续跑只用它
  # (旧服务端不下发该字段时回落注册令牌,行为同升级前)
  local progress
  progress="$(cfg_get progress_token)"
  if [[ -n "$progress" ]]; then
    printf '%s' "$progress" > "$STATE_DIR/token"
    chmod 600 "$STATE_DIR/token"
    use_token_file "$STATE_DIR/token"
  fi
  echo "-- bootstrap 完成: pool=$(cfg_get pool) rke2=$(cfg_get rke2_version)"
}

step_precheck() {
  [[ "$(uname -m)" == "x86_64" ]] || { echo "仅支持 x86_64"; return 1; }
  command -v python3 >/dev/null || { echo "缺少 python3"; return 1; }
  command -v systemctl >/dev/null || { echo "需要 systemd"; return 1; }
  lspci 2>/dev/null | grep -qi nvidia || { echo "未检测到 NVIDIA GPU"; return 1; }
  local avail_kb
  avail_kb="$(df --output=avail -k / | tail -1 | tr -d ' ')"
  [[ "$avail_kb" -ge $((50 * 1024 * 1024)) ]] || { echo "/ 分区可用空间不足 50G"; return 1; }
  # server 端口连通性(bash /dev/tcp,免装 nc;rke2 缺省 9345,k3s 缺省 6443)
  local server host port default_port=9345
  [[ "$DISTRO" == "k3s" ]] && default_port=6443
  server="$(cfg_get rke2_server_url)"
  host="$(python3 -c "from urllib.parse import urlparse;u=urlparse('$server');print(u.hostname)")"
  port="$(python3 -c "from urllib.parse import urlparse;u=urlparse('$server');print(u.port or $default_port)")"
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

step_nvidia_toolkit() {
  # NVIDIA Container Toolkit:k8s 认卡的前置(驱动之外的容器运行时依赖)。装好后
  # k3s/rke2 的 containerd 下次启动会探测 nvidia-container-runtime 并生成 nvidia RuntimeClass。
  if command -v nvidia-ctk >/dev/null 2>&1; then
    echo "-- nvidia-container-toolkit 已安装($(nvidia-ctk --version 2>/dev/null | head -1))"
  else
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
  fi
  # 若 agent 已在跑(重跑/补装场景),重启一次让 containerd 重新探测 nvidia runtime
  if systemctl is-active --quiet "$AGENT_UNIT" 2>/dev/null; then
    echo "-- $AGENT_UNIT 已运行,重启以探测 nvidia runtime"
    systemctl restart "$AGENT_UNIT"
  fi
}

step_nvme_vg() {
  local devices
  devices="$(cfg_get nvme_devices)"
  # 未登记 NVMe 时不自动用文件兜底:节点仍可加入,但无 TopoLVM 本地实例盘能力
  if [[ -z "$devices" ]]; then
    echo "!! 未登记 NVMe 设备:不创建 superdl-nvme VG,也不自动兜底。" \
         "该节点无本地实例盘能力;如需 TopoLVM 本地盘,请在管理端为本节点新建" \
         "带 NVMe 登记的注册令牌,并用 --force 重跑。"
    report nvme_vg running \
      "未登记 NVMe 设备:跳过实例盘 VG(superdl-nvme),不自动兜底。该节点暂无 TopoLVM 本地实例盘;新建带 NVMe 登记的注册令牌并 --force 重跑即可补齐。"
    return 0
  fi
  if vgs superdl-nvme >/dev/null 2>&1; then echo "-- VG 已存在,跳过"; return 0; fi
  # 真实块设备原样用;loop:<GB> 是登记时的显式选择(无专用盘的测试兜底)
  local dev size pvs=()
  for dev in $devices; do
    if [[ "$dev" == loop:* ]]; then
      size="${dev#loop:}"; size="${size%[Gg]}"
      mkdir -p "$LVM_IMG_DIR"
      [[ -f "$LVM_IMG_DIR/superdl-nvme.img" ]] || truncate -s "${size}G" "$LVM_IMG_DIR/superdl-nvme.img"
      pvs+=("$(losetup --find --show "$LVM_IMG_DIR/superdl-nvme.img")")
      _write_loop_unit
      echo "-- 按登记选择:用 loop 文件做实例盘(${size}G,仅测试,非专用盘性能)"
    else
      pvs+=("$dev")
    fi
  done
  pvcreate -f "${pvs[@]}"
  vgcreate superdl-nvme "${pvs[@]}"
}

_write_loop_unit() {
  # 开机重建 loop(重启后 losetup 关系丢失,先于 k3s/rke2-agent 恢复)
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
    report reboot failed "已重启 ${count} 次仍未就绪(nouveau/IOMMU/驱动),请人工检查 BIOS 与内核日志"
    exit 1
  fi
  echo $((count + 1)) > "$STATE_DIR/reboot_count"
  # 自持久化(管道执行时 $0 不是文件,从 API 重拉自身并校验 bootstrap 下发的指纹)
  if [[ -f "${BASH_SOURCE[0]:-/nonexistent}" ]]; then
    cp "${BASH_SOURCE[0]}" "$STATE_DIR/node-join.sh"
  else
    curl -fsSL "$API_BASE/api/v1/node-enroll/script" -o "$STATE_DIR/node-join.sh"
    local want actual
    want="$(cfg_get script_sha256)"
    actual="$(sha256sum "$STATE_DIR/node-join.sh" | awk '{print $1}')"
    if [[ -n "$want" && "$actual" != "$want" ]]; then
      report reboot failed "重拉脚本指纹不符(期望 $want,实际 $actual),请检查 API 链路"
      echo "!! 重拉脚本指纹不符,已中止(期望 $want,实际 $actual)" >&2
      exit 1
    fi
  fi
  chmod 700 "$STATE_DIR/node-join.sh"
  # 断点续跑用令牌:新流程下已是窄权限 progress 令牌(旧流程为注册令牌,服务端兼容)
  printf '%s' "$TOKEN" > "$STATE_DIR/token"
  chmod 600 "$STATE_DIR/token"
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
  report reboot rebooting "重启以生效 nouveau 卸载/IOMMU/驱动,oneshot 将自动续跑"
  echo "==== 重启续跑 ===="
  systemctl reboot
  exit 0
}

step_registries() {
  local content
  content="$(cfg_get registries_yaml)"
  [[ -n "$content" ]] || { echo "-- registries_yaml 为空,跳过(镜像缓存未配置)"; return 0; }
  mkdir -p "$RANCHER_DIR"
  printf '%s\n' "$content" > "$RANCHER_DIR"/registries.yaml
  chmod 644 "$RANCHER_DIR"/registries.yaml
}

step_agent_config() {
  mkdir -p "$RANCHER_DIR"
  cat > "$RANCHER_DIR"/config.yaml <<EOF
server: $(cfg_get rke2_server_url)
token: $(cfg_get rke2_join_token)
node-label:
  - "superdl.io/pool=$(cfg_get pool)"
EOF
  chmod 600 "$RANCHER_DIR"/config.yaml
}

step_agent_install() {
  local want mirror url pin
  want="$(cfg_get rke2_version)"
  mirror="$(cfg_get install_mirror)"
  if command -v "$DISTRO" >/dev/null 2>&1 && "$DISTRO" --version | grep -q "$want"; then
    echo "-- $DISTRO $want 已安装,跳过"
    return 0
  fi
  # 安装源受 node_install_mirror 控制(默认 cn):get.k3s.io/get.rke2.io 不认 *_MIRROR
  # 环境变量,cn 必须用 rancher-mirror.rancher.cn 自带的 install 脚本取二进制。
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
  # 不裸 curl|sh:先落临时文件,校验内置 sha256 pin 后再执行
  local installer
  installer="$(mktemp "$STATE_DIR/installer.XXXXXX")"
  curl -fsSL "$url" -o "$installer"
  chmod 700 "$installer"
  if ! echo "$pin  $installer" | sha256sum -c - >/dev/null 2>&1; then
    echo "!! $DISTRO 安装脚本校验和不符($url):上游已更新或链路被篡改;" \
         "请核对上游后更新本平台脚本内置 pin 再重跑" >&2
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
  # rke2-agent / k3s-agent 均为 Type=notify:enable --now 阻塞到就绪,失败非零由 ERR trap 上报
  systemctl enable --now "$AGENT_UNIT"
  echo "-- ${AGENT_UNIT%.service} 已运行"
}

finalize() {
  CURRENT_PHASE="waiting_node"
  report waiting_node ok "${AGENT_UNIT%.service} 已启动,等待平台对账确认节点 Ready(管理端「待加入节点」可见进度)"
  if systemctl is-enabled --quiet "${RESUME_UNIT}.service" 2>/dev/null; then
    systemctl disable "${RESUME_UNIT}.service" || true
  fi
  rm -f "$ETC_DIR/systemd/system/${RESUME_UNIT}.service"
  systemctl daemon-reload
  # 装机完成即清敏感落盘:bootstrap.json(含集群 join token)与令牌文件不再有用
  rm -f "$STATE_DIR/bootstrap.json" "$STATE_DIR/token" "$STATE_DIR/curl.conf"
  mark_done completed
  echo "==== 完成:节点已启动 ${AGENT_UNIT%.service},加入结果以管理端为准 ===="
}

# ---------- 幂等入口与令牌装载 ----------
if [[ "$FORCE" == "1" ]]; then
  echo "-- --force:清除本地断点/已下发配置/旧令牌,从头装机(须用管理端新签发的令牌)"
  rm -rf "$STATE_DIR/done.d" "$STATE_DIR/bootstrap.json" "$STATE_DIR/token" \
    "$STATE_DIR/curl.conf" "$STATE_DIR/reboot_count"
  mkdir -p "$STATE_DIR/done.d"
elif marker completed; then
  echo "本节点已完成加入,无需操作;如需从头重装:--force 并使用管理端新签发的令牌"
  exit 0
fi
if marker bootstrap && [[ -f "$STATE_DIR/token" ]]; then
  # 断点续跑:注册令牌已被消费(再 bootstrap 也是 404),只用 progress 令牌上报
  use_token_file "$STATE_DIR/token"
else
  use_token_file "$TOKEN_FILE"
fi

# ---------- 主流程 ----------
if marker bootstrap; then
  echo "-- bootstrap: 已完成,跳过(沿用已下发配置;注册令牌一次性,不重复 bootstrap)"
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
