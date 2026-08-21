#!/usr/bin/env bash
# 只读前置检查:helmfile apply 之前跑一遍,缺什么一次性列全。
# 用法:./preflight.sh <full|light>   (在 deploy/cluster/ 下执行)
set -euo pipefail

env_name="${1:-}"
[[ "$env_name" == "full" || "$env_name" == "light" ]] || {
  echo "用法:$0 <full|light>" >&2
  exit 2
}

fail=0
say() { printf '%s\n' "$*"; }
ok() { say "  ✓ $*"; }
miss() { say "  ✗ $*"; fail=1; }

say "== 工具链 =="
for bin in kubectl helm helmfile; do
  if command -v "$bin" >/dev/null 2>&1; then ok "$bin"; else miss "$bin 未安装"; fi
done

say "== 集群连通 =="
if kubectl version >/dev/null 2>&1; then
  ok "kube-apiserver 可达($(kubectl version 2>/dev/null | grep -i server | head -1 | tr -s ' '))"
else
  miss "kube-apiserver 不可达(检查 KUBECONFIG)"
fi

say "== 前置 Secret(helm 不代建,缺失则组件起不来)=="
check_secret() { # <ns> <name> <用途>
  if kubectl -n "$1" get secret "$2" >/dev/null 2>&1; then
    ok "$1/$2($3)"
  else
    miss "$1/$2($3)—— 建法见 README「前置检查」节"
  fi
}
check_secret kube-system superdl-juicefs-secret "JuiceFS 元数据/对象存储凭据"
check_secret monitoring superdl-alert-token "Alertmanager→平台告警 webhook token"
check_secret monitoring superdl-smtp-password "Alertmanager 邮件通道"
if [[ "$env_name" == "full" ]]; then
  check_secret monitoring grafana-admin "Grafana 管理员口令(light 档关 Grafana,不需要)"
fi

# 只查 helmfile apply 直接消费的 values/(rke2/*.yaml 是分发模板,占位符由 ansible /
# 一键加入脚本在落盘时替换,仓库里保留占位符是对的)
say "== values/ 占位符残留(未替换直接 apply 会让组件起不来)=="
placeholder_files=(values/cilium.yaml values/kps.yaml)
for f in "${placeholder_files[@]}"; do
  [[ -f "$f" ]] || continue
  if grep -qE '<server-ip>|CHANGE_ME' "$f"; then
    miss "$f 仍有 <server-ip>/CHANGE_ME 占位符未替换"
  else
    ok "$f"
  fi
done

if [[ "$env_name" == "light" ]]; then
  say "== light(k3s)专项 =="
  if kubectl get runtimeclass nvidia >/dev/null 2>&1; then
    ok "RuntimeClass nvidia 存在"
  else
    miss "RuntimeClass nvidia 不存在(k3s 需已装 NVIDIA 驱动+toolkit;node-join.sh 会就位)"
  fi
  if kubectl -n kube-system get deploy traefik >/dev/null 2>&1; then
    miss "traefik 未禁用(server config 需 disable: traefik,Ingress 统一走 ingress-nginx)"
  else
    ok "traefik 已禁用"
  fi
fi

say ""
if [[ "$fail" -eq 0 ]]; then
  say "全部就绪:helmfile -e $env_name apply"
else
  say "存在缺项(✗),补齐后重跑本脚本。"
  exit 1
fi
