#!/usr/bin/env bash
# SuperDL 发布流水线:迁移 → set image+apply → rollout status → 经 Ingress 外部冒烟,任一步失败即退。
#
# 用法: SUPERDL_IMAGE_PREFIX=harbor.<域>/superdl [SUPERDL_API_BASE_URL=https://<api-domain>] scripts/release.sh <tag>
#   tag:Harbor 已推送的发布标签(.github/workflows/release.yml 产物,形如 v1.2.3)。
#   SUPERDL_IMAGE_PREFIX:必填,Harbor 项目前缀(与 release.yml 的 HARBOR_HOST/HARBOR_PROJECT 一致),
#   替换各清单里的 CHANGE_IMAGE_PREFIX 占位;tag 替换 CHANGE_TAG。
#   SUPERDL_API_BASE_URL:可选,第 4 步外部冒烟用的公网 API 基址;缺省读 ConfigMap
#   superdl-api-config 的 SUPERDL_PUBLIC_BASE_URL,两者都取不到(或仍是占位)则跳过该步并提示。
#
# 顺序铁律:迁移 Job 必须先于滚动(expand-only 窗口内「老代码+新 schema」安全,
# 反序「新代码+旧 schema」会被 /readyz 的 schema_mismatch 拦下,表现为发布卡死)。
# 回滚:见 deploy/README.md「回滚指引」——rollout undo 各 Deployment 即可,
# 迁移只增不删(expand-only),向后兼容窗口内无需回滚库。
set -euo pipefail

TAG="${1:?用法: SUPERDL_IMAGE_PREFIX=harbor.<域>/superdl scripts/release.sh <tag>(形如 v1.2.3,release.yml 已推送 Harbor 的 tag)}"
IMAGE_PREFIX="${SUPERDL_IMAGE_PREFIX:?缺 SUPERDL_IMAGE_PREFIX(Harbor 项目前缀,如 harbor.example.com/superdl,与 release.yml 推送目标一致)}"
K8S_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../deploy/app/k8s" && pwd)"
NS=superdl

# 清单占位单点:CHANGE_IMAGE_PREFIX(Harbor 项目前缀)与 CHANGE_TAG(本次 tag)
render() { sed -e "s#CHANGE_IMAGE_PREFIX#${IMAGE_PREFIX}#g" -e "s/CHANGE_TAG/${TAG}/g"; }

case "$TAG" in
  v[0-9]*.[0-9]*.[0-9]*) ;;
  *)
    echo "::error::tag 必须形如 v1.2.3(禁止 latest/分支名):$TAG" >&2
    exit 2
    ;;
esac

echo "==> 0/4 前置:平台镜像拉取凭据 Secret(Harbor 机器人;首装按 deploy/app/secrets.example.yaml 手建)"
if ! kubectl -n "$NS" get secret superdl-registry-pull > /dev/null 2>&1; then
  # 项目 public 时缺它只是 kubelet 告警;private 项目缺它则新 Pod 一律 ImagePullBackOff
  echo "::warning::Secret ${NS}/superdl-registry-pull 不存在:Harbor 平台项目为 private 时新 Pod 将拉不到镜像" >&2
fi

echo "==> 1/4 迁移 Job(expand-only,先于滚动)"
render < "${K8S_DIR}/10-migrate-job.yaml" | kubectl create -f -
if ! kubectl -n "$NS" wait --for=condition=complete --timeout=300s "job/superdl-migrate-${TAG}"; then
  echo "::error::迁移 Job 未成功,终止发布;日志:" >&2
  kubectl -n "$NS" logs "job/superdl-migrate-${TAG}" --tail=100 >&2 || true
  exit 1
fi

echo "==> 2/4 set image + apply(清单占位 CHANGE_IMAGE_PREFIX → ${IMAGE_PREFIX},CHANGE_TAG → ${TAG})"
kubectl kustomize "${K8S_DIR}" | render | kubectl apply -f -

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

echo "==> 4/4 冒烟:经 Ingress 从集群外 GET /readyz(DNS/TLS/Ingress 一并验证)"
# Pod 内的 /healthz、/readyz 不再重复探测:第 3 步 rollout status 只在新 Pod 过 readinessProbe
# (/readyz,02-api.yaml)后才成功,/healthz 是它的子集。这里验证的是 Pod 之外的链路。
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
      # Pod 已 Ready 而外部不通 → DNS/TLS/Ingress;readyz 含 alembic_version 比对,
      # schema_mismatch 多为第 1 步迁移漏跑/未追平,不回滚,先查迁移 Job
      echo "::error::外部冒烟失败:GET ${API_BASE_URL%/}/readyz(查 DNS/TLS/Ingress;或 job/superdl-migrate-${TAG} 与 alembic_version)" >&2
      exit 1
    fi
    echo "外部冒烟通过:${API_BASE_URL%/}/readyz"
    ;;
esac

echo "发布完成:${TAG}(api/worker/web/admin 已滚动)"
