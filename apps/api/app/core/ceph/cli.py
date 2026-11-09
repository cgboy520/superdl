"""CephAdmin over the `ceph` CLI (package ceph-common in the platform image).

Commands are exactly the forms the restricted admin key is granted for: the subvolume group,
pool layout and namespace isolation are passed on every create. Each call writes a throwaway
ceph.conf + keyring (0600) into a private temporary directory and removes it afterwards; keys
never appear on a command line.
"""

import asyncio
import contextlib
import json
import os
import tempfile
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

from app.core.ceph.base import (
    ENOENT,
    Access,
    CephError,
    CephNotConfigured,
    CephSettings,
    GroupUsage,
    SubvolumeUsage,
)
from app.core.logging import get_logger

logger = get_logger(__name__)

CEPH_BINARY = "ceph"
SYSTEM_PYTHON = "/usr/bin/python3"
PATHCHECK_SCRIPT = Path(__file__).with_name("pathcheck.py")
CONNECT_TIMEOUT_SECONDS = 15
COMMAND_TIMEOUT_SECONDS = 60.0
_DETAIL_MAX = 500


def _conf_text(settings: CephSettings, keyring: Path) -> str:
    return (
        "[global]\n"
        f"fsid = {settings.fsid}\n"
        f"mon_host = {settings.mon_host}\n"
        f"keyring = {keyring}\n"
        "log_file =\n"
        "admin_socket =\n"
    )


def _keyring_text(client_id: str, key: str) -> str:
    return f"[client.{client_id}]\n\tkey = {key}\n"


@contextlib.contextmanager
def _credentials(settings: CephSettings, client_id: str, key: str) -> Iterator[Path]:
    """Private temporary directory with ceph.conf and a 0600 keyring for one command."""
    with tempfile.TemporaryDirectory(prefix="superdl-ceph-") as raw:
        tmp = Path(raw)
        keyring = tmp / "keyring"
        keyring.touch(mode=0o600)
        keyring.write_text(_keyring_text(client_id, key))
        (tmp / "ceph.conf").write_text(_conf_text(settings, keyring))
        yield tmp


def _quota(raw: Any) -> int | None:
    """bytes_quota is an integer or the string "infinite"."""
    return raw if isinstance(raw, int) and raw > 0 else None


class CliCephAdmin:
    def __init__(
        self,
        settings: CephSettings,
        *,
        binary: str = CEPH_BINARY,
        timeout: float = COMMAND_TIMEOUT_SECONDS,
    ) -> None:
        self.settings = settings
        self.binary = binary
        self.timeout = timeout

    async def _exec(self, argv: Sequence[str], *, label: str, env_home: str) -> str:
        env = {
            "PATH": os.environ.get("PATH", "/usr/sbin:/usr/bin:/sbin:/bin"),
            "HOME": env_home,
            "LANG": "C.UTF-8",
        }
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
            raise CephError(label, f"timed out after {self.timeout:.0f}s") from None
        if proc.returncode != 0:
            detail = err.decode(errors="replace").strip()[-_DETAIL_MAX:]
            raise CephError(label, detail, errno=proc.returncode)
        return out.decode(errors="replace").strip()

    async def _ceph(self, *args: str, json_out: bool = False) -> str:
        missing = self.settings.missing()
        if missing:
            raise CephNotConfigured(missing)
        label = " ".join(args[:3])
        with _credentials(self.settings, self.settings.admin_id, self.settings.admin_key) as tmp:
            argv = [
                self.binary,
                "--conf",
                str(tmp / "ceph.conf"),
                "--name",
                f"client.{self.settings.admin_id}",
                "--connect-timeout",
                str(CONNECT_TIMEOUT_SECONDS),
                *args,
            ]
            if json_out:
                argv += ["--format", "json"]
            return await self._exec(argv, label=label, env_home=str(tmp))

    async def _ceph_tolerating_missing(self, *args: str) -> None:
        try:
            await self._ceph(*args)
        except CephError as exc:
            if exc.errno != ENOENT:
                raise

    def _group(self) -> list[str]:
        return ["--group_name", self.settings.subvolume_group]

    async def create_subvolume(self, name: str, *, size_bytes: int, mode: str) -> None:
        s = self.settings
        args = ["fs", "subvolume", "create", s.fs_name, name, *self._group()]
        if s.pool_layout:
            args += ["--pool_layout", s.pool_layout]
        if s.namespace_isolated:
            args.append("--namespace-isolated")
        args += ["--size", str(size_bytes), "--uid", "0", "--gid", "0", "--mode", mode]
        await self._ceph(*args)

    async def resize_subvolume(self, name: str, *, size_bytes: int) -> None:
        s = self.settings
        await self._ceph(
            "fs", "subvolume", "resize", s.fs_name, name, str(size_bytes), *self._group()
        )

    async def authorize(self, name: str, auth_id: str, *, access: Access) -> None:
        s = self.settings
        await self._ceph(
            "fs",
            "subvolume",
            "authorize",
            s.fs_name,
            name,
            auth_id,
            *self._group(),
            "--access_level",
            access,
        )

    async def subvolume_path(self, name: str) -> str:
        s = self.settings
        path = await self._ceph("fs", "subvolume", "getpath", s.fs_name, name, *self._group())
        if not path.startswith("/"):
            raise CephError("fs subvolume getpath", f"unexpected output {path[:80]!r}")
        return path

    async def auth_key(self, auth_id: str) -> str:
        key = await self._ceph("auth", "print-key", f"client.{auth_id}")
        if not key:
            raise CephError("auth print-key", "empty key")
        return key

    async def subvolume_usage(self, name: str) -> SubvolumeUsage | None:
        s = self.settings
        try:
            raw = await self._ceph(
                "fs", "subvolume", "info", s.fs_name, name, *self._group(), json_out=True
            )
        except CephError as exc:
            if exc.errno == ENOENT:
                return None
            raise
        info = json.loads(raw)
        return SubvolumeUsage(
            bytes_used=int(info.get("bytes_used") or 0), bytes_quota=_quota(info.get("bytes_quota"))
        )

    async def evict(self, name: str, auth_id: str) -> None:
        s = self.settings
        await self._ceph_tolerating_missing(
            "fs", "subvolume", "evict", s.fs_name, name, auth_id, *self._group()
        )

    async def deauthorize(self, name: str, auth_id: str) -> None:
        s = self.settings
        await self._ceph_tolerating_missing(
            "fs", "subvolume", "deauthorize", s.fs_name, name, auth_id, *self._group()
        )

    async def remove_subvolume(self, name: str) -> None:
        s = self.settings
        await self._ceph_tolerating_missing(
            "fs", "subvolume", "rm", s.fs_name, name, *self._group()
        )

    async def group_usage(self) -> GroupUsage:
        s = self.settings
        raw = await self._ceph(
            "fs", "subvolumegroup", "info", s.fs_name, s.subvolume_group, json_out=True
        )
        info = json.loads(raw)
        return GroupUsage(
            bytes_used=int(info.get("bytes_used") or 0), bytes_quota=_quota(info.get("bytes_quota"))
        )

    async def check_client_path(self, *, client_id: str, key: str, path: str) -> None:
        s = self.settings
        missing = [
            key
            for key, value in (
                ("ceph_fsid", s.fsid),
                ("ceph_mon_host", s.mon_host),
                ("ceph_fs_name", s.fs_name),
            )
            if not value
        ]
        if missing:
            raise CephNotConfigured(missing)
        with _credentials(s, client_id, key) as tmp:
            argv = [
                SYSTEM_PYTHON,
                "-I",
                str(PATHCHECK_SCRIPT),
                "--conf",
                str(tmp / "ceph.conf"),
                "--client",
                client_id,
                "--fs",
                s.fs_name,
                "--path",
                path,
            ]
            await self._exec(argv, label="cephfs path check", env_home=str(tmp))
