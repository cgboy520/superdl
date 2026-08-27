#!/usr/bin/env bash
# Gateway API + Envoy Gateway 的 CRD 安装/升级。helmfile 的 envoy-gateway release 在 presync
# 调它;升 Envoy Gateway 版本时也要先单独跑一遍,再 ./apply.sh(chart 侧 crds.enabled=false,
# 不会替你升 CRD)。
#
# 用法:./gateway-api-crds.sh [--dry-run]   (在 deploy/cluster/ 下执行)
#   --dry-run 走 kubectl apply --dry-run=server:真在 apiserver 上校验一遍但不落盘,
#             需要能连集群(纯离线渲染没有意义——要看的正是它跟集群里现存 CRD 冲不冲突)。
#
# 为什么是 `helm template | kubectl apply` 而不是 `helm install`:
#   gateway-crds-helm 把 CRD 放在 templates/ 而不是 crds/(官方 README 明说的取舍:CRD 太大,
#   放 crds/ 会踩 helm 对该目录的已知限制),官方给出的装法就是本脚本这条管线。
#   顺带解决另一半问题:gateway-helm 内置的 CRD 子 chart 走的是 helm 的 crds/ 目录,
#   而那个目录在 `helm upgrade` 时永不更新 —— 跟着 chart 装,CRD 就永远停在首装那一版。
#   所以 CRD 的生命周期在这里单点管理,helmfile 侧一律 crds.enabled=false。
#
# 为什么必须 --server-side --force-conflicts:
#   这份清单渲染出来近 4 MB(experimental channel 的 Gateway API CRD 加 EG 自己的 CRD)。
#   客户端 apply 会把整份清单塞进 kubectl.kubernetes.io/last-applied-configuration 注解,
#   直接撞上注解体积上限而失败(报错是 "metadata.annotations: Too long",
#   完全不提 CRD 太大这回事)。
#   --force-conflicts 用来接管上一次由别的客户端/控制器写下的字段所有权,否则升级时
#   每个字段都报 conflict。
#
# channel 只有一次机会(本次迁移最容易踩死的一条):
#   CRD 一旦以 standard channel 装进集群,就再也换不成 experimental —— 随 CRD 一起装的
#   safe-upgrades ValidatingAdmissionPolicy 用 CEL 明文拒绝「standard 之上装 experimental」。
#   我们必须是 experimental(BackendTrafficPolicy 的每源 IP 本地限流等就落在这一档)。
#   装错了只能把 CRD 删净重来,而删 CRD 会连带删掉集群里全部 Gateway/HTTPRoute ——
#   平台三个域名加全部租户 Jupyter 入口一起消失。所以下面有一道前置闸门,发现 channel
#   不符时直接停手,不给「再 apply 一次试试」的机会。
set -euo pipefail

# 版本锁定:EG_VERSION 必须与 helmfile.yaml.gotmpl 里 envoy-gateway release 的 version 一致
# (控制面 chart 与 CRD chart 同版本发布,错版会装出控制面读不懂的 CRD)。
EG_VERSION="v1.9.0"
# 该 EG 版本对齐的 Gateway API 版本;只用于装完自检与人工核对(preflight.sh 也按它卡)。
GATEWAY_API_VERSION="v1.6.1"
CRDS_CHART="oci://docker.io/envoyproxy/gateway-crds-helm"
CHANNEL="experimental"

GW_CRD="gateways.gateway.networking.k8s.io"
CHANNEL_ANNOTATION="gateway.networking.k8s.io/channel"
BUNDLE_ANNOTATION="gateway.networking.k8s.io/bundle-version"

dry_run=0
case "${1:-}" in
  "") ;;
  --dry-run) dry_run=1 ;;
  *)
    echo "用法:$0 [--dry-run]" >&2
    exit 2
    ;;
esac

for bin in helm kubectl; do
  command -v "$bin" >/dev/null 2>&1 || {
    echo "::error::缺少 $bin(本脚本是 helm template | kubectl apply 的管线,两者缺一不可)" >&2
    exit 2
  }
done

# 读注解而不是 `helm list`:CRD 不属于任何 release,集群里的 channel 事实只写在注解上。
# CRD 不存在时 kubectl 返回非零,这里吞掉——那是首装,不是错。
crd_annotation() { # <注解名>
  kubectl get crd "$GW_CRD" -o "go-template={{index .metadata.annotations \"$1\"}}" 2>/dev/null || true
}

echo "==> 0/2 前置:集群里现存 Gateway API CRD 的 channel"
existing_channel="$(crd_annotation "$CHANNEL_ANNOTATION")"
if [[ -z "$existing_channel" || "$existing_channel" == "<no value>" ]]; then
  echo "    集群内尚无 $GW_CRD(首装),将按 channel=$CHANNEL 装入"
elif [[ "$existing_channel" == "$CHANNEL" ]]; then
  echo "    已是 channel=$existing_channel,bundle-version=$(crd_annotation "$BUNDLE_ANNOTATION")"
else
  # 这里停手是刻意的:继续 apply 只会被 safe-upgrades 策略拒掉,而它的报错指向 CEL 表达式,
  # 没人第一眼能读出「channel 不兼容」。
  echo "::error::集群里的 $GW_CRD 是 channel=$existing_channel,本脚本要装的是 $CHANNEL。" >&2
  echo "         safe-upgrades ValidatingAdmissionPolicy 拒绝 standard→experimental,换不回去。" >&2
  echo "         唯一出路是删净 Gateway API CRD 重装,而删 CRD 会连带删掉集群内全部" >&2
  echo "         Gateway/HTTPRoute(平台三域名 + 全部租户 Jupyter 入口)。请先评估再手工处理。" >&2
  exit 1
fi

echo "==> 1/2 渲染 CRD 清单(chart $CRDS_CHART $EG_VERSION,channel=$CHANNEL)"
render() {
  helm template eg-crds "$CRDS_CHART" --version "$EG_VERSION" \
    --set crds.gatewayAPI.enabled=true \
    --set "crds.gatewayAPI.channel=$CHANNEL" \
    --set crds.envoyGateway.enabled=true
}

if [[ "$dry_run" -eq 1 ]]; then
  echo "==> 2/2 apply --server-side --dry-run=server(只校验,不落盘)"
  render | kubectl apply --server-side --force-conflicts --dry-run=server -f -
  exit 0
fi

echo "==> 2/2 apply --server-side --force-conflicts"
render | kubectl apply --server-side --force-conflicts -f -

# 装完立刻回读注解:apply 成功不等于装对了 channel(比如有人手工改过 chart 参数),
# 而错的 channel 要到某条 experimental 策略静默失效时才会暴露。
installed_channel="$(crd_annotation "$CHANNEL_ANNOTATION")"
installed_bundle="$(crd_annotation "$BUNDLE_ANNOTATION")"
echo "==> 完成:$GW_CRD channel=$installed_channel bundle-version=$installed_bundle"
if [[ "$installed_channel" != "$CHANNEL" || "$installed_bundle" != "$GATEWAY_API_VERSION" ]]; then
  echo "::error::期望 channel=$CHANNEL bundle-version=$GATEWAY_API_VERSION,实际不符,先查清楚再继续 helmfile apply" >&2
  exit 1
fi
