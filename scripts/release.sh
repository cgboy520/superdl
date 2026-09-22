#!/usr/bin/env bash
set +x
set -euo pipefail

TAG="${1:?usage: SUPERDL_IMAGE_PREFIX=harbor.<domain>/superdl scripts/release.sh <tag> (of the form v1.2.3, a tag release.yml has pushed to Harbor)}"
IMAGE_PREFIX="${SUPERDL_IMAGE_PREFIX:?SUPERDL_IMAGE_PREFIX missing (Harbor project prefix such as harbor.example.com/superdl, the same target release.yml pushes to)}"
K8S_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../deploy/app/k8s" && pwd)"
NS=superdl
IMAGES=(api web admin)
WRITERS=(superdl-api superdl-worker superdl-worker-tenant-mgr superdl-worker-node-mgr
  superdl-worker-prewarm superdl-worker-disk-ops)
WRITER_SELECTOR='app in (superdl-api,superdl-worker)'
CHECKPOINT=superdl-release-maintenance
STOP_TIMEOUT="${SUPERDL_RELEASE_STOP_TIMEOUT:-900}"

fail() { echo "::error::$*" >&2; exit 1; }

# This acknowledgement also excludes concurrent release operators. GitOps and other
# external controllers cannot be suspended safely by a generic release script.
[[ "${SUPERDL_RELEASE_MAINTENANCE_ACK:-}" == yes ]] || fail \
  'set SUPERDL_RELEASE_MAINTENANCE_ACK=yes only after suspending GitOps/autoscalers and other writers; see deploy/README.md'
[[ "$STOP_TIMEOUT" =~ ^[1-9][0-9]{0,4}$ ]] || fail 'invalid SUPERDL_RELEASE_STOP_TIMEOUT (seconds)'
for tool in kubectl cosign curl; do
  command -v "$tool" >/dev/null || fail "required command missing: $tool"
done

# Default to the checkout's GitHub origin, never to an unrelated signing identity.
# Capture the origin without logging it (remote URLs can contain credentials).
RELEASE_REPOSITORY="${SUPERDL_RELEASE_REPOSITORY:-}"
if [[ -z "$RELEASE_REPOSITORY" ]]; then
  origin="$(git -C "${K8S_DIR}/../../.." remote get-url origin)"
  case "$origin" in
    https://github.com/*) RELEASE_REPOSITORY="${origin#https://github.com/}" ;;
    git@github.com:*) RELEASE_REPOSITORY="${origin#git@github.com:}" ;;
    ssh://git@github.com/*) RELEASE_REPOSITORY="${origin#ssh://git@github.com/}" ;;
    *) fail 'set SUPERDL_RELEASE_REPOSITORY=owner/repo for a nonstandard GitHub origin' ;;
  esac
  RELEASE_REPOSITORY="${RELEASE_REPOSITORY%.git}"
  unset origin
fi
[[ "$RELEASE_REPOSITORY" =~ ^[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || fail 'invalid SUPERDL_RELEASE_REPOSITORY'

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
    out="$(crane digest "$ref" 2> /dev/null)" || return 1
  elif command -v skopeo > /dev/null 2>&1; then
    out="$(skopeo inspect --format '{{.Digest}}' "docker://$ref" 2> /dev/null)" || return 1
  elif command -v docker > /dev/null 2>&1; then
    # Do not hash an empty/partial response after an inspect failure.
    out="$(docker buildx imagetools inspect "$ref" --format '{{.Manifest.Digest}}' 2> /dev/null)" || return 1
  else
    echo "::error::no tool to resolve digests found (crane / skopeo / docker buildx), refusing to release by mutable tag" >&2
    return 1
  fi
  if ! printf '%s' "$out" | grep -Eq '^sha256:[0-9a-f]{64}$'; then
    echo "::error::cannot resolve the digest of $ref (tag not pushed? no registry login?)" >&2
    return 1
  fi
  printf '%s' "$out"
}

declare -A DIGEST=()
for _img in "${IMAGES[@]}"; do
  DIGEST[$_img]="$(resolve_digest "${IMAGE_PREFIX}/superdl-${_img}:${TAG}")" || fail "digest resolution failed: $_img"
  echo "digest ${IMAGE_PREFIX}/superdl-${_img}:${TAG} -> ${DIGEST[$_img]}"
  # release.yml signs each RepoDigest keylessly using the Actions OIDC token.
  # Exact SAN, not an identity regexp; tag, repository and workflow must all match.
  cosign verify \
    --certificate-identity "https://github.com/${RELEASE_REPOSITORY}/.github/workflows/release.yml@refs/tags/${TAG}" \
    --certificate-oidc-issuer https://token.actions.githubusercontent.com \
    "${IMAGE_PREFIX}/superdl-${_img}@${DIGEST[$_img]}" >/dev/null || fail "signature verification failed: $_img"
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
  if grep -qE 'CHANGE_(TAG|IMAGE_PREFIX)' <<< "$out"; then
    echo "::error::CHANGE_* placeholders remain after rendering (a new unregistered placeholder in the manifests?)" >&2
    return 1
  fi
  if grep -E '^[[:space:]]*(- )?image:' <<< "$out" | grep -v '@sha256:' >/dev/null; then
    echo "::error::some images still render with a mutable tag (should be @sha256:...), refusing to roll out:" >&2
    printf '%s\n' "$out" | grep -E '^[[:space:]]*(- )?image:' | grep -v '@sha256:' >&2
    return 1
  fi
  printf '%s\n' "$out"
}

echo "==> preflight: the seven VAP admission policies must exist in the cluster as Deny"
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

echo "==> preflight: namespace and platform image pull Secret"
# First install bootstraps the namespace and Secrets out of band, before any dry-run.
kubectl get namespace "$NS" -o name >/dev/null
if ! kubectl -n "$NS" get secret superdl-registry-pull > /dev/null 2>&1; then
  echo "::warning::Secret ${NS}/superdl-registry-pull missing: with a private Harbor platform project new Pods cannot pull images" >&2
fi

check_hpa() {
  local targets kind name d
  targets="$(kubectl -n "$NS" get hpa -o jsonpath='{range .items[*]}{.spec.scaleTargetRef.kind}{" "}{.spec.scaleTargetRef.name}{"\n"}{end}')"
  while read -r kind name; do
    for d in "${WRITERS[@]}"; do
      [[ "$kind/$name" != "Deployment/$d" ]] || fail "HPA targets $d; remove it under maintenance and restore it only after success"
    done
  done <<< "$targets"
}
check_hpa

writer_nodes="$(kubectl -n "$NS" get pods -l "$WRITER_SELECTOR" -o jsonpath='{range .items[*]}{.spec.nodeName}{"\n"}{end}')"
check_writer_nodes() {
  local node ready
  while IFS= read -r node; do
    [[ -n "$node" ]] || continue
    ready="$(kubectl get node "$node" -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}')"
    [[ "$ready" == True ]] || fail "writer node $node is not Ready; recover/fence its processes before releasing"
  done <<< "$writer_nodes"
}
check_writer_nodes

# A timed-out migration may still be running, or awaiting Pod creation. Never
# start another one beside it, even if its Pod has not appeared yet.
migration_jobs="$(kubectl -n "$NS" get jobs -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.status.conditions[?(@.type=="Complete")].status}{" "}{.status.conditions[?(@.type=="Failed")].status}{"\n"}{end}')"
while read -r job complete failed; do
  case "$job" in
    superdl-migrate-*)
      [[ "$complete" == True || "$failed" == True ]] || fail "migration Job $job is not terminal; inspect/fence it before retrying"
      ;;
  esac
done <<< "$migration_jobs"
migration_pods="$(kubectl -n "$NS" get pods -l app=superdl-migrate \
  --field-selector=status.phase!=Succeeded,status.phase!=Failed -o name)"
[[ -z "$migration_pods" ]] || fail 'a migration Pod is still active; inspect/fence it before retrying'

checkpoint="$(kubectl -n "$NS" get configmap "$CHECKPOINT" --ignore-not-found -o name)"
if [[ -n "$checkpoint" && "${SUPERDL_RELEASE_RESUME:-}" != yes ]]; then
  fail 'maintenance checkpoint exists; inspect the failed release, then set SUPERDL_RELEASE_RESUME=yes to fix forward'
fi

declare -A REPLICAS=() EXISTS=()
defaults=(2 2 2 1 1 1)
for i in "${!WRITERS[@]}"; do
  d="${WRITERS[$i]}"
  live="$(kubectl -n "$NS" get deployment "$d" --ignore-not-found \
    -o jsonpath='{.metadata.name}{" "}{.spec.replicas}{" "}{.spec.paused}')"
  read -r name replicas paused <<< "$live"
  [[ "$paused" != true ]] || fail "$d is paused; unpause it under maintenance before releasing"
  EXISTS[$d]="$name"
  if [[ -n "$checkpoint" ]]; then
    replicas="$(kubectl -n "$NS" get configmap "$CHECKPOINT" -o "jsonpath={.data.$d}")"
  elif [[ -z "$name" ]]; then
    replicas="${defaults[$i]}"
  fi
  [[ "$replicas" =~ ^[0-9]+$ ]] || fail "invalid/missing saved replica count for $d"
  REPLICAS[$d]="$replicas"
done

# Freeze both versions locally before the first cluster write. Kustomize's replica
# transformer avoids line-based YAML rewriting and keeps new writers at zero too.
work="$(mktemp -d)"
stopping=no
cleanup() {
  local rc=$? d
  trap - EXIT
  if [[ "$rc" -ne 0 && "$stopping" == yes ]]; then
    echo '::error::release failed; keeping maintenance checkpoint and stopping writers (no rollback). Keep GitOps/HPA suspended.' >&2
    for d in "${WRITERS[@]}"; do
      kubectl -n "$NS" scale "deployment/$d" --replicas=0 >/dev/null 2>&1 || \
        echo "::warning::could not confirm scale-to-zero for $d; inspect manually" >&2
    done
  fi
  # Only the private mktemp directory created by this invocation is removed.
  rm -rf -- "$work"
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
for mode in stopped started; do
  mkdir "$work/$mode"
  {
    printf 'apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomization\nresources:\n  - %s\nreplicas:\n' "$K8S_DIR"
    for d in "${WRITERS[@]}"; do
      count=0
      [[ "$mode" != started ]] || count="${REPLICAS[$d]}"
      printf '  - name: %s\n    count: %s\n' "$d" "$count"
    done
  } > "$work/$mode/kustomization.yaml"
  kubectl kustomize "$work/$mode" | render_checked > "$work/$mode.yaml"
done
cp "${K8S_DIR}/00-namespace-config.yaml" "$work/config.yaml"
# Separate attempts have separate Jobs; an interrupted attempt is gated above.
MIGRATION_JOB="superdl-migrate-${TAG}-$(date +%s)-${RANDOM}"
[[ "${#MIGRATION_JOB}" -le 63 ]] || fail 'release tag is too long for a migration Job name'
render_checked < "${K8S_DIR}/10-migrate-job.yaml" | \
  sed "s/name: superdl-migrate-${TAG}$/name: ${MIGRATION_JOB}/" > "$work/migrate.yaml"
for file in config stopped started; do
  kubectl apply --dry-run=server --server-side --force-conflicts -f "$work/$file.yaml" >/dev/null
done
kubectl create --dry-run=server -f "$work/migrate.yaml" >/dev/null

echo '==> stop: save replicas, scale API and all five workers to zero, wait for old Pods'
if [[ -z "$checkpoint" ]]; then
  literals=()
  for d in "${WRITERS[@]}"; do literals+=("--from-literal=$d=${REPLICAS[$d]}"); done
  # Atomic create also prevents two fresh releases from owning maintenance.
  kubectl -n "$NS" create configmap "$CHECKPOINT" "${literals[@]}" >/dev/null
fi
stopping=yes
for d in "${WRITERS[@]}"; do
  [[ -z "${EXISTS[$d]}" ]] || kubectl -n "$NS" scale "deployment/$d" --replicas=0
done

assert_zero_desired() {
  local d count
  check_hpa
  for d in "${WRITERS[@]}"; do
    count="$(kubectl -n "$NS" get deployment "$d" --ignore-not-found -o jsonpath='{.spec.replicas}')"
    [[ -z "$count" || "$count" == 0 ]] || fail "$d was restarted by another controller; keep maintenance active"
  done
}
wait_writers_stopped() {
  local deadline=$((SECONDS + STOP_TIMEOUT)) pods rs remaining
  while true; do
    assert_zero_desired
    pods="$(kubectl -n "$NS" get pods -l "$WRITER_SELECTOR" -o name)"
    rs="$(kubectl -n "$NS" get replicasets -l "$WRITER_SELECTOR" -o jsonpath='{range .items[*]}{.spec.replicas}{"\n"}{end}')"
    if [[ -z "$pods" && ! "$rs" =~ [1-9] ]]; then return; fi
    remaining=$((deadline - SECONDS))
    [[ "$remaining" -gt 0 ]] || fail 'old writers did not exit before the stop timeout; migration not started'
    if [[ -n "$pods" ]]; then
      # Readiness/Deployment availability is not proof of termination. Include
      # terminating Pods and every worker component; never force-delete them.
      if ! kubectl -n "$NS" wait --for=delete pod -l "$WRITER_SELECTOR" --timeout="${remaining}s"; then
        # Pods can disappear between the list and wait. Accept only an empty
        # fresh list, not a timeout/error with old writers still present.
        pods="$(kubectl -n "$NS" get pods -l "$WRITER_SELECTOR" -o name)"
        [[ -z "$pods" ]] || fail 'old writer Pods remain; migration/restart refused'
      fi
    else
      sleep 2
    fi
  done
}
wait_writers_stopped

check_writer_nodes
echo '==> migration: no application writers remain'
kubectl apply --server-side --force-conflicts -f "$work/config.yaml"
kubectl create -f "$work/migrate.yaml"
if ! kubectl -n "$NS" wait --for=condition=complete --timeout=300s "job/$MIGRATION_JOB"; then
  # Do not dump application/migration logs: they can contain connection secrets.
  fail "migration did not complete: job/$MIGRATION_JOB; writers stay stopped; inspect it securely before retrying"
fi
wait_writers_stopped

echo '==> apply: install verified images while writers remain at zero'
kubectl apply --server-side --force-conflicts -f "$work/stopped.yaml"
for d in "${WRITERS[@]}"; do
  actual="$(kubectl -n "$NS" get deployment "$d" -o jsonpath='{.spec.template.spec.containers[0].image}')"
  [[ "$actual" == "${IMAGE_PREFIX}/superdl-api@${DIGEST[api]}" ]] || fail "unexpected writer image on $d"
  # Wait for the Deployment controller to observe the zero-replica new template
  # before changing its scale; do not race a stale controller generation.
  kubectl -n "$NS" rollout status "deploy/$d" --timeout=660s
done
assert_zero_desired
# The only restart path is after successful migration AND stopped-template apply.
for d in "${WRITERS[@]}"; do
  kubectl -n "$NS" scale "deployment/$d" --current-replicas=0 --replicas="${REPLICAS[$d]}"
done

echo '==> rollout status'
for d in "${WRITERS[@]}" superdl-web superdl-admin; do
  kubectl -n "$NS" rollout status "deploy/${d}" --timeout=660s
done

echo '==> smoke: GET /readyz through the external gateway'
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
      echo "::error::external smoke failed: GET ${API_BASE_URL%/}/readyz (check DNS/TLS/gateway; or job/${MIGRATION_JOB} and alembic_version)" >&2
      exit 1
    fi
    echo "external smoke passed: ${API_BASE_URL%/}/readyz"
    ;;
esac

kubectl -n "$NS" delete configmap "$CHECKPOINT" --wait=true >/dev/null
stopping=no
echo "release complete: ${TAG} (saved replicas restored; reconcile the new digests before resuming GitOps/HPA)"
