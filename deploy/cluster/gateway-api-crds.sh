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
    echo "usage: $0 [--dry-run]" >&2
    exit 2
    ;;
esac

for bin in helm kubectl; do
  command -v "$bin" >/dev/null 2>&1 || {
    echo "::error::missing $bin (this script is a helm template | kubectl apply pipeline, both are required)" >&2
    exit 2
  }
done

crd_annotation() {
  kubectl get crd "$GW_CRD" -o "go-template={{index .metadata.annotations \"$1\"}}" 2>/dev/null || true
}

echo "==> 0/2 prerequisite: channel of the Gateway API CRDs already in the cluster"
existing_channel="$(crd_annotation "$CHANNEL_ANNOTATION")"
if [[ -z "$existing_channel" || "$existing_channel" == "<no value>" ]]; then
  echo "    no $GW_CRD in the cluster yet (first install), installing with channel=$CHANNEL"
elif [[ "$existing_channel" == "$CHANNEL" ]]; then
  echo "    already channel=$existing_channel, bundle-version=$(crd_annotation "$BUNDLE_ANNOTATION")"
else
  echo "::error::$GW_CRD in the cluster is channel=$existing_channel, this script installs $CHANNEL." >&2
  echo "         the safe-upgrades ValidatingAdmissionPolicy refuses standard→experimental; it cannot be switched back." >&2
  echo "         the only way out is deleting every Gateway API CRD and reinstalling, and deleting the CRDs removes every" >&2
  echo "         Gateway/HTTPRoute in the cluster (the three platform domains + every tenant Jupyter entry). Assess first and handle by hand." >&2
  exit 1
fi

echo "==> 1/2 render the CRD manifests (chart $CRDS_CHART $EG_VERSION, channel=$CHANNEL)"
render() {
  helm template eg-crds "$CRDS_CHART" --version "$EG_VERSION" \
    --set crds.gatewayAPI.enabled=true \
    --set "crds.gatewayAPI.channel=$CHANNEL" \
    --set crds.envoyGateway.enabled=true
}

if [[ "$dry_run" -eq 1 ]]; then
  echo "==> 2/2 apply --server-side --dry-run=server (validation only, nothing written)"
  render | kubectl apply --server-side --force-conflicts --dry-run=server -f -
  exit 0
fi

echo "==> 2/2 apply --server-side --force-conflicts"
render | kubectl apply --server-side --force-conflicts -f -

installed_channel="$(crd_annotation "$CHANNEL_ANNOTATION")"
installed_bundle="$(crd_annotation "$BUNDLE_ANNOTATION")"
echo "==> done: $GW_CRD channel=$installed_channel bundle-version=$installed_bundle"
if [[ "$installed_channel" != "$CHANNEL" || "$installed_bundle" != "$GATEWAY_API_VERSION" ]]; then
  echo "::error::expected channel=$CHANNEL bundle-version=$GATEWAY_API_VERSION, the installed values differ; investigate before continuing with helmfile apply" >&2
  exit 1
fi
