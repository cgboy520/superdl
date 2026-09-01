#!/usr/bin/env bash
# 集群层 apply:准入策略(非 helm release)+ helmfile。
#
# 第一步是 admission/tenant-restrictions.yaml。它以前只写在 README 里靠人手动 apply,
# 而「Binding 不存在」是**静默 fail-open** —— failurePolicy: Fail 只在策略被求值时生效,
# 策略压根没装的集群等于全放行,tenant-mgr 的 pods:create 与 roles:escalate/bind 就是
# 全命名空间的。放进本脚本 = 每次集群层 apply 都重放一遍,不依赖谁记得。
#
# 后面 helmfile 的两个开关必须每次都带,漏一个 apply 就中途失败,而两处失败的报错
# 都不指向真正的原因。
#
#   HELM_DIFF_USE_UPGRADE_DRY_RUN=true
#     让 helm-diff 走服务端 dry-run,模板里的 lookup 才读得到。默认的客户端渲染下 lookup 恒空,
#     kata-deploy 的身份校验据此判定「无法确认上次安装的 multiInstallSuffix / deploymentMode」,
#     为防孤儿节点直接 fail —— 即便 ConfigMap 就在集群里躺着。
#
#   --skip-diff-on-install
#     gpu-operator 首装时 ClusterPolicy CRD 还不存在,服务端 dry-run 会报
#     no matches for kind "ClusterPolicy";首装本来就没有 diff 可看。
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

# 准入策略先行:cluster-scoped 对象,归集群层流水线(不进 deploy/app/k8s 的 kustomization ——
# 那是 namespace-scoped 应用清单,发布流水线不该因此需要集群级 VAP 写权限)。
# 幂等,可反复跑;失败即退(set -e),不带残缺的准入面继续装组件。
echo "==> 准入策略 admission/tenant-restrictions.yaml(七条 VAP,全部 Deny)"
kubectl apply -f admission/tenant-restrictions.yaml
# apply 成功 ≠ 生效:CEL 写错时 apiserver 只拒 Policy 而 Binding 照建,现象是「装了但全放行」。
# 这里当场回读,少一条就停。
for _b in superdl-platform-sa-scope superdl-tenant-pod-baseline superdl-node-field-scope \
  superdl-global-pod-guard superdl-platform-pod-secret-scope superdl-platform-job-secret-scope \
  superdl-node-delete-scope; do
  kubectl get validatingadmissionpolicy "$_b" > /dev/null
  kubectl get validatingadmissionpolicybinding "$_b" > /dev/null
done

exec env HELM_DIFF_USE_UPGRADE_DRY_RUN=true \
  helmfile -f helmfile.yaml.gotmpl -e "$env_name" apply --skip-diff-on-install "$@"
