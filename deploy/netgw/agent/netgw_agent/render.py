"""Pure renderers: spec → daemon config text / argv. Nothing here touches the system."""

import ipaddress

from netgw_agent import ovpn
from netgw_agent.spec import Spec

RUN_DIR = "/run/netgw"
WG_DEVICE = "wg0"
TUN_DEVICE = "tun0"
OVPN_MGMT = f"{RUN_DIR}/openvpn.sock"
OVPN_AUTH = f"{RUN_DIR}/openvpn.auth"
OC_SCRIPT = "/usr/libexec/netgw/oc-script"
WG_KEEPALIVE = 25

_PROPOSALS = {
    "compatible": (
        "aes256-sha256-modp2048,aes128-sha256-modp2048,aes256-sha1-modp2048,"
        "aes256-sha1-modp1024,aes128-sha1-modp1024",
        "aes256-sha256-modp2048,aes256-sha256,aes128-sha256,aes256-sha1,aes128-sha1",
    ),
    "modern": (
        "aes256gcm16-prfsha384-ecp384,aes256gcm16-prfsha256-ecp256,aes256-sha256-ecp256",
        "aes256gcm16-ecp384,aes256gcm16,aes256-sha256",
    ),
}


def wireguard_conf(spec: Spec) -> str:
    """`wg setconf` format (not wg-quick: no Address / PostUp, those stay with the agent)."""
    p = spec.params
    lines = [
        "[Interface]",
        f"PrivateKey = {p['private_key']}",
        "",
        "[Peer]",
        f"PublicKey = {p['peer_public_key']}",
    ]
    if p.get("preshared_key"):
        lines.append(f"PresharedKey = {p['preshared_key']}")
    lines += [
        f"Endpoint = {p['endpoint']}",
        f"AllowedIPs = {', '.join(str(n) for n in spec.remote_cidrs)}",
        f"PersistentKeepalive = {WG_KEEPALIVE}",
    ]
    return "\n".join(lines) + "\n"


def wireguard_mtu(spec: Spec, link_mtu: int) -> int:
    """Requested MTU capped by the pod link MTU minus the WireGuard overhead (80 bytes)."""
    return max(1280, min(int(spec.params.get("mtu", 1420)), link_mtu - 80))


def strongswan_conf() -> str:
    """charon logs to stderr, stays root (no uid / gid change: the container has no SETUID /
    SETGID / CHOWN), leaves routes, virtual IPs and resolv.conf to the agent."""
    return (
        "charon {\n"
        "  user = root\n"
        "  group = root\n"
        "  install_routes = no\n"
        "  install_virtual_ip = no\n"
        "  port = 500\n"
        "  port_nat_t = 4500\n"
        "  filelog {\n"
        "    stderr {\n"
        "      default = 1\n"
        "      time_format = %Y-%m-%dT%H:%M:%S\n"
        "    }\n"
        "  }\n"
        "  plugins {\n"
        "    resolve {\n"
        "      load = no\n"
        "    }\n"
        "  }\n"
        "}\n"
    )


def swanctl_conf(spec: Spec) -> str:
    """One policy-based connection: local_address/32 ↔ remote_cidrs, always UDP-encapsulated (the
    pod is behind the node SNAT), initiated and re-established by us."""
    p = spec.params
    version = int(p["version"])
    remote_ts = ",".join(str(n) for n in spec.remote_cidrs)
    preset = p.get("proposals", "compatible")
    lines = [
        "connections {",
        "  netgw {",
        f"    version = {version}",
        f"    remote_addrs = {p['peer']}",
        "    encap = yes",
        "    keyingtries = 0",
        "    dpd_delay = 30s",
    ]
    if version == 2:
        lines.append("    mobike = no")
    if preset in _PROPOSALS:
        lines.append(f"    proposals = {_PROPOSALS[preset][0]}")
    lines += [
        "    local {",
        "      auth = psk",
        *([f"      id = {p['local_id']}"] if p.get("local_id") else []),
        "    }",
        "    remote {",
        "      auth = psk",
        f"      id = {p.get('remote_id') or '%any'}",
        "    }",
        "    children {",
        "      netgw {",
        f"        local_ts = {spec.local_address}/32",
        f"        remote_ts = {remote_ts}",
        *([f"        esp_proposals = {_PROPOSALS[preset][1]}"] if preset in _PROPOSALS else []),
        "        start_action = start",
        "        dpd_action = restart",
        "        close_action = start",
        "      }",
        "    }",
        "  }",
        "}",
        "secrets {",
        "  ike-netgw {",
        f'    secret = "{p["psk"]}"',
        "  }",
        "}",
    ]
    return "\n".join(lines) + "\n"


def openvpn_conf(spec: Spec) -> str:
    """Tenant profile (allow-listed) + agent section: fixed device, no pushed routes / DNS /
    redirect-gateway, no scripts, management socket for health."""
    p = spec.params
    tail = [
        "# netgw agent",
        "client",
        f"dev {TUN_DEVICE}",
        "dev-type tun",
        "route-nopull",
        "route-noexec",
        'pull-filter ignore "redirect-gateway"',
        'pull-filter ignore "dhcp-option"',
        'pull-filter ignore "block-outside-dns"',
        "script-security 1",
        f"management {OVPN_MGMT} unix",
        "connect-retry 5 30",
        "ping 10",
        "ping-restart 60",
        "auth-nocache",
    ]
    if p.get("username"):
        tail.append(f"auth-user-pass {OVPN_AUTH}")
    return ovpn.render(p["config"]) + "\n".join(tail) + "\n"


def openvpn_auth(spec: Spec) -> str:
    return f"{spec.params['username']}\n{spec.params.get('password') or ''}\n"


def openconnect_argv(spec: Spec) -> list[str]:
    """Password goes on stdin; the script only brings the device up, routes stay with the agent."""
    p = spec.params
    argv = [
        "openconnect",
        f"--protocol={p['protocol']}",
        f"--user={p['username']}",
        "--passwd-on-stdin",
        "--non-inter",
        f"--interface={TUN_DEVICE}",
        f"--script={OC_SCRIPT}",
        "--reconnect-timeout=60",
    ]
    if p.get("servercert"):
        argv.append(f"--servercert={p['servercert']}")
    if p.get("authgroup"):
        argv.append(f"--authgroup={p['authgroup']}")
    argv.append(p["server"])
    return argv


def dnsmasq_conf(spec: Spec) -> str:
    """Resolver on 127.0.0.1: tenant domains go to the tenant servers (through the tunnel),
    everything else to the cluster DNS."""
    lines = [
        "no-resolv",
        "no-hosts",
        "listen-address=127.0.0.1",
        "bind-interfaces",
        "port=53",
        "pid-file=",
        "cache-size=1000",
        f"server={spec.dns_upstream}",
    ]
    for domain in spec.dns_domains:
        lines += [f"server=/{domain}/{server}" for server in spec.dns_servers]
    return "\n".join(lines) + "\n"


def route_batch(
    networks: tuple[ipaddress.IPv4Network, ...], *, dev: str, src: str, via: str | None = None
) -> str:
    """`ip -batch -` input: one idempotent route per remote network, with the tunnel source hint."""
    gw = f" via {via}" if via else ""
    return "".join(f"route replace {n}{gw} dev {dev} src {src}\n" for n in networks)
