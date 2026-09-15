#!/usr/bin/env bash
set -euo pipefail

TAG="${1:?用法: SUPERDL_IMAGE_PREFIX=harbor.<域>/superdl scripts/release.sh <tag>(形如 v1.2.3,release.yml 已推送 Harbor 的 tag)}"
IMAGE_PREFIX="${SUPERDL_IMAGE_PREFIX:?缺 SUPERDL_IMAGE_PREFIX(Harbor 项目前缀,如 harbor.example.com/superdl,与 release.yml 推送目标一致)}"
K8S_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../deploy/app/k8s" && pwd)"
NS=superdl
IMAGES=(api web admin)

if ! printf '%s' "$TAG" | grep -Eq '^v[0-9]+\.[0-9]+\.[0-9]+$'; then
  echo "::error::tag 必须严格形如 v1.2.3(禁止 latest / 分支名 / 预发布后缀 / 任何标点):$TAG" >&2
  exit 2
fi
if ! printf '%s' "$IMAGE_PREFIX" | grep -Eq '^[a-z0-9][a-z0-9.-]*(:[0-9]{1,5})?(/[a-z0-9]+([._-][a-z0-9]+)*)+$'; then
  echo "::error::SUPERDL_IMAGE_PREFIX 非法(需形如 harbor.example.com/superdl,全小写):$IMAGE_PREFIX" >&2
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
    echo "::error::找不到可解析 digest 的工具(crane / skopeo / docker buildx),拒绝按可变 tag 发布" >&2
    return 1
  fi
  if ! printf '%s' "$out" | grep -Eq '^sha256:[0-9a-f]{64}$'; then
    echo "::error::解析不到 $ref 的 digest(tag 未推送?未 docker login?):${out:-<空>}" >&2
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
    echo "::error::渲染后仍有 CHANGE_* 占位符残留(清单新增了未登记的占位?)" >&2
    return 1
  fi
  if printf '%s\n' "$out" | grep -E '^[[:space:]]*(- )?image:' | grep -qv '@sha256:'; then
    echo "::error::有镜像仍按可变 tag 渲染(应为 @sha256:...),拒绝下发:" >&2
    printf '%s\n' "$out" | grep -E '^[[:space:]]*(- )?image:' | grep -v '@sha256:' >&2
    return 1
  fi
  printf '%s\n' "$out"
}

echo "==> 0/5 前置:准入策略七条 VAP 必须在集群里且为 Deny"
vap_missing=0
for b in superdl-platform-sa-scope superdl-tenant-pod-baseline superdl-node-field-scope \
  superdl-global-pod-guard superdl-platform-pod-secret-scope superdl-platform-job-secret-scope \
  superdl-node-delete-scope; do
  actions="$(kubectl get validatingadmissionpolicybinding "$b" \
    -o jsonpath='{.spec.validationActions[*]}' 2> /dev/null || true)"
  if [[ -z "$actions" ]]; then
    echo "::error::ValidatingAdmissionPolicyBinding $b 不存在(先跑 deploy/cluster/apply.sh)" >&2
    vap_missing=1
  elif [[ " $actions " != *" Deny "* ]]; then
    echo "::error::ValidatingAdmissionPolicyBinding $b validationActions=[$actions] 不含 Deny" >&2
    vap_missing=1
  elif ! kubectl get validatingadmissionpolicy "$b" > /dev/null 2>&1; then
    echo "::error::ValidatingAdmissionPolicy $b 不存在而 Binding 在:策略被 apiserver 拒收,当前等于全放行" >&2
    vap_missing=1
  fi
done
if [[ "$vap_missing" -ne 0 ]]; then
  echo "::error::准入策略不完整,终止发布(deploy/cluster/admission/tenant-restrictions.yaml)" >&2
  exit 1
fi
echo "准入策略七条 Binding 均为 Deny"

echo "==> 1/5 前置:平台镜像拉取凭据 Secret(Harbor 机器人;首装按 deploy/app/secrets.example.yaml 手建)"
if ! kubectl -n "$NS" get secret superdl-registry-pull > /dev/null 2>&1; then
  echo "::warning::Secret ${NS}/superdl-registry-pull 不存在:Harbor 平台项目为 private 时新 Pod 将拉不到镜像" >&2
fi

echo "==> 2/5 迁移 Job(先于滚动)"
kubectl apply --server-side --force-conflicts -f "${K8S_DIR}/00-namespace-config.yaml"
render_checked < "${K8S_DIR}/10-migrate-job.yaml" | kubectl create -f -
if ! kubectl -n "$NS" wait --for=condition=complete --timeout=300s "job/superdl-migrate-${TAG}"; then
  echo "::error::迁移 Job 未成功,终止发布;日志:" >&2
  kubectl -n "$NS" logs "job/superdl-migrate-${TAG}" --tail=100 >&2 || true
  exit 1
fi

echo "==> 3/5 set image + apply(server-side apply,镜像按 digest 钉死,CHANGE_IMAGE_PREFIX → ${IMAGE_PREFIX})"
kubectl kustomize "${K8S_DIR}" | render_checked | kubectl apply --server-side --force-conflicts -f -

echo "==> 4/5 rollout status"
for d in superdl-api superdl-worker superdl-worker-tenant-mgr superdl-worker-node-mgr \
  superdl-worker-prewarm superdl-worker-disk-ops superdl-web superdl-admin; do
  if ! kubectl -n "$NS" rollout status "deploy/${d}" --timeout=660s; then
    echo "::error::${d} 滚动超时/失败,修复后重新发布(平台不支持发布回滚)" >&2
    exit 1
  fi
done

echo "==> 5/5 冒烟:经网关从集群外 GET /readyz(DNS/TLS/网关一并验证)"
API_BASE_URL="${SUPERDL_API_BASE_URL:-}"
if [ -z "$API_BASE_URL" ]; then
  API_BASE_URL="$(kubectl -n "$NS" get configmap superdl-api-config \
    -o jsonpath='{.data.SUPERDL_PUBLIC_BASE_URL}' 2>/dev/null || true)"
fi
case "$API_BASE_URL" in
  ""|*example.com*)
    echo "::notice::跳过外部冒烟:未提供 API 域名(设 SUPERDL_API_BASE_URL=https://<api-domain>,或把 ConfigMap superdl-api-config 的 SUPERDL_PUBLIC_BASE_URL 从占位改为真实域名)"
    ;;
  *)
    if ! curl -fsS --max-time 10 "${API_BASE_URL%/}/readyz" >/dev/null; then
      echo "::error::外部冒烟失败:GET ${API_BASE_URL%/}/readyz(查 DNS/TLS/网关;或 job/superdl-migrate-${TAG} 与 alembic_version)" >&2
      exit 1
    fi
    echo "外部冒烟通过:${API_BASE_URL%/}/readyz"
    ;;
esac

echo "发布完成:${TAG}(api/worker/web/admin 已滚动)"
