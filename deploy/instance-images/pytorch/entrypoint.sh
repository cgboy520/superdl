#!/usr/bin/env bash
# 平台镜像 entrypoint(契约见 ../README.md):注入公钥 → 起 sshd → 起 JupyterLab。
# Jupyter 必须绑 0.0.0.0,否则 Service/Ingress 打不通。
set -euo pipefail

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

# sshd(密钥登录;主机密钥首次生成)
ssh-keygen -A >/dev/null 2>&1 || true
/usr/sbin/sshd

# JupyterLab:0.0.0.0:8888,token 由平台注入。
# Origin 校验必须留着,禁止 allow_origin='*'(契约见 ../README.md)。
# 平台注入本实例自己的域名(JUPYTER_ALLOW_ORIGIN);未注入则用 Jupyter 默认同源校验。
origin_args=()
if [[ -n "${JUPYTER_ALLOW_ORIGIN:-}" ]]; then
  origin_args+=(--ServerApp.allow_origin="$JUPYTER_ALLOW_ORIGIN")
fi

exec jupyter lab \
  --ip=0.0.0.0 \
  --port=8888 \
  --no-browser \
  --allow-root \
  --ServerApp.root_dir=/root \
  --ServerApp.token="${JUPYTER_TOKEN:-}" \
  "${origin_args[@]}" \
  --ServerApp.trust_xheaders=True
