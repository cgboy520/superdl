#!/usr/bin/env bash
# SuperDL GPU 节点一键加入脚本。
# 用法(命令由管理端生成;token 经 stdin 落 0600 文件,不进 URL 也不进任何进程 argv):
#   echo 'sdln_xxx' | sudo sh -c 'umask 077; cat > /run/superdl-join.token; curl -fsSL <API>/api/v1/node-enroll/script | bash -s -- --token-file /run/superdl-join.token; s=$?; rm -f /run/superdl-join.token; exit $s'
#   wget -qO node-join.sh <API>/api/v1/node-enroll/script && echo 'sdln_xxx' | sudo sh -c 'umask 077; cat > /run/superdl-join.token; bash node-join.sh --token-file /run/superdl-join.token; s=$?; rm -f /run/superdl-join.token; exit $s'
#
# 约束:
# - 脚本本体零密钥;server 地址与 join token 凭注册令牌 POST /bootstrap 换取。
# - 注册令牌一次性:bootstrap 后换发 progress 令牌落 $STATE_DIR/token(0600),续跑只用它。
# - k8s_distro 由服务端下发(rke2 / k3s)。
# - 全幂等:每步落 marker($STATE_DIR/done.d/);已完成重跑直接退出,从头重装须 --force + 新令牌。
# - 池标签与 GPU operand 标签**一律由平台写,本脚本不碰**:发行版的 node-label 只在首次注册时生效,
#   留在节点侧只会变成第二事实源。切池因此不需要重跑本脚本(见 docs/reference/nodes.md)。
# - IOMMU 是装机基线,对全部带卡池都做:它只能开机生效,做成 kata 专属就把重启绑进了切池。
# - 需重启的步骤合并为一次重启,systemd oneshot 断点续跑,最多 2 次;重启前从 API 重拉自身并校验 script_sha256。
# - k3s/rke2 安装器先落临时文件、校验内置 sha256 pin 再执行。
# - phase 取值与后端契约一致:bootstrap precheck nouveau sysctl iommu driver
#   nvidia_toolkit nvme_vg reboot registries agent_config agent_install agent_start waiting_node
set -eEuo pipefail
# umask 077:令牌/配置落盘即 0600;仅日志显式放宽 0644
umask 077

API_BASE="__API_BASE__" # 服务端下发时替换;可用 --api-base 覆盖(测试用)
# 路径可经 env 覆盖(仅 bats 测试隔离)
STATE_DIR="${SUPERDL_JOIN_STATE_DIR:-/var/lib/superdl-node-join}"
LOG_FILE="${SUPERDL_JOIN_LOG_FILE:-/var/log/superdl-node-join.log}"
ETC_DIR="${SUPERDL_JOIN_ETC_DIR:-/etc}"
RANCHER_STATE_DIR="${SUPERDL_JOIN_RANCHER_STATE_DIR:-/var/lib/rancher}"  # kubelet drop-in 落点 <distro>/agent/etc/kubelet.conf.d
LVM_IMG_DIR="${SUPERDL_JOIN_LVM_DIR:-/var/lib/superdl-lvm}"  # loop 兜底镜像目录(仅显式选择时用)
# IOMMU 分组目录:非空 = 直通已生效(可覆盖仅为 bats 造状态)
IOMMU_GROUPS_DIR="${SUPERDL_JOIN_IOMMU_DIR:-/sys/kernel/iommu_groups}"
RESUME_UNIT="superdl-node-join-resume"
TOKEN=""
TOKEN_FILE=""
FORCE=0
UNINSTALL=0
CURRENT_PHASE="init"
NEED_REBOOT=0
DRIVER_VERSION=""
CUDA_VERSION=""

# k3s/rke2 安装器 sha256 pin;上游更新后同步改本值与 deploy/ansible/site.yml 的同名 pin。SUPERDL_JOIN_PIN_* 为 bats 与应急覆盖口
PIN_K3S_OFFICIAL="${SUPERDL_JOIN_PIN_K3S_OFFICIAL:-ed01f89fd977bf20ac1516bbebf8370bf3ddbaa55dac8aba610956a4c78cc00b}"
PIN_K3S_CN="${SUPERDL_JOIN_PIN_K3S_CN:-3944aa467eb945b5ff2151a8e4f8d4a5f3a210d31ab39aec81f37606936d0863}"
PIN_RKE2_OFFICIAL="${SUPERDL_JOIN_PIN_RKE2_OFFICIAL:-42983c86d1da64a92061d83afb57630cedd69241989f1b0673f3db6c3d92ee6b}"
PIN_RKE2_CN="${SUPERDL_JOIN_PIN_RKE2_CN:-5541410b86d4d19d927d820be85156e787be3fdb24be72507af52932a1d12de1}"

# nvidia-container-toolkit 版本下限(CVE-2025-23266 修复版起)
NVCTK_MIN_VERSION="${SUPERDL_JOIN_NVCTK_MIN_VERSION:-1.17.8}"
# 单 Pod PID 上限,落 kubelet.conf.d drop-in;与 deploy/cluster/rke2/kubelet-superdl.conf 同值
POD_PIDS_LIMIT="${SUPERDL_JOIN_POD_PIDS_LIMIT:-4096}"

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
# superdl-nvme VG 与 loop 镜像一律保留,不 vgremove
if [[ "$UNINSTALL" == "1" ]]; then
  echo "==== $(date -Is) node-join --uninstall ===="
  DISTRO_NAME=""
  for d in rke2 k3s; do
    if [[ -d "$ETC_DIR/rancher/$d" ]] || command -v "$d" >/dev/null 2>&1; then DISTRO_NAME="$d"; fi
  done
  # 本机是 server 时 agent 相关与 server 配置一律不动
  SERVER_HERE=0
  if [[ "$DISTRO_NAME" == "k3s" ]] && systemctl is-active --quiet k3s.service 2>/dev/null; then SERVER_HERE=1; fi
  if [[ "$DISTRO_NAME" == "rke2" ]] && systemctl is-active --quiet rke2-server.service 2>/dev/null; then SERVER_HERE=1; fi
  AGENT="${DISTRO_NAME:+${DISTRO_NAME}-agent.service}"
  if [[ -n "$AGENT" && "$SERVER_HERE" == "0" ]]; then
    systemctl disable --now "$AGENT" 2>/dev/null || true
  fi
  # 发行版卸载脚本存在即执行;server 本机跳过
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
      "$ETC_DIR/rancher/$DISTRO_NAME/harbor-ca.crt" \
      "$RANCHER_STATE_DIR/$DISTRO_NAME/agent/etc/kubelet.conf.d/50-superdl.conf"
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
# 日志 0644(不含令牌)
touch "$LOG_FILE"
chmod 644 "$LOG_FILE"
exec > >(tee -a "$LOG_FILE") 2>&1
echo "==== $(date -Is) node-join 启动 (api=$API_BASE) ===="

# ---------- 基础函数 ----------
json_escape() { python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))'; }

# 令牌经 curl --config 注入 Authorization 头,不进 argv
use_token_file() { # use_token_file <path>
  TOKEN="$(cat "$1")"
  printf 'header = "Authorization: Bearer %s"\n' "$TOKEN" > "$STATE_DIR/curl.conf"
  chmod 600 "$STATE_DIR/curl.conf"
}

report() { # report <phase> <state> [message]
  local phase="$1" state="$2" message="${3:-}"
  local msg_json extra=""
  # json_escape 失败不得触发 ERR trap 递归
  msg_json="$(printf '%s' "$message" | json_escape || true)"
  # 驱动/CUDA 版本在 collect_driver_versions 之后才有值
  if [[ -n "$DRIVER_VERSION" ]]; then extra+=",\"driver_version\":\"$DRIVER_VERSION\""; fi
  if [[ -n "$CUDA_VERSION" ]]; then extra+=",\"cuda_version\":\"$CUDA_VERSION\""; fi
  curl -fsS -m 10 --retry 2 --config "$STATE_DIR/curl.conf" \
    -H "Content-Type: application/json" \
    -d "{\"phase\":\"$phase\",\"state\":\"$state\",\"message\":$msg_json$extra}" \
    "$API_BASE/api/v1/node-enroll/progress" >/dev/null || true
}

collect_driver_versions() {
  # cpu 池留空
  if is_cpu_pool; then return 0; fi
  # 驱动版本在收尾采集(首装 bootstrap 时驱动未加载);只留数字与点
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

# cpu 池跳过整条 NVIDIA 链路;只在 bootstrap 之后可用
is_cpu_pool() { [[ "$(cfg_get pool)" == "cpu" ]]; }

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

# 装载发行版参数(k8s_distro:rke2 / k3s)
load_distro() {
  DISTRO="$(cfg_get k8s_distro)"
  RANCHER_DIR="$ETC_DIR/rancher/$DISTRO"
  AGENT_UNIT="${DISTRO}-agent.service"
  SERVER_UNIT="$([[ "$DISTRO" == "k3s" ]] && echo k3s.service || echo rke2-server.service)"
}

# 本机已是 server(light 单机):不装 agent、不改 server config.yaml,池标签经本机 kubectl 打
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
  # 全卡清单 [{name, memory_mib}]:优先 nvidia-smi(无驱动返回非零,须 || true 兜住),取不到退回 lspci 名称
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
  # 无驱动 → lspci 名称;驱动只报 "NVIDIA Graphics Device" → 名称改用 lspci 方括号内型号
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
  # 驱动/CUDA 版本在收尾上报(collect_driver_versions)
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
  # 换发的 progress 令牌落盘,此后只用它
  printf '%s' "$(cfg_get progress_token)" > "$STATE_DIR/token"
  chmod 600 "$STATE_DIR/token"
  use_token_file "$STATE_DIR/token"
  echo "-- bootstrap 完成: pool=$(cfg_get pool) agent=$(cfg_get cluster_agent_version)"
}

step_precheck() {
  case "$(uname -m)" in x86_64 | aarch64) ;; *) echo "仅支持 x86_64 / aarch64(当前 $(uname -m))"; return 1 ;; esac
  command -v python3 >/dev/null || { echo "缺少 python3"; return 1; }
  command -v systemctl >/dev/null || { echo "需要 systemd"; return 1; }
  # 不用 grep -q(pipefail 下 SIGPIPE 误判);cpu 池不检查
  if is_cpu_pool; then
    echo "-- cpu 池:跳过 NVIDIA GPU 探测"
  else
    lspci 2>/dev/null | grep -i nvidia >/dev/null || { echo "未检测到 NVIDIA GPU"; return 1; }
  fi
  local avail_kb
  avail_kb="$(df --output=avail -k / | tail -1 | tr -d ' ')"
  [[ "$avail_kb" -ge $((50 * 1024 * 1024)) ]] || { echo "/ 分区可用空间不足 50G"; return 1; }
  # server 端口连通性(rke2 缺省 9345,k3s 缺省 6443)
  local server host port default_port=9345
  [[ "$DISTRO" == "k3s" ]] && default_port=6443
  server="$(cfg_get cluster_server_url)"
  host="$(python3 -c "from urllib.parse import urlparse;u=urlparse('$server');print(u.hostname)")"
  port="$(python3 -c "from urllib.parse import urlparse;u=urlparse('$server');print(u.port or $default_port)")"
  timeout 5 bash -c "</dev/tcp/$host/$port" || { echo "无法连通 $host:$port(检查内网路由/防火墙)"; return 1; }
}

step_nouveau() {
  if is_cpu_pool; then echo "-- cpu 池:无 NVIDIA 卡,跳过 nouveau 黑名单"; return 0; fi
  cat > "$ETC_DIR"/modprobe.d/blacklist-nouveau.conf <<'EOF'
blacklist nouveau
options nouveau modeset=0
EOF
  update-initramfs -u
  if lsmod | grep -q '^nouveau'; then NEED_REBOOT=1; echo "-- nouveau 已加载,需重启卸载"; fi
}

step_sysctl() {
  # inotify 实例数默认 128,GPU 栈起齐即耗尽
  printf 'user.max_user_namespaces=65536\nfs.inotify.max_user_instances=8192\nfs.inotify.max_user_watches=1048576\n' \
    > "$ETC_DIR"/sysctl.d/99-superdl.conf
  sysctl --system >/dev/null
}

# IOMMU 是**装机基线**,对全部带卡池都做,不按池分支:它只能开机生效,做成 kata 专属就等于把
# 一次重启绑进「切池」。iommu=pt 让宿主设备跳过 DMA 翻译,对不做直通的节点没有成本。
step_iommu() {
  if is_cpu_pool; then echo "-- cpu 池:无卡机,跳过 IOMMU"; return 0; fi
  # intel_iommu / iommu=pt 是 x86 参数;aarch64 的 SMMU 由固件 ACPI IORT 描述,内核启动即绑,无需 cmdline
  if [[ "$(uname -m)" == "x86_64" && ! -f "$ETC_DIR"/default/grub.d/99-superdl.cfg ]]; then
    mkdir -p "$ETC_DIR"/default/grub.d
    # shellcheck disable=SC2016  # 变量须由 GRUB 展开,单引号是预期
    echo 'GRUB_CMDLINE_LINUX_DEFAULT="$GRUB_CMDLINE_LINUX_DEFAULT intel_iommu=on iommu=pt"' \
      > "$ETC_DIR"/default/grub.d/99-superdl.cfg
    update-grub
  fi
  if [[ -z "$(ls -A "$IOMMU_GROUPS_DIR" 2>/dev/null)" ]]; then
    NEED_REBOOT=1
    echo "-- IOMMU 未生效,需重启(重启后仍未生效请检查 BIOS VT-d/AMD-Vi 或固件 SMMU 设置)"
  else
    echo "-- IOMMU 已生效(${IOMMU_GROUPS_DIR} 有分组)"
  fi
}

step_driver() {
  if is_cpu_pool; then echo "-- cpu 池:跳过 NVIDIA 驱动安装"; return 0; fi
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
  if is_cpu_pool; then echo "-- cpu 池:跳过 nvidia-container-toolkit"; return 0; fi
  # 已装但低于 NVCTK_MIN_VERSION 的必须升级
  local installed
  installed="$(dpkg-query -W -f='${Version}' nvidia-container-toolkit 2>/dev/null || true)"
  if [[ -n "$installed" ]] && dpkg --compare-versions "$installed" ge "$NVCTK_MIN_VERSION"; then
    echo "-- nvidia-container-toolkit $installed ≥ $NVCTK_MIN_VERSION,跳过"
  else
    if [[ -n "$installed" ]]; then
      echo "-- nvidia-container-toolkit $installed 低于下限 $NVCTK_MIN_VERSION,升级"
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
    # 装/升完仍低于下限即失败
    if [[ -z "$installed" ]] || dpkg --compare-versions "$installed" lt "$NVCTK_MIN_VERSION"; then
      echo "!! nvidia-container-toolkit 版本 ${installed:-缺失} 低于安全下限 $NVCTK_MIN_VERSION" >&2
      return 1
    fi
  fi
  # agent / server 已在跑时重启一次,让 containerd 重新探测 nvidia runtime
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
  # 未登记 NVMe 时不自动兜底
  if [[ -z "$devices" ]]; then
    echo "!! 未登记 NVMe 设备:不创建 superdl-nvme VG,也不自动兜底。" \
         "该节点无本地实例盘能力;如需 TopoLVM 本地盘,请在管理端为本节点新建" \
         "带 NVMe 登记的注册令牌,并用 --force 重跑。"
    report nvme_vg running \
      "未登记 NVMe 设备:跳过实例盘 VG(superdl-nvme),不自动兜底。该节点暂无 TopoLVM 本地实例盘;新建带 NVMe 登记的注册令牌并 --force 重跑即可补齐。"
    return 0
  fi
  # lvm.conf 落地不新增 phase
  step_lvm_discards
  if vgs superdl-nvme >/dev/null 2>&1; then echo "-- VG 已存在,跳过"; return 0; fi
  # loop:<GB> 是登记时的显式选择(测试兜底)
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
      # pvcreate 前硬检查:设备存在且无签名
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

# lvm.conf issue_discards=1(与 deploy/cluster/topolvm/lvm-config.configmap.yaml 同义);幂等
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
  # 开机重建 loop,先于 k3s/rke2-agent
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
  # 自持久化(管道执行时从 API 重拉自身并校验指纹)
  if [[ -f "${BASH_SOURCE[0]:-/nonexistent}" ]]; then
    cp "${BASH_SOURCE[0]}" "$STATE_DIR/node-join.sh"
  else
    curl -fsSL "$API_BASE/api/v1/node-enroll/script" -o "$STATE_DIR/node-join.sh"
    local want actual
    want="$(cfg_get script_sha256)"
    actual="$(sha256sum "$STATE_DIR/node-join.sh" | awk '{print $1}')"
    if [[ -z "$want" ]]; then
      report reboot failed "bootstrap 未下发 script_sha256,拒绝执行无法校验的重拉脚本"
      echo "!! bootstrap 未下发 script_sha256,已中止(拒绝执行无法校验的重拉脚本)" >&2
      exit 1
    fi
    if [[ "$actual" != "$want" ]]; then
      report reboot failed "重拉脚本指纹不符(期望 $want,实际 $actual),请检查 API 链路"
      echo "!! 重拉脚本指纹不符,已中止(期望 $want,实际 $actual)" >&2
      exit 1
    fi
  fi
  chmod 700 "$STATE_DIR/node-join.sh"
  # 续跑用 $STATE_DIR/token
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
    # Harbor 私有 CA 0644;registries.yaml 的 ca_file 占位替换为本机发行版目录
    printf '%s\n' "$ca" > "$RANCHER_DIR"/harbor-ca.crt
    chmod 644 "$RANCHER_DIR"/harbor-ca.crt
  else
    rm -f "$RANCHER_DIR"/harbor-ca.crt
  fi
  printf '%s\n' "${content//__RANCHER_DIR__/$RANCHER_DIR}" > "$RANCHER_DIR"/registries.yaml
  # 600(高级覆盖可能含仓库配置)
  chmod 600 "$RANCHER_DIR"/registries.yaml
}

# 池标签与 GPU Operator operand 标签**一律由平台写**,本脚本不碰:发行版的 node-label 只在节点首次
# 注册时生效,改不了已注册节点,留在这里只会变成第二事实源(切池后 config.yaml 永远过时,Node 对象
# 一旦重建就把旧池带回来)。节点不自声明池,顺带也没有冒名可伪造。见 docs/reference/nodes.md。
step_agent_config() {
  if is_server_node; then
    echo "-- 本机是 $SERVER_UNIT:不写 agent config;池标签由平台打到节点 $(hostname)"
    # 驱动/CUDA 版本趁早上报(平台打完标签即判 joined,之后上报 404 属预期)
    collect_driver_versions
    report agent_config running "server 本机:上报驱动版本,等平台打池标签"
    return 0
  fi
  mkdir -p "$RANCHER_DIR"
  # join token 必须非空
  local join_token server_url
  join_token="$(cfg_get cluster_join_token)"
  server_url="$(cfg_get cluster_server_url)"
  if [[ -z "$join_token" ]]; then
    echo "!! bootstrap 下发的 cluster_join_token 为空:拒绝写 agent 配置。" \
         "检查管理端「平台配置 · 集群接入」与 ansible agent_token(site.yml 有渲染前断言)" >&2
    return 1
  fi
  # 两个值原样进 YAML:字符集锁死(与 platform_config 的 pattern 同口径),换行/引号 = 注入任意 agent 参数
  if [[ ! "$join_token" =~ ^[A-Za-z0-9:._~+/=-]{16,512}$ ]]; then
    echo "!! cluster_join_token 含非法字符(只许 [A-Za-z0-9:._~+/=-]):拒绝写 agent 配置" >&2
    return 1
  fi
  if [[ ! "$server_url" =~ ^https://[][0-9A-Za-z.:-]+:[0-9]{1,5}$ ]]; then
    echo "!! cluster_server_url 格式非法:拒绝写 agent 配置" >&2
    return 1
  fi
  {
    echo "server: \"$server_url\""
    echo "token: \"$join_token\""
  } > "$RANCHER_DIR"/config.yaml
  chmod 600 "$RANCHER_DIR"/config.yaml
  # podPidsLimit 走 kubelet 配置 drop-in(不是 kubelet flag)
  local dropin_dir="$RANCHER_STATE_DIR/$DISTRO/agent/etc/kubelet.conf.d"
  mkdir -p "$dropin_dir"
  printf 'apiVersion: kubelet.config.k8s.io/v1beta1\nkind: KubeletConfiguration\npodPidsLimit: %s\n' \
    "$POD_PIDS_LIMIT" > "$dropin_dir/50-superdl.conf"
  chmod 644 "$dropin_dir/50-superdl.conf"
}

step_agent_install() {
  local want mirror url pin
  if is_server_node; then
    echo "-- 本机是 $SERVER_UNIT(已在集群内),跳过 agent 安装"
    return 0
  fi
  want="$(cfg_get cluster_agent_version)"
  mirror="$(cfg_get install_mirror)"
  if command -v "$DISTRO" >/dev/null 2>&1 && "$DISTRO" --version | grep -q "$want"; then
    echo "-- $DISTRO $want 已安装,跳过"
    return 0
  fi
  # 安装源受 node_install_mirror 控制(默认 cn,用 rancher-mirror.rancher.cn 的 install 脚本)
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
  # 先落临时文件,校验 sha256 pin 后执行
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
  # Type=notify:enable --now 阻塞到就绪
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
  # 清敏感落盘
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
  # 断点续跑只用盘上的 progress 令牌
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
