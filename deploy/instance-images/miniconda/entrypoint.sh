#!/usr/bin/env bash
# 平台镜像 entrypoint(契约见 ../README.md):注入公钥 → 起 sshd → 守护 JupyterLab。
# Jupyter 必须绑 0.0.0.0,否则 Service/Ingress 打不通。
set -euo pipefail

# Jupyter token 由平台注入且必须非空:空 token 等于无鉴权,宁可启动失败也不放出裸奔实例
: "${JUPYTER_TOKEN:?required(平台经 Pod env 注入,缺失说明编排层装配错误)}"

# 工作目录是 /root(实例盘挂载点),HOME 与 Jupyter 的运行/配置目录都指到 /root:
# 基础镜像默认的 /home/jovyan 在共享池 userns(hostUsers:false)下不可写。
export HOME=/root
export JUPYTER_RUNTIME_DIR="${JUPYTER_RUNTIME_DIR:-/root/.local/share/jupyter/runtime}"
export JUPYTER_DATA_DIR="${JUPYTER_DATA_DIR:-/root/.local/share/jupyter}"
export JUPYTER_CONFIG_DIR="${JUPYTER_CONFIG_DIR:-/root/.jupyter}"
mkdir -p "$JUPYTER_RUNTIME_DIR" "$JUPYTER_CONFIG_DIR" /root/.cache

# SSH 公钥(平台经 env 注入,多行)
if [[ -n "${AUTHORIZED_KEYS:-}" ]]; then
  mkdir -p /root/.ssh
  printf '%s\n' "$AUTHORIZED_KEYS" > /root/.ssh/authorized_keys
  chmod 700 /root/.ssh
  chmod 600 /root/.ssh/authorized_keys
fi

# SSH host key 持久化到实例盘(/root):Pod 重建后指纹不变,不触发 known_hosts
# 变更告警。/etc/ssh 下的是指向持久目录的符号链接。
hostkey_dir=/root/.ssh/host_keys
mkdir -p "$hostkey_dir"
chmod 700 "$hostkey_dir"
for kt in rsa ecdsa ed25519; do
  key="$hostkey_dir/ssh_host_${kt}_key"
  if [[ ! -f "$key" ]]; then
    ssh-keygen -q -t "$kt" -f "$key" -N "" >/dev/null
  fi
  ln -sf "$key" "/etc/ssh/ssh_host_${kt}_key"
  ln -sf "$key.pub" "/etc/ssh/ssh_host_${kt}_key.pub"
done
/usr/sbin/sshd

# JupyterLab:0.0.0.0:8888,token 由平台注入。
# Origin 校验必须留着,禁止 allow_origin='*'(契约见 ../README.md)。
# 平台注入本实例自己的域名(JUPYTER_ALLOW_ORIGIN);未注入则用 Jupyter 默认同源校验。
origin_args=()
if [[ -n "${JUPYTER_ALLOW_ORIGIN:-}" ]]; then
  origin_args+=(--ServerApp.allow_origin="$JUPYTER_ALLOW_ORIGIN")
fi

# 一次性票据 bootstrap 扩展(见 superdl_jupyter_auth.py):/access 签发的入场 URL
# 落在 /superdl-bootstrap,核销后种第一方 cookie。扩展加载失败时回落 stock token
# 鉴权(?token= 依旧可用),不因为扩展问题把实例打死。
ext_args=()
export PYTHONPATH="/opt/superdl${PYTHONPATH:+:$PYTHONPATH}"
if python -c "import superdl_jupyter_auth" 2>/dev/null; then
  ext_args+=(
    "--ServerApp.jpserver_extensions={\"superdl_jupyter_auth\": true}"
    "--ServerApp.identity_provider_class=superdl_jupyter_auth.SuperDLIdentityProvider"
  )
else
  echo "warn: superdl_jupyter_auth 不可导入,回退 stock token 鉴权" >&2
fi

# 守护循环而非 exec:jupyter 不做 PID 1,用户误杀或崩溃后自动拉起,不让整台实例转 failed。
# 连续秒退(如配置错误)超过 5 次则放弃,让 Pod 失败收敛,不无限假活。
fast_failures=0
while true; do
  start_ts=$SECONDS
  jupyter lab \
    --ip=0.0.0.0 \
    --port=8888 \
    --no-browser \
    --allow-root \
    --ServerApp.root_dir=/root \
    --ServerApp.token="$JUPYTER_TOKEN" \
    "${origin_args[@]}" \
    "${ext_args[@]}" \
    --ServerApp.trust_xheaders=True &
  pid=$!
  wait "$pid" || true
  if (( SECONDS - start_ts < 10 )); then
    fast_failures=$((fast_failures + 1))
  else
    fast_failures=0
  fi
  if (( fast_failures >= 5 )); then
    echo "jupyter 连续快速退出 ${fast_failures} 次,放弃守护让 Pod 失败收敛" >&2
    exit 1
  fi
  echo "jupyter 已退出,2s 后重启" >&2
  sleep 2
done
