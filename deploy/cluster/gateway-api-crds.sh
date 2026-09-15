#!/usr/bin/env bash
set -euo pipefail

EG_VERSION="v1.9.0"
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

crd_annotation() {
  kubectl get crd "$GW_CRD" -o "go-template={{index .metadata.annotations \"$1\"}}" 2>/dev/null || true
}

echo "==> 0/2 前置:集群里现存 Gateway API CRD 的 channel"
existing_channel="$(crd_annotation "$CHANNEL_ANNOTATION")"
if [[ -z "$existing_channel" || "$existing_channel" == "<no value>" ]]; then
  echo "    集群内尚无 $GW_CRD(首装),将按 channel=$CHANNEL 装入"
elif [[ "$existing_channel" == "$CHANNEL" ]]; then
  echo "    已是 channel=$existing_channel,bundle-version=$(crd_annotation "$BUNDLE_ANNOTATION")"
else
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

installed_channel="$(crd_annotation "$CHANNEL_ANNOTATION")"
installed_bundle="$(crd_annotation "$BUNDLE_ANNOTATION")"
echo "==> 完成:$GW_CRD channel=$installed_channel bundle-version=$installed_bundle"
if [[ "$installed_channel" != "$CHANNEL" || "$installed_bundle" != "$GATEWAY_API_VERSION" ]]; then
  echo "::error::期望 channel=$CHANNEL bundle-version=$GATEWAY_API_VERSION,实际不符,先查清楚再继续 helmfile apply" >&2
  exit 1
fi
