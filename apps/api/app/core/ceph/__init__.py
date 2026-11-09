"""External CephFS storage: settings, the admin implementation selected with the K8s backend
(real → `ceph` CLI, fake → in-memory) and the single-row storage status."""

from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ceph.base import (
    DATA_DISK_MODE,
    GIB,
    SYSTEM_DISK_MODE,
    CephAdmin,
    CephError,
    CephNotConfigured,
    CephSettings,
    GroupUsage,
    SubvolumeUsage,
    data_subvolume,
    system_subvolume,
)
from app.core.ceph.cli import CliCephAdmin
from app.core.ceph.fake import FakeCephAdmin
from app.core.config import get_settings
from app.core.platform_config import RuntimeConfig, get_runtime_config

__all__ = [
    "DATA_DISK_MODE",
    "GIB",
    "SYSTEM_DISK_MODE",
    "CephAdmin",
    "CephError",
    "CephNotConfigured",
    "CephSettings",
    "FakeCephAdmin",
    "GroupUsage",
    "SubvolumeUsage",
    "ceph_settings",
    "data_subvolume",
    "get_ceph_admin",
    "load_ceph",
    "set_ceph_admin",
    "system_subvolume",
]

_override: CephAdmin | None = None


def ceph_settings(cfg: RuntimeConfig) -> CephSettings:
    return CephSettings(
        fsid=cfg.ceph_fsid.strip(),
        mon_host=cfg.ceph_mon_host.strip(),
        fs_name=cfg.ceph_fs_name.strip(),
        subvolume_group=cfg.ceph_subvolume_group.strip(),
        pool_layout=cfg.ceph_pool_layout.strip(),
        namespace_isolated=cfg.ceph_namespace_isolated,
        admin_id=cfg.ceph_admin_id.strip(),
        admin_key=cfg.ceph_admin_key.strip(),
        auth_prefix=cfg.ceph_auth_prefix.strip(),
        mount_options=cfg.ceph_mount_options.strip(),
    )


@lru_cache
def _default_fake() -> FakeCephAdmin:
    return FakeCephAdmin()


def get_ceph_admin(settings: CephSettings) -> CephAdmin:
    """The real backend talks to the cluster through the CLI; the fake K8s backend pairs with the
    in-memory admin."""
    if _override is not None:
        return _override
    if get_settings().k8s_backend == "real":
        return CliCephAdmin(settings)
    return _default_fake()


def set_ceph_admin(admin: CephAdmin | None) -> None:
    """Test injection. None restores the default."""
    global _override
    _override = admin


async def load_ceph(session: AsyncSession) -> tuple[CephSettings, CephAdmin]:
    """Effective settings plus the admin bound to them (worker side only)."""
    settings = ceph_settings(await get_runtime_config(session))
    return settings, get_ceph_admin(settings)
