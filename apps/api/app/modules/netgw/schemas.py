"""Private-network gateway contract. Secret fields are write-only: a null secret on update keeps
the stored value; outputs only say whether one is set."""

from datetime import datetime
from ipaddress import IPv4Address
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from app.modules.netgw.validation import NAME_PATTERN

Protocol = Literal["wireguard", "ipsec", "openvpn", "openconnect"]
OpenconnectFlavor = Literal["anyconnect", "array", "f5", "fortinet", "gp", "pulse"]
IpsecProposals = Literal["default", "compatible", "modern"]
GatewayState = Literal["applying", "connected", "disconnected", "deleting"]
Name = Annotated[str, Field(min_length=1, max_length=40, pattern=NAME_PATTERN)]
Port = Annotated[int, Field(ge=1, le=65535)]


class DnsIn(BaseModel):
    """Split DNS: these domains are resolved by these servers (inside remote_cidrs) through the
    tunnel."""

    servers: list[IPv4Address] = Field(default_factory=list, max_length=3)
    domains: list[Annotated[str, Field(min_length=1, max_length=253)]] = Field(
        default_factory=list, max_length=8
    )


class WireGuardIn(BaseModel):
    protocol: Literal["wireguard"]
    endpoint: str = Field(min_length=3, max_length=260, description="Peer host:port")
    peer_public_key: str = Field(min_length=44, max_length=44)
    preshared_key: str | None = Field(default=None, min_length=44, max_length=44)
    mtu: int = Field(default=1420, ge=1280, le=1500)


class IpsecIn(BaseModel):
    protocol: Literal["ipsec"]
    version: Literal[1, 2] = 2
    peer: str = Field(min_length=1, max_length=253)
    psk: str | None = Field(default=None, min_length=16, max_length=128)
    local_id: str | None = Field(default=None, max_length=128)
    remote_id: str | None = Field(default=None, max_length=128)
    proposals: IpsecProposals = "compatible"


class OpenVpnIn(BaseModel):
    protocol: Literal["openvpn"]
    config: str | None = Field(default=None, max_length=65536, description="Client profile")
    username: str | None = Field(default=None, max_length=256)
    password: str | None = Field(default=None, max_length=256)


class OpenConnectIn(BaseModel):
    protocol: Literal["openconnect"]
    flavor: OpenconnectFlavor = "anyconnect"
    server: str = Field(min_length=9, max_length=512, description="https://host[:port][/path]")
    username: str = Field(min_length=1, max_length=256)
    password: str | None = Field(default=None, max_length=256)
    servercert: str | None = Field(default=None, max_length=140)
    authgroup: str | None = Field(default=None, max_length=128)


TunnelIn = Annotated[
    WireGuardIn | IpsecIn | OpenVpnIn | OpenConnectIn, Field(discriminator="protocol")
]


class GatewayIn(BaseModel):
    name: Name
    remote_cidrs: list[Annotated[str, Field(max_length=18)]] = Field(min_length=1, max_length=16)
    local_address: IPv4Address | None = None
    dns: DnsIn | None = None
    tunnel: TunnelIn


class WireGuardOut(BaseModel):
    protocol: Literal["wireguard"]
    endpoint: str
    peer_public_key: str
    public_key: str
    has_preshared_key: bool
    mtu: int


class IpsecOut(BaseModel):
    protocol: Literal["ipsec"]
    version: int
    peer: str
    local_id: str | None
    remote_id: str | None
    proposals: IpsecProposals


class OpenVpnOut(BaseModel):
    protocol: Literal["openvpn"]
    remotes: list[str]
    username: str | None
    has_password: bool


class OpenConnectOut(BaseModel):
    protocol: Literal["openconnect"]
    flavor: OpenconnectFlavor
    server: str
    username: str
    servercert: str | None
    authgroup: str | None


TunnelOut = Annotated[
    WireGuardOut | IpsecOut | OpenVpnOut | OpenConnectOut, Field(discriminator="protocol")
]


class IntranetForwardIn(BaseModel):
    """Instances reach `<name>:<port>`; the gateway connects to target_host:target_port."""

    kind: Literal["intranet"]
    name: Name
    port: Port
    target_host: str = Field(min_length=1, max_length=253)
    target_port: Port


class InstanceForwardIn(BaseModel):
    """The tenant network reaches `<tunnel address>:<port>`; the gateway connects to the
    instance's target_port."""

    kind: Literal["instance"]
    name: Name
    port: Port
    instance_uuid: str = Field(pattern=r"^[0-9a-f]{32}$")
    target_port: Port


ForwardIn = Annotated[IntranetForwardIn | InstanceForwardIn, Field(discriminator="kind")]


class ForwardOut(BaseModel):
    id: int
    kind: Literal["intranet", "instance"]
    name: str
    port: int
    target_host: str | None
    instance_uuid: str | None
    instance_name: str | None
    target_port: int
    available: bool = Field(description="false: the target instance is released")
    created_at: datetime


class DnsOut(BaseModel):
    servers: list[str]
    domains: list[str]


class GatewayOut(BaseModel):
    uuid: str
    name: str
    protocol: Protocol
    state: GatewayState
    tunnel_up: bool
    tunnel_changed_at: datetime | None
    checked_at: datetime | None
    remote_cidrs: list[str]
    local_address: str | None
    dns: DnsOut | None
    tunnel: TunnelOut
    socks_url: str = Field(description="SOCKS5 endpoint for the tenant's instances")
    forwards: list[ForwardOut]
    created_at: datetime
    updated_at: datetime


class AdminGatewayOut(GatewayOut):
    user_id: int
    pod_phase: str | None


class NetgwConfigOut(BaseModel):
    enabled: bool
    egress_ips: list[str]
    max_gateways: int
    max_forwards: int
    bandwidth_mbps: int
    socks_port: int


class AdminGatewayDelete(BaseModel):
    reason: str = Field(min_length=2, max_length=500)
