#!/usr/bin/env bash
# Create a platform volume on the external Ceph cluster and the Kubernetes objects that mount it:
# a subvolume in the platform's subvolume group, a cephx key authorized for that subvolume only,
# the node-stage Secret and a static PV pre-bound to <namespace>/<name> (the PVC itself ships with
# the manifests). Run where the ceph CLI reaches the cluster with the restricted admin key, e.g.
#   CEPH_ARGS="-n client.cloud-admin" ./static-volume.sh --name superdl-uploads --size-gib 1024 \
#     --namespace superdl --uid 10001 --gid 10001 --fsid <fsid> | kubectl apply -f -
# Settings mirror the storage group of the platform configuration (file system, subvolume group,
# data pool, isolated namespace, key prefix, mount options). Prints YAML; the key is never logged.
set -euo pipefail

NAME="" NAMESPACE="superdl" SIZE_GIB="" UID_="0" GID_="0" MODE="700" FSID=""
FS_NAME="${CEPH_FS_NAME:-cephfs}"
GROUP="${CEPH_SUBVOLUME_GROUP:-cloud}"
POOL_LAYOUT="${CEPH_POOL_LAYOUT:-}"
NAMESPACE_ISOLATED="${CEPH_NAMESPACE_ISOLATED:-true}"
AUTH_PREFIX="${CEPH_AUTH_PREFIX:-cloud.}"
MOUNT_OPTIONS="${CEPH_MOUNT_OPTIONS:-ms_mode=prefer-crc}"

usage() {
  echo "usage: $0 --name <pvc name> --size-gib <n> --fsid <cluster fsid> [--namespace superdl] [--uid 0] [--gid 0] [--mode 700]" >&2
  echo "  environment: CEPH_FS_NAME CEPH_SUBVOLUME_GROUP CEPH_POOL_LAYOUT CEPH_NAMESPACE_ISOLATED CEPH_AUTH_PREFIX CEPH_MOUNT_OPTIONS CEPH_ARGS" >&2
  exit 2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --name) NAME="$2"; shift 2 ;;
    --namespace) NAMESPACE="$2"; shift 2 ;;
    --size-gib) SIZE_GIB="$2"; shift 2 ;;
    --uid) UID_="$2"; shift 2 ;;
    --gid) GID_="$2"; shift 2 ;;
    --mode) MODE="$2"; shift 2 ;;
    --fsid) FSID="$2"; shift 2 ;;
    *) usage ;;
  esac
done
[[ -n "$NAME" && -n "$SIZE_GIB" && -n "$FSID" ]] || usage
[[ "$NAME" =~ ^[a-z0-9][a-z0-9-]{0,52}$ ]] || { echo "name: lowercase letters, digits and dashes" >&2; exit 2; }
[[ "$SIZE_GIB" =~ ^[0-9]+$ ]] || { echo "size-gib: a whole number" >&2; exit 2; }

subvolume="platform-${NAME}"
auth_id="${AUTH_PREFIX}${subvolume}"
create=(fs subvolume create "$FS_NAME" "$subvolume" --group_name "$GROUP"
  --size "$((SIZE_GIB * 1024 * 1024 * 1024))" --uid "$UID_" --gid "$GID_" --mode "$MODE")
[[ -n "$POOL_LAYOUT" ]] && create+=(--pool_layout "$POOL_LAYOUT")
[[ "$NAMESPACE_ISOLATED" == "true" ]] && create+=(--namespace-isolated)

ceph "${create[@]}" >&2
ceph fs subvolume authorize "$FS_NAME" "$subvolume" "$auth_id" --group_name "$GROUP" --access_level rw >&2
root_path="$(ceph fs subvolume getpath "$FS_NAME" "$subvolume" --group_name "$GROUP")"
key="$(ceph auth print-key "client.${auth_id}")"

cat <<YAML
apiVersion: v1
kind: Secret
metadata:
  name: ceph-${NAME}
  namespace: ${NAMESPACE}
type: Opaque
stringData:
  userID: "${auth_id}"
  userKey: "${key}"
---
apiVersion: v1
kind: PersistentVolume
metadata:
  name: ${NAME}
spec:
  accessModes: ["ReadWriteMany"]
  capacity: {storage: ${SIZE_GIB}Gi}
  persistentVolumeReclaimPolicy: Retain
  storageClassName: ""
  volumeMode: Filesystem
  claimRef: {namespace: ${NAMESPACE}, name: ${NAME}}
  csi:
    driver: cephfs.csi.ceph.com
    volumeHandle: ${NAME}
    nodeStageSecretRef: {name: ceph-${NAME}, namespace: ${NAMESPACE}}
    volumeAttributes:
      clusterID: "${FSID}"
      fsName: "${FS_NAME}"
      staticVolume: "true"
      rootPath: "${root_path}"
      mounter: kernel
      kernelMountOptions: "${MOUNT_OPTIONS}"
YAML
