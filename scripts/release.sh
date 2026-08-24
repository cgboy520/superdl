#!/usr/bin/env bash
# SuperDL 发布流水线:迁移 → set image+apply → rollout status → 冒烟,四步任一失败即退。
#
# 用法: scripts/release.sh <tag>
#   tag:ghcr 已推送的发布标签(.github/workflows/release.yml 产物,形如 v1.2.3)。
#
# 顺序铁律:迁移 Job 必须先于滚动(expand-only 窗口内「老代码+新 schema」安全,
# 反序「新代码+旧 schema」会被 /readyz 的 schema_mismatch 拦下,表现为发布卡死)。
# 回滚:见 deploy/README.md「回滚指引」——rollout undo 各 Deployment 即可,
# 迁移只增不删(expand-only),向后兼容窗口内无需回滚库。
set -euo pipefail

TAG="${1:?用法: scripts/release.sh <tag>(形如 v1.2.3,release.yml 已推送 ghcr 的 tag)}"
K8S_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../deploy/app/k8s" && pwd)"
NS=superdl

case "$TAG" in
  v[0-9]*.[0-9]*.[0-9]*) ;;
  *)
    echo "::error::tag 必须形如 v1.2.3(禁止 latest/分支名):$TAG" >&2
    exit 2
    ;;
esac

echo "==> 1/4 迁移 Job(expand-only,先于滚动)"
sed "s/CHANGE_TAG/${TAG}/g" "${K8S_DIR}/10-migrate-job.yaml" | kubectl create -f -
if ! kubectl -n "$NS" wait --for=condition=complete --timeout=300s "job/superdl-migrate-${TAG}"; then
  echo "::error::迁移 Job 未成功,终止发布;日志:" >&2
  kubectl -n "$NS" logs "job/superdl-migrate-${TAG}" --tail=100 >&2 || true
  exit 1
fi

echo "==> 2/4 set image + apply(tag 单点:kustomization.yaml images 的 CHANGE_TAG → ${TAG})"
kubectl kustomize "${K8S_DIR}" | sed "s/CHANGE_TAG/${TAG}/g" | kubectl apply -f -

echo "==> 3/4 rollout status"
# worker 组件集群:同一镜像的 5 个 Deployment 必须全部滚动到位,
# 漏一个即旧代码继续领任务(队列兼容窗口靠任务幂等与 reaper 兜底,不替你做版本收敛)
for d in superdl-api superdl-worker superdl-worker-tenant-mgr superdl-worker-node-mgr \
  superdl-worker-prewarm superdl-worker-disk-ops superdl-web superdl-admin; do
  if ! kubectl -n "$NS" rollout status "deploy/${d}" --timeout=660s; then
    echo "::error::${d} 滚动超时/失败,按 deploy/README.md「回滚指引」回滚" >&2
    exit 1
  fi
done

echo "==> 4/4 冒烟(API /healthz + /readyz,在新 Pod 内容器内直连)"
POD=""
for _ in $(seq 1 30); do
  POD="$(kubectl -n "$NS" get pod -l app=superdl-api \
    --field-selector=status.phase=Running \
    -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
  [ -n "$POD" ] && break
  sleep 2
done
if [ -z "$POD" ]; then
  echo "::error::找不到 Running 的 superdl-api Pod,冒烟失败" >&2
  exit 1
fi
# 容器为 python 镜像,用 urllib 避免依赖 curl/wget;readOnlyRootFilesystem 不影响本命令
smoke() {
  kubectl -n "$NS" exec "$POD" -- python -c "
import sys, urllib.request
try:
    with urllib.request.urlopen('http://localhost:8000$1', timeout=5) as r:
        sys.exit(0 if r.status == 200 else 1)
except Exception:
    sys.exit(1)
"
}
if ! smoke /healthz; then
  echo "::error::/healthz 冒烟失败(Pod=${POD})" >&2
  exit 1
fi
if ! smoke /readyz; then
  # readyz 含 alembic_version 比对:失败多为第 1 步迁移漏跑/未追平,不回滚,先查迁移 Job
  echo "::error::/readyz 冒烟失败(schema_mismatch?查 job/superdl-migrate-${TAG} 与 alembic_version)" >&2
  exit 1
fi

echo "发布完成:${TAG}(api/worker/web/admin 已滚动,冒烟通过)"
