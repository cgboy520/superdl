"""TCP relays and the SOCKS5 CONNECT proxy. Destinations are checked after resolution: forwards and
SOCKS reach remote_cidrs only (never the internet through the gateway's egress), reverse relays
reach cluster_cidrs only."""

import asyncio
import ipaddress
import socket
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from netgw_agent.spec import SOCKS_PORT, Forward, Spec

CONNECT_TIMEOUT = 10.0
HANDSHAKE_TIMEOUT = 10.0
MAX_CONNECTIONS = 1024
BUFFER = 64 * 1024

Networks = tuple[ipaddress.IPv4Network, ...]


@dataclass
class State:
    """Counters exported on /metrics; kind ∈ forward / reverse / socks."""

    tunnel_up: bool = False
    opened: Counter[str] = field(default_factory=Counter)
    active: Counter[str] = field(default_factory=Counter)
    rejected: Counter[str] = field(default_factory=Counter)
    bytes: Counter[str] = field(default_factory=Counter)
    slots: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(MAX_CONNECTIONS))


async def resolve_allowed(host: str, port: int, allowed: Networks) -> str | None:
    """First resolved IPv4 address inside `allowed`; None when nothing resolves there."""
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            host, port, family=socket.AF_INET, type=socket.SOCK_STREAM
        )
    except (OSError, UnicodeError):
        return None
    for *_, sockaddr in infos:
        ip = ipaddress.IPv4Address(sockaddr[0])
        if any(ip in n for n in allowed):
            return str(ip)
    return None


async def _pipe(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, state: State, kind: str
) -> None:
    try:
        while data := await reader.read(BUFFER):
            writer.write(data)
            await writer.drain()
            state.bytes[kind] += len(data)
        if writer.can_write_eof():
            writer.write_eof()
    except (OSError, RuntimeError):
        writer.close()


async def splice(
    a: tuple[asyncio.StreamReader, asyncio.StreamWriter],
    b: tuple[asyncio.StreamReader, asyncio.StreamWriter],
    state: State,
    kind: str,
) -> None:
    await asyncio.gather(_pipe(a[0], b[1], state, kind), _pipe(b[0], a[1], state, kind))


async def _connect(ip: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    return await asyncio.wait_for(asyncio.open_connection(ip, port), CONNECT_TIMEOUT)


Handler = Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]]


def _guarded(state: State, kind: str, body: Handler) -> Handler:
    """Connection bookkeeping shared by every listener: slot limit, counters, close on exit."""

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if state.slots.locked():
            state.rejected[kind] += 1
            writer.close()
            return
        async with state.slots:
            state.opened[kind] += 1
            state.active[kind] += 1
            try:
                await body(reader, writer)
            except (OSError, TimeoutError, asyncio.IncompleteReadError, UnicodeError):
                state.rejected[kind] += 1
            finally:
                state.active[kind] -= 1
                writer.close()

    return handle


def forward_handler(fwd: Forward, allowed: Networks, state: State, kind: str) -> Handler:
    async def body(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        ip = await resolve_allowed(fwd.host, fwd.target_port, allowed)
        if ip is None:
            state.rejected[kind] += 1
            return
        upstream = await _connect(ip, fwd.target_port)
        try:
            await splice((reader, writer), upstream, state, kind)
        finally:
            upstream[1].close()

    return _guarded(state, kind, body)


def _socks_reply(code: int) -> bytes:
    return bytes([5, code, 0, 1, 0, 0, 0, 0, 0, 0])


async def _socks_target(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> tuple[str, int] | None:
    """RFC 1928 greeting + CONNECT request; replies with an error and returns None otherwise."""
    ver, n = await reader.readexactly(2)
    methods = await reader.readexactly(n)
    if ver != 5 or 0 not in methods:
        writer.write(b"\x05\xff")
        return None
    writer.write(b"\x05\x00")
    ver, cmd, _, atyp = await reader.readexactly(4)
    if ver != 5 or cmd != 1:
        writer.write(_socks_reply(7))
        return None
    if atyp == 1:
        host = socket.inet_ntoa(await reader.readexactly(4))
    elif atyp == 3:
        length = (await reader.readexactly(1))[0]
        host = (await reader.readexactly(length)).decode("ascii")
    else:
        writer.write(_socks_reply(8))
        return None
    port = int.from_bytes(await reader.readexactly(2), "big")
    return host, port


def socks_handler(allowed: Networks, state: State) -> Handler:
    async def body(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        target = await asyncio.wait_for(_socks_target(reader, writer), HANDSHAKE_TIMEOUT)
        if target is None:
            state.rejected["socks"] += 1
            await writer.drain()
            return
        ip = await resolve_allowed(target[0], target[1], allowed)
        if ip is None:
            state.rejected["socks"] += 1
            writer.write(_socks_reply(2))
            await writer.drain()
            return
        try:
            upstream = await _connect(ip, target[1])
        except (OSError, TimeoutError):
            state.rejected["socks"] += 1
            writer.write(_socks_reply(5))
            await writer.drain()
            return
        writer.write(_socks_reply(0))
        await writer.drain()
        try:
            await splice((reader, writer), upstream, state, "socks")
        finally:
            upstream[1].close()

    return _guarded(state, "socks", body)


async def start(spec: Spec, state: State, host: str = "0.0.0.0") -> list[asyncio.Server]:
    """Bind every listener; a port already in use is a hard error (the spec reserved it)."""
    servers = [
        await asyncio.start_server(socks_handler(spec.remote_cidrs, state), host, SOCKS_PORT)
    ]
    for fwd in spec.forwards:
        h = forward_handler(fwd, spec.remote_cidrs, state, "forward")
        servers.append(await asyncio.start_server(h, host, fwd.port))
    for rev in spec.reverse:
        h = forward_handler(rev, spec.cluster_cidrs, state, "reverse")
        servers.append(await asyncio.start_server(h, host, rev.port))
    return servers
