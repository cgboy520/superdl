#!/usr/bin/env bash
# Gateway API + Envoy Gateway 的 CRD 安装/升级。helmfile 的 envoy-gateway release 在 presync
# 调它;升 Envoy Gateway 版本时也要先单独跑一遍,再 ./apply.sh(chart 侧 crds.enabled=false,
# 不会替你升 CRD)。
#
# 用法:./gateway-api-crds.sh [--dry-run]   (在 deploy/cluster/ 下执行)
#   --dry-run 走 kubectl apply --dry-run=server:在 apiserver 上真校验一遍但不落盘,需要能连集群。
#
# 约束:
# - 装法必须是 `helm template | kubectl apply`:gateway-crds-helm 把 CRD 放在 templates/ 而不是
#   crds/,这是官方给出的管线。CRD 生命周期在本脚本单点管理,helmfile 侧一律 crds.enabled=false。
# - 必须 --server-side --force-conflicts:清单近 4 MB,客户端 apply 会撞上
#   last-applied-configuration 注解体积上限(报错只说 "metadata.annotations: Too long");
#   --force-conflicts 接管上次由别的客户端写下的字段所有权,否则升级时每个字段都报 conflict。
# - channel 只有一次机会:必须 experimental(BackendTrafficPolicy 的每源 IP 本地限流落在这一档)。
#   装成 standard 后换不回来——随 CRD 一起装的 safe-upgrades VAP 用 CEL 拒绝
#   standard→experimental,只能删净 CRD 重来,而删 CRD 会连带删掉集群里全部 Gateway/HTTPRoute。
#   下面的前置闸门在 channel 不符时直接停手。
set -euo pipefail

# EG_VERSION 必须与 helmfile.yaml.gotmpl 里 envoy-gateway release 的 version 一致:
# 控制面 chart 与 CRD chart 同版本发布,错版会装出控制面读不懂的 CRD。
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
# CRD 不存在时 kubectl 返回非零,吞掉——那是首装,不是错。
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
  # 必须停手:继续 apply 只会被 safe-upgrades 策略拒掉,而它的报错指向 CEL 表达式,
  # 第一眼读不出「channel 不兼容」。
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

# 装完立刻回读注解:apply 成功不等于装对了 channel,而错的 channel 要到某条 experimental
# 策略静默失效时才会暴露。
installed_channel="$(crd_annotation "$CHANNEL_ANNOTATION")"
installed_bundle="$(crd_annotation "$BUNDLE_ANNOTATION")"
echo "==> 完成:$GW_CRD channel=$installed_channel bundle-version=$installed_bundle"
if [[ "$installed_channel" != "$CHANNEL" || "$installed_bundle" != "$GATEWAY_API_VERSION" ]]; then
  echo "::error::期望 channel=$CHANNEL bundle-version=$GATEWAY_API_VERSION,实际不符,先查清楚再继续 helmfile apply" >&2
  exit 1
fi
