#!/usr/bin/env bash
# SuperDL 发布流水线:准入策略断言 → 迁移 → set image+apply → rollout status → 经网关从集群外
# 冒烟,任一步失败即退。
#
# 用法: SUPERDL_IMAGE_PREFIX=harbor.<域>/superdl [SUPERDL_API_BASE_URL=https://<api-domain>] scripts/release.sh <tag>
#   tag:Harbor 已推送的发布标签(.github/workflows/release.yml 产物,形如 v1.2.3)。
#   SUPERDL_IMAGE_PREFIX:必填,Harbor 项目前缀(与 release.yml 的 HARBOR_HOST/HARBOR_PROJECT 一致),
#   替换各清单里的 CHANGE_IMAGE_PREFIX 占位。
#   SUPERDL_API_BASE_URL:可选,末步外部冒烟用的公网 API 基址;缺省读 ConfigMap
#   superdl-api-config 的 SUPERDL_PUBLIC_BASE_URL,两者都取不到(或仍是占位)则跳过该步并提示。
#
# 前置工具:kubectl + 以下任一能解析镜像 digest 的工具(需对 Harbor 有读权限,已 docker login):
#   crane / skopeo / docker buildx。三个都没有就不发布 —— 见下方 resolve_digest 的注释。
#
# 顺序铁律:迁移 Job 必须先于滚动 —— /readyz 只认 DB==代码 head,反序「新代码+旧 schema」
# 直接 503 卡死发布。停机发布模型、无兼容窗口:含迁移的发布在迁移完成到滚动完成之间,
# 旧 Pod 同样短暂 503 摘流(已知且接受)。不支持发布回滚:失败修复后重新发布(fix-forward)。
set -euo pipefail

TAG="${1:?用法: SUPERDL_IMAGE_PREFIX=harbor.<域>/superdl scripts/release.sh <tag>(形如 v1.2.3,release.yml 已推送 Harbor 的 tag)}"
IMAGE_PREFIX="${SUPERDL_IMAGE_PREFIX:?缺 SUPERDL_IMAGE_PREFIX(Harbor 项目前缀,如 harbor.example.com/superdl,与 release.yml 推送目标一致)}"
K8S_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../deploy/app/k8s" && pwd)"
NS=superdl
IMAGES=(api web admin)

# 两个入参都会进 sed 的**程序串**,校验必须在替换之前且必须是全串锚定的正则。
# 曾经这里写的是 `case "$TAG" in v[0-9]*.[0-9]*.[0-9]*)`:glob 的 `*` 匹配任意字符,
# `v0.0.0;s/x/y/e` 一样能过,而 sed 的 e 标志会把匹配结果当 shell 命令执行。
if ! printf '%s' "$TAG" | grep -Eq '^v[0-9]+\.[0-9]+\.[0-9]+$'; then
  echo "::error::tag 必须严格形如 v1.2.3(禁止 latest / 分支名 / 预发布后缀 / 任何标点):$TAG" >&2
  exit 2
fi
# 前缀同样落进 sed 程序串,且此前**完全没有校验**。只放行合法的 <host>[:port]/<path...>:
# 全小写、无空白、无 `#`(sed 分隔符)、无 `&` / `\`(sed 替换串里的元字符)。
if ! printf '%s' "$IMAGE_PREFIX" | grep -Eq '^[a-z0-9][a-z0-9.-]*(:[0-9]{1,5})?(/[a-z0-9]+([._-][a-z0-9]+)*)+$'; then
  echo "::error::SUPERDL_IMAGE_PREFIX 非法(需形如 harbor.example.com/superdl,全小写):$IMAGE_PREFIX" >&2
  exit 2
fi

# tag → 不可变 digest。**清单里不许再出现可变 tag**:Harbor 默认不开 immutable rule,
# 同名 tag 重推之后,已在跑的节点仍用旧镜像(imagePullPolicy: IfNotPresent),而新调度的
# Pod 拉到的是新内容 —— 两个版本同时在线且无任何提示。release.yml 的 cosign 也是按 digest
# 签的,按 digest 下发才让「签了名的那份」和「跑起来的那份」是同一个东西。
# (集群侧还没有验签准入控制器,那属于基础设施决策,不在本脚本范围。)
resolve_digest() { # <完整镜像引用> → sha256:...
  local ref="$1" out=""
  if command -v crane > /dev/null 2>&1; then
    out="$(crane digest "$ref" 2> /dev/null || true)"
  elif command -v skopeo > /dev/null 2>&1; then
    out="$(skopeo inspect --format '{{.Digest}}' "docker://$ref" 2> /dev/null || true)"
  elif command -v docker > /dev/null 2>&1; then
    # --raw 取顶层 manifest/index 原文,digest 即其 sha256;--format '{{.Manifest.Digest}}' 在 buildx
    # 0.2x 上对单 manifest 镜像会退回默认文本输出,解析不出 digest
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

# 清单占位单点:CHANGE_IMAGE_PREFIX(Harbor 项目前缀)与 CHANGE_TAG(发布 tag)。
# 顺序固定:先把三条镜像整串换成 @sha256,再做通用替换 —— 通用的 CHANGE_TAG 还得留给
# 10-migrate-job.yaml 的 Job 名(superdl-migrate-CHANGE_TAG),那不是镜像。
render() {
  sed \
    -e "s#CHANGE_IMAGE_PREFIX/superdl-api:CHANGE_TAG#${IMAGE_PREFIX}/superdl-api@${DIGEST[api]}#g" \
    -e "s#CHANGE_IMAGE_PREFIX/superdl-web:CHANGE_TAG#${IMAGE_PREFIX}/superdl-web@${DIGEST[web]}#g" \
    -e "s#CHANGE_IMAGE_PREFIX/superdl-admin:CHANGE_TAG#${IMAGE_PREFIX}/superdl-admin@${DIGEST[admin]}#g" \
    -e "s#CHANGE_IMAGE_PREFIX#${IMAGE_PREFIX}#g" \
    -e "s#CHANGE_TAG#${TAG}#g"
}

# 渲染后自检:占位符零残留 + 平台镜像一律带 @sha256。清单里新增一条没登记的平台镜像时,
# 上面的 render 会把它渲成可变 tag 而不报错,这道自检就是为那种情况准备的。
render_checked() {
  local out
  out="$(render)"
  if printf '%s' "$out" | grep -qE 'CHANGE_(TAG|IMAGE_PREFIX)'; then
    echo "::error::渲染后仍有 CHANGE_* 占位符残留(清单新增了未登记的占位?)" >&2
    return 1
  fi
  if printf '%s\n' "$out" | grep -E '^[[:space:]]*image:.*superdl-' | grep -qv '@sha256:'; then
    echo "::error::有平台镜像仍按可变 tag 渲染(应为 @sha256:...),拒绝下发" >&2
    return 1
  fi
  printf '%s\n' "$out"
}

echo "==> 0/5 前置:准入策略七条 VAP 必须在集群里且为 Deny"
# 缺 Binding 是**静默 fail-open**(failurePolicy: Fail 只在策略被求值时生效),而
# tenant-mgr 的 pods:create 与 roles:escalate/bind 是全命名空间的 —— 没有策略①它约等于
# cluster-admin。策略由 deploy/cluster/apply.sh 下发;这里在滚动前再断言一次,
# 免得集群层与应用层各发各的、谁也不知道准入面已经没了。
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
    # Binding 在而 Policy 不在:CEL 被 apiserver 拒收,现象是「装了但全放行」
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
  # 项目 public 时缺它只是 kubelet 告警;private 项目缺它则新 Pod 一律 ImagePullBackOff
  echo "::warning::Secret ${NS}/superdl-registry-pull 不存在:Harbor 平台项目为 private 时新 Pod 将拉不到镜像" >&2
fi

echo "==> 2/5 迁移 Job(先于滚动)"
# Job 的 envFrom 引用 ConfigMap superdl-api-config:首装时它还不存在(随第 3 步的 kustomize 一起下发),
# 先单独 apply 命名空间与 ConfigMap(不含任何镜像),否则 Job Pod 停在 CreateContainerConfigError
kubectl apply -f "${K8S_DIR}/00-namespace-config.yaml"
render_checked < "${K8S_DIR}/10-migrate-job.yaml" | kubectl create -f -
if ! kubectl -n "$NS" wait --for=condition=complete --timeout=300s "job/superdl-migrate-${TAG}"; then
  echo "::error::迁移 Job 未成功,终止发布;日志:" >&2
  kubectl -n "$NS" logs "job/superdl-migrate-${TAG}" --tail=100 >&2 || true
  exit 1
fi

echo "==> 3/5 set image + apply(镜像按 digest 钉死,CHANGE_IMAGE_PREFIX → ${IMAGE_PREFIX})"
kubectl kustomize "${K8S_DIR}" | render_checked | kubectl apply -f -

echo "==> 4/5 rollout status"
# 下列 8 个 Deployment 必须全部滚动到位;superdl-worker* 是同一镜像的 5 个 worker 组件,
# 漏一个即旧代码继续领任务(任务幂等与 reaper 只兜底,不做版本收敛)
for d in superdl-api superdl-worker superdl-worker-tenant-mgr superdl-worker-node-mgr \
  superdl-worker-prewarm superdl-worker-disk-ops superdl-web superdl-admin; do
  if ! kubectl -n "$NS" rollout status "deploy/${d}" --timeout=660s; then
    echo "::error::${d} 滚动超时/失败,修复后重新发布(平台不支持发布回滚)" >&2
    exit 1
  fi
done

echo "==> 5/5 冒烟:经网关从集群外 GET /readyz(DNS/TLS/网关一并验证)"
# 不重复探测 Pod 内的 /healthz、/readyz:第 4 步 rollout status 只在新 Pod 过 readinessProbe
# (/readyz,02-api.yaml)后才成功。这里验证的是 Pod 之外的链路。
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
      # Pod 已 Ready 而外部不通 → 查 DNS/TLS/网关;readyz 含 alembic_version 比对,
      # schema_mismatch 多为第 2 步迁移漏跑/未追平,不回滚,先查迁移 Job
      echo "::error::外部冒烟失败:GET ${API_BASE_URL%/}/readyz(查 DNS/TLS/网关;或 job/superdl-migrate-${TAG} 与 alembic_version)" >&2
      exit 1
    fi
    echo "外部冒烟通过:${API_BASE_URL%/}/readyz"
    ;;
esac

echo "发布完成:${TAG}(api/worker/web/admin 已滚动)"
