"""Runtime side: process supervision, tunnel drivers and routes. Everything that touches the system
lives here; renderers stay pure."""

import asyncio
import os
import socket
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from netgw_agent import render
from netgw_agent.spec import Spec

CHARON = "/usr/lib/strongswan/charon"
CHARON_VICI = "/run/charon.vici"
HANDSHAKE_FRESH_SECONDS = 180
ENDPOINT_REFRESH_SECONDS = 120


def log(msg: str) -> None:
    print(f"netgw: {msg}", file=sys.stderr, flush=True)


class CommandError(RuntimeError):
    pass


def sh(argv: list[str], *, stdin: str | None = None, check: bool = True) -> str:
    """Run one command; on failure the error carries argv[0] and the first stderr line only."""
    proc = subprocess.run(
        argv, input=stdin, capture_output=True, text=True, timeout=20, check=False
    )
    if check and proc.returncode != 0:
        first = (proc.stderr or proc.stdout).strip().splitlines()[:1]
        raise CommandError(f"{argv[0]} {argv[1] if len(argv) > 1 else ''}: {' '.join(first)}")
    return proc.stdout


def write_file(path: str, text: str, mode: int = 0o600) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)


def ensure_tun() -> None:
    """openvpn / openconnect need /dev/net/tun; create the node when the runtime did not."""
    if os.path.exists("/dev/net/tun"):
        return
    os.makedirs("/dev/net", exist_ok=True)
    os.mknod("/dev/net/tun", 0o600 | stat.S_IFCHR, os.makedev(10, 200))


def link_mtu(dev: str = "eth0") -> int:
    try:
        with open(f"/sys/class/net/{dev}/mtu", encoding="ascii") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return 1500


def device_addr(dev: str) -> str | None:
    out = sh(["ip", "-4", "-o", "address", "show", "dev", dev], check=False)
    for word_list in (line.split() for line in out.splitlines()):
        if "inet" in word_list:
            return word_list[word_list.index("inet") + 1].split("/")[0]
    return None


def default_route() -> tuple[str, str | None]:
    """(device, gateway) of the IPv4 default route."""
    words = sh(["ip", "-4", "route", "show", "default"]).split()
    dev = words[words.index("dev") + 1] if "dev" in words else "eth0"
    via = words[words.index("via") + 1] if "via" in words else None
    return dev, via


@dataclass
class Proc:
    name: str
    argv: list[str]
    env: dict[str, str] | None = None
    stdin: str | None = None
    after_start: Callable[[], Coroutine[Any, Any, None]] | None = None


class Supervisor:
    """Keep daemons running: restart with exponential backoff (1 s → 30 s), reset after a minute
    of uptime; output is forwarded line by line with the daemon name."""

    def __init__(self) -> None:
        self._procs: dict[str, asyncio.subprocess.Process] = {}
        self._tasks: list[asyncio.Task[None]] = []
        self._stopping = False

    def start(self, proc: Proc) -> None:
        self._tasks.append(asyncio.create_task(self._run(proc)))

    def running(self, name: str) -> bool:
        p = self._procs.get(name)
        return p is not None and p.returncode is None

    async def _pump(self, name: str, stream: asyncio.StreamReader) -> None:
        while line := await stream.readline():
            print(f"[{name}] {line.decode(errors='replace').rstrip()[:2000]}", file=sys.stderr)
        sys.stderr.flush()

    async def _run(self, proc: Proc) -> None:
        backoff = 1.0
        while not self._stopping:
            started = time.monotonic()
            env = {**os.environ, **(proc.env or {})}
            p = await asyncio.create_subprocess_exec(
                *proc.argv,
                stdin=asyncio.subprocess.PIPE
                if proc.stdin is not None
                else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
            )
            self._procs[proc.name] = p
            if proc.stdin is not None and p.stdin is not None:
                p.stdin.write(proc.stdin.encode())
                await p.stdin.drain()
                p.stdin.close()
            assert p.stdout is not None
            pump = asyncio.create_task(self._pump(proc.name, p.stdout))
            if proc.after_start is not None:
                self._tasks.append(asyncio.create_task(proc.after_start()))
            rc = await p.wait()
            await pump
            if self._stopping:
                return
            backoff = 1.0 if time.monotonic() - started > 60 else min(backoff * 2, 30.0)
            log(f"{proc.name} exited with {rc}; restarting in {backoff:.0f}s")
            await asyncio.sleep(backoff)

    async def stop(self) -> None:
        self._stopping = True
        for p in self._procs.values():
            if p.returncode is None:
                p.terminate()
        deadline = time.monotonic() + 10
        for p in self._procs.values():
            try:
                await asyncio.wait_for(p.wait(), max(0.1, deadline - time.monotonic()))
            except TimeoutError:
                p.kill()
        for t in self._tasks:
            t.cancel()


class Driver:
    """One tunnel protocol: prepare() writes configs and interfaces (retried until it succeeds),
    processes() lists daemons, healthy() decides readiness, route_target() gives (dev, src, via)."""

    def __init__(self, spec: Spec) -> None:
        self.spec = spec

    def prepare(self) -> None:
        pass

    def processes(self) -> list[Proc]:
        return []

    def healthy(self, sup: Supervisor) -> bool:
        raise NotImplementedError

    def route_target(self) -> tuple[str, str, str | None] | None:
        raise NotImplementedError

    def maintain(self, up: bool) -> None:
        pass


class WireGuard(Driver):
    conf = f"{render.RUN_DIR}/wg0.conf"

    def __init__(self, spec: Spec) -> None:
        super().__init__(spec)
        self._refreshed = time.monotonic()

    def prepare(self) -> None:
        write_file(self.conf, render.wireguard_conf(self.spec))
        dev = render.WG_DEVICE
        if sh(["ip", "link", "show", "dev", dev], check=False) == "":
            sh(["ip", "link", "add", dev, "type", "wireguard"])
        sh(["wg", "setconf", dev, self.conf])
        sh(["ip", "address", "replace", f"{self.spec.local_address}/32", "dev", dev])
        mtu = render.wireguard_mtu(self.spec, link_mtu())
        sh(["ip", "link", "set", dev, "mtu", str(mtu), "up"])

    def _handshake_age(self) -> float | None:
        out = sh(["wg", "show", render.WG_DEVICE, "latest-handshakes"], check=False).split()
        stamp = int(out[1]) if len(out) >= 2 and out[1].isdigit() else 0
        return time.time() - stamp if stamp else None

    def healthy(self, sup: Supervisor) -> bool:
        age = self._handshake_age()
        return age is not None and age < HANDSHAKE_FRESH_SECONDS

    def route_target(self) -> tuple[str, str, str | None]:
        return render.WG_DEVICE, str(self.spec.local_address), None

    def maintain(self, up: bool) -> None:
        """Re-resolve a DNS endpoint while the handshake is stale (dynamic peer addresses)."""
        if up or time.monotonic() - self._refreshed < ENDPOINT_REFRESH_SECONDS:
            return
        self._refreshed = time.monotonic()
        p = self.spec.params
        sh(
            [
                "wg",
                "set",
                render.WG_DEVICE,
                "peer",
                p["peer_public_key"],
                "endpoint",
                p["endpoint"],
            ],
            check=False,
        )


class Ipsec(Driver):
    """Policy-based IPsec: the tunnel address sits on lo, routes to remote_cidrs carry it as the
    source hint so the xfrm policies match; no xfrm interface module needed."""

    strongswan = f"{render.RUN_DIR}/strongswan.conf"
    swanctl = f"{render.RUN_DIR}/swanctl.conf"

    def prepare(self) -> None:
        write_file(self.strongswan, render.strongswan_conf(), 0o644)
        write_file(self.swanctl, render.swanctl_conf(self.spec))
        sh(["ip", "address", "replace", f"{self.spec.local_address}/32", "dev", "lo"])

    async def _load(self) -> None:
        for _ in range(60):
            if os.path.exists(CHARON_VICI):
                break
            await asyncio.sleep(0.5)
        proc = await asyncio.create_subprocess_exec(
            "swanctl",
            "--load-all",
            "--file",
            self.swanctl,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await proc.communicate()
        if proc.returncode != 0:
            log(f"swanctl --load-all failed: {out.decode(errors='replace').strip()[-300:]}")

    def processes(self) -> list[Proc]:
        return [
            Proc(
                "charon", [CHARON], env={"STRONGSWAN_CONF": self.strongswan}, after_start=self._load
            )
        ]

    def healthy(self, sup: Supervisor) -> bool:
        if not sup.running("charon"):
            return False
        out = sh(["swanctl", "--list-sas", "--ike", "netgw"], check=False)
        return "ESTABLISHED" in out and "INSTALLED" in out

    def route_target(self) -> tuple[str, str, str | None]:
        dev, via = default_route()
        return dev, str(self.spec.local_address), via


def _openvpn_state() -> str:
    """Current state word from the management socket (CONNECTED, RECONNECTING, …); '' if
    unreachable."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(2)
            s.connect(render.OVPN_MGMT)
            s.sendall(b"state\n")
            buf = b""
            while b"\nEND" not in buf and len(buf) < 65536:
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
    except OSError:
        return ""
    for line in buf.decode(errors="replace").splitlines():
        parts = line.split(",")
        if len(parts) > 1 and parts[0].isdigit():
            return parts[1]
    return ""


class OpenVpn(Driver):
    conf = f"{render.RUN_DIR}/client.ovpn"

    def prepare(self) -> None:
        ensure_tun()
        write_file(self.conf, render.openvpn_conf(self.spec))
        if self.spec.params.get("username"):
            write_file(render.OVPN_AUTH, render.openvpn_auth(self.spec))

    def processes(self) -> list[Proc]:
        return [Proc("openvpn", ["openvpn", "--config", self.conf])]

    def healthy(self, sup: Supervisor) -> bool:
        return sup.running("openvpn") and _openvpn_state() == "CONNECTED"

    def route_target(self) -> tuple[str, str, str | None] | None:
        addr = device_addr(render.TUN_DEVICE)
        return (render.TUN_DEVICE, addr, None) if addr else None


class OpenConnect(Driver):
    def prepare(self) -> None:
        ensure_tun()

    def processes(self) -> list[Proc]:
        argv = render.openconnect_argv(self.spec)
        return [Proc("openconnect", argv, stdin=self.spec.params["password"] + "\n")]

    def healthy(self, sup: Supervisor) -> bool:
        return sup.running("openconnect") and device_addr(render.TUN_DEVICE) is not None

    def route_target(self) -> tuple[str, str, str | None] | None:
        addr = device_addr(render.TUN_DEVICE)
        return (render.TUN_DEVICE, addr, None) if addr else None


DRIVERS: dict[str, type[Driver]] = {
    "wireguard": WireGuard,
    "ipsec": Ipsec,
    "openvpn": OpenVpn,
    "openconnect": OpenConnect,
}


def ensure_routes(spec: Spec, target: tuple[str, str, str | None]) -> None:
    dev, src, via = target
    sh(
        ["ip", "-batch", "-"],
        stdin=render.route_batch(spec.remote_cidrs, dev=dev, src=src, via=via),
    )


def dnsmasq_proc(spec: Spec) -> Proc:
    path = f"{render.RUN_DIR}/dnsmasq.conf"
    write_file(path, render.dnsmasq_conf(spec), 0o644)
    return Proc(
        "dnsmasq", ["dnsmasq", "--no-daemon", "--log-facility=-", f"--conf-file={path}"]
    )
