"""Health and metrics endpoint: GET /healthz (200 while the tunnel is up, else 503), GET /livez
(the event loop answers), GET /metrics (Prometheus text)."""

import asyncio

from netgw_agent.relay import State
from netgw_agent.spec import HEALTH_PORT

KINDS = ("forward", "reverse", "socks")


def metrics_text(state: State) -> str:
    lines = [
        "# HELP netgw_tunnel_up Whether the tunnel to the tenant network is established.",
        "# TYPE netgw_tunnel_up gauge",
        f"netgw_tunnel_up {int(state.tunnel_up)}",
    ]
    for name, kind, counter in (
        ("netgw_connections_total", "counter", state.opened),
        ("netgw_connections_active", "gauge", state.active),
        ("netgw_rejected_total", "counter", state.rejected),
        ("netgw_relay_bytes_total", "counter", state.bytes),
    ):
        lines.append(f"# TYPE {name} {kind}")
        lines.extend(f'{name}{{kind="{k}"}} {counter[k]}' for k in KINDS)
    return "\n".join(lines) + "\n"


def _response(code: int, body: str, ctype: str = "text/plain; charset=utf-8") -> bytes:
    reason = {200: "OK", 404: "Not Found", 503: "Service Unavailable"}[code]
    raw = body.encode()
    head = (
        f"HTTP/1.1 {code} {reason}\r\nContent-Type: {ctype}\r\n"
        f"Content-Length: {len(raw)}\r\nConnection: close\r\n\r\n"
    )
    return head.encode() + raw


def route(state: State, path: str) -> bytes:
    if path == "/healthz":
        return _response(200, "up\n") if state.tunnel_up else _response(503, "down\n")
    if path == "/livez":
        return _response(200, "ok\n")
    if path == "/metrics":
        return _response(200, metrics_text(state), "text/plain; version=0.0.4")
    return _response(404, "not found\n")


async def start(state: State, host: str = "0.0.0.0") -> asyncio.Server:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
            parts = head.split(b"\r\n", 1)[0].decode("latin-1").split()
            path = parts[1].split("?", 1)[0] if len(parts) >= 2 else "/"
            writer.write(route(state, path))
            await writer.drain()
        except (OSError, TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            pass
        finally:
            writer.close()

    return await asyncio.start_server(handle, host, HEALTH_PORT)
