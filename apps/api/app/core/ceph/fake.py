"""In-memory CephAdmin for dev and tests (selected together with k8s_backend=fake).

fail_next holds method names whose next call raises once; unreadable_paths makes
check_client_path fail for those paths until cleared.
"""

import hashlib
from dataclasses import dataclass, field

from app.core.ceph.base import (
    ENOENT,
    Access,
    CephError,
    GroupUsage,
    SubvolumeUsage,
)


@dataclass
class FakeSubvolume:
    size_bytes: int
    mode: str
    path: str
    bytes_used: int = 0
    grants: dict[str, Access] = field(default_factory=dict)


@dataclass
class FakeCephAdmin:
    group: str = "cloud"
    group_quota_bytes: int | None = 100 * 1024**4
    subvolumes: dict[str, FakeSubvolume] = field(default_factory=dict)
    keys: dict[str, str] = field(default_factory=dict)
    removed: list[str] = field(default_factory=list)
    evicted: list[tuple[str, str]] = field(default_factory=list)
    fail_next: set[str] = field(default_factory=set)
    unreachable: bool = False
    unreadable_paths: set[str] = field(default_factory=set)
    checked_paths: list[tuple[str, str]] = field(default_factory=list)

    def _maybe_fail(self, method: str) -> None:
        if self.unreachable:
            raise CephError(method, "fake: cluster unreachable", errno=110)
        if method in self.fail_next:
            self.fail_next.discard(method)
            raise CephError(method, "fake: injected failure", errno=5)

    def _get(self, name: str, method: str) -> FakeSubvolume:
        sv = self.subvolumes.get(name)
        if sv is None:
            raise CephError(method, f"subvolume '{name}' does not exist", errno=ENOENT)
        return sv

    async def create_subvolume(self, name: str, *, size_bytes: int, mode: str) -> None:
        self._maybe_fail("create_subvolume")
        sv = self.subvolumes.get(name)
        if sv is None:
            digest = hashlib.sha256(name.encode()).hexdigest()
            uuid = f"{digest[:8]}-{digest[8:12]}-{digest[12:16]}-{digest[16:20]}-{digest[20:32]}"
            self.subvolumes[name] = FakeSubvolume(
                size_bytes=size_bytes, mode=mode, path=f"/volumes/{self.group}/{name}/{uuid}"
            )
            return
        sv.size_bytes = size_bytes
        sv.mode = mode

    async def resize_subvolume(self, name: str, *, size_bytes: int) -> None:
        self._maybe_fail("resize_subvolume")
        self._get(name, "resize_subvolume").size_bytes = size_bytes

    async def authorize(self, name: str, auth_id: str, *, access: Access) -> None:
        self._maybe_fail("authorize")
        self._get(name, "authorize").grants[auth_id] = access
        digest = hashlib.sha256(f"key:{auth_id}".encode()).hexdigest()
        self.keys.setdefault(auth_id, f"AQ{digest[:38]}==")

    async def subvolume_path(self, name: str) -> str:
        self._maybe_fail("subvolume_path")
        return self._get(name, "subvolume_path").path

    async def auth_key(self, auth_id: str) -> str:
        self._maybe_fail("auth_key")
        key = self.keys.get(auth_id)
        if key is None:
            raise CephError("auth print-key", f"no key for client.{auth_id}", errno=ENOENT)
        return key

    async def subvolume_usage(self, name: str) -> SubvolumeUsage | None:
        self._maybe_fail("subvolume_usage")
        sv = self.subvolumes.get(name)
        if sv is None:
            return None
        return SubvolumeUsage(bytes_used=sv.bytes_used, bytes_quota=sv.size_bytes)

    async def evict(self, name: str, auth_id: str) -> None:
        self._maybe_fail("evict")
        self.evicted.append((name, auth_id))

    async def deauthorize(self, name: str, auth_id: str) -> None:
        self._maybe_fail("deauthorize")
        sv = self.subvolumes.get(name)
        if sv is not None:
            sv.grants.pop(auth_id, None)
        if not any(auth_id in s.grants for s in self.subvolumes.values()):
            self.keys.pop(auth_id, None)

    async def remove_subvolume(self, name: str) -> None:
        self._maybe_fail("remove_subvolume")
        if self.subvolumes.pop(name, None) is not None:
            self.removed.append(name)

    async def group_usage(self) -> GroupUsage:
        self._maybe_fail("group_usage")
        used = sum(sv.bytes_used for sv in self.subvolumes.values())
        return GroupUsage(bytes_used=used, bytes_quota=self.group_quota_bytes)

    async def check_client_path(self, *, client_id: str, key: str, path: str) -> None:
        self._maybe_fail("check_client_path")
        self.checked_paths.append((client_id, path))
        if not key:
            raise CephError("cephfs path check", "mount failed: no key", errno=1)
        if path in self.unreadable_paths:
            raise CephError("cephfs path check", "permission denied", errno=13)

    def set_used(self, name: str, bytes_used: int) -> None:
        """Test injection: pretend the subvolume holds bytes_used bytes."""
        self.subvolumes[name].bytes_used = bytes_used
