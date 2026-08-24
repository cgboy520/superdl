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
check_secret cert-manager alidns-credentials "cert-manager alidns DNS01 solver 凭据(泛域名证书签发)"
check_secret registry registry-htpasswd "私有镜像仓库 htpasswd 凭据(不落 git,建法见 registry/registry.yaml 头注释)"
if [[ "$env_name" == "full" ]]; then
  check_secret monitoring grafana-admin "Grafana 管理员口令(light 档关 Grafana,不需要)"
fi

# 只查 helmfile apply 直接消费的 values/;rke2/*.yaml 是分发模板,占位符由 ansible /
# 一键加入脚本落盘时替换,仓库里保留占位符。
say "== values/ 占位符残留(未替换直接 apply 会让组件起不来)=="
placeholder_files=(values/cilium.yaml values/kps.yaml registry/registry.yaml)
for f in "${placeholder_files[@]}"; do
  [[ -f "$f" ]] || continue
  if grep -qE '<server-ip>|CHANGE_ME|example\.com' "$f"; then
    miss "$f 仍有 <server-ip>/CHANGE_ME/example.com 占位符未替换"
  else
    ok "$f"
  fi
done

say "== registry 私有镜像仓库(P0-07)=="
# 回归拦截:原占位口令 CHANGE_ME 的 bcrypt hash 已泄漏,凭据禁止再落 git
leaked_hash='$2y$05$0JWd7XLpA2WBKjaBnw4KqupCZERl2Yx3cg9giAruPDxz1km4OCfSW'
if [[ -f registry/registry.yaml ]] && grep -qF "$leaked_hash" registry/registry.yaml; then
  miss "registry/registry.yaml 含已泄漏的 htpasswd hash 字面值(部署时手工建 Secret,见该文件头注释)"
else
  ok "registry/registry.yaml 无已知泄漏 hash"
fi
# 入方向边界:NetworkPolicy 必须在集群内存在(ipBlock 示例段按真实网段启用后 apply)
if kubectl -n registry get networkpolicy registry-default-deny >/dev/null 2>&1; then
  ok "NetworkPolicy registry/registry-default-deny 已存在"
else
  miss "NetworkPolicy registry/registry-default-deny 不存在(registry.yaml 的 ipBlock 示例段按真实节点/运维网段启用后 apply;缺失则仓库入方向无边界)"
fi

say "== 应用入口(../app,P0-08)=="
app_ingress=../app/k8s/04-ingress.yaml
if [[ -f "$app_ingress" ]]; then
  if grep -q 'CHANGE_ME_OFFICE_CIDR' "$app_ingress"; then
    miss "$app_ingress 管理端白名单仍是 CHANGE_ME_OFFICE_CIDR 占位(替换为办公网/跳板机出口 CIDR)"
  else
    ok "$app_ingress 管理端白名单已配真实网段"
  fi
fi

say "== 准入策略(P1-24:ValidatingAdmissionPolicy 必须 Deny 生效)=="
# 首次上线可先 [Audit] 观察一周(见 admission/tenant-restrictions.yaml 头注释),
# 但正式发布前必须改回 Deny——本检查按 Deny 卡。
for binding in superdl-platform-sa-scope superdl-tenant-pod-baseline; do
  actions=$(kubectl get validatingadmissionpolicybinding "$binding" \
    -o jsonpath='{.spec.validationActions[*]}' 2>/dev/null || true)
  if [[ -z "$actions" ]]; then
    miss "ValidatingAdmissionPolicyBinding $binding 不存在(kubectl apply -f admission/tenant-restrictions.yaml)"
  elif [[ " $actions " == *" Deny "* ]]; then
    ok "ValidatingAdmissionPolicyBinding $binding validationActions=[$actions]"
  else
    miss "ValidatingAdmissionPolicyBinding $binding validationActions=[$actions] 不含 Deny(Audit 观察期结束后改回)"
  fi
done

say "== 应用 NetworkPolicy 出向(报告3#11,提示性)=="
netpol=../app/k8s/09-networkpolicy.yaml
if [[ -f "$netpol" ]]; then
  if grep -q 'CHANGE_ME' "$netpol"; then
    say "  ⚠ $netpol 出向规则含 CHANGE_ME 占位(PG/对象存储/支付·短信网关/K8s API 端点):"
    say "    apply 前必须替换为真实端点,否则平台出向全断(提示项,不阻断)"
  else
    ok "$netpol 出向已收敛为真实端点"
  fi
fi

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
