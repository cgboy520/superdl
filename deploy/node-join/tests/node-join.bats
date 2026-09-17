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
  export SUPERDL_JOIN_OS_RELEASE="$TMP/os-release"
  export SUPERDL_JOIN_CPUINFO="$TMP/cpuinfo"
  _write_os_release ubuntu debian "Ubuntu 24.04 LTS"
  _write_cpuinfo GenuineIntel
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

_write_os_release() {
  printf 'ID=%s\nID_LIKE="%s"\nPRETTY_NAME="%s"\n' "$1" "$2" "$3" > "$TMP/os-release"
}

_write_cpuinfo() {
  printf 'processor\t: 0\nvendor_id\t: %s\nmodel name\t: fixture\n' "$1" > "$TMP/cpuinfo"
}

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

@test "missing --token-file exits 2" {
  run bash "$SCRIPT" --api-base http://fake.local
  [ "$status" -eq 2 ]
  [[ "$output" == *"missing --token-file"* ]]
}

@test "missing token file exits 2" {
  run bash "$SCRIPT" --token-file "$TMP/no-such" --api-base http://fake.local
  [ "$status" -eq 2 ]
  [[ "$output" == *"token file not found"* ]]
}

@test "unreplaced placeholder without --api-base exits 2" {
  run bash "$SCRIPT" --token-file "$TMP/token"
  [ "$status" -eq 2 ]
  [[ "$output" == *"placeholder not replaced"* ]]
}

@test "Harbor self-signed CA: writes harbor-ca.crt (0644) and replaces __RANCHER_DIR__ in registries.yaml with the local directory" {
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

@test "join token with a newline (YAML injection) is refused for agent config.yaml" {
  export FIXTURE_JOIN_TOKEN=$'superdl-agent-fixture-token-0123456789\nkubelet-arg:\n  - "anonymous-auth=true"'
  _write_fixture hami
  run_script
  [ "$status" -ne 0 ]
  [[ "$output" == *"cluster_join_token contains illegal characters"* ]]
  [ ! -f "$TMP/etc/rancher/rke2/config.yaml" ]
}

@test "server node-token (K10<64hex>::server:...) is refused: no agent config.yaml" {
  export FIXTURE_JOIN_TOKEN="K10$(printf 'a%.0s' $(seq 1 64))::server:secret"
  _write_fixture hami
  run_script
  [ "$status" -ne 0 ]
  [[ "$output" == *"server node-token"* ]]
  [ ! -f "$TMP/etc/rancher/rke2/config.yaml" ]
}

@test "agent token in K10<64hex>::node:<pw> form is accepted" {
  export FIXTURE_JOIN_TOKEN="K10$(printf 'a%.0s' $(seq 1 64))::node:secret"
  _write_fixture hami
  run_script
  [ "$status" -eq 0 ]
  grep -q "::node:secret" "$TMP/etc/rancher/rke2/config.yaml"
}

@test "no CA: no harbor-ca.crt, registries.yaml has only the Spegel section" {
  run_script
  [ "$status" -eq 0 ]
  [ ! -f "$TMP/etc/rancher/rke2/harbor-ca.crt" ]
  ! grep -q 'configs:' "$TMP/etc/rancher/rke2/registries.yaml"
}

@test "full run (driver ready, no reboot): rke2 config/registries written, all markers present, progress reported" {
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
  [[ "$output" == *"nvidia-container-toolkit 1.17.8 >= 1.17.8, skipping"* ]]
  grep -q 'podPidsLimit: 4096' "$TMP/rancher/rke2/agent/etc/kubelet.conf.d/50-superdl.conf"
  ! grep -q 'podPidsLimit' "$TMP/etc/rancher/rke2/config.yaml"
  grep -q '"phase":"agent_start","state":"ok"' "$CURL_LOG"
  grep -q '"phase":"waiting_node","state":"ok"' "$CURL_LOG"
  grep -q '"gpu_details": \[{"name": "NVIDIA GeForce RTX 4090", "memory_mib": 24564}\]' "$CURL_LOG"
  grep -q '"phase":"waiting_node","state":"ok".*"driver_version":"580.65.06","cuda_version":"12.8"' "$CURL_LOG"
}

@test "tokens never enter process argv; bootstrap.json and the token file are removed on completion" {
  run_script
  [ "$status" -eq 0 ]
  grep -q -- "--config" "$CURL_LOG"
  ! grep -q 'sdln_testtoken' "$CURL_LOG"
  ! grep -q 'sdlp_fixturetoken' "$CURL_LOG"
  [ ! -e "$SUPERDL_JOIN_STATE_DIR/bootstrap.json" ]
  [ ! -e "$SUPERDL_JOIN_STATE_DIR/token" ]
  [ ! -e "$SUPERDL_JOIN_STATE_DIR/curl.conf" ]
}

@test "rerun after completion exits immediately without repeating bootstrap or install" {
  run_script
  [ "$status" -eq 0 ]
  run_script
  [ "$status" -eq 0 ]
  [[ "$output" == *"already joined"* ]]
  [ "$(grep -c 'node-enroll/bootstrap' "$CURL_LOG")" = "1" ]
  ! grep -q "vgcreate superdl-nvme" "$SHIM_CALLS"
}

@test "--force clears markers and reinstalls from scratch (needs a newly issued token)" {
  run_script
  [ "$status" -eq 0 ]
  run_script --force
  [ "$status" -eq 0 ]
  [[ "$output" == *"--force: clearing local markers"* ]]
  [ "$(grep -c 'node-enroll/bootstrap' "$CURL_LOG")" = "2" ]
}

@test "resume: with the bootstrap marker present it is skipped and progress uses the on-disk token" {
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
  [[ "$output" == *"bootstrap: already done, skipping"* ]]
  [ "$(grep -c 'node-enroll/bootstrap' "$CURL_LOG")" = "1" ]
  [ ! -e "$SUPERDL_JOIN_STATE_DIR/token" ]
}

@test "driver not ready triggers the reboot checkpoint: driver installed, oneshot written, token 0600, systemctl reboot, rke2 not configured yet" {
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

@test "piped execution reboot checkpoint: re-downloads itself from the API and verifies the bootstrap checksum" {
  export NVIDIA_OK=0
  run bash -s -- --token-file "$TMP/token" --api-base http://fake.local < "$SCRIPT"
  [ "$status" -eq 0 ]
  grep -q "systemctl reboot" "$SHIM_CALLS"
}

@test "re-downloaded script checksum mismatch aborts the reboot and reports failed" {
  export NVIDIA_OK=0
  _write_fixture hami rke2 "" "0000000000000000000000000000000000000000000000000000000000000000"
  run bash -s -- --token-file "$TMP/token" --api-base http://fake.local < "$SCRIPT"
  [ "$status" -eq 1 ]
  [[ "$output" == *"checksum mismatch"* ]]
  grep -q '"phase":"reboot","state":"failed"' "$CURL_LOG"
}

@test "bootstrap without script_sha256: piped resume refuses the re-downloaded script (fail-closed)" {
  export NVIDIA_OK=0
  _write_fixture hami rke2 "" ""
  run bash -s -- --token-file "$TMP/token" --api-base http://fake.local < "$SCRIPT"
  [ "$status" -eq 1 ]
  [[ "$output" == *"script_sha256"* ]]
  grep -q '"phase":"reboot","state":"failed"' "$CURL_LOG"
  ! grep -q "systemctl reboot" "$SHIM_CALLS"
}

@test "installer pin checksum mismatch refuses to run and reports failed" {
  cat > "$TMP/bin/rke2" <<'RKESHIM'
#!/usr/bin/env bash
echo "rke2 version v0.0.0+rke2r0"
RKESHIM
  chmod +x "$TMP/bin/rke2"
  export SUPERDL_JOIN_PIN_RKE2_CN="0000000000000000000000000000000000000000000000000000000000000000"
  run_script
  [ "$status" -eq 1 ]
  [[ "$output" == *"checksum mismatch"* ]]
  grep -q '"phase":"agent_install","state":"failed"' "$CURL_LOG"
  ! grep -q "sh INSTALL_RKE2_MIRROR" "$SHIM_CALLS"
}

@test "IOMMU is a baseline: the hami pool also writes GRUB (x86), no per-pool branching" {
  _write_fixture hami
  run_script
  [ "$status" -eq 0 ]
  grep -q "intel_iommu=on iommu=pt" "$TMP/etc/default/grub.d/99-superdl.cfg"
  grep -q "update-grub" "$SHIM_CALLS"
}

@test "IOMMU argument follows the CPU vendor: AMD writes amd_iommu=on" {
  _write_cpuinfo AuthenticAMD
  run_script
  [ "$status" -eq 0 ]
  grep -q "amd_iommu=on iommu=pt" "$TMP/etc/default/grub.d/99-superdl.cfg"
  ! grep -q "intel_iommu" "$TMP/etc/default/grub.d/99-superdl.cfg"
}

@test "unknown CPU vendor fails the iommu step closed unless SUPERDL_JOIN_IOMMU_ARGS is set" {
  _write_cpuinfo "SomethingElse"
  run_script
  [ "$status" -eq 1 ]
  [[ "$output" == *"unknown CPU vendor"* ]]
  grep -q '"phase":"iommu","state":"failed"' "$CURL_LOG"
  [ ! -f "$TMP/etc/default/grub.d/99-superdl.cfg" ]
  export SUPERDL_JOIN_IOMMU_ARGS="iommu=pt custom_iommu=on"
  run_script --force
  [ "$status" -eq 0 ]
  grep -q "custom_iommu=on" "$TMP/etc/default/grub.d/99-superdl.cfg"
}

@test "non-Debian distribution fails precheck closed" {
  _write_os_release fedora "rhel fedora" "Fedora Linux 41"
  run_script
  [ "$status" -eq 1 ]
  [[ "$output" == *"unsupported distribution: Fedora Linux 41"* ]]
  grep -q '"phase":"precheck","state":"failed"' "$CURL_LOG"
  ! grep -q "apt-get" "$SHIM_CALLS"
}

@test "ID_LIKE=debian derivatives pass the distro gate and bootstrap reports PRETTY_NAME" {
  _write_os_release pop "ubuntu debian" "Pop!_OS 22.04 LTS"
  run_script
  [ "$status" -eq 0 ]
  [[ "$output" == *"distribution: Pop!_OS 22.04 LTS (Debian family)"* ]]
  grep -q '"os_release": "Pop!_OS 22.04 LTS"' "$CURL_LOG"
}

@test "inactive IOMMU requires a reboot" {
  _write_fixture kata
  export NVIDIA_OK=0
  rmdir "$SUPERDL_JOIN_IOMMU_DIR/0"
  run_script
  [ "$status" -eq 0 ]
  [[ "$output" == *"IOMMU not active; reboot required"* ]]
}

@test "cpu pool (no GPU): skips NVIDIA detection/driver/toolkit/IOMMU, writes no node-label, reports no driver version" {
  _write_fixture cpu
  export NVIDIA_OK=0 LSPCI_NVIDIA=0
  run_script
  [ "$status" -eq 0 ]
  ! grep -q "node-label" "$TMP/etc/rancher/rke2/config.yaml"
  ! grep -q "nvidia.com/" "$TMP/etc/rancher/rke2/config.yaml"
  ! grep -q "apt-get install" "$SHIM_CALLS"
  [ ! -f "$TMP/etc/modprobe.d/blacklist-nouveau.conf" ]
  [ ! -f "$TMP/etc/default/grub.d/99-superdl.cfg" ]
  [[ "$output" == *"skipping NVIDIA GPU detection"* ]]
  grep -q '"phase":"waiting_node","state":"ok"' "$CURL_LOG"
  ! grep -q '"driver_version"' "$CURL_LOG"
  grep -q '"gpu_details": \[\]' "$CURL_LOG"
}

@test "kata pool: without a host driver, driver and toolkit installs are skipped" {
  _write_fixture kata
  export NVIDIA_OK=0
  run_script
  [ "$status" -eq 0 ]
  [[ "$output" == *"skipping NVIDIA driver install"* ]]
  [[ "$output" == *"skipping nvidia-container-toolkit"* ]]
  ! grep -q "apt-get install -y -qq nvidia-driver" "$SHIM_CALLS"
  ! grep -q "apt-get install -y -qq nvidia-container-toolkit" "$SHIM_CALLS"
  grep -q '"phase":"waiting_node","state":"ok"' "$CURL_LOG"
}

@test "kata pool: driver installed but not loaded (nvidia-smi fails) is also a failure" {
  _write_fixture kata
  export NVIDIA_OK=0 DPKG_INSTALLED=1
  run_script
  [ "$status" -eq 1 ]
  [[ "$output" == *"kata pool requires a host without the NVIDIA driver"* ]]
  grep -q '"phase":"driver","state":"failed"' "$CURL_LOG"
}

@test "kata pool: a pre-installed host driver fails the driver phase instead of joining unhealthy" {
  _write_fixture kata
  export NVIDIA_OK=1
  run_script
  [ "$status" -eq 1 ]
  [[ "$output" == *"kata pool requires a host without the NVIDIA driver"* ]]
  grep -q '"phase":"driver","state":"failed"' "$CURL_LOG"
}

@test "toolkit missing: install branch runs and continues once the minimum is met" {
  export DPKG_NVCTK_VERSION="" DPKG_NVCTK_VERSION_AFTER="1.17.8"
  run_script
  [ "$status" -eq 0 ]
  grep -q "apt-get install -y -qq nvidia-container-toolkit" "$SHIM_CALLS"
  grep -q '"phase":"nvidia_toolkit","state":"ok"' "$CURL_LOG"
}

@test "toolkit still below the minimum after install reports failed instead of joining unhealthy" {
  export DPKG_NVCTK_VERSION="" DPKG_NVCTK_VERSION_AFTER="1.16.0"
  run_script
  [ "$status" -eq 1 ]
  [[ "$output" == *"below the security minimum"* ]]
  grep -q '"phase":"nvidia_toolkit","state":"failed"' "$CURL_LOG"
}

@test "toolkit minimum env override: SUPERDL_JOIN_NVCTK_MIN_VERSION takes effect" {
  export SUPERDL_JOIN_NVCTK_MIN_VERSION="99.0.0"
  run_script
  [ "$status" -eq 1 ]
  [[ "$output" == *"below the minimum 99.0.0"* || "$output" == *"below the security minimum 99.0.0"* ]]
}

@test "k3s mode: config/registries land in /etc/rancher/k3s, agent installed from the cn mirror and k3s-agent started" {
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

@test "rke2 install uses the cn mirror when install_mirror=cn" {
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

@test "install_mirror=official downloads from get.rke2.io" {
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

@test "loop fallback must be registered explicitly (nvme_devices=loop:80G): creates the loop VG and the boot-time unit" {
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
  [[ "$output" == *"loop file as the instance disk"* ]]
}

@test "no NVMe registered: explicit warning, nothing guessed (no VG created)" {
  run_script
  [ "$status" -eq 0 ]
  [[ "$output" == *"nothing guessed"* ]]
  ! grep -q "vgcreate superdl-nvme" "$SHIM_CALLS"
  [ ! -f "$TMP/etc/systemd/system/superdl-nvme-loop.service" ]
}

@test "reboot loop guard: still not ready after 2 reboots reports failed and exits 1" {
  export NVIDIA_OK=0
  export DPKG_INSTALLED=1
  mkdir -p "$SUPERDL_JOIN_STATE_DIR"
  echo 2 > "$SUPERDL_JOIN_STATE_DIR/reboot_count"
  run_script
  [ "$status" -eq 1 ]
  grep -q '"phase":"reboot","state":"failed"' "$CURL_LOG"
}

@test "--uninstall: stops the agent, removes everything this script wrote and the state dir, leaves the VG and driver alone" {
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

@test "server host (k3s running): no agent config, no agent install/start, no pool label (platform writes it)" {
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
  [[ "$output" == *"done: k3s started"* ]]
  [ -f "$SUPERDL_JOIN_STATE_DIR/done.d/completed" ]
}

@test "server host --uninstall: no distribution uninstall script, server config/registries kept" {
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
  [[ "$output" == *"distribution and server config left untouched"* ]]
}

@test "nvidia-smi reports only a generic name (NVIDIA Graphics Device): bootstrap falls back to the lspci bracket name, memory from nvidia-smi" {
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

