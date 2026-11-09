"""CephFS administration protocol: the platform manages subvolumes of one subvolume group on an
external Ceph cluster through a restricted admin key, and every subvolume gets its own cephx key.
Business code programs against `CephAdmin` only; the CLI implementation is `cli.py`, dev and tests
use `fake.py`."""

from dataclasses import dataclass
from typing import Literal, Protocol

GIB = 1024**3

Access = Literal["r", "rw"]

# Mode of the subvolume root as created: the system disk is /root (sshd StrictModes), data disks
# mount at /root/data. uid / gid 0 = root inside the user-namespaced container (idmapped mount).
SYSTEM_DISK_MODE = "700"
DATA_DISK_MODE = "755"


@dataclass(frozen=True)
class CephSettings:
    """Effective Ceph connection settings (platform configuration `storage` group)."""

    fsid: str
    mon_host: str
    fs_name: str
    subvolume_group: str
    pool_layout: str
    namespace_isolated: bool
    admin_id: str
    admin_key: str
    auth_prefix: str
    mount_options: str

    def missing(self) -> list[str]:
        """Setting keys a real cluster needs that are still empty."""
        required = {
            "ceph_fsid": self.fsid,
            "ceph_mon_host": self.mon_host,
            "ceph_fs_name": self.fs_name,
            "ceph_subvolume_group": self.subvolume_group,
            "ceph_admin_id": self.admin_id,
            "ceph_admin_key": self.admin_key,
            "ceph_auth_prefix": self.auth_prefix,
        }
        return [key for key, value in required.items() if not value]

    def auth_id(self, subvolume: str) -> str:
        """cephx id (without `client.`) of the key that mounts one subvolume."""
        return f"{self.auth_prefix}{subvolume}"


@dataclass(frozen=True)
class SubvolumeUsage:
    bytes_used: int
    bytes_quota: int | None


@dataclass(frozen=True)
class GroupUsage:
    """bytes_quota None = the group has no quota."""

    bytes_used: int
    bytes_quota: int | None


class CephError(Exception):
    """A Ceph command failed; detail is the trimmed stderr, errno the CLI exit status."""

    def __init__(self, command: str, detail: str, *, errno: int | None = None) -> None:
        super().__init__(f"{command}: {detail}" if detail else command)
        self.command = command
        self.detail = detail
        self.errno = errno


class CephNotConfigured(CephError):
    """Required storage settings are empty."""

    def __init__(self, missing: list[str]) -> None:
        super().__init__("ceph", f"storage settings missing: {', '.join(missing)}")
        self.missing = missing


ENOENT = 2


def system_subvolume(user_id: int, instance_uuid: str) -> str:
    """Subvolume of an instance's system disk (mounted at /root)."""
    return f"u{user_id}-sys-{instance_uuid}"


def data_subvolume(user_id: int, disk_uuid: str) -> str:
    """Subvolume of a data disk (mounted at /root/data)."""
    return f"u{user_id}-disk-{disk_uuid}"


class CephAdmin(Protocol):
    """Every call is idempotent: creating an existing subvolume updates its attributes, removing
    or deauthorizing something that is gone succeeds."""

    async def create_subvolume(self, name: str, *, size_bytes: int, mode: str) -> None:
        """Create the subvolume (quota = size_bytes, owner uid/gid 0) or update an existing one."""
        ...

    async def resize_subvolume(self, name: str, *, size_bytes: int) -> None:
        """Set the subvolume quota; mounted clients see the new size at once."""
        ...

    async def authorize(self, name: str, auth_id: str, *, access: Access) -> None:
        """Grant auth_id access to exactly this subvolume (creates the key on first use)."""
        ...

    async def subvolume_path(self, name: str) -> str:
        """Absolute path of the subvolume inside the file system (the PV rootPath)."""
        ...

    async def auth_key(self, auth_id: str) -> str:
        """Secret of auth_id."""
        ...

    async def subvolume_usage(self, name: str) -> SubvolumeUsage | None:
        """Bytes used and quota; None when the subvolume does not exist."""
        ...

    async def evict(self, name: str, auth_id: str) -> None:
        """Disconnect clients of auth_id that still mount the subvolume."""
        ...

    async def deauthorize(self, name: str, auth_id: str) -> None:
        """Revoke auth_id from the subvolume; the key disappears with its last grant."""
        ...

    async def remove_subvolume(self, name: str) -> None:
        """Delete the subvolume and its data (irreversible)."""
        ...

    async def group_usage(self) -> GroupUsage:
        """Usage and quota of the whole subvolume group; also the reachability probe."""
        ...

    async def check_client_path(self, *, client_id: str, key: str, path: str) -> None:
        """Mount path with a caller-supplied key in user space and list it; raises CephError when
        the key cannot authenticate or read the path."""
        ...
