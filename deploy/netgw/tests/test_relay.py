import asyncio
import ipaddress
import socket
import unittest

import fixtures  # noqa: F401  (sys.path)
from netgw_agent import relay, status
from netgw_agent.spec import Forward

LOOPBACK = (ipaddress.IPv4Network("127.0.0.0/8"),)
ELSEWHERE = (ipaddress.IPv4Network("10.99.0.0/16"),)


async def _echo_server() -> tuple[asyncio.Server, int]:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        while data := await reader.read(1024):
            writer.write(data)
            await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


async def _listen(handler: relay.Handler) -> tuple[asyncio.Server, int]:
    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


class RelayTest(unittest.IsolatedAsyncioTestCase):
    async def test_forward_relays_inside_allowed_networks(self) -> None:
        """Fails when an intranet forward does not carry bytes both ways."""
        echo, port = await _echo_server()
        state = relay.State()
        server, lport = await _listen(
            relay.forward_handler(Forward(0, "127.0.0.1", port), LOOPBACK, state, "forward")
        )
        reader, writer = await asyncio.open_connection("127.0.0.1", lport)
        writer.write(b"ping")
        await writer.drain()
        self.assertEqual(await reader.readexactly(4), b"ping")
        writer.close()
        await asyncio.sleep(0.05)
        self.assertEqual(state.opened["forward"], 1)
        self.assertGreaterEqual(state.bytes["forward"], 8)
        server.close()
        echo.close()

    async def test_forward_refuses_destination_outside_allowed(self) -> None:
        """Fails when a forward could reach an address outside remote_cidrs (e.g. the internet
        through the gateway's own egress)."""
        echo, port = await _echo_server()
        state = relay.State()
        server, lport = await _listen(
            relay.forward_handler(Forward(0, "127.0.0.1", port), ELSEWHERE, state, "forward")
        )
        reader, writer = await asyncio.open_connection("127.0.0.1", lport)
        self.assertEqual(await reader.read(10), b"")
        writer.close()
        self.assertEqual(state.rejected["forward"], 1)
        server.close()
        echo.close()

    async def _socks(
        self, allowed: relay.Networks, host: bytes, port: int
    ) -> tuple[int, relay.State]:
        state = relay.State()
        server, lport = await _listen(relay.socks_handler(allowed, state))
        reader, writer = await asyncio.open_connection("127.0.0.1", lport)
        writer.write(b"\x05\x01\x00")
        self.assertEqual(await reader.readexactly(2), b"\x05\x00")
        addr = (
            b"\x01" + socket.inet_aton(host.decode())
            if host[0:1].isdigit()
            else b"\x03" + bytes([len(host)]) + host
        )
        writer.write(b"\x05\x01\x00" + addr + port.to_bytes(2, "big"))
        await writer.drain()
        reply = await reader.readexactly(10)
        if reply[1] == 0:
            writer.write(b"hello")
            await writer.drain()
            self.assertEqual(await reader.readexactly(5), b"hello")
        writer.close()
        server.close()
        return reply[1], state

    async def test_socks_connect_inside_allowed(self) -> None:
        """Fails when SOCKS5 CONNECT to an intranet address does not work."""
        echo, port = await _echo_server()
        code, _ = await self._socks(LOOPBACK, b"127.0.0.1", port)
        self.assertEqual(code, 0)
        echo.close()

    async def test_socks_domain_resolution(self) -> None:
        echo, port = await _echo_server()
        code, _ = await self._socks(LOOPBACK, b"localhost", port)
        self.assertEqual(code, 0)
        echo.close()

    async def test_socks_refuses_outside_allowed(self) -> None:
        """Fails when SOCKS can be used as a general egress proxy."""
        echo, port = await _echo_server()
        code, state = await self._socks(ELSEWHERE, b"127.0.0.1", port)
        self.assertEqual(code, 2)
        self.assertEqual(state.rejected["socks"], 1)
        echo.close()

    async def test_socks_rejects_bind_and_udp(self) -> None:
        state = relay.State()
        server, lport = await _listen(relay.socks_handler(LOOPBACK, state))
        reader, writer = await asyncio.open_connection("127.0.0.1", lport)
        writer.write(b"\x05\x01\x00")
        await reader.readexactly(2)
        writer.write(b"\x05\x03\x00\x01\x7f\x00\x00\x01\x00\x50")
        await writer.drain()
        self.assertEqual((await reader.readexactly(10))[1], 7)
        writer.close()
        server.close()


class StatusTest(unittest.TestCase):
    def test_healthz_follows_tunnel(self) -> None:
        """Fails when readiness would not track the tunnel (the platform reads Pod readiness)."""
        state = relay.State()
        self.assertTrue(status.route(state, "/healthz").startswith(b"HTTP/1.1 503"))
        state.tunnel_up = True
        self.assertTrue(status.route(state, "/healthz").startswith(b"HTTP/1.1 200"))
        self.assertTrue(status.route(state, "/livez").startswith(b"HTTP/1.1 200"))

    def test_metrics_text(self) -> None:
        state = relay.State(tunnel_up=True)
        state.opened["socks"] += 2
        text = status.metrics_text(state)
        self.assertIn("netgw_tunnel_up 1", text)
        self.assertIn('netgw_connections_total{kind="socks"} 2', text)


if __name__ == "__main__":
    unittest.main()
