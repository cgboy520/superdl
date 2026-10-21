"""OpenVPN client profile allow-list: tenant profiles keep only directives that cannot run commands,
read local files or touch routing; the agent appends its own routing and management directives.

The platform keeps an identical copy (apps/api/app/modules/netgw/ovpn.py); a test compares them.
"""

import shlex

ALLOWED_DIRECTIVES = frozenset(
    {
        "allow-compression",
        "auth",
        "auth-nocache",
        "auth-retry",
        "auth-user-pass",
        "cipher",
        "client",
        "comp-lzo",
        "compress",
        "connect-retry",
        "connect-retry-max",
        "connect-timeout",
        "data-ciphers",
        "data-ciphers-fallback",
        "dev",
        "dev-type",
        "explicit-exit-notify",
        "float",
        "fragment",
        "hand-window",
        "keepalive",
        "key-direction",
        "link-mtu",
        "mssfix",
        "mute",
        "mute-replay-warnings",
        "nobind",
        "ns-cert-type",
        "persist-key",
        "persist-tun",
        "ping",
        "ping-restart",
        "port",
        "proto",
        "pull",
        "rcvbuf",
        "remote",
        "remote-cert-eku",
        "remote-cert-ku",
        "remote-cert-tls",
        "remote-random",
        "reneg-bytes",
        "reneg-pkts",
        "reneg-sec",
        "resolv-retry",
        "server-poll-timeout",
        "sndbuf",
        "tls-cipher",
        "tls-ciphersuites",
        "tls-client",
        "tls-version-max",
        "tls-version-min",
        "topology",
        "tran-window",
        "tun-mtu",
        "verb",
        "verify-x509-name",
    }
)
INLINE_BLOCKS = frozenset(
    {"ca", "cert", "extra-certs", "key", "pkcs12", "tls-auth", "tls-crypt", "tls-crypt-v2"}
)
_PROTOS = frozenset({"udp", "udp4", "tcp", "tcp4", "tcp-client", "tcp4-client"})
MAX_PROFILE_BYTES = 64 * 1024
MAX_REMOTES = 8


class ProfileError(ValueError):
    """The profile uses a directive or value outside the allow-list."""


def _check_args(name: str, args: list[str]) -> None:
    if name == "dev" and args != ["tun"]:
        raise ProfileError("dev must be tun")
    if name == "dev-type" and args != ["tun"]:
        raise ProfileError("dev-type must be tun")
    if name == "proto" and (len(args) != 1 or args[0] not in _PROTOS):
        raise ProfileError("proto must be udp or tcp")
    if name == "auth-user-pass" and args:
        raise ProfileError("auth-user-pass takes no file; credentials are entered separately")
    if name == "remote" and not 1 <= len(args) <= 3:
        raise ProfileError("remote takes host [port] [proto]")
    if name == "remote" and len(args) == 3 and args[2] not in _PROTOS:
        raise ProfileError("remote proto must be udp or tcp")
    if name in ("port", "remote") and len(args) >= (2 if name == "remote" else 1):
        port = args[1] if name == "remote" else args[0]
        if not port.isdigit() or not 1 <= int(port) <= 65535:
            raise ProfileError(f"{name} port must be 1-65535")


def _split(line: str, lineno: int) -> list[str]:
    try:
        return shlex.split(line, comments=False, posix=True)
    except ValueError as exc:
        raise ProfileError(f"line {lineno}: {exc}") from None


def _read_block(lines: list[str], i: int, tag: str) -> tuple[str, int]:
    """Body of the inline block opened on line i-1; returns (body, index after </tag>)."""
    body: list[str] = []
    while i < len(lines) and lines[i].strip() != f"</{tag}>":
        if lines[i].strip().startswith("<"):
            raise ProfileError(f"line {i + 1}: unterminated inline block <{tag}>")
        body.append(lines[i].rstrip())
        i += 1
    if i >= len(lines):
        raise ProfileError(f"inline block <{tag}> is not closed")
    return "\n".join(body).strip("\n"), i + 1


def parse(profile: str) -> tuple[list[tuple[str, list[str]]], dict[str, str]]:
    """Returns (directives in order, inline blocks); raises ProfileError on anything outside the
    allow-list."""
    if len(profile.encode()) > MAX_PROFILE_BYTES:
        raise ProfileError("profile too large")
    if "\x00" in profile:
        raise ProfileError("profile contains NUL")
    directives: list[tuple[str, list[str]]] = []
    blocks: dict[str, str] = {}
    lines = profile.replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines):
        raw = lines[i].strip()
        i += 1
        if not raw or raw[0] in "#;":
            continue
        if raw.startswith("<") and raw.endswith(">") and not raw.startswith("</"):
            tag = raw[1:-1]
            if tag not in INLINE_BLOCKS:
                raise ProfileError(f"line {i}: inline block <{tag}> is not allowed")
            if tag in blocks:
                raise ProfileError(f"line {i}: duplicate inline block <{tag}>")
            blocks[tag], i = _read_block(lines, i, tag)
            continue
        parts = _split(raw, i)
        name, args = parts[0].lower(), parts[1:]
        if name not in ALLOWED_DIRECTIVES:
            raise ProfileError(f"line {i}: directive {parts[0]!r} is not allowed")
        _check_args(name, args)
        directives.append((name, args))
    remotes = [a for n, a in directives if n == "remote"]
    if not remotes:
        raise ProfileError("profile has no remote")
    if len(remotes) > MAX_REMOTES:
        raise ProfileError(f"at most {MAX_REMOTES} remote lines")
    return directives, blocks


def remotes(profile: str) -> list[tuple[str, int, str]]:
    """(host, port, proto) of every remote line, with the profile-level port / proto as defaults."""
    directives, _ = parse(profile)
    port, proto = 1194, "udp"
    for name, args in directives:
        if name == "port":
            port = int(args[0])
        elif name == "proto":
            proto = args[0]
    out: list[tuple[str, int, str]] = []
    for name, args in directives:
        if name != "remote":
            continue
        out.append(
            (
                args[0],
                int(args[1]) if len(args) > 1 else port,
                (args[2] if len(args) > 2 else proto).removesuffix("-client").removesuffix("4"),
            )
        )
    return out


def render(profile: str) -> str:
    """Re-serialise the accepted directives and inline blocks; the agent appends its own section."""
    directives, blocks = parse(profile)
    out = [" ".join([name, *(shlex.quote(a) for a in args)]) for name, args in directives]
    for tag, body in blocks.items():
        out.extend([f"<{tag}>", body, f"</{tag}>"])
    return "\n".join(out) + "\n"
