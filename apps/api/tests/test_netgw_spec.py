# pyright: reportPrivateUsage=false
"""Platform-rendered gateway specs against the agent's own parser, and the K8s objects built from
them (offline)."""

import importlib
import json
import pathlib
import sys
from datetime import UTC, datetime
from types import ModuleType
from typing import Any

import pytest

from app.core.k8s.base import (
    INSTANCE_LABEL,
    MANAGED_LABEL,
    NETGW_LABEL,
    NETGW_NAMESPACE,
    NETGW_NODE_LABEL,
    NETGW_TENANT_LABEL,
    PRIVATE_CIDRS,
)
from app.core.k8s.netgw import (
    NETGW_CAPABILITIES,
    SPEC_HASH_ANNOTATION,
    build_netgw_deployment,
    build_netgw_netpol,
    build_netgw_services,
    build_tenant_services,
)
from app.modules.netgw import ovpn, service
from app.modules.netgw.models import NetGateway, NetGatewayForward
from app.modules.netgw.schemas import GatewayIn
from app.modules.netgw.spec import RenderInput, agent_spec, k8s_spec

AGENT_DIR = pathlib.Path(__file__).resolve().parents[3] / "deploy" / "netgw" / "agent"
UUID = "0123456789abcdef0123456789abcdef"
INSTANCE_UUID = "fedcba9876543210fedcba9876543210"
PROFILE = (
    "client\ndev tun\nproto tcp-client\nremote vpn.example.com 443\nremote 9.9.9.9 1194 udp\n"
    "<ca>\nX\n</ca>\n"
)
TUNNELS: dict[str, dict[str, Any]] = {
    "wireguard": {
        "protocol": "wireguard",
        "endpoint": "vpn.example.com:51820",
        "peer_public_key": "yAnz5TF+lXXJte14tji3zlMNq+hd2rYUIgJBgB3fBmk=",
    },
    "ipsec": {"protocol": "ipsec", "peer": "9.9.9.9", "psk": "correct horse battery staple"},
    "openvpn": {"protocol": "openvpn", "config": PROFILE, "username": "u", "password": "p"},
    "openconnect": {
        "protocol": "openconnect",
        "flavor": "fortinet",
        "server": "https://vpn.example.com:8443/remote",
        "username": "u",
        "password": "p",
    },
}


def _agent(name: str) -> ModuleType:
    if str(AGENT_DIR) not in sys.path:
        sys.path.insert(0, str(AGENT_DIR))
    return importlib.import_module(f"netgw_agent.{name}")


def _input(protocol: str) -> RenderInput:
    body = GatewayIn.model_validate(
        {
            "name": "corp",
            "remote_cidrs": ["10.1.0.0/16"],
            "local_address": "10.250.0.2" if protocol in ("wireguard", "ipsec") else None,
            "dns": {"servers": ["10.1.0.53"], "domains": ["corp.example"]},
            "tunnel": TUNNELS[protocol],
        }
    )
    config, secrets = service._config(body, None)
    if protocol == "wireguard":
        secrets["private_key"], config["tunnel"]["public_key"] = service._wireguard_keypair()
    gw = NetGateway(id=1, uuid=UUID, user_id=7, name="corp", protocol=protocol, config=config)
    now = datetime.now(UTC)
    forwards = [
        NetGatewayForward(
            id=3,
            gateway_id=1,
            user_id=7,
            kind="intranet",
            name="nas",
            port=2049,
            target_host="nas.corp.example",
            target_port=2049,
            created_at=now,
        ),
        NetGatewayForward(
            id=4,
            gateway_id=1,
            user_id=7,
            kind="instance",
            name="box",
            port=2222,
            target_port=22,
            instance_id=5,
            created_at=now,
        ),
    ]
    return RenderInput(
        gateway=gw,
        secrets=secrets,
        forwards=forwards,
        instance_uuids={5: INSTANCE_UUID},
        tenant_namespace="tenant-7",
        dns_upstream="10.43.0.10",
    )


@pytest.mark.parametrize("protocol", list(TUNNELS))
def test_agent_accepts_platform_spec(protocol: str):
    """Fails when the platform renders a spec the gateway agent refuses (the Pod would exit 2)."""
    raw = json.loads(json.dumps(agent_spec(_input(protocol))))
    parsed = _agent("spec").parse(raw)
    assert parsed.protocol == protocol
    assert [f.port for f in parsed.forwards] == [20000]
    assert parsed.reverse[0].host == f"netgw-{UUID}-4.tenant-7.svc.cluster.local."


def test_ovpn_allow_lists_identical():
    """Fails when the platform and the agent disagree on which OpenVPN directives are allowed."""
    agent = _agent("ovpn")
    assert agent.ALLOWED_DIRECTIVES == ovpn.ALLOWED_DIRECTIVES
    assert agent.INLINE_BLOCKS == ovpn.INLINE_BLOCKS
    agent_body = pathlib.Path(str(agent.__file__)).read_text().split('"""', 2)[2]
    platform_body = pathlib.Path(ovpn.__file__).read_text().split('"""', 2)[2]
    assert agent_body == platform_body


@pytest.mark.parametrize(
    ("protocol", "ports"),
    [
        ("wireguard", (("udp", 51820),)),
        ("ipsec", (("udp", 500), ("udp", 4500))),
        ("openvpn", (("tcp", 443), ("udp", 1194))),
        ("openconnect", (("tcp", 8443), ("udp", 8443))),
    ],
)
def test_egress_ports(protocol: str, ports: tuple[tuple[str, int], ...]):
    """Fails when the gateway policy would block the peer (or open more than it needs)."""
    assert k8s_spec(_input(protocol)).egress_ports == ports


class TestObjects:
    def test_deployment_hardening(self):
        """Fails when the gateway Pod gains privileges beyond NET_ADMIN / NET_BIND_SERVICE /
        MKNOD, a service-account token, a writable root, or can land on any node."""
        spec = k8s_spec(_input("wireguard"))
        dep: Any = build_netgw_deployment(spec)
        assert dep.metadata.namespace == NETGW_NAMESPACE
        assert dep.spec.replicas == 1 and dep.spec.strategy.type == "Recreate"
        pod = dep.spec.template.spec
        assert pod.automount_service_account_token is False
        assert pod.service_account_name is None
        assert pod.host_network is None and pod.host_users is None
        assert pod.dns_policy == "None" and pod.dns_config.nameservers == ["127.0.0.1"]
        assert pod.node_selector == {NETGW_NODE_LABEL: "true"}
        assert not any(v.host_path for v in pod.volumes)
        (c,) = pod.containers
        sc = c.security_context
        assert sc.capabilities.drop == ["ALL"]
        assert sorted(sc.capabilities.add) == sorted(NETGW_CAPABILITIES)
        assert sc.allow_privilege_escalation is False
        assert sc.read_only_root_filesystem is True
        assert sc.privileged is None
        assert sc.seccomp_profile.type == "RuntimeDefault"
        labels = dep.spec.template.metadata.labels
        assert labels[NETGW_LABEL] == UUID and labels[NETGW_TENANT_LABEL] == "tenant-7"
        assert MANAGED_LABEL not in labels

    def test_spec_change_rolls_the_pod(self):
        a = k8s_spec(_input("ipsec"))
        b = k8s_spec(_input("wireguard"))
        dep_a: Any = build_netgw_deployment(a)
        dep_b: Any = build_netgw_deployment(b)
        ann_a = dep_a.spec.template.metadata.annotations
        ann_b = dep_b.spec.template.metadata.annotations
        assert ann_a[SPEC_HASH_ANNOTATION] != ann_b[SPEC_HASH_ANNOTATION]
        assert ann_a["kubernetes.io/egress-bandwidth"] == "100M"

    def test_network_policy(self):
        """Fails when another tenant can reach the gateway or the gateway can reach private
        ranges / other ports than the peer's."""
        pol: Any = build_netgw_netpol(k8s_spec(_input("ipsec")), except_cidrs=PRIVATE_CIDRS).spec
        assert pol.pod_selector.match_labels == {NETGW_LABEL: UUID}
        (tenant_in,) = pol.ingress
        assert tenant_in._from[0].namespace_selector.match_labels == {
            "kubernetes.io/metadata.name": "tenant-7"
        }
        assert tenant_in._from[0].pod_selector is None
        dns, peer, tenant_out = pol.egress
        assert {(p.protocol, p.port) for p in dns.ports} == {("UDP", 53), ("TCP", 53)}
        assert peer.to[0].ip_block.cidr == "0.0.0.0/0"
        assert set(peer.to[0].ip_block._except) == set(PRIVATE_CIDRS)
        assert {(p.protocol, p.port) for p in peer.ports} == {("UDP", 500), ("UDP", 4500)}
        assert tenant_out.to[0].namespace_selector.match_labels == {
            "kubernetes.io/metadata.name": "tenant-7"
        }

    def test_services(self):
        """Fails when the names instances use stop pointing at the gateway, or an instance
        forward selects the wrong Pod."""
        spec = k8s_spec(_input("wireguard"))
        gw_svcs: list[Any] = build_netgw_services(spec)
        assert [
            (s.metadata.name, s.spec.ports[0].port, s.spec.ports[0].target_port) for s in gw_svcs
        ] == [
            (f"netgw-{UUID}", 1080, 1080),
            (f"netgw-{UUID}-3", 2049, 20000),
        ]
        tenant: list[Any] = build_tenant_services(spec)
        names = {s.metadata.name: s for s in tenant}
        assert names["corp"].spec.type == "ExternalName"
        assert (
            names["corp"].spec.external_name == f"netgw-{UUID}.{NETGW_NAMESPACE}.svc.cluster.local"
        )
        assert (
            names["nas"].spec.external_name == f"netgw-{UUID}-3.{NETGW_NAMESPACE}.svc.cluster.local"
        )
        box = names[f"netgw-{UUID}-4"]
        assert box.spec.selector == {INSTANCE_LABEL: INSTANCE_UUID}
        assert box.spec.ports[0].port == 22
        for svc in gw_svcs + tenant:
            assert MANAGED_LABEL not in (svc.metadata.labels or {})
            assert svc.metadata.labels[NETGW_LABEL] == UUID
