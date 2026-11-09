"""CephFS volumes of instances and data disks: every system disk and every data disk is one
subvolume of the external cluster with its own cephx key, bound into the owner's tenant namespace
as a static PV + PVC. Worker side only (tenant-mgr runs every volume task);
the request path reads the stored path and usage, never Ceph.

Provisioning order: subvolume (quota) → key grant → path → K8s objects → the row remembers the
path. Release order: K8s objects → evict → revoke → remove; every step is idempotent so the outbox
can replay from any point."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ceph import (
    DATA_DISK_MODE,
    GIB,
    SYSTEM_DISK_MODE,
    CephAdmin,
    CephSettings,
    data_subvolume,
    load_ceph,
    system_subvolume,
)
from app.core.config import get_settings
from app.core.k8s import get_orchestrator
from app.core.k8s.base import (
    CEPH_VOLUME_LABEL,
    ORG_MOUNT_LABEL,
    ORG_MOUNT_REV_LABEL,
    CephVolumeSpec,
    PodVolumeMount,
    ceph_pv_name,
    ceph_secret_name,
    data_disk_pvc_name,
    instance_disk_pvc_name,
    org_mount_claim_name,
    org_mount_pv_name,
)
from app.core.logging import get_logger
from app.modules.orchestrator.models import DataDisk, Instance
from app.modules.orgs import service as orgs_service

logger = get_logger(__name__)


def tenant_namespace(user_id: int) -> str:
    return f"{get_settings().k8s_namespace_prefix}{user_id}"


def _volume_spec(
    settings: CephSettings,
    *,
    namespace: str,
    pvc_name: str,
    subvolume: str,
    root_path: str,
    client_key: str,
    size_gb: int,
) -> CephVolumeSpec:
    return CephVolumeSpec(
        namespace=namespace,
        pvc_name=pvc_name,
        pv_name=ceph_pv_name(subvolume),
        secret_name=ceph_secret_name(subvolume),
        cluster_id=settings.fsid,
        fs_name=settings.fs_name,
        root_path=root_path,
        client_id=settings.auth_id(subvolume),
        client_key=client_key,
        size_gb=max(size_gb, 1),
        mount_options=settings.mount_options,
        labels={CEPH_VOLUME_LABEL: subvolume},
    )


async def _provision_subvolume(
    settings: CephSettings, admin: CephAdmin, subvolume: str, *, size_gb: int, mode: str
) -> tuple[str, str]:
    """Create (or update) the subvolume and its key grant; returns (path, key)."""
    auth_id = settings.auth_id(subvolume)
    await admin.create_subvolume(subvolume, size_bytes=size_gb * GIB, mode=mode)
    await admin.authorize(subvolume, auth_id, access="rw")
    path = await admin.subvolume_path(subvolume)
    key = await admin.auth_key(auth_id)
    return path, key


async def _remove_subvolume(settings: CephSettings, admin: CephAdmin, subvolume: str) -> None:
    auth_id = settings.auth_id(subvolume)
    await admin.evict(subvolume, auth_id)
    await admin.deauthorize(subvolume, auth_id)
    await admin.remove_subvolume(subvolume)


def system_volume_name(instance: Instance) -> str:
    return system_subvolume(instance.user_id, instance.uuid)


def data_volume_name(disk: DataDisk) -> str:
    return data_subvolume(disk.user_id, disk.uuid)


async def ensure_system_volume(session: AsyncSession, instance: Instance) -> None:
    """Before every Pod creation: the subvolume exists (created at the SKU's disk size on first
    use, or again after a reclamation of a failed first boot) and its objects are bound into the
    instance's namespace. Re-running only re-reads the key and re-applies the objects."""
    settings, admin = await load_ceph(session)
    subvolume = system_volume_name(instance)
    size_gb = int(instance.spec["disk_gb"])
    if instance.system_disk_path is None:
        path, key = await _provision_subvolume(
            settings, admin, subvolume, size_gb=size_gb, mode=SYSTEM_DISK_MODE
        )
        instance.system_disk_path = path
        logger.info("system_volume_provisioned", instance_id=instance.id, size_gb=size_gb)
    else:
        path = instance.system_disk_path
        key = await admin.auth_key(settings.auth_id(subvolume))
    await get_orchestrator().ensure_ceph_volume(
        _volume_spec(
            settings,
            namespace=instance.k8s_namespace,
            pvc_name=instance_disk_pvc_name(instance.uuid),
            subvolume=subvolume,
            root_path=path,
            client_key=key,
            size_gb=size_gb,
        )
    )


async def _unbind(namespace: str, pvc_name: str, subvolume: str) -> None:
    """Delete the volume's objects; raises while a Pod still holds the PVC (pvc-protection), so
    the subvolume is only removed once nothing mounts it."""
    orch = get_orchestrator()
    await orch.delete_ceph_volume(
        namespace,
        pvc_name=pvc_name,
        pv_name=ceph_pv_name(subvolume),
        secret_name=ceph_secret_name(subvolume),
    )
    if await orch.pvc_exists(namespace, pvc_name):
        raise RuntimeError(
            f"pvc {namespace}/{pvc_name} still mounted; removal resumes when its Pod is gone"
        )


async def release_system_volume(session: AsyncSession, instance: Instance) -> None:
    """Destroy the system disk (instance terminated or a failed first boot); the caller has
    confirmed the Pod is gone."""
    settings, admin = await load_ceph(session)
    subvolume = system_volume_name(instance)
    await _unbind(instance.k8s_namespace, instance_disk_pvc_name(instance.uuid), subvolume)
    await _remove_subvolume(settings, admin, subvolume)
    instance.system_disk_path = None
    instance.system_disk_bytes_used = None
    logger.info("system_volume_removed", instance_id=instance.id)


async def ensure_data_volume(session: AsyncSession, disk: DataDisk) -> None:
    """Create the data disk's subvolume and objects, or apply a grown size to its quota (the
    subvolume quota is the hard limit; the static PV / PVC sizes stay nominal)."""
    settings, admin = await load_ceph(session)
    subvolume = data_volume_name(disk)
    if disk.ceph_path is not None:
        await admin.resize_subvolume(subvolume, size_bytes=disk.size_gb * GIB)
        return
    path, key = await _provision_subvolume(
        settings, admin, subvolume, size_gb=disk.size_gb, mode=DATA_DISK_MODE
    )
    await get_orchestrator().ensure_ceph_volume(
        _volume_spec(
            settings,
            namespace=tenant_namespace(disk.user_id),
            pvc_name=data_disk_pvc_name(disk.uuid),
            subvolume=subvolume,
            root_path=path,
            client_key=key,
            size_gb=disk.size_gb,
        )
    )
    disk.ceph_path = path


async def release_data_volume(session: AsyncSession, disk: DataDisk) -> None:
    """Destroy the data disk: objects first, then the subvolume and its data, never under a
    running Pod."""
    settings, admin = await load_ceph(session)
    subvolume = data_volume_name(disk)
    await _unbind(tenant_namespace(disk.user_id), data_disk_pvc_name(disk.uuid), subvolume)
    await _remove_subvolume(settings, admin, subvolume)
    disk.ceph_path = None


async def org_storage_mounts(
    session: AsyncSession, instance: Instance
) -> tuple[PodVolumeMount, ...]:
    """The owner's organization storage (ready and enabled mounts): volume objects of the current
    revision are bound into the instance's namespace with the organization's key."""
    runtimes = await orgs_service.ready_mounts_for_user(session, instance.user_id)
    if not runtimes:
        return ()
    settings, _admin = await load_ceph(session)
    orch = get_orchestrator()
    out: list[PodVolumeMount] = []
    for m in runtimes:
        claim = org_mount_claim_name(m.uuid, m.revision)
        await orch.ensure_ceph_volume(
            CephVolumeSpec(
                namespace=instance.k8s_namespace,
                pvc_name=claim,
                pv_name=org_mount_pv_name(m.uuid, m.revision, instance.k8s_namespace),
                secret_name=claim,
                cluster_id=settings.fsid,
                fs_name=settings.fs_name,
                root_path=m.ceph_path,
                client_id=m.ceph_user,
                client_key=m.ceph_key,
                size_gb=1,
                read_only=m.read_only,
                mount_options=settings.mount_options,
                labels={ORG_MOUNT_LABEL: m.uuid, ORG_MOUNT_REV_LABEL: str(m.revision)},
            )
        )
        out.append(PodVolumeMount(pvc_name=claim, mount_path=m.mount_path, read_only=m.read_only))
    return tuple(out)
