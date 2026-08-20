#!/usr/bin/env bash
# 平台镜像 entrypoint(契约见 ../README.md):注入公钥 → 起 sshd → 起 JupyterLab。
# 关键:Jupyter 必须绑 0.0.0.0,否则 Service/Ingress 打不通(只绑 localhost 是典型故障)。
set -euo pipefail

# 平台约定工作目录 /root(实例盘挂载点)。基础镜像默认 HOME=/home/jovyan,
# 共享池 Pod 又开了 userns(hostUsers:false),jovyan 目录不可写 → Jupyter 启动即 PermissionError。
# 统一把 HOME 与 Jupyter 的运行/配置目录指到 /root。
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

# JupyterLab:0.0.0.0:8888,token 由平台注入
exec jupyter lab \
  --ip=0.0.0.0 \
  --port=8888 \
  --no-browser \
  --allow-root \
  --ServerApp.root_dir=/root \
  --ServerApp.token="${JUPYTER_TOKEN:-}" \
  --ServerApp.allow_origin='*' \
  --ServerApp.trust_xheaders=True
