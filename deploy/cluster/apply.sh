#!/usr/bin/env bash
set -euo pipefail

env_name="${1:-}"
[[ "$env_name" == "full" || "$env_name" == "light" ]] || {
  echo "usage: $0 <full|light> [helmfile args...]" >&2
  exit 2
}
shift

cd "$(dirname "$0")"

echo "==> admission policies admission/tenant-restrictions.yaml (seven VAPs, all Deny)"
kubectl apply -f admission/tenant-restrictions.yaml
for _b in superdl-platform-sa-scope superdl-tenant-pod-baseline superdl-node-field-scope \
  superdl-global-pod-guard superdl-platform-pod-secret-scope superdl-platform-job-secret-scope \
  superdl-node-delete-scope; do
  kubectl get validatingadmissionpolicy "$_b" > /dev/null
  kubectl get validatingadmissionpolicybinding "$_b" > /dev/null
done

if kubectl get namespace cert-manager > /dev/null 2>&1; then
  kubectl label namespace cert-manager --overwrite \
    pod-security.kubernetes.io/enforce=baseline \
    pod-security.kubernetes.io/audit=restricted \
    pod-security.kubernetes.io/warn=restricted
fi

exec env HELM_DIFF_USE_UPGRADE_DRY_RUN=true \
  helmfile -f helmfile.yaml.gotmpl -e "$env_name" apply --skip-diff-on-install "$@"
