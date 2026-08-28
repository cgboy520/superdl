#!/usr/bin/env bash
# helmfile apply 的薄包装:两个开关必须每次都带,漏一个 apply 就中途失败,而两处失败的报错
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
exec env HELM_DIFF_USE_UPGRADE_DRY_RUN=true \
  helmfile -f helmfile.yaml.gotmpl -e "$env_name" apply --skip-diff-on-install "$@"
