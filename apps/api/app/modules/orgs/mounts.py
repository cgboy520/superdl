"""Organization storage mounts: a directory of the platform's Ceph cluster, reached with the
organization's own cephx key, mounted at /mnt/org/<name> in every member's instances from their
next start. Owners and admins manage them; members see name, mount point and access.

The key is checked in user space (libcephfs) by tenant-mgr before any instance mounts it; only
ready and enabled mounts reach a Pod. Volume objects are per (mount revision, member namespace)
and labelled, so a change, a removed member, a deleted mount or a dissolved organization is a
label sweep; claims still mounted finish deleting when their Pod is gone."""

import re
from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ceph import CephError, load_ceph
from app.core.config import get_settings
from app.core.crypto import decrypt_str, encrypt_str
from app.core.errors import conflict, not_found
from app.core.k8s import get_orchestrator
from app.core.logging import get_logger
from app.core.outbox import OutboxTask, enqueue, outbox_handler
from app.core.platform_config import get_runtime_config
from app.core.servercopy import copy as server_copy
from app.core.timeutil import now_utc
from app.modules.notify import service as notify_service
from app.modules.orgs.access import require_org_role
from app.modules.orgs.models import (
    MOUNT_CHECKING,
    MOUNT_FAILED,
    MOUNT_READY,
    ORG_ACTIVE,
    ROLE_ADMIN,
    ROLE_OWNER,
    Organization,
    OrgMember,
    OrgMount,
)
from app.modules.orgs.schemas import OrgMountCreate, OrgMountOut, OrgMountUpdate

logger = get_logger(__name__)

MOUNT_ROOT = "/mnt/org"
MOUNT_CHECK_TASK = "orgs.mount_check"
MOUNT_SWEEP_TASK = "orgs.mount_sweep"
_ADMINS = (ROLE_OWNER, ROLE_ADMIN)
_ERROR_MAX = 500


@dataclass(frozen=True)
class OrgMountRuntime:
    """What a member's Pod needs to mount one organization directory (key decrypted)."""

    uuid: str
    revision: int
    ceph_path: str
    ceph_user: str
    ceph_key: str
    mount_path: str
    read_only: bool


def mount_path_of(name: str) -> str:
    return f"{MOUNT_ROOT}/{name}"


def _aad(uuid: str) -> str:
    return f"org-mount:{uuid}"


def _namespace(user_id: int) -> str:
    return f"{get_settings().k8s_namespace_prefix}{user_id}"


def _out(m: OrgMount, *, admin: bool) -> OrgMountOut:
    return OrgMountOut(
        uuid=m.uuid,
        name=m.name,
        mount_path=mount_path_of(m.name),
        read_only=m.read_only,
        enabled=m.enabled,
        status=m.status,  # type: ignore[arg-type]
        last_error=m.last_error if admin else None,
        checked_at=m.checked_at,
        ceph_path=m.ceph_path if admin else None,
        ceph_user=m.ceph_user if admin else None,
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


async def _membership(session: AsyncSession, user_id: int) -> tuple[Organization, OrgMember]:
    member = await session.scalar(select(OrgMember).where(OrgMember.user_id == user_id))
    org = await session.get(Organization, member.org_id) if member else None
    if member is None or org is None or org.status != ORG_ACTIVE:
        raise not_found(key="orgs.notMember")
    return org, member


async def _admin_of(session: AsyncSession, user_id: int) -> Organization:
    """The caller's organization, locked, when the caller is its owner or an admin."""
    org, _member = await require_org_role(session, user_id, _ADMINS, lock=True)
    return org


async def _mount_in(session: AsyncSession, org_id: int, uuid: str) -> OrgMount:
    mount = await session.scalar(
        select(OrgMount).where(OrgMount.org_id == org_id, OrgMount.uuid == uuid)
    )
    if mount is None:
        raise not_found(key="orgs.mountNotFound")
    return mount


async def _reject_platform_user(session: AsyncSession, ceph_user: str) -> None:
    """The organization brings its own key: ids the platform issues for disks, and the admin
    id, are refused."""
    cfg = await get_runtime_config(session)
    prefix = re.escape(cfg.ceph_auth_prefix.strip())
    issued = re.fullmatch(rf"{prefix}u\d+-(?:sys|disk)-[0-9a-f]{{32}}", ceph_user)
    if issued or (cfg.ceph_admin_id and ceph_user == cfg.ceph_admin_id.strip()):
        raise conflict(key="orgs.mountUserReserved")


def _schedule_check(session: AsyncSession, mount: OrgMount) -> None:
    mount.status = MOUNT_CHECKING
    mount.last_error = None
    enqueue(session, MOUNT_CHECK_TASK, {"mount_id": mount.id, "revision": mount.revision})


async def list_mounts(session: AsyncSession, user_id: int) -> list[OrgMountOut]:
    org, member = await _membership(session, user_id)
    rows = (
        await session.scalars(
            select(OrgMount).where(OrgMount.org_id == org.id).order_by(OrgMount.name)
        )
    ).all()
    admin = member.role in _ADMINS
    return [_out(m, admin=admin) for m in rows]


async def admin_mounts(session: AsyncSession, org_id: int) -> list[OrgMountOut]:
    """Platform admins: every mount of the tenant with its Ceph path and user (never the key)."""
    rows = (
        await session.scalars(
            select(OrgMount).where(OrgMount.org_id == org_id).order_by(OrgMount.name)
        )
    ).all()
    return [_out(m, admin=True) for m in rows]


async def create_mount(session: AsyncSession, user_id: int, body: OrgMountCreate) -> OrgMountOut:
    org = await _admin_of(session, user_id)
    count = await session.scalar(
        select(func.count()).select_from(OrgMount).where(OrgMount.org_id == org.id)
    )
    limit = get_settings().org_max_mounts
    if (count or 0) >= limit:
        raise conflict(key="orgs.mountQuota", params={"max": limit})
    if await session.scalar(
        select(OrgMount.id).where(OrgMount.org_id == org.id, OrgMount.name == body.name)
    ):
        raise conflict(key="orgs.mountNameTaken", params={"name": body.name})
    await _reject_platform_user(session, body.ceph_user)
    uuid = uuid4().hex
    mount = OrgMount(
        uuid=uuid,
        org_id=org.id,
        name=body.name,
        ceph_path=body.ceph_path,
        ceph_user=body.ceph_user,
        key_encrypted=encrypt_str(body.ceph_key, aad=_aad(uuid)),
        read_only=body.read_only,
        enabled=body.enabled,
        revision=1,
        created_by=user_id,
    )
    session.add(mount)
    await session.flush()
    _schedule_check(session, mount)
    await _notify_added(session, org, mount, exclude=user_id)
    await session.commit()
    await session.refresh(mount)
    logger.info("org_mount_created", org_uuid=org.uuid, mount_uuid=uuid)
    return _out(mount, admin=True)


async def _notify_added(
    session: AsyncSession, org: Organization, mount: OrgMount, *, exclude: int
) -> None:
    member_ids = (
        await session.scalars(select(OrgMember.user_id).where(OrgMember.org_id == org.id))
    ).all()
    for uid in member_ids:
        if uid == exclude:
            continue
        await notify_service.notify(
            session,
            uid,
            type_="org",
            title=server_copy("orgs.mount_added.title", org=org.name),
            content=server_copy(
                "orgs.mount_added.content", org=org.name, path=mount_path_of(mount.name)
            ),
        )


async def update_mount(
    session: AsyncSession, user_id: int, uuid: str, body: OrgMountUpdate
) -> OrgMountOut:
    """A new path, user, key or access bumps the revision: it is checked again, new Pods get new
    volume objects and the previous revision's objects are swept (kept while still mounted)."""
    org = await _admin_of(session, user_id)
    mount = await _mount_in(session, org.id, uuid)
    changed = False
    if body.ceph_user is not None and body.ceph_user != mount.ceph_user:
        await _reject_platform_user(session, body.ceph_user)
        mount.ceph_user = body.ceph_user
        changed = True
    if body.ceph_path is not None and body.ceph_path != mount.ceph_path:
        mount.ceph_path = body.ceph_path
        changed = True
    if body.ceph_key is not None:
        mount.key_encrypted = encrypt_str(body.ceph_key, aad=_aad(mount.uuid))
        changed = True
    if body.read_only is not None and body.read_only != mount.read_only:
        mount.read_only = body.read_only
        changed = True
    if body.enabled is not None:
        mount.enabled = body.enabled
    if changed:
        mount.revision += 1
        _schedule_check(session, mount)
        enqueue(
            session,
            MOUNT_SWEEP_TASK,
            {"mount_uuids": [mount.uuid], "keep_revision": mount.revision},
        )
    await session.commit()
    await session.refresh(mount)
    return _out(mount, admin=True)


async def recheck_mount(session: AsyncSession, user_id: int, uuid: str) -> OrgMountOut:
    org = await _admin_of(session, user_id)
    mount = await _mount_in(session, org.id, uuid)
    _schedule_check(session, mount)
    await session.commit()
    await session.refresh(mount)
    return _out(mount, admin=True)


async def delete_mount(session: AsyncSession, user_id: int, uuid: str) -> None:
    """Gone from the next start of each instance; running instances keep it until they stop."""
    org = await _admin_of(session, user_id)
    mount = await _mount_in(session, org.id, uuid)
    await session.delete(mount)
    enqueue(session, MOUNT_SWEEP_TASK, {"mount_uuids": [uuid]})
    await session.commit()
    logger.info("org_mount_deleted", org_uuid=org.uuid, mount_uuid=uuid)


async def _mount_uuids(session: AsyncSession, org_id: int) -> list[str]:
    return list(
        (await session.scalars(select(OrgMount.uuid).where(OrgMount.org_id == org_id))).all()
    )


async def on_members_removed(session: AsyncSession, org_id: int, user_ids: set[int]) -> None:
    """Removed members' namespaces lose the organization's mount objects (same transaction as the
    removal; the sweep runs on tenant-mgr)."""
    uuids = await _mount_uuids(session, org_id)
    if not uuids:
        return
    for uid in sorted(user_ids):
        enqueue(session, MOUNT_SWEEP_TASK, {"namespace": _namespace(uid), "mount_uuids": uuids})


async def on_org_dissolved(session: AsyncSession, org_id: int) -> None:
    """Every mount of a dissolved organization is deleted and swept everywhere."""
    uuids = await _mount_uuids(session, org_id)
    if not uuids:
        return
    for mount in (await session.scalars(select(OrgMount).where(OrgMount.org_id == org_id))).all():
        await session.delete(mount)
    enqueue(session, MOUNT_SWEEP_TASK, {"mount_uuids": uuids})


async def ready_mounts_for_user(session: AsyncSession, user_id: int) -> list[OrgMountRuntime]:
    """The ready, enabled mounts of the user's organization (worker side: keys decrypted)."""
    member = await session.scalar(select(OrgMember).where(OrgMember.user_id == user_id))
    if member is None:
        return []
    org = await session.get(Organization, member.org_id)
    if org is None or org.status != ORG_ACTIVE:
        return []
    rows = (
        await session.scalars(
            select(OrgMount)
            .where(
                OrgMount.org_id == org.id,
                OrgMount.enabled.is_(True),
                OrgMount.status == MOUNT_READY,
            )
            .order_by(OrgMount.name)
        )
    ).all()
    return [
        OrgMountRuntime(
            uuid=m.uuid,
            revision=m.revision,
            ceph_path=m.ceph_path,
            ceph_user=m.ceph_user,
            ceph_key=decrypt_str(m.key_encrypted, aad=_aad(m.uuid)),
            mount_path=mount_path_of(m.name),
            read_only=m.read_only,
        )
        for m in rows
    ]


@outbox_handler(MOUNT_CHECK_TASK)
async def handle_mount_check(session: AsyncSession, task: OutboxTask) -> None:
    """Mount the directory in user space with the organization's key and list it; the result
    lands only when the revision is still the one that was checked."""
    mount = await session.get(OrgMount, task.payload["mount_id"])
    if mount is None or mount.revision != task.payload["revision"]:
        return
    _settings, admin = await load_ceph(session)
    key = decrypt_str(mount.key_encrypted, aad=_aad(mount.uuid))
    error: str | None = None
    try:
        await admin.check_client_path(client_id=mount.ceph_user, key=key, path=mount.ceph_path)
    except CephError as exc:
        error = (exc.detail or str(exc))[:_ERROR_MAX]
    locked = await session.get(OrgMount, mount.id, with_for_update=True, populate_existing=True)
    if locked is None or locked.revision != task.payload["revision"]:
        return
    locked.status = MOUNT_FAILED if error else MOUNT_READY
    locked.last_error = error
    locked.checked_at = now_utc()
    logger.info("org_mount_checked", mount_uuid=locked.uuid, ok=error is None)


@outbox_handler(MOUNT_SWEEP_TASK)
async def handle_mount_sweep(_session: AsyncSession, task: OutboxTask) -> None:
    """Delete organization mount objects: per mount (everywhere, or except keep_revision) and
    optionally only in one namespace."""
    orch = get_orchestrator()
    namespace = task.payload.get("namespace")
    keep = task.payload.get("keep_revision")
    for uuid in task.payload.get("mount_uuids") or ():
        await orch.delete_org_mount_volumes(
            namespace=namespace, mount_uuid=str(uuid), keep_revision=keep
        )
