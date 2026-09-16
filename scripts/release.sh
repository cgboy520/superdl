#!/usr/bin/env bash
set -euo pipefail

TAG="${1:?usage: SUPERDL_IMAGE_PREFIX=harbor.<domain>/superdl scripts/release.sh <tag> (of the form v1.2.3, a tag release.yml has pushed to Harbor)}"
IMAGE_PREFIX="${SUPERDL_IMAGE_PREFIX:?SUPERDL_IMAGE_PREFIX missing (Harbor project prefix such as harbor.example.com/superdl, the same target release.yml pushes to)}"
K8S_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../deploy/app/k8s" && pwd)"
NS=superdl
IMAGES=(api web admin)

if ! printf '%s' "$TAG" | grep -Eq '^v[0-9]+\.[0-9]+\.[0-9]+$'; then
  echo "::error::the tag must be exactly of the form v1.2.3 (no latest / branch names / pre-release suffixes / punctuation): $TAG" >&2
  exit 2
fi
if ! printf '%s' "$IMAGE_PREFIX" | grep -Eq '^[a-z0-9][a-z0-9.-]*(:[0-9]{1,5})?(/[a-z0-9]+([._-][a-z0-9]+)*)+$'; then
  echo "::error::SUPERDL_IMAGE_PREFIX invalid (must look like harbor.example.com/superdl, all lowercase): $IMAGE_PREFIX" >&2
  exit 2
fi

resolve_digest() {
  local ref="$1" out=""
  if command -v crane > /dev/null 2>&1; then
    out="$(crane digest "$ref" 2> /dev/null || true)"
  elif command -v skopeo > /dev/null 2>&1; then
    out="$(skopeo inspect --format '{{.Digest}}' "docker://$ref" 2> /dev/null || true)"
  elif command -v docker > /dev/null 2>&1; then
    out="$(docker buildx imagetools inspect --raw "$ref" 2> /dev/null | sha256sum | awk 'NF && $1 ~ /^[0-9a-f]{64}$/ {print "sha256:" $1}' || true)"
  else
    echo "::error::no tool to resolve digests found (crane / skopeo / docker buildx), refusing to release by mutable tag" >&2
    return 1
  fi
  if ! printf '%s' "$out" | grep -Eq '^sha256:[0-9a-f]{64}$'; then
    echo "::error::cannot resolve the digest of $ref (tag not pushed? no docker login?): ${out:-<empty>}" >&2
    return 1
  fi
  printf '%s' "$out"
}

declare -A DIGEST=()
for _img in "${IMAGES[@]}"; do
  DIGEST[$_img]="$(resolve_digest "${IMAGE_PREFIX}/superdl-${_img}:${TAG}")" || exit 1
  echo "digest ${IMAGE_PREFIX}/superdl-${_img}:${TAG} -> ${DIGEST[$_img]}"
done

render() {
  sed \
    -e "s#CHANGE_IMAGE_PREFIX/superdl-api:CHANGE_TAG#${IMAGE_PREFIX}/superdl-api@${DIGEST[api]}#g" \
    -e "s#CHANGE_IMAGE_PREFIX/superdl-web:CHANGE_TAG#${IMAGE_PREFIX}/superdl-web@${DIGEST[web]}#g" \
    -e "s#CHANGE_IMAGE_PREFIX/superdl-admin:CHANGE_TAG#${IMAGE_PREFIX}/superdl-admin@${DIGEST[admin]}#g" \
    -e "s#CHANGE_IMAGE_PREFIX#${IMAGE_PREFIX}#g" \
    -e "s#CHANGE_TAG#${TAG}#g"
}

render_checked() {
  local out
  out="$(render)"
  if printf '%s' "$out" | grep -qE 'CHANGE_(TAG|IMAGE_PREFIX)'; then
    echo "::error::CHANGE_* placeholders remain after rendering (a new unregistered placeholder in the manifests?)" >&2
    return 1
  fi
  if printf '%s\n' "$out" | grep -E '^[[:space:]]*(- )?image:' | grep -qv '@sha256:'; then
    echo "::error::some images still render with a mutable tag (should be @sha256:...), refusing to roll out:" >&2
    printf '%s\n' "$out" | grep -E '^[[:space:]]*(- )?image:' | grep -v '@sha256:' >&2
    return 1
  fi
  printf '%s\n' "$out"
}

echo "==> 0/5 prerequisite: the seven VAP admission policies must exist in the cluster as Deny"
vap_missing=0
for b in superdl-platform-sa-scope superdl-tenant-pod-baseline superdl-node-field-scope \
  superdl-global-pod-guard superdl-platform-pod-secret-scope superdl-platform-job-secret-scope \
  superdl-node-delete-scope; do
  actions="$(kubectl get validatingadmissionpolicybinding "$b" \
    -o jsonpath='{.spec.validationActions[*]}' 2> /dev/null || true)"
  if [[ -z "$actions" ]]; then
    echo "::error::ValidatingAdmissionPolicyBinding $b missing (run deploy/cluster/apply.sh first)" >&2
    vap_missing=1
  elif [[ " $actions " != *" Deny "* ]]; then
    echo "::error::ValidatingAdmissionPolicyBinding $b validationActions=[$actions] lacks Deny" >&2
    vap_missing=1
  elif ! kubectl get validatingadmissionpolicy "$b" > /dev/null 2>&1; then
    echo "::error::ValidatingAdmissionPolicy $b missing while its Binding exists: the policy was rejected by the apiserver, which currently means everything is allowed" >&2
    vap_missing=1
  fi
done
if [[ "$vap_missing" -ne 0 ]]; then
  echo "::error::admission policies incomplete, aborting the release (deploy/cluster/admission/tenant-restrictions.yaml)" >&2
  exit 1
fi
echo "all seven admission policy Bindings are Deny"

echo "==> 1/5 prerequisite: platform image pull Secret (Harbor robot; created by hand at first install per deploy/app/secrets.example.yaml)"
if ! kubectl -n "$NS" get secret superdl-registry-pull > /dev/null 2>&1; then
  echo "::warning::Secret ${NS}/superdl-registry-pull missing: with a private Harbor platform project new Pods cannot pull images" >&2
fi

echo "==> 2/5 migration Job (before the rollout)"
kubectl apply --server-side --force-conflicts -f "${K8S_DIR}/00-namespace-config.yaml"
render_checked < "${K8S_DIR}/10-migrate-job.yaml" | kubectl create -f -
if ! kubectl -n "$NS" wait --for=condition=complete --timeout=300s "job/superdl-migrate-${TAG}"; then
  echo "::error::migration Job did not succeed, aborting the release; log:" >&2
  kubectl -n "$NS" logs "job/superdl-migrate-${TAG}" --tail=100 >&2 || true
  exit 1
fi

echo "==> 3/5 set image + apply (server-side apply, images pinned by digest, CHANGE_IMAGE_PREFIX → ${IMAGE_PREFIX})"
kubectl kustomize "${K8S_DIR}" | render_checked | kubectl apply --server-side --force-conflicts -f -

echo "==> 4/5 rollout status"
for d in superdl-api superdl-worker superdl-worker-tenant-mgr superdl-worker-node-mgr \
  superdl-worker-prewarm superdl-worker-disk-ops superdl-web superdl-admin; do
  if ! kubectl -n "$NS" rollout status "deploy/${d}" --timeout=660s; then
    echo "::error::${d} rollout timed out / failed; fix and release again (the platform does not support release rollback)" >&2
    exit 1
  fi
done

echo "==> 5/5 smoke: GET /readyz through the gateway from outside the cluster (verifies DNS/TLS/gateway together)"
API_BASE_URL="${SUPERDL_API_BASE_URL:-}"
if [ -z "$API_BASE_URL" ]; then
  API_BASE_URL="$(kubectl -n "$NS" get configmap superdl-api-config \
    -o jsonpath='{.data.SUPERDL_PUBLIC_BASE_URL}' 2>/dev/null || true)"
fi
case "$API_BASE_URL" in
  ""|*example.com*)
    echo "::notice::skipping the external smoke: no API domain given (set SUPERDL_API_BASE_URL=https://<api-domain>, or change SUPERDL_PUBLIC_BASE_URL in the ConfigMap superdl-api-config from the placeholder to the real domain)"
    ;;
  *)
    if ! curl -fsS --max-time 10 "${API_BASE_URL%/}/readyz" >/dev/null; then
      echo "::error::external smoke failed: GET ${API_BASE_URL%/}/readyz (check DNS/TLS/gateway; or job/superdl-migrate-${TAG} and alembic_version)" >&2
      exit 1
    fi
    echo "external smoke passed: ${API_BASE_URL%/}/readyz"
    ;;
esac

echo "release complete: ${TAG} (api/worker/web/admin rolled out)"
