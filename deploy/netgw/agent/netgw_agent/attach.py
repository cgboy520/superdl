"""Instance side of whole-subnet access: `python3 -m netgw_agent.attach [CONFIG]` (default
/etc/netgw-attach/attach.json) runs once as the platform init container of an instance Pod
(NET_ADMIN inside the Pod's user namespace) and leaves netgw0 behind: a WireGuard interface to the
gateway hub carrying the routes to remote_cidrs. The workspace container starts afterwards without
NET_ADMIN, so it can use the interface but not read its key or change its routes.
"""

import ipaddress
import json
import sys
from dataclasses import dataclass
from typing import Any

from netgw_agent import render, run
from netgw_agent.spec import (
    FORBIDDEN_NETS,
    HUB_PORT,
    MAX_CIDRS,
    SpecError,
    ipv4,
    is_host,
    networks,
    wg_key,
)

ATTACH_VERSION = 1


@dataclass(frozen=True)
class Attach:
    address: ipaddress.IPv4Address
    private_key: str
    peer_public_key: str
    routes: tuple[ipaddress.IPv4Network, ...]
    endpoint_host: str | None


def parse(raw: Any) -> Attach:
    if not isinstance(raw, dict) or raw.get("version") != ATTACH_VERSION:
        raise SpecError(f"version: expected {ATTACH_VERSION}")
    cluster = networks(raw.get("cluster_cidrs"), "cluster_cidrs", limit=4)
    routes = networks(raw.get("routes"), "routes", limit=MAX_CIDRS)
    for net in routes:
        for bad in (*FORBIDDEN_NETS, *cluster):
            if net.overlaps(bad):
                raise SpecError(f"routes: {net} overlaps {bad}")
    address = ipv4(raw.get("address"), "address")
    if any(address in n for n in (*FORBIDDEN_NETS, *cluster, *routes)):
        raise SpecError("address: inside a forbidden or routed range")
    host = raw.get("endpoint_host")
    if host is not None and (not isinstance(host, str) or not is_host(host.removesuffix("."))):
        raise SpecError("endpoint_host: not a host name")
    return Attach(
        address=address,
        private_key=wg_key(raw.get("private_key"), "private_key"),
        peer_public_key=wg_key(raw.get("peer_public_key"), "peer_public_key"),
        routes=routes,
        endpoint_host=host,
    )


def apply(a: Attach) -> str | None:
    """Recreate netgw0 from scratch; the hub endpoint is a hint (the hub also dials the instance
    by name), so an unresolvable gateway leaves it unset. Returns the endpoint used."""
    dev = render.ATTACH_DEVICE
    if run.sh(["ip", "link", "show", "dev", dev], check=False) != "":
        run.sh(["ip", "link", "delete", "dev", dev])
    run.sh(["ip", "link", "add", dev, "type", "wireguard"])
    ip = run.resolve(a.endpoint_host) if a.endpoint_host else None
    argv = [
        "wg",
        "set",
        dev,
        "private-key",
        "/dev/stdin",
        "listen-port",
        str(HUB_PORT),
        "peer",
        a.peer_public_key,
        "allowed-ips",
        ",".join(str(n) for n in a.routes),
        "persistent-keepalive",
        str(render.WG_KEEPALIVE),
    ]
    if ip:
        argv += ["endpoint", f"{ip}:{HUB_PORT}"]
    run.sh(argv, stdin=a.private_key)
    run.sh(["ip", "address", "add", f"{a.address}/32", "dev", dev])
    mtu = run.link_mtu() - render.WG_OVERHEAD
    run.sh(["ip", "link", "set", dev, "mtu", str(mtu), "up"])
    run.sh(
        ["ip", "-batch", "-"],
        stdin=render.route_batch(a.routes, dev=dev, src=str(a.address)),
    )
    return ip


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else "/etc/netgw-attach/attach.json"
    try:
        with open(path, encoding="utf-8") as f:
            attach = parse(json.load(f))
    except (OSError, ValueError, SpecError) as exc:
        print(f"netgw-attach: invalid config: {exc}", file=sys.stderr)
        return 2
    try:
        endpoint = apply(attach)
    except (OSError, run.CommandError) as exc:
        print(f"netgw-attach: {exc}", file=sys.stderr)
        return 1
    routes = ",".join(str(n) for n in attach.routes)
    print(
        f"netgw-attach: {render.ATTACH_DEVICE} {attach.address} routes {routes} "
        f"hub {endpoint or 'pending'}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
