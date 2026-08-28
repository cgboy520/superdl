#!/usr/bin/env bash
# 平台镜像 entrypoint,契约见 README.md。
# 只有「实例本身不可用」才允许退出,其余步骤失败只 warn。
set -euo pipefail

warn() { echo "warn: $*" >&2; }

: "${JUPYTER_TOKEN:?required(平台经 Pod env 注入,缺失说明编排层装配错误)}"

export HOME=/root
export JUPYTER_RUNTIME_DIR="${JUPYTER_RUNTIME_DIR:-/run/jupyter}"   # 该目录下的文件含 token,不能落实例盘
export JUPYTER_DATA_DIR="${JUPYTER_DATA_DIR:-/root/.local/share/jupyter}"
export JUPYTER_CONFIG_DIR="${JUPYTER_CONFIG_DIR:-/root/.jupyter}"
mkdir -p "$JUPYTER_RUNTIME_DIR" "$JUPYTER_CONFIG_DIR" /root/.cache
chmod 700 "$JUPYTER_RUNTIME_DIR" 2>/dev/null || true
rm -f /root/.local/share/jupyter/runtime/jpserver-* 2>/dev/null || true   # 清残留(含 token)

# 用户装的包落到实例盘(/opt/conda、/opt/julia 在容器可写层,Pod 重建即丢)
export PYTHONUSERBASE=/root/.local
export PIP_USER=1
if [[ -n "${JULIA_DEPOT_PATH:-}" ]]; then
  julia_base="${JULIA_DEPOT_PATH%%:*}"
  export JULIA_DEPOT_PATH="/root/.julia:${JULIA_DEPOT_PATH}"
  # 活动环境取 DEPOT_PATH 里第一个已存在的 environments/vX.Y:不把镜像那份复制到实例盘,
  # Pkg.add 就会去写只读的镜像 depot
  if [[ -d "$julia_base/environments" && ! -d /root/.julia/environments ]]; then
    # 用 cp -r 而非 -a:没有 CAP_CHOWN 时保留属主会失败并返回非零(文件其实已复制)
    { mkdir -p /root/.julia && cp -r "$julia_base/environments" /root/.julia/; } 2>/dev/null \
      || warn "Julia 环境未能复制到实例盘,Pkg.add 会失败"
  fi
elif command -v julia >/dev/null 2>&1; then
  export JULIA_DEPOT_PATH="/root/.julia"
fi
export LITELLM_LOCAL_MODEL_COST_MAP=True   # 关掉 jupyter-ai 启动时的远端价格表拉取

# 给注入的宿主补充组补名字,并把 root 列为成员(sshd 按 /etc/group 重建补充组)
fix_group_names() {
  local gids tmp
  gids="$(id -G 2>/dev/null || true)"
  [[ -n "$gids" ]] || return 0
  tmp="$(mktemp 2>/dev/null)" || return 1
  awk -F: -v gids="$gids" '
    BEGIN { n = split(gids, a, " "); for (i = 1; i <= n; i++) if (a[i] != 0) want[a[i]] = 1 }
    {
      if ($3 in want) {
        seen[$3] = 1
        if ($4 !~ /(^|,)root(,|$)/) $4 = ($4 == "" ? "root" : $4 ",root")
        print $1 ":" $2 ":" $3 ":" $4
        next
      }
      print $0
    }
    END { for (g in want) if (!(g in seen)) printf "hostgrp%s:x:%s:root\n", g, g }
  ' /etc/group > "$tmp" 2>/dev/null && [[ -s "$tmp" ]] || { rm -f "$tmp"; return 1; }
  cat "$tmp" > /etc/group 2>/dev/null || { rm -f "$tmp"; return 1; }
  rm -f "$tmp"
}
fix_group_names || warn "/etc/group 未能更新(GID 无名告警、SSH 侧 /dev/dri 访问受影响)"

# 探测 cuInit;失败才从 LD_LIBRARY_PATH 摘 CUDA compat,摘完仍失败就还原。
# 返回 0=可用 1=cuInit 失败 2=判定不了
cuda_probe() {
  command -v python >/dev/null 2>&1 || return 2
  local rc=0
  timeout 20 python - <<'PYCHK' >/dev/null 2>&1 || rc=$?
import ctypes, sys
try:
    lib = ctypes.CDLL("libcuda.so.1")
except OSError:
    sys.exit(3)
sys.exit(0 if lib.cuInit(0) == 0 else 1)
PYCHK
  case "$rc" in 0) return 0 ;; 1) return 1 ;; *) return 2 ;; esac
}
if [[ -n "${LD_LIBRARY_PATH:-}" ]]; then
  cuda_rc=0
  cuda_probe || cuda_rc=$?
  if (( cuda_rc == 1 )); then
    ld_orig="$LD_LIBRARY_PATH" ld_kept="" removed=0
    IFS=':' read -r -a ld_parts <<< "$LD_LIBRARY_PATH"
    for ld_p in "${ld_parts[@]}"; do
      ld_norm="$ld_p"
      while [[ "$ld_norm" == */ ]]; do ld_norm="${ld_norm%/}"; done
      case "$ld_norm" in
        */cuda*/compat | */cuda*/compat/*) removed=1; continue ;;
      esac
      ld_kept="${ld_kept:+$ld_kept:}$ld_p"
    done
    if (( removed )); then
      export LD_LIBRARY_PATH="$ld_kept"
      if cuda_probe; then
        echo "info: 已从 LD_LIBRARY_PATH 摘除 CUDA compat 目录(宿主驱动比镜像新)" >&2
      else
        export LD_LIBRARY_PATH="$ld_orig"
        warn "摘除 CUDA compat 后 cuInit 仍失败,已还原"
      fi
    fi
  elif (( cuda_rc == 2 )); then
    warn "CUDA 可用性无法判定(无 python/libcuda 或探测超时),跳过 compat 处理"
  fi
fi

# 把 PID 1 的环境落盘给 SSH 会话用:/etc/environment 走 PAM(非交互),profile.d 走登录 shell。
# 黑名单剔除敏感值与 shell 私有变量。
env_lines() {
  local name value
  while IFS='=' read -r -d '' name value; do
    case "$name" in
      JUPYTER_TOKEN | AUTHORIZED_KEYS) continue ;;
      *TOKEN* | *SECRET* | *PASSWORD* | *PASSWD* | *KEY* | *CREDENTIAL*) continue ;;
      PWD | OLDPWD | SHLVL | IFS | PS1 | PS2 | HOME | USER | LOGNAME | SHELL | TERM | HOSTNAME) continue ;;
      _ | BASH* | FUNCNAME | RANDOM | SECONDS | LINENO | EUID | UID | PPID | OPTIND) continue ;;
    esac
    [[ "$name" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    [[ "$value" == *$'\n'* ]] && continue
    printf '%s\t%s\n' "$name" "$value"
  done < <(env -0)
}
write_session_env() {
  local name value
  while IFS=$'\t' read -r name value; do
    printf '%s=%s\n' "$name" "$value"
  done < <(env_lines) > /etc/environment || return 1
  {
    while IFS=$'\t' read -r name value; do
      printf 'export %s=%s\n' "$name" "$(printf '%q' "$value")"
    done < <(env_lines)
    [[ -f /opt/conda/etc/profile.d/conda.sh ]] && echo '. /opt/conda/etc/profile.d/conda.sh'
    # PIP_USER=1 在已激活的 venv 里会让 pip 直接报错(user site 在 venv 中不可见),
    # 进 venv 就关掉它:venv 自己就是隔离环境,不需要再往实例盘装
    cat <<'PIPFN'
pip() { if [ -n "${VIRTUAL_ENV:-}" ]; then PIP_USER=0 command pip "$@"; else command pip "$@"; fi; }
PIPFN
  } > /etc/profile.d/superdl-env.sh || return 1
  chmod 644 /etc/environment /etc/profile.d/superdl-env.sh || return 1
}
write_session_env || warn "会话环境未能落盘(SSH 进来可能缺 PATH)"

if [[ -n "${AUTHORIZED_KEYS:-}" ]]; then
  mkdir -p /root/.ssh
  printf '%s\n' "$AUTHORIZED_KEYS" > /root/.ssh/authorized_keys
  chmod 700 /root/.ssh
  chmod 600 /root/.ssh/authorized_keys
fi

# TopoLVM 把 /root 挂成 2777,sshd StrictModes 会因此拒绝公钥认证
chmod g-w,o-w /root 2>/dev/null || warn "/root 权限未能收紧,sshd 可能拒绝公钥认证"

# host key 持久化到实例盘,Pod 重建后指纹不变
hostkey_dir=/root/.ssh/host_keys
mkdir -p "$hostkey_dir"
chmod 700 "$hostkey_dir"
for kt in rsa ecdsa ed25519; do
  key="$hostkey_dir/ssh_host_${kt}_key"
  [[ -f "$key" ]] || ssh-keygen -q -t "$kt" -f "$key" -N "" >/dev/null
  ln -sf "$key" "/etc/ssh/ssh_host_${kt}_key"
  ln -sf "$key.pub" "/etc/ssh/ssh_host_${kt}_key.pub"
done
/usr/sbin/sshd

# Lab 默认设置(界面中文)构建期已放在 /opt/superdl/labsettings
lab_args=()
if [[ -f /opt/superdl/labsettings/overrides.json ]]; then
  lab_args+=(--LabApp.app_settings_dir=/opt/superdl/labsettings)
else
  warn "Lab 默认设置缺失,界面语言回落 en"
fi

# jupyter-ai:模型提供方白名单 + 默认 persona。persona id 必须钉死:上游默认值写的是
# ::jupyter_ai::,实际类在 ::jupyter_ai_jupyternaut::,不钉就没有应答者
ai_args=(
  --PersonaManager.default_persona_id=jupyter-ai-personas::jupyter_ai_jupyternaut::JupyternautPersona
)
for p in openai anthropic github_copilot ollama ollama_chat; do
  ai_args+=("--JupyternautExtension.allowed_providers=$p")
done

# 禁止 allow_origin='*'(契约见 README.md)
origin_args=()
if [[ -n "${JUPYTER_ALLOW_ORIGIN:-}" ]]; then
  origin_args+=(--ServerApp.allow_origin="$JUPYTER_ALLOW_ORIGIN")
fi

# 一次性票据扩展(见 superdl_jupyter_auth.py);import 失败要打日志,否则入场 URL 静默 404
ext_args=()
export PYTHONPATH="/opt/superdl${PYTHONPATH:+:$PYTHONPATH}"
if ext_import_err="$(python -c "import superdl_jupyter_auth" 2>&1)"; then
  # jpserver_extensions 是 Dict trait,命令行只认 key=value
  ext_args+=(
    --ServerApp.jpserver_extensions superdl_jupyter_auth=True
    "--ServerApp.identity_provider_class=superdl_jupyter_auth.SuperDLIdentityProvider"
  )
else
  warn "superdl_jupyter_auth 不可导入,回退 stock token 鉴权(入场 URL 将 404):"
  echo "$ext_import_err" | tail -3 >&2
fi

# 转发 SIGTERM,让 jupyter 干净退出
jupyter_pid=""
on_term() {
  trap - TERM INT
  [[ -n "$jupyter_pid" ]] && kill -TERM "$jupyter_pid" 2>/dev/null || true
  exit 0
}
trap on_term TERM INT

# 守护循环:jupyter 崩了自动拉起;连续秒退 5 次放弃,让 Pod 失败收敛
fast_failures=0
while true; do
  start_ts=$SECONDS
  jupyter lab \
    --ip=0.0.0.0 \
    --port=8888 \
    --no-browser \
    --allow-root \
    --ServerApp.root_dir=/root \
    --ServerApp.default_url=/lab \
    --ResourceUseDisplay.track_cpu_percent=True \
    "${lab_args[@]}" \
    "${ai_args[@]}" \
    "${origin_args[@]}" \
    "${ext_args[@]}" \
    --ServerApp.trust_xheaders=True &
  jupyter_pid=$!
  wait "$jupyter_pid" || true
  jupyter_pid=""
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
