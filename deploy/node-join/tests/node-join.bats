#!/usr/bin/env bats
# node-join.sh 单测:PATH shim 伪造系统命令,不触碰真实系统。
# 运行:bats deploy/node-join/tests(需 apt install bats)

SCRIPT="$BATS_TEST_DIRNAME/../../../apps/api/app/modules/nodes/assets/node-join.sh"

setup() {
  TMP="$(mktemp -d)"
  export SUPERDL_JOIN_STATE_DIR="$TMP/state"
  export SUPERDL_JOIN_LOG_FILE="$TMP/join.log"
  export SUPERDL_JOIN_ETC_DIR="$TMP/etc"
  export SUPERDL_JOIN_LVM_DIR="$TMP/lvm"
  export CURL_LOG="$TMP/curl.log"
  export SHIM_CALLS="$TMP/calls.log"
  export BOOTSTRAP_FIXTURE="$TMP/bootstrap-fixture.json"
  export NVIDIA_OK=1
  export DPKG_INSTALLED=0
  # 注册令牌经文件传入(与生成命令同形态),不进进程 argv
  printf 'sdln_testtoken' > "$TMP/token"
  # 假安装器内容固定,pin 经 env 覆盖指向其真实 sha256(脚本内置 pin 是真上游的)
  export FAKE_SCRIPT_SHA256="$(printf '#!/bin/bash\n' | sha256sum | awk '{print $1}')"
  local fake_installer_sha256
  fake_installer_sha256="$(printf 'fake-installer\n' | sha256sum | awk '{print $1}')"
  export SUPERDL_JOIN_PIN_K3S_OFFICIAL="$fake_installer_sha256"
  export SUPERDL_JOIN_PIN_K3S_CN="$fake_installer_sha256"
  export SUPERDL_JOIN_PIN_RKE2_OFFICIAL="$fake_installer_sha256"
  export SUPERDL_JOIN_PIN_RKE2_CN="$fake_installer_sha256"
  mkdir -p "$TMP/etc/modprobe.d" "$TMP/etc/sysctl.d" "$TMP/etc/systemd/system" "$TMP/bin"
  _write_fixture hami
  _write_shims
  PATH="$TMP/bin:$PATH"
}

teardown() { rm -rf "$TMP"; }

_write_fixture() { # _write_fixture <pool> [distro] [mirror=cn] [script_sha256];字段与 BootstrapOut 契约一致,必发
  python3 - "$1" "${2:-rke2}" "${3:-}" "${4:-$FAKE_SCRIPT_SHA256}" > "$BOOTSTRAP_FIXTURE" <<'PYEOF'
import json, sys
distro = sys.argv[2]
data = {
    "pool": sys.argv[1],
    "rke2_version": "v1.36.2+rke2r1" if distro == "rke2" else "v1.36.3+k3s1",
    "rke2_server_url": "https://10.0.0.10:9345" if distro == "rke2" else "https://10.0.0.10:6443",
    "rke2_join_token": "K10fixture::server:secret",
    "driver_version": "580",
    "nvme_devices": [],
    "registries_yaml": 'mirrors:\n  "*": {}\n',
    # 首次 bootstrap 换发的窄权限 progress 令牌(仅上报进度)
    "progress_token": "sdlp_fixturetoken",
    "script_sha256": sys.argv[4],
}
data["k8s_distro"] = distro
data["install_mirror"] = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] else "cn"
print(json.dumps(data))
PYEOF
}

_write_shims() {
  # curl:bootstrap → 落 fixture;script → 假脚本;安装器 → 假安装器;progress → 记录后成功
  cat > "$TMP/bin/curl" <<'EOF'
#!/usr/bin/env bash
echo "$*" >> "$CURL_LOG"
out=""; prev=""; mode=""
for a in "$@"; do
  [[ "$prev" == "-o" ]] && out="$a"
  [[ "$a" == *node-enroll/bootstrap* ]] && mode=bootstrap
  [[ "$a" == *node-enroll/script* ]] && mode=script
  [[ "$a" == *get.k3s.io* || "$a" == *k3s-install.sh* ]] && mode=installer
  [[ "$a" == *get.rke2.io* || "$a" == *rke2/install.sh* ]] && mode=installer
  prev="$a"
done
if [[ "$mode" == "bootstrap" && -n "$out" ]]; then cp "$BOOTSTRAP_FIXTURE" "$out"; fi
if [[ "$mode" == "script" && -n "$out" ]]; then echo "#!/bin/bash" > "$out"; fi
if [[ "$mode" == "installer" && -n "$out" ]]; then echo "fake-installer" > "$out"; fi
exit 0
EOF
  # id:伪装 root(脚本入口即查 root,测试须能以任意用户跑)
  cat > "$TMP/bin/id" <<'EOF'
#!/usr/bin/env bash
echo 0
EOF
  # nvidia-smi:NVIDIA_OK 控制;dpkg:DPKG_INSTALLED 控制
  cat > "$TMP/bin/nvidia-smi" <<'EOF'
#!/usr/bin/env bash
[[ "$NVIDIA_OK" == "1" ]] || exit 1
if [[ "$*" == *"name,memory.total"* ]]; then echo "NVIDIA GeForce RTX 4090, 24564"; exit 0; fi
# 无参数 = 概览表头(脚本从中取 CUDA 版本)
if [[ $# -eq 0 ]]; then echo "| NVIDIA-SMI 580.65.06    Driver Version: 580.65.06    CUDA Version: 12.8 |"; exit 0; fi
echo "580.65.06"
EOF
  cat > "$TMP/bin/dpkg" <<'EOF'
#!/usr/bin/env bash
echo "$*" >> "$SHIM_CALLS"
[[ "$DPKG_INSTALLED" == "1" ]] && { echo "ii  nvidia-driver-580-server"; exit 0; }
exit 1
EOF
  cat > "$TMP/bin/lspci" <<'EOF'
#!/usr/bin/env bash
echo "01:00.0 3D controller: NVIDIA Corporation AD102 RTX4090"
EOF
  cat > "$TMP/bin/df" <<'EOF'
#!/usr/bin/env bash
printf 'Avail\n999999999\n'
EOF
  cat > "$TMP/bin/systemctl" <<'EOF'
#!/usr/bin/env bash
echo "systemctl $*" >> "$SHIM_CALLS"
case "$1" in
  is-active) exit 0 ;;
  is-enabled) exit 1 ;;
  *) exit 0 ;;
esac
EOF
  cat > "$TMP/bin/rke2" <<'EOF'
#!/usr/bin/env bash
echo "rke2 version v1.36.2+rke2r1"
EOF
  # sh:安装器执行入口(sh <落盘文件>);记录安装器 env(如 INSTALL_K3S_MIRROR)。
  # 注意不可读 stdin:管道执行本脚本时 stdin 是脚本本体,偷读会吃掉未执行部分
  cat > "$TMP/bin/sh" <<'EOF'
#!/usr/bin/env bash
[[ -n "${INSTALL_K3S_MIRROR:-}" ]] && echo "sh INSTALL_K3S_MIRROR=$INSTALL_K3S_MIRROR" >> "$SHIM_CALLS"
[[ -n "${INSTALL_RKE2_MIRROR:-}" ]] && echo "sh INSTALL_RKE2_MIRROR=$INSTALL_RKE2_MIRROR" >> "$SHIM_CALLS"
exit 0
EOF
  # gpg:消费 stdin(curl 输出的 key),向 -o 目标写占位 keyring
  cat > "$TMP/bin/gpg" <<'EOF'
#!/usr/bin/env bash
out=""; prev=""
for a in "$@"; do [[ "$prev" == "-o" ]] && out="$a"; prev="$a"; done
cat >/dev/null 2>&1 || true
[[ -n "$out" ]] && echo "dummy-keyring" > "$out"
exit 0
EOF
  # nvidia-ctk:固定"已安装"(与宿主机状态解耦)
  cat > "$TMP/bin/nvidia-ctk" <<'EOF'
#!/usr/bin/env bash
echo "NVIDIA Container Toolkit CLI version 1.20.0"
EOF
  # losetup:伪造 loop 设备(loop:<GB> 兜底路径用)
  cat > "$TMP/bin/losetup" <<'EOF'
#!/usr/bin/env bash
echo "losetup $*" >> "$SHIM_CALLS"
echo "/dev/loop7"
EOF
  cat > "$TMP/bin/truncate" <<'EOF'
#!/usr/bin/env bash
echo "truncate $*" >> "$SHIM_CALLS"
# 末位参数即目标文件;路径已被 SUPERDL_JOIN_LVM_DIR 隔离到 TMP
f="${@: -1}"; : > "$f" 2>/dev/null || true
EOF
  local name
  for name in update-initramfs sysctl update-grub apt-get vgs pvcreate vgcreate lsmod; do
    cat > "$TMP/bin/$name" <<EOF
#!/usr/bin/env bash
echo "$name \$*" >> "\$SHIM_CALLS"
$([[ "$name" == vgs ]] && echo 'exit 1')
exit 0
EOF
  done
  # timeout:直接成功(跳过 /dev/tcp 连通性探测)
  cat > "$TMP/bin/timeout" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
  chmod +x "$TMP"/bin/*
}

run_script() { run bash "$SCRIPT" --token-file "$TMP/token" --api-base http://fake.local "$@"; }

@test "缺少 --token-file 退出 2" {
  run bash "$SCRIPT" --api-base http://fake.local
  [ "$status" -eq 2 ]
  [[ "$output" == *"缺少 --token-file"* ]]
}

@test "token 文件不存在退出 2" {
  run bash "$SCRIPT" --token-file "$TMP/no-such" --api-base http://fake.local
  [ "$status" -eq 2 ]
  [[ "$output" == *"token 文件不存在"* ]]
}

@test "占位符未替换且未给 --api-base 退出 2" {
  run bash "$SCRIPT" --token-file "$TMP/token"
  [ "$status" -eq 2 ]
  [[ "$output" == *"占位符未替换"* ]]
}

@test "全流程(驱动就绪免重启):写出 rke2 config/registries,marker 齐全,进度上报到位" {
  run_script
  [ "$status" -eq 0 ]
  # rke2 config:server/token/池标签,0600
  grep -q "superdl.io/pool=hami" "$TMP/etc/rancher/rke2/config.yaml"
  grep -q "K10fixture::server:secret" "$TMP/etc/rancher/rke2/config.yaml"
  [ "$(stat -c %a "$TMP/etc/rancher/rke2/config.yaml")" = "600" ]
  # registries.yaml 落位
  grep -q 'mirrors:' "$TMP/etc/rancher/rke2/registries.yaml"
  # markers 齐全(含 bootstrap 与完成标记)
  for m in bootstrap precheck nouveau sysctl iommu driver nvidia_toolkit nvme_vg registries agent_config agent_install agent_start completed; do
    [ -f "$SUPERDL_JOIN_STATE_DIR/done.d/$m" ]
  done
  # NVIDIA Container Toolkit:此处 nvidia-ctk 已存在,走跳过分支
  [[ "$output" == *"nvidia-container-toolkit 已安装"* ]]
  # 进度上报含关键阶段与收尾
  grep -q '"phase":"agent_start","state":"ok"' "$CURL_LOG"
  grep -q '"phase":"waiting_node","state":"ok"' "$CURL_LOG"
  # 非 kata 池不写 GRUB
  [ ! -f "$TMP/etc/default/grub.d/99-superdl.cfg" ]
  # bootstrap 上报全卡清单(名称+显存 MiB)
  grep -q '"gpu_details": \[{"name": "NVIDIA GeForce RTX 4090", "memory_mib": 24564}\]' "$CURL_LOG"
  # 驱动/CUDA 版本在驱动已加载的收尾上报附带(首装需重启,bootstrap 时采不到)
  grep -q '"phase":"waiting_node","state":"ok".*"driver_version":"580.65.06","cuda_version":"12.8"' "$CURL_LOG"
}

@test "令牌全程不进进程 argv;完成后 bootstrap.json 与令牌落盘即清" {
  run_script
  [ "$status" -eq 0 ]
  # curl 全部经 --config 注入 Authorization,argv(curl.log)不含任何令牌
  grep -q -- "--config" "$CURL_LOG"
  ! grep -q 'sdln_testtoken' "$CURL_LOG"
  ! grep -q 'sdlp_fixturetoken' "$CURL_LOG"
  # 装机完成即清敏感落盘
  [ ! -e "$SUPERDL_JOIN_STATE_DIR/bootstrap.json" ]
  [ ! -e "$SUPERDL_JOIN_STATE_DIR/token" ]
  [ ! -e "$SUPERDL_JOIN_STATE_DIR/curl.conf" ]
}

@test "完成后重跑:直接退出,不重复 bootstrap 不重复装机" {
  run_script
  [ "$status" -eq 0 ]
  run_script
  [ "$status" -eq 0 ]
  [[ "$output" == *"已完成加入"* ]]
  # bootstrap 只发生一次(注册令牌一次性,重跑也不再换配置)
  [ "$(grep -c 'node-enroll/bootstrap' "$CURL_LOG")" = "1" ]
  ! grep -q "vgcreate superdl-nvme" "$SHIM_CALLS"
}

@test "--force:清除断点从头重装(须管理端新签发令牌)" {
  run_script
  [ "$status" -eq 0 ]
  run_script --force
  [ "$status" -eq 0 ]
  [[ "$output" == *"--force:清除本地断点"* ]]
  [ "$(grep -c 'node-enroll/bootstrap' "$CURL_LOG")" = "2" ]
}

@test "断点续跑:bootstrap 已有 marker 时跳过并用盘上 progress 令牌上报" {
  # 首轮在 agent_start 处人为失败(agent 启动失败):保留 bootstrap marker/已下发配置/令牌落盘
  cat > "$TMP/bin/systemctl" <<'EOF'
#!/usr/bin/env bash
echo "systemctl $*" >> "$SHIM_CALLS"
case "$1" in
  is-active) exit 0 ;;
  is-enabled) exit 1 ;;
  enable) [[ "$*" == *--now* ]] && exit 1; exit 0 ;;
  *) exit 0 ;;
esac
EOF
  chmod +x "$TMP/bin/systemctl"
  run_script
  [ "$status" -eq 1 ]
  grep -q '"phase":"agent_start","state":"failed"' "$CURL_LOG"
  [ -f "$SUPERDL_JOIN_STATE_DIR/done.d/bootstrap" ]
  [ "$(cat "$SUPERDL_JOIN_STATE_DIR/token")" = "sdlp_fixturetoken" ]
  # 修复环境后重跑同一命令:bootstrap 跳过,盘上 progress 令牌接管上报
  cat > "$TMP/bin/systemctl" <<'EOF'
#!/usr/bin/env bash
echo "systemctl $*" >> "$SHIM_CALLS"
case "$1" in
  is-active) exit 0 ;;
  is-enabled) exit 1 ;;
  *) exit 0 ;;
esac
EOF
  chmod +x "$TMP/bin/systemctl"
  run_script
  [ "$status" -eq 0 ]
  [[ "$output" == *"bootstrap: 已完成,跳过"* ]]
  [ "$(grep -c 'node-enroll/bootstrap' "$CURL_LOG")" = "1" ]
  # 完成后令牌落盘即清
  [ ! -e "$SUPERDL_JOIN_STATE_DIR/token" ]
}

@test "驱动未就绪触发重启断点:装驱动+写 oneshot+progress 令牌 0600+systemctl reboot,rke2 尚未配置" {
  export NVIDIA_OK=0
  run_script
  [ "$status" -eq 0 ]
  grep -q "apt-get install" "$SHIM_CALLS"
  grep -q "systemctl reboot" "$SHIM_CALLS"
  [ -f "$TMP/etc/systemd/system/superdl-node-join-resume.service" ]
  grep -q -- "--token-file" "$TMP/etc/systemd/system/superdl-node-join-resume.service"
  # 续跑令牌已是 bootstrap 换发的窄权限 progress 令牌
  [ "$(cat "$SUPERDL_JOIN_STATE_DIR/token")" = "sdlp_fixturetoken" ]
  [ "$(stat -c %a "$SUPERDL_JOIN_STATE_DIR/token")" = "600" ]
  [ "$(stat -c %a "$SUPERDL_JOIN_STATE_DIR/curl.conf")" = "600" ]
  [ "$(cat "$SUPERDL_JOIN_STATE_DIR/reboot_count")" = "1" ]
  [ ! -f "$TMP/etc/rancher/rke2/config.yaml" ]
  grep -q '"phase":"reboot","state":"rebooting"' "$CURL_LOG"
}

@test "管道执行的重启断点:从 API 重拉自身并校验 bootstrap 下发的指纹" {
  export NVIDIA_OK=0
  run bash -s -- --token-file "$TMP/token" --api-base http://fake.local < "$SCRIPT"
  [ "$status" -eq 0 ]
  # 重拉的副本经指纹校验(fixture 的 script_sha256 即假脚本正文的 sha256)
  [ "$(cat "$SUPERDL_JOIN_STATE_DIR/node-join.sh")" = "#!/bin/bash" ]
  grep -q "systemctl reboot" "$SHIM_CALLS"
}

@test "重拉脚本指纹不符:中止重启并上报 failed" {
  export NVIDIA_OK=0
  _write_fixture hami rke2 "" "0000000000000000000000000000000000000000000000000000000000000000"
  run bash -s -- --token-file "$TMP/token" --api-base http://fake.local < "$SCRIPT"
  [ "$status" -eq 1 ]
  [[ "$output" == *"指纹不符"* ]]
  grep -q '"phase":"reboot","state":"failed"' "$CURL_LOG"
}

@test "安装脚本 pin 校验和不符:拒绝执行并上报 failed" {
  cat > "$TMP/bin/rke2" <<'RKESHIM'
#!/usr/bin/env bash
echo "rke2 version v0.0.0+rke2r0"
RKESHIM
  chmod +x "$TMP/bin/rke2"
  export SUPERDL_JOIN_PIN_RKE2_CN="0000000000000000000000000000000000000000000000000000000000000000"
  run_script
  [ "$status" -eq 1 ]
  [[ "$output" == *"校验和不符"* ]]
  grep -q '"phase":"agent_install","state":"failed"' "$CURL_LOG"
  # 校验不过的安装器绝不执行
  ! grep -q "sh INSTALL_RKE2_MIRROR" "$SHIM_CALLS"
}

@test "kata 池写 GRUB IOMMU 配置" {
  _write_fixture kata
  run_script
  [ "$status" -eq 0 ]
  grep -q "intel_iommu=on iommu=pt" "$TMP/etc/default/grub.d/99-superdl.cfg"
  grep -q "update-grub" "$SHIM_CALLS"
}

@test "k3s 模式:config/registries 落 /etc/rancher/k3s,走中国镜像 agent 安装并起 k3s-agent" {
  _write_fixture hami k3s
  # k3s shim 报低版本:覆盖宿主机可能存在的真 k3s,并兼测版本不符触发重装
  cat > "$TMP/bin/k3s" <<'EOF'
#!/usr/bin/env bash
echo "k3s version v0.0.0+k3s0"
EOF
  chmod +x "$TMP/bin/k3s"
  run_script
  [ "$status" -eq 0 ]
  grep -q "superdl.io/pool=hami" "$TMP/etc/rancher/k3s/config.yaml"
  [ "$(stat -c %a "$TMP/etc/rancher/k3s/config.yaml")" = "600" ]
  grep -q 'mirrors:' "$TMP/etc/rancher/k3s/registries.yaml"
  [ ! -e "$TMP/etc/rancher/rke2" ]
  # 默认走 k3s 官方中国镜像的安装脚本(认 INSTALL_K3S_MIRROR=cn)
  grep -q "rancher-mirror.rancher.cn/k3s/k3s-install.sh" "$CURL_LOG"
  grep -q "INSTALL_K3S_MIRROR=cn" "$SHIM_CALLS"
  grep -q "systemctl enable --now k3s-agent.service" "$SHIM_CALLS"
}

@test "rke2 安装默认走中国镜像(install_mirror=cn)" {
  cat > "$TMP/bin/rke2" <<'RKESHIM'
#!/usr/bin/env bash
echo "rke2 version v0.0.0+rke2r0"
RKESHIM
  chmod +x "$TMP/bin/rke2"
  run_script
  [ "$status" -eq 0 ]
  grep -q "rancher-mirror.rancher.cn/rke2/install.sh" "$CURL_LOG"
  grep -q "sh INSTALL_RKE2_MIRROR=cn" "$SHIM_CALLS"
}

@test "install_mirror=official 走 get.rke2.io 官方源" {
  _write_fixture hami rke2 official
  cat > "$TMP/bin/rke2" <<'RKESHIM'
#!/usr/bin/env bash
echo "rke2 version v0.0.0+rke2r0"
RKESHIM
  chmod +x "$TMP/bin/rke2"
  run_script
  [ "$status" -eq 0 ]
  grep -q "get.rke2.io" "$CURL_LOG"
  ! grep -q "sh INSTALL_RKE2_MIRROR" "$SHIM_CALLS"
}

@test "loop 兜底须显式登记(nvme_devices=loop:80G):建 loop VG + 写开机重建 unit" {
  python3 - > "$BOOTSTRAP_FIXTURE" <<'PYEOF'
import json
print(json.dumps({"pool":"hami","k8s_distro":"rke2","install_mirror":"cn",
  "rke2_version":"v1.36.2+rke2r1","rke2_server_url":"https://10.0.0.10:9345","rke2_join_token":"K10::server:secret",
  "driver_version":"580","nvme_devices":["loop:80G"],"registries_yaml":"","progress_token":"sdlp_fixturetoken"}))
PYEOF
  run_script
  [ "$status" -eq 0 ]
  grep -q "truncate -s 80G" "$SHIM_CALLS"
  grep -q "losetup --find --show" "$SHIM_CALLS"
  grep -q "vgcreate superdl-nvme /dev/loop7" "$SHIM_CALLS"
  [ -f "$TMP/etc/systemd/system/superdl-nvme-loop.service" ]
  [[ "$output" == *"用 loop 文件做实例盘"* ]]
}

@test "未登记 NVMe:显式告警不兜底(不建任何 VG)" {
  run_script
  [ "$status" -eq 0 ]
  [[ "$output" == *"不自动兜底"* ]]
  ! grep -q "vgcreate superdl-nvme" "$SHIM_CALLS"
  [ ! -f "$TMP/etc/systemd/system/superdl-nvme-loop.service" ]
}

@test "重启循环保护:2 次后仍未就绪上报 failed 并退出 1" {
  export NVIDIA_OK=0
  export DPKG_INSTALLED=1
  mkdir -p "$SUPERDL_JOIN_STATE_DIR"
  echo 2 > "$SUPERDL_JOIN_STATE_DIR/reboot_count"
  run_script
  [ "$status" -eq 1 ]
  grep -q '"phase":"reboot","state":"failed"' "$CURL_LOG"
}

@test "日志 0644 且敏感落盘 0600/0700(umask 前置,无先宽后窄窗口)" {
  run_script
  [ "$status" -eq 0 ]
  [ "$(stat -c %a "$TMP/join.log")" = "644" ]
  [ "$(stat -c %a "$TMP/etc/rancher/rke2/config.yaml")" = "600" ]
  # registries.yaml 含仓库认证凭据(configs.auth):必须与 config.yaml 同口径 600
  [ "$(stat -c %a "$TMP/etc/rancher/rke2/registries.yaml")" = "600" ]
  [ "$(stat -c %a "$SUPERDL_JOIN_STATE_DIR")" = "700" ]
}

@test "--uninstall:停 agent、删本脚本写入的全部配置、清状态目录,不碰 VG 与驱动" {
  run_script
  [ "$status" -eq 0 ]
  # 断言用动作行都是 uninstall 独有的,不截断 SHIM_CALLS(安装期 tee 仍在收尾,截断会竞态)
  run bash "$SCRIPT" --uninstall --token-file "$TMP/token" --api-base http://fake.local
  [ "$status" -eq 0 ]
  grep -q "systemctl disable --now rke2-agent.service" "$SHIM_CALLS"
  # 本脚本写入的文件全部清除
  [ ! -e "$SUPERDL_JOIN_STATE_DIR" ]
  [ ! -f "$TMP/etc/rancher/rke2/config.yaml" ]
  [ ! -f "$TMP/etc/rancher/rke2/registries.yaml" ]
  [ ! -f "$TMP/etc/sysctl.d/99-superdl.conf" ]
  [ ! -f "$TMP/etc/modprobe.d/blacklist-nouveau.conf" ]
  # 业务数据与驱动不动:绝不出现 vgremove / apt-get remove
  ! grep -q "vgremove" "$SHIM_CALLS"
  ! grep -q "apt-get remove" "$SHIM_CALLS"
  [[ "$output" == *"kubectl delete node"* ]]
}
