#!/usr/bin/env bats
# node-join.sh 单测:PATH shim 伪造系统命令,不触碰真实系统。
# 运行:bats deploy/node-join/tests(CI 已接;本地需 apt install bats)

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
  mkdir -p "$TMP/etc/modprobe.d" "$TMP/etc/sysctl.d" "$TMP/etc/systemd/system" "$TMP/bin"
  _write_fixture hami
  _write_shims
  PATH="$TMP/bin:$PATH"
}

teardown() { rm -rf "$TMP"; }

_write_fixture() { # _write_fixture <pool> [distro];rke2 fixture 不带 k8s_distro 键,兼测旧服务端缺省
  python3 - "$1" "${2:-rke2}" > "$BOOTSTRAP_FIXTURE" <<'PYEOF'
import json, sys
distro = sys.argv[2]
data = {
    "pool": sys.argv[1],
    "hostname_expected": None,
    "rke2_version": "v1.36.2+rke2r1" if distro == "rke2" else "v1.36.3+k3s1",
    "rke2_server_url": "https://10.0.0.10:9345" if distro == "rke2" else "https://10.0.0.10:6443",
    "rke2_join_token": "K10fixture::server:secret",
    "driver_version": "580",
    "nvme_devices": [],
    "registries_yaml": 'mirrors:\n  "*": {}\n',
}
if distro != "rke2":
    data["k8s_distro"] = distro
print(json.dumps(data))
PYEOF
}

_write_shims() {
  # curl:bootstrap → 落 fixture;progress/script → 记录后成功
  cat > "$TMP/bin/curl" <<'EOF'
#!/usr/bin/env bash
echo "$*" >> "$CURL_LOG"
out=""; prev=""; mode=""
for a in "$@"; do
  [[ "$prev" == "-o" ]] && out="$a"
  [[ "$a" == *node-enroll/bootstrap* ]] && mode=bootstrap
  [[ "$a" == *node-enroll/script* ]] && mode=script
  prev="$a"
done
if [[ "$mode" == "bootstrap" && -n "$out" ]]; then cp "$BOOTSTRAP_FIXTURE" "$out"; fi
if [[ "$mode" == "script" && -n "$out" ]]; then echo "#!/bin/bash" > "$out"; fi
exit 0
EOF
  # nvidia-smi:NVIDIA_OK 控制;dpkg:DPKG_INSTALLED 控制
  cat > "$TMP/bin/nvidia-smi" <<'EOF'
#!/usr/bin/env bash
[[ "$NVIDIA_OK" == "1" ]] || exit 1
if [[ "$*" == *"name,memory.total"* ]]; then echo "NVIDIA GeForce RTX 4090, 24564"; exit 0; fi
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
  # sh:安装管道末端(curl|sh -);记录安装器 env(如 INSTALL_K3S_MIRROR)后消费 stdin
  cat > "$TMP/bin/sh" <<'EOF'
#!/usr/bin/env bash
[[ -n "${INSTALL_K3S_MIRROR:-}" ]] && echo "sh INSTALL_K3S_MIRROR=$INSTALL_K3S_MIRROR" >> "$SHIM_CALLS"
cat >/dev/null 2>&1 || true
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
  # nvidia-ctk:固定"已安装"(与宿主机状态解耦;缺失时的安装分支由节点实跑验证)
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

run_script() { run bash "$SCRIPT" --token sdln_testtoken --api-base http://fake.local "$@"; }

@test "缺少 --token 退出 2" {
  run bash "$SCRIPT" --api-base http://fake.local
  [ "$status" -eq 2 ]
  [[ "$output" == *"缺少 --token"* ]]
}

@test "占位符未替换且未给 --api-base 退出 2" {
  run bash "$SCRIPT" --token sdln_x
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
  # markers 齐全
  for m in precheck nouveau sysctl iommu driver nvidia_toolkit nvme_vg registries rke2_config rke2_install rke2_start; do
    [ -f "$SUPERDL_JOIN_STATE_DIR/done.d/$m" ]
  done
  # NVIDIA Container Toolkit 步骤已过(此处 nvidia-ctk 已存在,走跳过分支)
  [[ "$output" == *"nvidia-container-toolkit 已安装"* ]]
  # 进度上报含关键阶段与收尾
  grep -q '"phase":"rke2_start","state":"ok"' "$CURL_LOG"
  grep -q '"phase":"waiting_node","state":"ok"' "$CURL_LOG"
  # 非 kata 池不写 GRUB
  [ ! -f "$TMP/etc/default/grub.d/99-superdl.cfg" ]
  # WP26:bootstrap 上报全卡清单(名称+显存 MiB)
  grep -q '"gpu_details": \[{"name": "NVIDIA GeForce RTX 4090", "memory_mib": 24564}\]' "$CURL_LOG"
}

@test "重跑幂等:第二次运行全部步骤跳过" {
  run_script
  [ "$status" -eq 0 ]
  run_script
  [ "$status" -eq 0 ]
  [[ "$output" == *"precheck: 已完成,跳过"* ]]
  [[ "$output" == *"rke2_start: 已完成,跳过"* ]]
}

@test "驱动未就绪触发重启断点:装驱动+写 oneshot+token 0600+systemctl reboot,rke2 尚未配置" {
  export NVIDIA_OK=0
  run_script
  [ "$status" -eq 0 ]
  grep -q "apt-get install" "$SHIM_CALLS"
  grep -q "systemctl reboot" "$SHIM_CALLS"
  [ -f "$TMP/etc/systemd/system/superdl-node-join-resume.service" ]
  grep -q -- "--token-file" "$TMP/etc/systemd/system/superdl-node-join-resume.service"
  [ "$(stat -c %a "$SUPERDL_JOIN_STATE_DIR/token")" = "600" ]
  [ "$(cat "$SUPERDL_JOIN_STATE_DIR/reboot_count")" = "1" ]
  [ ! -f "$TMP/etc/rancher/rke2/config.yaml" ]
  grep -q '"phase":"reboot","state":"rebooting"' "$CURL_LOG"
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
  # 默认走 k3s 官方中国镜像的安装脚本(认 INSTALL_K3S_MIRROR=cn),规避 github 下载卡死
  grep -q "rancher-mirror.rancher.cn/k3s/k3s-install.sh" "$CURL_LOG"
  grep -q "INSTALL_K3S_MIRROR=cn" "$SHIM_CALLS"
  grep -q "systemctl enable --now k3s-agent.service" "$SHIM_CALLS"
}

@test "loop 兜底须显式登记(nvme_devices=loop:80G):建 loop VG + 写开机重建 unit" {
  python3 - > "$BOOTSTRAP_FIXTURE" <<'PYEOF'
import json
print(json.dumps({"pool":"hami","hostname_expected":None,"rke2_version":"v1.36.2+rke2r1",
  "rke2_server_url":"https://10.0.0.10:9345","rke2_join_token":"K10::server:secret",
  "driver_version":"580","nvme_devices":["loop:80G"],"registries_yaml":""}))
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
