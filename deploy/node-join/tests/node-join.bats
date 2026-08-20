#!/usr/bin/env bats
# node-join.sh 单测(WP23):PATH shim 伪造系统命令,不触碰真实系统。
# 运行:bats deploy/node-join/tests(CI 已接;本地需 apt install bats)

SCRIPT="$BATS_TEST_DIRNAME/../../../apps/api/app/modules/nodes/assets/node-join.sh"

setup() {
  TMP="$(mktemp -d)"
  export SUPERDL_JOIN_STATE_DIR="$TMP/state"
  export SUPERDL_JOIN_LOG_FILE="$TMP/join.log"
  export SUPERDL_JOIN_ETC_DIR="$TMP/etc"
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

_write_fixture() { # _write_fixture <pool>
  python3 - "$1" > "$BOOTSTRAP_FIXTURE" <<'PYEOF'
import json, sys
print(json.dumps({
    "pool": sys.argv[1],
    "hostname_expected": None,
    "rke2_version": "v1.36.2+rke2r1",
    "rke2_server_url": "https://10.0.0.10:9345",
    "rke2_join_token": "K10fixture::server:secret",
    "driver_version": "580",
    "nvme_devices": [],
    "registries_yaml": 'mirrors:\n  "*": {}\n',
}))
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
  # registries.yaml 落位(WP22 接缝)
  grep -q 'mirrors:' "$TMP/etc/rancher/rke2/registries.yaml"
  # markers 齐全
  for m in precheck nouveau sysctl iommu driver nvme_vg registries rke2_config rke2_install rke2_start; do
    [ -f "$SUPERDL_JOIN_STATE_DIR/done.d/$m" ]
  done
  # 进度上报含关键阶段与收尾
  grep -q '"phase":"rke2_start","state":"ok"' "$CURL_LOG"
  grep -q '"phase":"waiting_node","state":"ok"' "$CURL_LOG"
  # 非 kata 池不写 GRUB
  [ ! -f "$TMP/etc/default/grub.d/99-superdl.cfg" ]
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

@test "重启循环保护:2 次后仍未就绪上报 failed 并退出 1" {
  export NVIDIA_OK=0
  export DPKG_INSTALLED=1
  mkdir -p "$SUPERDL_JOIN_STATE_DIR"
  echo 2 > "$SUPERDL_JOIN_STATE_DIR/reboot_count"
  run_script
  [ "$status" -eq 1 ]
  grep -q '"phase":"reboot","state":"failed"' "$CURL_LOG"
}
