"""Shared spec fixtures; importing this module puts the agent package on sys.path."""

import copy
import pathlib
import sys
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "agent"))

KEY_A = "GRPm58dm4hvTzL4+0ObdCFEcRrY1fIduzhiIVgkymU8="
KEY_B = "yAnz5TF+lXXJte14tji3zlMNq+hd2rYUIgJBgB3fBmk="
KEY_C = "8QbOyu9lRhGy7bxJuK4h+2NkOvqlk6Hdz8ai3D93fFk="
UUID = "0123456789abcdef0123456789abcdef"

PROFILE = """\
client
dev tun
proto udp
remote vpn.example.com 1194
resolv-retry infinite
nobind
remote-cert-tls server
auth-user-pass
<ca>
-----BEGIN CERTIFICATE-----
MIIB
-----END CERTIFICATE-----
</ca>
"""

PARAMS: dict[str, dict[str, Any]] = {
    "wireguard": {
        "private_key": KEY_A,
        "peer_public_key": KEY_B,
        "preshared_key": None,
        "endpoint": "vpn.example.com:51820",
        "mtu": 1420,
    },
    "ipsec": {
        "version": 2,
        "peer": "203.0.113.5",
        "psk": "correct horse battery staple",
        "local_id": "gw.example.com",
        "remote_id": None,
        "proposals": "compatible",
    },
    "openvpn": {"config": PROFILE, "username": "alice", "password": "s3cret"},
    "openconnect": {
        "protocol": "anyconnect",
        "server": "https://vpn.example.com",
        "username": "alice",
        "password": "s3cret",
        "servercert": None,
        "authgroup": None,
    },
}


def raw_spec(protocol: str = "wireguard", **overrides: Any) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "version": 1,
        "gateway": UUID,
        "protocol": protocol,
        "remote_cidrs": ["10.1.0.0/16", "192.168.10.0/24"],
        "cluster_cidrs": ["10.42.0.0/16", "10.43.0.0/16"],
        "local_address": "10.250.0.2" if protocol in ("wireguard", "ipsec") else None,
        "dns": {"upstream": "10.43.0.10", "servers": ["10.1.0.53"], "domains": ["corp.example"]},
        "forwards": [{"port": 20000, "host": "nas.corp.example", "target_port": 2049}],
        "reverse": [
            {
                "port": 2222,
                "host": "netgw-0123456789abcdef0123456789abcdef-1.tenant-7.svc.cluster.local",
                "target_port": 22,
            }
        ],
        protocol: copy.deepcopy(PARAMS[protocol]),
    }
    spec.update(overrides)
    return spec


def l3(nat: bool = False) -> dict[str, Any]:
    return {
        "cidr": "192.168.250.0/24",
        "address": "192.168.250.1",
        "nat": nat,
        "private_key": KEY_C,
    }


def peer(n: int = 2, key: str = KEY_B) -> dict[str, Any]:
    return {
        "public_key": key,
        "address": f"192.168.250.{n}",
        "host": f"{n:032x}.netgw-{UUID}-l3.tenant-7.svc.cluster.local.",
    }


def raw_attach(**overrides: Any) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "version": 1,
        "address": "192.168.250.2",
        "private_key": KEY_A,
        "peer_public_key": KEY_C,
        "routes": ["10.1.0.0/16", "192.168.10.0/24"],
        "cluster_cidrs": ["10.42.0.0/16", "10.43.0.0/16"],
        "endpoint_host": f"netgw-{UUID}-hub.superdl-netgw.svc.cluster.local.",
    }
    raw.update(overrides)
    return raw
