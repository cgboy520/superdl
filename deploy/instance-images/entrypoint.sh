#!/usr/bin/env bash
set -euo pipefail

warn() { echo "warn: $*" >&2; }

: "${JUPYTER_TOKEN:?required (injected by the platform through the Pod env; missing means the orchestration layer assembled the Pod wrongly)}"

export HOME=/root
export SHELL=/bin/bash
export JUPYTER_RUNTIME_DIR="${JUPYTER_RUNTIME_DIR:-/run/jupyter}"
export JUPYTER_DATA_DIR="${JUPYTER_DATA_DIR:-/root/.local/share/jupyter}"
export JUPYTER_CONFIG_DIR="${JUPYTER_CONFIG_DIR:-/run/jupyter-config}"
mkdir -p "$JUPYTER_RUNTIME_DIR" "$JUPYTER_CONFIG_DIR" /root/.cache
chmod 700 "$JUPYTER_RUNTIME_DIR" "$JUPYTER_CONFIG_DIR" 2>/dev/null || true
rm -f /root/.local/share/jupyter/runtime/jpserver-* 2>/dev/null || true

export PYTHONUSERBASE=/root/.local
export PIP_USER=1
if [[ -n "${JULIA_DEPOT_PATH:-}" ]]; then
  julia_base="${JULIA_DEPOT_PATH%%:*}"
  export JULIA_DEPOT_PATH="/root/.julia:${JULIA_DEPOT_PATH}"
  if [[ -d "$julia_base/environments" && ! -d /root/.julia/environments ]]; then
    { mkdir -p /root/.julia && cp -r "$julia_base/environments" /root/.julia/; } 2>/dev/null \
      || warn "Julia environment could not be copied to the instance disk, Pkg.add will fail"
  fi
elif command -v julia >/dev/null 2>&1; then
  export JULIA_DEPOT_PATH="/root/.julia"
fi
export LITELLM_LOCAL_MODEL_COST_MAP=True

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
fix_group_names || warn "/etc/group could not be updated (unnamed GID warnings, SSH-side /dev/dri access affected)"

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
        echo "info: removed the CUDA compat directory from LD_LIBRARY_PATH (host driver newer than the image)" >&2
      else
        export LD_LIBRARY_PATH="$ld_orig"
        warn "cuInit still fails after removing CUDA compat, restored"
      fi
    fi
  elif (( cuda_rc == 2 )); then
    warn "CUDA availability undetermined (no python/libcuda or probe timeout), skipping compat handling"
  fi
fi

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
    cat <<'PIPFN'
pip() { if [ -n "${VIRTUAL_ENV:-}" ]; then PIP_USER=0 command pip "$@"; else command pip "$@"; fi; }
PIPFN
  } > /etc/profile.d/superdl-env.sh || return 1
  chmod 644 /etc/environment /etc/profile.d/superdl-env.sh || return 1
}
write_session_env || warn "session environment could not be written (SSH sessions may lack PATH)"

mkdir -p /root/.ssh
chmod 700 /root/.ssh
printf '%s\n' "${AUTHORIZED_KEYS:-}" > /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys

chmod g-w,o-w /root 2>/dev/null || warn "/root permissions could not be tightened, sshd may refuse public key auth"

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

lab_args=()
if [[ -f /opt/superdl/labsettings/overrides.json ]]; then
  lab_args+=(--LabApp.app_settings_dir=/opt/superdl/labsettings)
else
  warn "Lab default settings missing, UI language falls back to en"
fi

ai_args=(
  --PersonaManager.default_persona_id=jupyter-ai-personas::jupyter_ai_jupyternaut::JupyternautPersona
)
for p in openai anthropic github_copilot ollama ollama_chat; do
  ai_args+=("--JupyternautExtension.allowed_providers=$p")
done

origin_args=()
if [[ -n "${JUPYTER_ALLOW_ORIGIN:-}" ]]; then
  origin_args+=(--ServerApp.allow_origin="$JUPYTER_ALLOW_ORIGIN")
fi

ext_args=()
export PYTHONPATH="/opt/superdl${PYTHONPATH:+:$PYTHONPATH}"
if ext_import_err="$(python -c "import superdl_jupyter_auth" 2>&1)"; then
  ext_args+=(
    --ServerApp.jpserver_extensions superdl_jupyter_auth=True
    "--ServerApp.identity_provider_class=superdl_jupyter_auth.SuperDLIdentityProvider"
  )
else
  warn "superdl_jupyter_auth not importable, falling back to stock token auth (the entry URL will 404):"
  echo "$ext_import_err" | tail -3 >&2
fi

jupyter_pid=""
on_term() {
  trap - TERM INT
  [[ -n "$jupyter_pid" ]] && kill -TERM "$jupyter_pid" 2>/dev/null || true
  exit 0
}
trap on_term TERM INT

exec > >(sed -u 's/token=[^&[:space:]]*/token=<redacted>/g') 2>&1

fast_failures=0
while true; do
  start_ts=$SECONDS
  jupyter lab \
    --ip=0.0.0.0 \
    --port=8888 \
    --no-browser \
    --allow-root \
    --IdentityProvider.token="$JUPYTER_TOKEN" \
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
    echo "jupyter exited quickly ${fast_failures} times in a row, giving up supervision so the Pod fails" >&2
    exit 1
  fi
  echo "jupyter exited, restarting in 2s" >&2
  sleep 2
done
