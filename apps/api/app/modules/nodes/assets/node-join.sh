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
# 生成文件一律先窄后宽:umask 077 保证令牌/配置落盘即 0600(不存在先 0644 再 chmod 的窗口),
# 仅日志显式放宽 0644 供运维日常 tail
umask 077

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
UNINSTALL=0
CURRENT_PHASE="init"
NEED_REBOOT=0
DRIVER_VERSION=""
CUDA_VERSION=""

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
    --uninstall) UNINSTALL=1; shift ;;
    *) echo "未知参数: $1" >&2; exit 2 ;;
  esac
done
[[ "$(id -u)" == "0" ]] || { echo "必须 root 执行(sudo bash ...)" >&2; exit 2; }

# ---------- 卸载(本地拆除,不碰业务数据) ----------
# 逆向拆除本脚本安装的一切;superdl-nvme VG 与 loop 镜像属业务数据,一律保留
# (节点清退后盘数据由平台另行处置,本地脚本绝不自动 vgremove)
if [[ "$UNINSTALL" == "1" ]]; then
  echo "==== $(date -Is) node-join --uninstall ===="
  DISTRO_NAME=""
  for d in rke2 k3s; do
    if [[ -d "$ETC_DIR/rancher/$d" ]] || command -v "$d" >/dev/null 2>&1; then DISTRO_NAME="$d"; fi
  done
  # 本机同时是 server(单机 light):agent 相关一律不动——发行版卸载脚本会把整个控制面拆掉,
  # server 的 config.yaml / registries.yaml 也不属本脚本所有
  SERVER_HERE=0
  if [[ "$DISTRO_NAME" == "k3s" ]] && systemctl is-active --quiet k3s.service 2>/dev/null; then SERVER_HERE=1; fi
  if [[ "$DISTRO_NAME" == "rke2" ]] && systemctl is-active --quiet rke2-server.service 2>/dev/null; then SERVER_HERE=1; fi
  AGENT="${DISTRO_NAME:+${DISTRO_NAME}-agent.service}"
  if [[ -n "$AGENT" && "$SERVER_HERE" == "0" ]]; then
    systemctl disable --now "$AGENT" 2>/dev/null || true
  fi
  # 发行版自带卸载脚本(k3s-agent-uninstall.sh / rke2-uninstall.sh)存在即执行;server 本机跳过
  if [[ -n "$DISTRO_NAME" && "$SERVER_HERE" == "0" ]]; then
    for us in "/usr/local/bin/${DISTRO_NAME}-agent-uninstall.sh" "/usr/local/bin/${DISTRO_NAME}-uninstall.sh"; do
      [[ -x "$us" ]] && { echo "-- 执行 $us"; "$us"; }
    done
  elif [[ "$SERVER_HERE" == "1" ]]; then
    echo "-- 本机是 ${DISTRO_NAME} server:不卸载发行版、不动 server 配置;池标签需在平台侧摘除(kubectl label node ... superdl.io/pool-)"
  fi
  # 续跑 oneshot 与 loop 重建 unit
  systemctl disable "${RESUME_UNIT}.service" 2>/dev/null || true
  rm -f "$ETC_DIR/systemd/system/${RESUME_UNIT}.service"
  systemctl disable superdl-nvme-loop.service 2>/dev/null || true
  rm -f "$ETC_DIR/systemd/system/superdl-nvme-loop.service"
  systemctl daemon-reload || true
  # 本脚本写入的 sysctl / GRUB IOMMU / nouveau 黑名单 / 集群配置
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
      "$ETC_DIR/rancher/$DISTRO_NAME/harbor-ca.crt"
  fi
  rm -rf "$STATE_DIR"
  echo "==== 卸载完成:agent 已移除,平台侧请记得在集群中删除该节点(kubectl delete node) ===="
  echo "-- 注意:superdl-nvme VG 与 loop 镜像属业务数据,未动;NVIDIA 驱动/container-toolkit 未动"
  exit 0
fi

[[ -n "$TOKEN_FILE" ]] || { echo "缺少 --token-file(在管理端「添加节点」生成命令,token 不落命令行)" >&2; exit 2; }
[[ -f "$TOKEN_FILE" ]] || { echo "token 文件不存在: $TOKEN_FILE" >&2; exit 2; }
[[ "$API_BASE" != "__API_BASE__" ]] || { echo "脚本须经 API 下发(占位符未替换),或用 --api-base 指定" >&2; exit 2; }

mkdir -p "$STATE_DIR/done.d"
chmod 700 "$STATE_DIR"
# 日志显式 0644(运维日常 tail 无需 root;不含令牌,令牌只落 0600 的 STATE_DIR 文件)
touch "$LOG_FILE"
chmod 644 "$LOG_FILE"
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
  local msg_json extra=""
  # json_escape 失败(python3 缺失等)不得让 ERR trap 在 on_error 里递归
  msg_json="$(printf '%s' "$message" | json_escape || true)"
  # 驱动/CUDA 版本只在 collect_driver_versions 之后有值(收尾上报附带,巡检落台账)
  if [[ -n "$DRIVER_VERSION" ]]; then extra+=",\"driver_version\":\"$DRIVER_VERSION\""; fi
  if [[ -n "$CUDA_VERSION" ]]; then extra+=",\"cuda_version\":\"$CUDA_VERSION\""; fi
  curl -fsS -m 10 --retry 2 --config "$STATE_DIR/curl.conf" \
    -H "Content-Type: application/json" \
    -d "{\"phase\":\"$phase\",\"state\":\"$state\",\"message\":$msg_json$extra}" \
    "$API_BASE/api/v1/node-enroll/progress" >/dev/null || true
}

collect_driver_versions() {
  # 驱动版本只有内核模块加载后才取得到:首装要经一次重启,bootstrap 时采不到;
  # 装机收尾时采集并随 waiting_node 上报,巡检把它落进节点台账。只留数字与点,便于直接拼 JSON
  DRIVER_VERSION="$({ nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null || true; } | head -1 | tr -cd '0-9.')"
  # 兼容 "CUDA Version: 12.8" 与新驱动的 "CUDA UMD Version: 13.3"
  CUDA_VERSION="$({ nvidia-smi 2>/dev/null || true; } | sed -n 's/.*CUDA[^:]*Version: \([0-9.]*\).*/\1/p' | head -1)"
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
  SERVER_UNIT="$([[ "$DISTRO" == "k3s" ]] && echo k3s.service || echo rke2-server.service)"
}

# 本机已是本集群的 server(light 单机:server 兼跑 GPU 负载)。判据是 server 服务在运行:
# 这种机器不装 agent、不改写 server 的 config.yaml,池标签经本机 kubectl 打到节点对象上
is_server_node() { systemctl is-active --quiet "$SERVER_UNIT" 2>/dev/null; }

server_kubectl() {
  if [[ "$DISTRO" == "k3s" ]]; then
    k3s kubectl "$@"
  else
    KUBECONFIG="$RANCHER_DIR/rke2.yaml" "${RKE2_BIN_DIR:-/var/lib/rancher/rke2/bin}/kubectl" "$@"
  fi
}

# ---------- 步骤实现 ----------
step_bootstrap() {
  local hostname kernel arch os_release gpu_details payload
  hostname="$(hostname)"
  kernel="$(uname -r)"
  arch="$(uname -m)"
  # shellcheck disable=SC1091  # 运行期 source 目标机文件
  os_release="$(. /etc/os-release && echo "$PRETTY_NAME")"
  # 全卡清单 [{name, memory_mib}]:优先 nvidia-smi(带显存,台账显存口径)。nvidia-smi 无驱动时
  # 返回非零,必须由 { ... || true; } 兜住,否则 pipefail 会中断整段采集;取不到退回 lspci
  # 名称(无显存,巡检按型号默认表补)
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
  # 无驱动 → 整体退回 lspci 名称;驱动只报通用名(CMP/工程样卡的 "NVIDIA Graphics Device",
  # 型号无法归一)→ 名称改用 lspci 方括号内型号,显存仍沿用 nvidia-smi
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
  # 驱动/CUDA 版本不在此采集:首装此时驱动未加载,统一在收尾上报(collect_driver_versions)
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
  # 注册令牌一次性:服务端已消费并换发 progress 令牌,此后上报与续跑只用它
  printf '%s' "$(cfg_get progress_token)" > "$STATE_DIR/token"
  chmod 600 "$STATE_DIR/token"
  use_token_file "$STATE_DIR/token"
  echo "-- bootstrap 完成: pool=$(cfg_get pool) rke2=$(cfg_get rke2_version)"
}

step_precheck() {
  [[ "$(uname -m)" == "x86_64" ]] || { echo "仅支持 x86_64"; return 1; }
  command -v python3 >/dev/null || { echo "缺少 python3"; return 1; }
  command -v systemctl >/dev/null || { echo "需要 systemd"; return 1; }
  # 不用 grep -q:pipefail 下 grep 命中即退出会让仍在输出的 lspci 收到 SIGPIPE,整条判为失败
  # (PCI 设备多的多卡机必现);grep 读完全部输出再判定
  lspci 2>/dev/null | grep -i nvidia >/dev/null || { echo "未检测到 NVIDIA GPU"; return 1; }
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
  # 若 agent 已在跑(重跑/补装场景),重启一次让 containerd 重新探测 nvidia runtime;
  # server 本机同理(单机 light),重启的是 server 服务
  if systemctl is-active --quiet "$AGENT_UNIT" 2>/dev/null; then
    echo "-- $AGENT_UNIT 已运行,重启以探测 nvidia runtime"
    systemctl restart "$AGENT_UNIT"
  elif is_server_node; then
    echo "-- 本机是 $SERVER_UNIT,重启以探测 nvidia runtime"
    systemctl restart "$SERVER_UNIT"
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
  # VG 新建与已存在(重跑/补装)都保证 lvm.conf 落地;不新增 phase(后端契约不变)
  step_lvm_discards
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
      # pvcreate 前硬检查(P2):设备必须存在且为空盘(无文件系统/RAID/分区签名)。
      # 登记错设备时 wipefs 能发现签名——宁可入群失败,不可误格有数据的盘
      if [[ ! -b "$dev" ]]; then
        echo "!! NVMe 设备不存在:$dev(登记信息有误?管理端核对节点 NVMe 登记)" >&2
        return 1
      fi
      if wipefs -n "$dev" 2>/dev/null | grep -q .; then
        echo "!! $dev 上已有签名(非空盘),拒绝 pvcreate:" >&2
        wipefs -n "$dev" >&2
        echo "!! 确认为空后先 wipefs -a $dev 再重跑;数据盘误登记请改登记信息" >&2
        return 1
      fi
      pvs+=("$dev")
    fi
  done
  pvcreate -f "${pvs[@]}"
  vgcreate superdl-nvme "${pvs[@]}"
}

# 实例盘擦除语义(P0-3):lvremove 对 extent 发 NVMe TRIM。TopoLVM lvmd 容器内
# 由 ConfigMap 注入(见 deploy/cluster/topolvm/lvm-config.configmap.yaml),此处保证
# 宿主机直接执行 LVM 时同语义(双保险)。幂等:已含 issue_discards 配置则跳过;
# 追加独立 devices 段,LVM 同键重复段后者生效,与既有配置合并安全。
step_lvm_discards() {
  local conf="$ETC_DIR/lvm/lvm.conf"
  if grep -q "^[[:space:]]*issue_discards[[:space:]]*=" "$conf" 2>/dev/null; then
    echo "-- lvm.conf 已含 issue_discards,跳过"
    return 0
  fi
  mkdir -p "$ETC_DIR/lvm"
  printf '\n# superdl:实例盘销毁发 NVMe TRIM(跨租户数据残留防护)\ndevices {\n    issue_discards = 1\n}\n' >> "$conf"
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
  # 断点续跑用的 progress 令牌已由 step_bootstrap 落在 $STATE_DIR/token(0600)
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
  local content ca
  content="$(cfg_get registries_yaml)"
  [[ -n "$content" ]] || { echo "-- registries_yaml 为空,跳过(镜像缓存未配置)"; return 0; }
  mkdir -p "$RANCHER_DIR"
  ca="$(cfg_get registry_ca_pem)"
  if [[ -n "$ca" ]]; then
    # Harbor 自签/私有 CA:公钥材料 0644;registries.yaml 里的 ca_file 占位替换为本机发行版目录
    printf '%s\n' "$ca" > "$RANCHER_DIR"/harbor-ca.crt
    chmod 644 "$RANCHER_DIR"/harbor-ca.crt
  else
    rm -f "$RANCHER_DIR"/harbor-ca.crt
  fi
  printf '%s\n' "${content//__RANCHER_DIR__/$RANCHER_DIR}" > "$RANCHER_DIR"/registries.yaml
  # 与 config.yaml 同口径 600:平台生成正文不含凭据,但高级覆盖可能含仓库配置,
  # 不放给节点上任意本地用户(含租户 Pod 逃逸后的立足点)
  chmod 600 "$RANCHER_DIR"/registries.yaml
}

# GPU Operator 的 operand 落点由节点标签决定(ClusterPolicy 不认各组件 nodeSelector,
# 见 deploy/cluster/values/gpu-operator.yaml 头注释)。这套标签必须与池标签同时落,
# 否则 hami 池会被官方 device-plugin 与 HAMi 抢注 nvidia.com/gpu、kata 池拿不到 VFIO 直通。
pool_gpu_labels() {  # pool_gpu_labels <pool> —— 输出 0 个或多个 key=value(mig 池无需额外标签)
  case "$1" in
    hami) echo "nvidia.com/gpu.deploy.device-plugin=false" ;;
    kata) echo "nvidia.com/gpu.workload.config=vm-passthrough" ;;
  esac
}

step_agent_config() {
  local pool extra
  pool="$(cfg_get pool)"
  # shellcheck disable=SC2207  # 逐行切词正是所需(每行一个 key=value,不含空格)
  extra=($(pool_gpu_labels "$pool"))
  if is_server_node; then
    # server 的 config.yaml 不能被 agent 配置覆盖;池标签用本机 kubectl 打到节点对象上
    # (Node 标签持久在集群数据库里,与 server 启动参数无关),对账器据此判定 joined
    echo "-- 本机是 $SERVER_UNIT:不写 agent config,池标签直接打到节点 $(hostname)"
    # 节点早已 Ready:池标签一落,对账器(30s)立即判 joined(终态,之后的上报一律 404),
    # 所以驱动/CUDA 版本要在打标签之前上报,否则台账永远缺这两列
    collect_driver_versions
    report agent_config running "server 本机:先上报驱动版本,再打池标签"
    server_kubectl label node "$(hostname)" "superdl.io/pool=$pool" "${extra[@]}" --overwrite
    return 0
  fi
  mkdir -p "$RANCHER_DIR"
  {
    echo "server: $(cfg_get rke2_server_url)"
    echo "token: $(cfg_get rke2_join_token)"
    echo "node-label:"
    echo "  - \"superdl.io/pool=$pool\""
    local l
    for l in "${extra[@]}"; do echo "  - \"$l\""; done
  } > "$RANCHER_DIR"/config.yaml
  chmod 600 "$RANCHER_DIR"/config.yaml
}

step_agent_install() {
  local want mirror url pin
  if is_server_node; then
    echo "-- 本机是 $SERVER_UNIT(已在集群内),跳过 agent 安装"
    return 0
  fi
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
  if is_server_node; then
    echo "-- 本机是 $SERVER_UNIT,无 agent 可启动"
    return 0
  fi
  # rke2-agent / k3s-agent 均为 Type=notify:enable --now 阻塞到就绪,失败非零由 ERR trap 上报
  systemctl enable --now "$AGENT_UNIT"
  echo "-- ${AGENT_UNIT%.service} 已运行"
}

finalize() {
  CURRENT_PHASE="waiting_node"
  collect_driver_versions
  local unit="$AGENT_UNIT"
  is_server_node && unit="$SERVER_UNIT"
  report waiting_node ok "${unit%.service} 已启动,等待平台对账确认节点 Ready(管理端「待加入节点」可见进度)"
  if systemctl is-enabled --quiet "${RESUME_UNIT}.service" 2>/dev/null; then
    systemctl disable "${RESUME_UNIT}.service" || true
  fi
  rm -f "$ETC_DIR/systemd/system/${RESUME_UNIT}.service"
  systemctl daemon-reload
  # 装机完成即清敏感落盘:bootstrap.json(含集群 join token)与令牌文件不再有用
  rm -f "$STATE_DIR/bootstrap.json" "$STATE_DIR/token" "$STATE_DIR/curl.conf"
  mark_done completed
  echo "==== 完成:节点已启动 ${unit%.service},加入结果以管理端为准 ===="
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
if marker bootstrap; then
  # 断点续跑:注册令牌已被消费(再 bootstrap 也是 404),只用盘上的 progress 令牌上报
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
