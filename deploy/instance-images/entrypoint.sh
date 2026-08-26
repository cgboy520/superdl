#!/usr/bin/env bash
# 平台镜像 entrypoint(契约见 README.md):注入公钥 → 起 sshd → 守护 JupyterLab。
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

# CUDA 前向兼容库(/usr/local/cuda*/compat)排在 LD_LIBRARY_PATH 前面时,进程加载的是镜像自带的
# 旧 libcuda,而不是容器运行时注入的宿主驱动库;宿主驱动更新时 cuInit 直接失败,框架看到 0 张卡
# (PaddlePaddle 官方镜像即如此,实测 device_count=0、run_check 回落 CPU)。compat 只有在宿主驱动
# 比镜像 CUDA 老时才有用,所以先探测:能 cuInit 就原样不动,失败才摘 compat 再探,仍失败就还原
# (说明不是这个原因,别把可用配置改坏)。
cuda_init_ok() {
  python - <<'PYCHK' >/dev/null 2>&1
import ctypes, sys
try:
    sys.exit(0 if ctypes.CDLL("libcuda.so.1").cuInit(0) == 0 else 1)
except Exception:
    sys.exit(1)
PYCHK
}
if [[ -n "${LD_LIBRARY_PATH:-}" ]] && ! cuda_init_ok; then
  ld_orig="$LD_LIBRARY_PATH"
  ld_kept=""
  IFS=':' read -r -a ld_parts <<< "$LD_LIBRARY_PATH"
  for ld_p in "${ld_parts[@]}"; do
    if [[ "${ld_p%/}" == */compat ]]; then continue; fi
    ld_kept="${ld_kept:+$ld_kept:}$ld_p"
  done
  if [[ "$ld_kept" != "$ld_orig" ]]; then
    export LD_LIBRARY_PATH="$ld_kept"
    if cuda_init_ok; then
      echo "info: 已从 LD_LIBRARY_PATH 摘除 CUDA compat 目录(宿主驱动比镜像新,compat 会让 cuInit 失败)" >&2
    else
      export LD_LIBRARY_PATH="$ld_orig"
    fi
  fi
fi

# sshd 不把自己的环境透传给用户会话:PATH / LD_LIBRARY_PATH 必须显式落盘,否则 ssh 进实例后
# python、conda、jupyter、nvcc 全不在 PATH(实测)。/etc/environment 经 PAM 生效(覆盖
# `ssh host <cmd>` 这类非交互会话),/etc/profile.d 覆盖登录 shell;只写下面这几个白名单变量,
# JUPYTER_TOKEN 等敏感值绝不落盘(镜像层与实例盘都不留)。
{
  echo "PATH=$PATH"
  if [[ -n "${LD_LIBRARY_PATH:-}" ]]; then echo "LD_LIBRARY_PATH=$LD_LIBRARY_PATH"; fi
  if [[ -n "${CUDA_HOME:-}" ]]; then echo "CUDA_HOME=$CUDA_HOME"; fi
} > /etc/environment
{
  echo "export PATH=\"$PATH\""
  if [[ -n "${LD_LIBRARY_PATH:-}" ]]; then echo "export LD_LIBRARY_PATH=\"$LD_LIBRARY_PATH\""; fi
  if [[ -n "${CUDA_HOME:-}" ]]; then echo "export CUDA_HOME=\"$CUDA_HOME\""; fi
  # conda activate 需要 conda.sh;有 conda 的镜像顺带在登录 shell 里就绪
  if [[ -f /opt/conda/etc/profile.d/conda.sh ]]; then echo '. /opt/conda/etc/profile.d/conda.sh'; fi
} > /etc/profile.d/superdl-env.sh
chmod 644 /etc/environment /etc/profile.d/superdl-env.sh

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

# JupyterLab 默认设置(界面中文 + 关掉外网新闻拉取):落到 app settings 目录,
# 用户在「设置 · 语言」里改的是自己的 user settings,优先级更高,不会被这里覆盖。
# app dir 问 jupyterlab 自己要(pip 装到 /usr/local 时它不等于 sys.prefix/share/jupyter/lab)
lab_settings_dir="$(python -c 'from jupyterlab.commands import get_app_dir; import os; print(os.path.join(get_app_dir(), "settings"))' 2>/dev/null || true)"
if [[ -n "$lab_settings_dir" && -f /opt/superdl/lab-overrides.json ]]; then
  mkdir -p "$lab_settings_dir"
  cp -f /opt/superdl/lab-overrides.json "$lab_settings_dir/overrides.json" || true
fi

# JupyterLab:0.0.0.0:8888,token 由平台注入。
# Origin 校验必须留着,禁止 allow_origin='*'(契约见 README.md)。
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
# import 失败的原因必须进日志(基类/依赖随 jupyter_server 版本变动时,只剩一句 warn 无从排查)
if ext_import_err="$(python -c "import superdl_jupyter_auth" 2>&1)"; then
  # jpserver_extensions 是 Dict trait:命令行只认 key=value(JSON 字面量会被当成列表项,
  # Jupyter 启动即报 "expected a dict, not the list" 退出,守护循环 5 次后放弃——实机首开暴露)
  ext_args+=(
    --ServerApp.jpserver_extensions superdl_jupyter_auth=True
    "--ServerApp.identity_provider_class=superdl_jupyter_auth.SuperDLIdentityProvider"
  )
else
  echo "warn: superdl_jupyter_auth 不可导入,回退 stock token 鉴权(入场 URL 将 404):" >&2
  echo "$ext_import_err" | tail -3 >&2
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
    --ServerApp.default_url=/lab \
    --ResourceUseDisplay.track_cpu_percent=True \
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
