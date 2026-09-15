#!/usr/bin/env bats

SCRIPT="$BATS_TEST_DIRNAME/../../../apps/api/app/modules/nodes/assets/node-join.sh"

setup() {
  TMP="$(mktemp -d)"
  export SUPERDL_JOIN_STATE_DIR="$TMP/state"
  export SUPERDL_JOIN_LOG_FILE="$TMP/join.log"
  export SUPERDL_JOIN_ETC_DIR="$TMP/etc"
  export SUPERDL_JOIN_LVM_DIR="$TMP/lvm"
  export SUPERDL_JOIN_RANCHER_STATE_DIR="$TMP/rancher"
  export SUPERDL_JOIN_IOMMU_DIR="$TMP/iommu_groups"
  mkdir -p "$TMP/iommu_groups/0"
  export CURL_LOG="$TMP/curl.log"
  export SHIM_CALLS="$TMP/calls.log"
  export BOOTSTRAP_FIXTURE="$TMP/bootstrap-fixture.json"
  export NVIDIA_OK=1
  export DPKG_INSTALLED=0
  printf 'sdln_testtoken' > "$TMP/token"
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

_write_fixture() {
  python3 - "$1" "${2:-rke2}" "${3:-}" "${4-$FAKE_SCRIPT_SHA256}" > "$BOOTSTRAP_FIXTURE" <<'PYEOF'
import json, os, sys
distro = sys.argv[2]
data = {
    "pool": sys.argv[1],
    "cluster_agent_version": "v1.36.2+rke2r1" if distro == "rke2" else "v1.36.3+k3s1",
    "cluster_server_url": "https://10.0.0.10:9345" if distro == "rke2" else "https://10.0.0.10:6443",
    "cluster_join_token": os.environ.get("FIXTURE_JOIN_TOKEN", "superdl-agent-fixture-token-0123456789"),
    "driver_version": "580",
    "nvme_devices": [],
    "registries_yaml": os.environ.get("FIXTURE_REGISTRIES_YAML", 'mirrors:\n  "*": {}\n'),
    "registry_ca_pem": os.environ.get("FIXTURE_REGISTRY_CA", ""),
    "progress_token": "sdlp_fixturetoken",
    "script_sha256": sys.argv[4],
}
data["k8s_distro"] = distro
data["install_mirror"] = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] else "cn"
print(json.dumps(data))
PYEOF
}

_write_shims() {
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
  cat > "$TMP/bin/id" <<'EOF'
#!/usr/bin/env bash
echo 0
EOF
  cat > "$TMP/bin/nvidia-smi" <<'EOF'
#!/usr/bin/env bash
[[ "$NVIDIA_OK" == "1" ]] || exit 1
if [[ "$*" == *"name,memory.total"* ]]; then echo "NVIDIA GeForce RTX 4090, 24564"; exit 0; fi
if [[ $# -eq 0 ]]; then echo "| NVIDIA-SMI 580.65.06    Driver Version: 580.65.06    CUDA Version: 12.8 |"; exit 0; fi
echo "580.65.06"
EOF
  cat > "$TMP/bin/dpkg" <<'EOF'
#!/usr/bin/env bash
echo "$*" >> "$SHIM_CALLS"
if [[ "$1" == "--compare-versions" ]]; then
  a="$2"; op="$3"; b="$4"
  case "$op" in
    ge) [ "$(printf '%s\n%s\n' "$a" "$b" | sort -V | head -1)" = "$b" ] && exit 0 || exit 1 ;;
    lt) { [ "$(printf '%s\n%s\n' "$a" "$b" | sort -V | head -1)" = "$a" ] && [ "$a" != "$b" ]; } && exit 0 || exit 1 ;;
    *) exit 2 ;;
  esac
fi
[[ "$DPKG_INSTALLED" == "1" ]] && { echo "ii  nvidia-driver-580-server"; exit 0; }
exit 1
EOF
  cat > "$TMP/bin/dpkg-query" <<'EOF'
#!/usr/bin/env bash
echo "dpkg-query $*" >> "$SHIM_CALLS"
v="${DPKG_NVCTK_VERSION-1.17.8}"
if grep -q '^apt-get install -y -qq nvidia-container-toolkit' "$SHIM_CALLS" 2>/dev/null; then
  v="${DPKG_NVCTK_VERSION_AFTER:-$v}"
fi
[[ -n "$v" ]] || exit 1
echo "$v"
EOF
  cat > "$TMP/bin/lspci" <<'EOF'
#!/usr/bin/env bash
[[ "${LSPCI_NVIDIA:-1}" == "1" ]] || exit 0
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
  is-active) if [[ "$*" == *k3s.service* || "$*" == *rke2-server.service* ]]; then [[ "${SERVER_ACTIVE:-0}" == "1" ]] && exit 0 || exit 3; fi; exit 0 ;;
  is-enabled) exit 1 ;;
  *) exit 0 ;;
esac
EOF
  cat > "$TMP/bin/rke2" <<'EOF'
#!/usr/bin/env bash
echo "rke2 version v1.36.2+rke2r1"
EOF
  cat > "$TMP/bin/sh" <<'EOF'
#!/usr/bin/env bash
[[ -n "${INSTALL_K3S_MIRROR:-}" ]] && echo "sh INSTALL_K3S_MIRROR=$INSTALL_K3S_MIRROR" >> "$SHIM_CALLS"
[[ -n "${INSTALL_RKE2_MIRROR:-}" ]] && echo "sh INSTALL_RKE2_MIRROR=$INSTALL_RKE2_MIRROR" >> "$SHIM_CALLS"
exit 0
EOF
  cat > "$TMP/bin/gpg" <<'EOF'
#!/usr/bin/env bash
out=""; prev=""
for a in "$@"; do [[ "$prev" == "-o" ]] && out="$a"; prev="$a"; done
cat >/dev/null 2>&1 || true
[[ -n "$out" ]] && echo "dummy-keyring" > "$out"
exit 0
EOF
  cat > "$TMP/bin/nvidia-ctk" <<'EOF'
#!/usr/bin/env bash
echo "NVIDIA Container Toolkit CLI version 1.20.0"
EOF
  cat > "$TMP/bin/losetup" <<'EOF'
#!/usr/bin/env bash
echo "losetup $*" >> "$SHIM_CALLS"
echo "/dev/loop7"
EOF
  cat > "$TMP/bin/truncate" <<'EOF'
#!/usr/bin/env bash
echo "truncate $*" >> "$SHIM_CALLS"
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

@test "Harbor 自签 CA:落 harbor-ca.crt(0644),registries.yaml 的 __RANCHER_DIR__ 占位替换为本机目录" {
  export FIXTURE_REGISTRY_CA=$'-----BEGIN CERTIFICATE-----\nMIIBfake\n-----END CERTIFICATE-----'
  export FIXTURE_REGISTRIES_YAML=$'mirrors:\n  "*": {}\nconfigs:\n  "harbor.example.com":\n    tls:\n      ca_file: "__RANCHER_DIR__/harbor-ca.crt"\n'
  _write_fixture hami
  run_script
  [ "$status" -eq 0 ]
  grep -q 'BEGIN CERTIFICATE' "$TMP/etc/rancher/rke2/harbor-ca.crt"
  [ "$(stat -c %a "$TMP/etc/rancher/rke2/harbor-ca.crt")" = "644" ]
  grep -q "ca_file: \"$TMP/etc/rancher/rke2/harbor-ca.crt\"" "$TMP/etc/rancher/rke2/registries.yaml"
  ! grep -q '__RANCHER_DIR__' "$TMP/etc/rancher/rke2/registries.yaml"
}

@test "join token 含换行(YAML 注入)时拒绝写 agent config.yaml" {
  export FIXTURE_JOIN_TOKEN=$'superdl-agent-fixture-token-0123456789\nkubelet-arg:\n  - "anonymous-auth=true"'
  _write_fixture hami
  run_script
  [ "$status" -ne 0 ]
  [[ "$output" == *"cluster_join_token 含非法字符"* ]]
  [ ! -f "$TMP/etc/rancher/rke2/config.yaml" ]
}

@test "拒绝 server node-token(K10<64hex>::server:…):不写 agent config.yaml" {
  export FIXTURE_JOIN_TOKEN="K10$(printf 'a%.0s' $(seq 1 64))::server:secret"
  _write_fixture hami
  run_script
  [ "$status" -ne 0 ]
  [[ "$output" == *"server node-token"* ]]
  [ ! -f "$TMP/etc/rancher/rke2/config.yaml" ]
}

@test "agent token 取 K10<64hex>::node:<pw> 形态时放行" {
  export FIXTURE_JOIN_TOKEN="K10$(printf 'a%.0s' $(seq 1 64))::node:secret"
  _write_fixture hami
  run_script
  [ "$status" -eq 0 ]
  grep -q "::node:secret" "$TMP/etc/rancher/rke2/config.yaml"
}

@test "无 CA:不落 harbor-ca.crt,registries.yaml 只有 Spegel 段" {
  run_script
  [ "$status" -eq 0 ]
  [ ! -f "$TMP/etc/rancher/rke2/harbor-ca.crt" ]
  ! grep -q 'configs:' "$TMP/etc/rancher/rke2/registries.yaml"
}

@test "全流程(驱动就绪免重启):写出 rke2 config/registries,marker 齐全,进度上报到位" {
  run_script
  [ "$status" -eq 0 ]
  grep -q "superdl-agent-fixture-token-0123456789" "$TMP/etc/rancher/rke2/config.yaml"
  ! grep -q "node-label" "$TMP/etc/rancher/rke2/config.yaml"
  ! grep -q "superdl-pool" "$TMP/etc/rancher/rke2/config.yaml"
  ! grep -q "superdl.io/pool" "$TMP/etc/rancher/rke2/config.yaml"
  [ "$(stat -c %a "$TMP/etc/rancher/rke2/config.yaml")" = "600" ]
  grep -q 'mirrors:' "$TMP/etc/rancher/rke2/registries.yaml"
  [ "$(stat -c %a "$TMP/etc/rancher/rke2/registries.yaml")" = "600" ]
  [ "$(stat -c %a "$SUPERDL_JOIN_STATE_DIR")" = "700" ]
  [ "$(stat -c %a "$TMP/join.log")" = "644" ]
  for m in bootstrap precheck nouveau sysctl iommu driver nvidia_toolkit nvme_vg registries agent_config agent_install agent_start completed; do
    [ -f "$SUPERDL_JOIN_STATE_DIR/done.d/$m" ]
  done
  [[ "$output" == *"nvidia-container-toolkit 1.17.8 ≥ 1.17.8,跳过"* ]]
  grep -q 'podPidsLimit: 4096' "$TMP/rancher/rke2/agent/etc/kubelet.conf.d/50-superdl.conf"
  ! grep -q 'podPidsLimit' "$TMP/etc/rancher/rke2/config.yaml"
  grep -q '"phase":"agent_start","state":"ok"' "$CURL_LOG"
  grep -q '"phase":"waiting_node","state":"ok"' "$CURL_LOG"
  grep -q '"gpu_details": \[{"name": "NVIDIA GeForce RTX 4090", "memory_mib": 24564}\]' "$CURL_LOG"
  grep -q '"phase":"waiting_node","state":"ok".*"driver_version":"580.65.06","cuda_version":"12.8"' "$CURL_LOG"
}

@test "令牌全程不进进程 argv;完成后 bootstrap.json 与令牌落盘即清" {
  run_script
  [ "$status" -eq 0 ]
  grep -q -- "--config" "$CURL_LOG"
  ! grep -q 'sdln_testtoken' "$CURL_LOG"
  ! grep -q 'sdlp_fixturetoken' "$CURL_LOG"
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
  cat > "$TMP/bin/systemctl" <<'EOF'
#!/usr/bin/env bash
echo "systemctl $*" >> "$SHIM_CALLS"
case "$1" in
  is-active) if [[ "$*" == *k3s.service* || "$*" == *rke2-server.service* ]]; then [[ "${SERVER_ACTIVE:-0}" == "1" ]] && exit 0 || exit 3; fi; exit 0 ;;
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
  cat > "$TMP/bin/systemctl" <<'EOF'
#!/usr/bin/env bash
echo "systemctl $*" >> "$SHIM_CALLS"
case "$1" in
  is-active) if [[ "$*" == *k3s.service* || "$*" == *rke2-server.service* ]]; then [[ "${SERVER_ACTIVE:-0}" == "1" ]] && exit 0 || exit 3; fi; exit 0 ;;
  is-enabled) exit 1 ;;
  *) exit 0 ;;
esac
EOF
  chmod +x "$TMP/bin/systemctl"
  run_script
  [ "$status" -eq 0 ]
  [[ "$output" == *"bootstrap: 已完成,跳过"* ]]
  [ "$(grep -c 'node-enroll/bootstrap' "$CURL_LOG")" = "1" ]
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
  [ "$(cat "$SUPERDL_JOIN_STATE_DIR/token")" = "sdlp_fixturetoken" ]
  [ "$(stat -c %a "$SUPERDL_JOIN_STATE_DIR/token")" = "600" ]
  [ "$(stat -c %a "$SUPERDL_JOIN_STATE_DIR/curl.conf")" = "600" ]
  [ "$(cat "$SUPERDL_JOIN_STATE_DIR/reboot_count")" = "1" ]
  [ ! -f "$TMP/etc/rancher/rke2/config.yaml" ]
  grep -q '"phase":"reboot","state":"rebooting"' "$CURL_LOG"
  grep -q '"gpu_details": \[{"name": "NVIDIA Corporation AD102 RTX4090"}\]' "$CURL_LOG"
}

@test "管道执行的重启断点:从 API 重拉自身并校验 bootstrap 下发的指纹" {
  export NVIDIA_OK=0
  run bash -s -- --token-file "$TMP/token" --api-base http://fake.local < "$SCRIPT"
  [ "$status" -eq 0 ]
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

@test "bootstrap 未下发 script_sha256:管道续跑拒绝执行重拉脚本(fail-closed)" {
  export NVIDIA_OK=0
  _write_fixture hami rke2 "" ""
  run bash -s -- --token-file "$TMP/token" --api-base http://fake.local < "$SCRIPT"
  [ "$status" -eq 1 ]
  [[ "$output" == *"script_sha256"* ]]
  grep -q '"phase":"reboot","state":"failed"' "$CURL_LOG"
  ! grep -q "systemctl reboot" "$SHIM_CALLS"
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
  ! grep -q "sh INSTALL_RKE2_MIRROR" "$SHIM_CALLS"
}

@test "IOMMU 是装机基线:hami 池也写 GRUB(x86),不因池而异" {
  _write_fixture hami
  run_script
  [ "$status" -eq 0 ]
  grep -q "intel_iommu=on iommu=pt" "$TMP/etc/default/grub.d/99-superdl.cfg"
  grep -q "update-grub" "$SHIM_CALLS"
}

@test "IOMMU 未生效则要求重启" {
  _write_fixture kata
  export NVIDIA_OK=0
  rmdir "$SUPERDL_JOIN_IOMMU_DIR/0"
  run_script
  [ "$status" -eq 0 ]
  [[ "$output" == *"IOMMU 未生效,需重启"* ]]
}

@test "cpu 池(无卡机):跳过 NVIDIA 探测/驱动/toolkit/IOMMU,不写 node-label,不上报驱动版本" {
  _write_fixture cpu
  export NVIDIA_OK=0 LSPCI_NVIDIA=0
  run_script
  [ "$status" -eq 0 ]
  ! grep -q "node-label" "$TMP/etc/rancher/rke2/config.yaml"
  ! grep -q "nvidia.com/" "$TMP/etc/rancher/rke2/config.yaml"
  ! grep -q "apt-get install" "$SHIM_CALLS"
  [ ! -f "$TMP/etc/modprobe.d/blacklist-nouveau.conf" ]
  [ ! -f "$TMP/etc/default/grub.d/99-superdl.cfg" ]
  [[ "$output" == *"跳过 NVIDIA GPU 探测"* ]]
  grep -q '"phase":"waiting_node","state":"ok"' "$CURL_LOG"
  ! grep -q '"driver_version"' "$CURL_LOG"
  grep -q '"gpu_details": \[\]' "$CURL_LOG"
}

@test "kata 池:宿主无驱动时跳过驱动与 toolkit 安装" {
  _write_fixture kata
  export NVIDIA_OK=0
  run_script
  [ "$status" -eq 0 ]
  [[ "$output" == *"跳过 NVIDIA 驱动安装"* ]]
  [[ "$output" == *"跳过 nvidia-container-toolkit"* ]]
  ! grep -q "apt-get install -y -qq nvidia-driver" "$SHIM_CALLS"
  ! grep -q "apt-get install -y -qq nvidia-container-toolkit" "$SHIM_CALLS"
  grep -q '"phase":"waiting_node","state":"ok"' "$CURL_LOG"
}

@test "kata 池:驱动装了但没加载(nvidia-smi 不通)同样判失败" {
  _write_fixture kata
  export NVIDIA_OK=0 DPKG_INSTALLED=1
  run_script
  [ "$status" -eq 1 ]
  [[ "$output" == *"kata 池要求宿主无 NVIDIA 驱动"* ]]
  grep -q '"phase":"driver","state":"failed"' "$CURL_LOG"
}

@test "kata 池:宿主预装驱动时 driver 阶段判失败,不带病入群" {
  _write_fixture kata
  export NVIDIA_OK=1
  run_script
  [ "$status" -eq 1 ]
  [[ "$output" == *"kata 池要求宿主无 NVIDIA 驱动"* ]]
  grep -q '"phase":"driver","state":"failed"' "$CURL_LOG"
}

@test "toolkit 未安装:走安装分支,装完达下限继续" {
  export DPKG_NVCTK_VERSION="" DPKG_NVCTK_VERSION_AFTER="1.17.8"
  run_script
  [ "$status" -eq 0 ]
  grep -q "apt-get install -y -qq nvidia-container-toolkit" "$SHIM_CALLS"
  grep -q '"phase":"nvidia_toolkit","state":"ok"' "$CURL_LOG"
}

@test "toolkit 装完仍低于下限:按 failed 上报,不带病入群" {
  export DPKG_NVCTK_VERSION="" DPKG_NVCTK_VERSION_AFTER="1.16.0"
  run_script
  [ "$status" -eq 1 ]
  [[ "$output" == *"低于安全下限"* ]]
  grep -q '"phase":"nvidia_toolkit","state":"failed"' "$CURL_LOG"
}

@test "toolkit 下限 env 覆盖:SUPERDL_JOIN_NVCTK_MIN_VERSION 生效" {
  export SUPERDL_JOIN_NVCTK_MIN_VERSION="99.0.0"
  run_script
  [ "$status" -eq 1 ]
  [[ "$output" == *"低于下限 99.0.0"* || "$output" == *"低于安全下限 99.0.0"* ]]
}

@test "k3s 模式:config/registries 落 /etc/rancher/k3s,走中国镜像 agent 安装并起 k3s-agent" {
  _write_fixture hami k3s
  cat > "$TMP/bin/k3s" <<'EOF'
#!/usr/bin/env bash
echo "k3s version v0.0.0+k3s0"
EOF
  chmod +x "$TMP/bin/k3s"
  run_script
  [ "$status" -eq 0 ]
  ! grep -q "node-label" "$TMP/etc/rancher/k3s/config.yaml"
  grep -q 'podPidsLimit: 4096' "$TMP/rancher/k3s/agent/etc/kubelet.conf.d/50-superdl.conf"
  ! grep -q 'podPidsLimit' "$TMP/etc/rancher/k3s/config.yaml"
  [ "$(stat -c %a "$TMP/etc/rancher/k3s/config.yaml")" = "600" ]
  grep -q 'mirrors:' "$TMP/etc/rancher/k3s/registries.yaml"
  [ ! -e "$TMP/etc/rancher/rke2" ]
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
  "cluster_agent_version":"v1.36.2+rke2r1","cluster_server_url":"https://10.0.0.10:9345","cluster_join_token":"superdl-agent-fixture-token-0123456789",
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

@test "--uninstall:停 agent、删本脚本写入的全部配置、清状态目录,不碰 VG 与驱动" {
  run_script
  [ "$status" -eq 0 ]
  run bash "$SCRIPT" --uninstall --token-file "$TMP/token" --api-base http://fake.local
  [ "$status" -eq 0 ]
  grep -q "systemctl disable --now rke2-agent.service" "$SHIM_CALLS"
  [ ! -e "$SUPERDL_JOIN_STATE_DIR" ]
  [ ! -f "$TMP/etc/rancher/rke2/config.yaml" ]
  [ ! -f "$TMP/etc/rancher/rke2/registries.yaml" ]
  [ ! -f "$TMP/etc/rancher/rke2/harbor-ca.crt" ]
  [ ! -f "$TMP/etc/sysctl.d/99-superdl.conf" ]
  [ ! -f "$TMP/etc/modprobe.d/blacklist-nouveau.conf" ]
  ! grep -q "vgremove" "$SHIM_CALLS"
  ! grep -q "apt-get remove" "$SHIM_CALLS"
  [[ "$output" == *"kubectl delete node"* ]]
}

@test "server 本机(k3s 在跑):不写 agent config、不装/不起 agent、不打池标签(平台写)" {
  _write_fixture hami k3s
  export SERVER_ACTIVE=1
  cat > "$TMP/bin/k3s" <<'EOF'
#!/usr/bin/env bash
echo "k3s $*" >> "$SHIM_CALLS"
[[ "$1" == "--version" ]] && echo "k3s version v1.36.3+k3s1"
exit 0
EOF
  chmod +x "$TMP/bin/k3s"
  run_script
  [ "$status" -eq 0 ]
  [ ! -e "$TMP/etc/rancher/k3s/config.yaml" ]
  ! grep -q "kubectl label node" "$SHIM_CALLS"
  grep -q '"phase":"agent_config".*"driver_version":"580.65.06"' "$CURL_LOG"
  ! grep -q "k3s-install.sh" "$CURL_LOG"
  ! grep -q "systemctl enable --now k3s-agent.service" "$SHIM_CALLS"
  grep -q "waiting_node" "$CURL_LOG"
  [[ "$output" == *"节点已启动 k3s"* ]]
  [ -f "$SUPERDL_JOIN_STATE_DIR/done.d/completed" ]
}

@test "server 本机 --uninstall:不执行发行版卸载脚本、不删 server 的 config/registries" {
  export SERVER_ACTIVE=1
  mkdir -p "$TMP/etc/rancher/k3s"
  echo "server: config" > "$TMP/etc/rancher/k3s/config.yaml"
  echo 'mirrors:' > "$TMP/etc/rancher/k3s/registries.yaml"
  cat > "$TMP/bin/k3s" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
  chmod +x "$TMP/bin/k3s"
  run bash "$SCRIPT" --uninstall
  [ "$status" -eq 0 ]
  [ -f "$TMP/etc/rancher/k3s/config.yaml" ]
  [ -f "$TMP/etc/rancher/k3s/registries.yaml" ]
  ! grep -q "disable --now k3s-agent.service" "$SHIM_CALLS"
  [[ "$output" == *"不卸载发行版"* ]]
}

@test "nvidia-smi 只报通用名(NVIDIA Graphics Device):bootstrap 型号回落 lspci 方括号名,显存沿用 nvidia-smi" {
  cat > "$TMP/bin/nvidia-smi" <<'EOF'
#!/usr/bin/env bash
if [[ "$*" == *"name,memory.total"* ]]; then echo "NVIDIA Graphics Device, 65536"; exit 0; fi
if [[ $# -eq 0 ]]; then echo "| NVIDIA-SMI 610.57.04    Driver Version: 610.57.04    CUDA Version: 13.3 |"; exit 0; fi
echo "610.57.04"
EOF
  cat > "$TMP/bin/lspci" <<'EOF'
#!/usr/bin/env bash
echo "01:00.0 3D controller: NVIDIA Corporation GA100 [CMP 170HX] (rev a1)"
EOF
  chmod +x "$TMP/bin/nvidia-smi" "$TMP/bin/lspci"
  run_script
  [ "$status" -eq 0 ]
  grep -q '"gpu_details": \[{"name": "CMP 170HX", "memory_mib": 65536}\]' "$CURL_LOG"
}

