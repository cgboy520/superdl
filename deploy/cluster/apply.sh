#!/usr/bin/env bash
# 集群层 apply:admission/tenant-restrictions.yaml(非 helm release,每次重放)+ helmfile。
# helmfile 两个开关必带:HELM_DIFF_USE_UPGRADE_DRY_RUN=true(helm-diff 走服务端 dry-run,kata-deploy 的 lookup 才读得到)、
# --skip-diff-on-install(gpu-operator 首装时 ClusterPolicy CRD 不存在)。
#
# 用法:./apply.sh <full|light> [helmfile 参数...]
#   ./apply.sh light                      # 全量
#   ./apply.sh light -l name=gpu-operator # 单个 release
set -euo pipefail

env_name="${1:-}"
[[ "$env_name" == "full" || "$env_name" == "light" ]] || {
  echo "用法:$0 <full|light> [helmfile 参数...]" >&2
  exit 2
}
shift

cd "$(dirname "$0")"

# 准入策略先行(cluster-scoped,不进 deploy/app/k8s 的 kustomization);幂等,失败即退
echo "==> 准入策略 admission/tenant-restrictions.yaml(七条 VAP,全部 Deny)"
kubectl apply -f admission/tenant-restrictions.yaml
# 回读七条 Policy(CEL 写错时 apiserver 只拒 Policy、Binding 照建),少一条就停
for _b in superdl-platform-sa-scope superdl-tenant-pod-baseline superdl-node-field-scope \
  superdl-global-pod-guard superdl-platform-pod-secret-scope superdl-platform-job-secret-scope \
  superdl-node-delete-scope; do
  kubectl get validatingadmissionpolicy "$_b" > /dev/null
  kubectl get validatingadmissionpolicybinding "$_b" > /dev/null
done

# cert-manager ns(持有 ACME 账户与平台 TLS Secret;full 档 acme-dns 公网 53 端口也在此)打 PSA 标签:
# baseline 强制、restricted 审计/告警,与租户 ns 同款;ns 由 helmfile createNamespace 建,首次 apply 前不存在则跳过
if kubectl get namespace cert-manager > /dev/null 2>&1; then
  kubectl label namespace cert-manager --overwrite \
    pod-security.kubernetes.io/enforce=baseline \
    pod-security.kubernetes.io/audit=restricted \
    pod-security.kubernetes.io/warn=restricted
fi

exec env HELM_DIFF_USE_UPGRADE_DRY_RUN=true \
  helmfile -f helmfile.yaml.gotmpl -e "$env_name" apply --skip-diff-on-install "$@"
