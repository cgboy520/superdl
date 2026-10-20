"""Private-network gateways: contract, validation, apply / delete through the outbox, patrol."""

import json
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select, update

from app.core.config import get_settings
from app.core.k8s.base import NETGW_NAMESPACE, NetgwSpec
from app.modules.account.models import User
from app.modules.netgw.models import NetGateway
from app.modules.netgw.patrol import netgw_patrol
from app.modules.orchestrator.models import Instance
from tests.helpers import admin_headers, drain, seed_instance, user_headers_with_id

pytestmark = pytest.mark.usefixtures("fake")

IMAGE = "registry.example.com/superdl/superdl-netgw@sha256:" + "a" * 64
PEER_KEY = "yAnz5TF+lXXJte14tji3zlMNq+hd2rYUIgJBgB3fBmk="
PROFILE = (
    "client\ndev tun\nproto udp\nremote vpn.example.com 1194\nnobind\n"
    "<ca>\n-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n</ca>\n"
)


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(get_settings(), "netgw_image", IMAGE)
    monkeypatch.setattr(get_settings(), "netgw_egress_ips", "203.0.113.7")


def wg_body(**over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "corp",
        "remote_cidrs": ["10.1.0.0/16"],
        "local_address": "10.250.0.2",
        "dns": {"servers": ["10.1.0.53"], "domains": ["corp.example"]},
        "tunnel": {
            "protocol": "wireguard",
            "endpoint": "vpn.example.com:51820",
            "peer_public_key": PEER_KEY,
        },
    }
    body.update(over)
    return body


TUNNELS: dict[str, dict[str, Any]] = {
    "ipsec": {
        "protocol": "ipsec",
        "peer": "9.9.9.9",
        "psk": "correct horse battery staple",
        "local_id": "gw.example.com",
    },
    "openvpn": {"protocol": "openvpn", "config": PROFILE, "username": "alice", "password": "pw1"},
    "openconnect": {
        "protocol": "openconnect",
        "server": "https://vpn.example.com",
        "username": "alice",
        "password": "pw2",
    },
}


def body_for(protocol: str) -> dict[str, Any]:
    if protocol == "wireguard":
        return wg_body()
    local = "10.250.0.2" if protocol == "ipsec" else None
    return wg_body(tunnel=TUNNELS[protocol], local_address=local)


async def _user(client, n: int = 1) -> tuple[dict[str, str], int]:
    return await user_headers_with_id(client, f"netgw{n}@test.local")


async def _create(client, headers, body=None) -> dict[str, Any]:
    resp = await client.post("/api/v1/netgw/gateways", json=body or wg_body(), headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _agent_spec(fake, uuid: str) -> dict[str, Any]:
    spec: NetgwSpec = fake.netgw[uuid]
    return json.loads(spec.spec_json)


class TestAvailability:
    async def test_disabled_without_image(self, client):
        """Fails when gateways could be created on a platform that has no gateway image."""
        headers, _ = await _user(client)
        cfg = (await client.get("/api/v1/netgw/config", headers=headers)).json()
        assert cfg["enabled"] is False
        resp = await client.post("/api/v1/netgw/gateways", json=wg_body(), headers=headers)
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "netgw.unavailable"

    @pytest.mark.usefixtures("enabled")
    async def test_config_shows_egress_and_limits(self, client):
        headers, _ = await _user(client)
        cfg = (await client.get("/api/v1/netgw/config", headers=headers)).json()
        assert cfg["enabled"] is True
        assert cfg["egress_ips"] == ["203.0.113.7"]
        assert cfg["socks_port"] == 1080


@pytest.mark.usefixtures("enabled")
class TestLifecycle:
    async def test_create_apply_connect_delete(self, client, sm, fake):
        """Fails when the create → apply → ready → delete chain breaks anywhere."""
        headers, user_id = await _user(client)
        gw = await _create(client, headers)
        assert gw["state"] == "applying"
        assert len(gw["tunnel"]["public_key"]) == 44
        assert gw["socks_url"] == "socks5h://corp:1080"

        await drain(sm)
        spec = fake.netgw[gw["uuid"]]
        assert spec.tenant_namespace == f"tenant-{user_id}"
        assert spec.image == IMAGE
        assert spec.socks_name == "corp"
        assert spec.egress_ports == (("udp", 51820),)
        agent = _agent_spec(fake, gw["uuid"])
        assert agent["wireguard"]["private_key"]
        assert agent["dns"] == {
            "upstream": "10.43.0.10",
            "servers": ["10.1.0.53"],
            "domains": ["corp.example"],
        }
        assert agent["cluster_cidrs"] == ["10.42.0.0/16", "10.43.0.0/16"]
        assert f"tenant-{user_id}" in fake.namespaces
        assert NETGW_NAMESPACE not in fake.netgw_deleted

        detail = (await client.get(f"/api/v1/netgw/gateways/{gw['uuid']}", headers=headers)).json()
        assert detail["state"] == "disconnected"

        fake.set_netgw_ready(gw["uuid"], True)
        await netgw_patrol(sm)
        detail = (await client.get(f"/api/v1/netgw/gateways/{gw['uuid']}", headers=headers)).json()
        assert detail["state"] == "connected"
        assert detail["tunnel_up"] is True

        resp = await client.delete(f"/api/v1/netgw/gateways/{gw['uuid']}", headers=headers)
        assert resp.status_code == 202
        assert resp.json()["state"] == "deleting"
        await drain(sm)
        assert fake.netgw_deleted[-1] == (gw["uuid"], f"tenant-{user_id}")
        resp = await client.get(f"/api/v1/netgw/gateways/{gw['uuid']}", headers=headers)
        assert resp.status_code == 404

    @pytest.mark.parametrize("protocol", ["wireguard", "ipsec", "openvpn", "openconnect"])
    async def test_secrets_never_returned(self, client, sm, protocol):
        """Fails when a key, PSK, password or the OpenVPN profile leaks into a response or the
        stored config column."""
        headers, _ = await _user(client)
        gw = await _create(client, headers, body_for(protocol))
        listing = (await client.get("/api/v1/netgw/gateways", headers=headers)).text
        for secret in ("correct horse", "pw1", "pw2", "BEGIN CERTIFICATE", "private_key"):
            assert secret not in json.dumps(gw)
            assert secret not in listing
        async with sm() as session:
            row = await session.scalar(select(NetGateway).where(NetGateway.uuid == gw["uuid"]))
            assert row is not None
            assert "pw1" not in json.dumps(row.config) and "pw2" not in json.dumps(row.config)
            assert "correct horse" not in row.secrets_encrypted

    async def test_update_keeps_null_secrets_and_fixes_protocol(self, client, sm, fake):
        """Fails when an edit without the PSK wipes it, or a protocol switch is accepted."""
        headers, _ = await _user(client)
        gw = await _create(client, headers, body_for("ipsec"))
        body = body_for("ipsec")
        body["tunnel"] = {**body["tunnel"], "psk": None, "remote_id": "peer.example"}
        resp = await client.put(f"/api/v1/netgw/gateways/{gw['uuid']}", json=body, headers=headers)
        assert resp.status_code == 200, resp.text
        await drain(sm)
        agent = _agent_spec(fake, gw["uuid"])
        assert agent["ipsec"]["psk"] == "correct horse battery staple"
        assert agent["ipsec"]["remote_id"] == "peer.example"

        resp = await client.put(
            f"/api/v1/netgw/gateways/{gw['uuid']}", json=wg_body(), headers=headers
        )
        assert resp.json()["message_key"] == "netgw.protocolImmutable"

    async def test_idempotent_create(self, client):
        headers, _ = await _user(client)
        h = {**headers, "Idempotency-Key": "netgw-1"}
        first = await client.post("/api/v1/netgw/gateways", json=wg_body(), headers=h)
        second = await client.post("/api/v1/netgw/gateways", json=wg_body(), headers=h)
        assert first.status_code == 201
        assert second.status_code == 200
        assert second.headers.get("X-Idempotent-Replay") == "true"
        assert second.json()["uuid"] == first.json()["uuid"]

    async def test_owner_isolation(self, client):
        """Fails when another tenant can read or change a gateway."""
        headers, _ = await _user(client, 1)
        other, _ = await _user(client, 2)
        gw = await _create(client, headers)
        url = f"/api/v1/netgw/gateways/{gw['uuid']}"
        assert (await client.get(url, headers=other)).status_code == 404
        assert (await client.put(url, json=wg_body(), headers=other)).status_code == 404
        assert (await client.delete(url, headers=other)).status_code == 404
        assert (await client.get(f"{url}/logs", headers=other)).status_code == 404
        assert (await client.get("/api/v1/netgw/gateways", headers=other)).json() == []


@pytest.mark.usefixtures("enabled")
class TestValidation:
    @pytest.mark.parametrize(
        ("over", "key"),
        [
            ({"remote_cidrs": ["10.42.0.0/24"]}, "netgw.cidrForbidden"),
            ({"remote_cidrs": ["10.43.0.0/16"]}, "netgw.cidrForbidden"),
            ({"remote_cidrs": ["169.254.0.0/16"]}, "netgw.cidrForbidden"),
            ({"remote_cidrs": ["0.0.0.0/0"]}, "netgw.cidrTooWide"),
            ({"remote_cidrs": ["10.1.0.0/16", "10.1.2.0/24"]}, "netgw.cidrOverlap"),
            ({"local_address": None}, "netgw.localAddressRequired"),
            ({"local_address": "10.42.0.9"}, "netgw.localAddressForbidden"),
            ({"name": "abcdef0123456789abcdef0123456789"}, "netgw.nameReserved"),
            ({"name": "netgw-x"}, "netgw.nameReserved"),
            (
                {"dns": {"servers": ["8.8.8.8"], "domains": ["corp.example"]}},
                "netgw.dnsServerOutside",
            ),
            ({"dns": {"servers": ["10.1.0.53"], "domains": []}}, "netgw.dnsIncomplete"),
        ],
    )
    async def test_network_rules(self, client, over, key):
        """Fails when a gateway could route the Pod / Service ranges, link-local, the default route
        or a reserved name into the tunnel."""
        headers, _ = await _user(client)
        resp = await client.post("/api/v1/netgw/gateways", json=wg_body(**over), headers=headers)
        assert resp.json()["message_key"] == key, resp.text

    @pytest.mark.parametrize(
        ("tunnel", "key"),
        [
            ({"endpoint": "10.0.0.1:51820"}, "netgw.peerNotPublic"),
            ({"endpoint": "10.1.0.1:51820"}, "netgw.peerNotPublic"),
            ({"endpoint": "vpn.example.com"}, "netgw.endpointInvalid"),
            ({"peer_public_key": "x" * 44}, "netgw.wireguardKeyInvalid"),
        ],
    )
    async def test_wireguard_peer_rules(self, client, tunnel, key):
        headers, _ = await _user(client)
        body = wg_body()
        body["tunnel"] = {**body["tunnel"], **tunnel}
        resp = await client.post("/api/v1/netgw/gateways", json=body, headers=headers)
        assert resp.json()["message_key"] == key, resp.text

    async def test_peer_inside_tunnel_rejected(self, client):
        headers, _ = await _user(client)
        body = wg_body(remote_cidrs=["9.9.9.0/24"], dns=None)
        body["tunnel"] = {**body["tunnel"], "endpoint": "9.9.9.9:51820"}
        resp = await client.post("/api/v1/netgw/gateways", json=body, headers=headers)
        assert resp.json()["message_key"] == "netgw.peerInsideRemote"

    @pytest.mark.parametrize(
        ("profile", "key"),
        [
            (PROFILE + "up /bin/sh\n", "netgw.openvpnProfileInvalid"),
            (
                PROFILE.replace("proto udp", "proto tcp").replace("1194", "25"),
                "netgw.peerPortBlocked",
            ),
            (PROFILE.replace("vpn.example.com", "192.168.1.1"), "netgw.peerNotPublic"),
        ],
    )
    async def test_openvpn_profile_rules(self, client, profile, key):
        """Fails when a profile could run commands or reach a blocked port / private peer."""
        headers, _ = await _user(client)
        body = wg_body(tunnel={**TUNNELS["openvpn"], "config": profile}, local_address=None)
        resp = await client.post("/api/v1/netgw/gateways", json=body, headers=headers)
        assert resp.json()["message_key"] == key, resp.text

    async def test_ipsec_psk_quote_rejected(self, client):
        headers, _ = await _user(client)
        body = body_for("ipsec")
        body["tunnel"] = {**body["tunnel"], "psk": 'abcdefghijklmnop" x'}
        resp = await client.post("/api/v1/netgw/gateways", json=body, headers=headers)
        assert resp.json()["message_key"] == "netgw.pskInvalid"

    async def test_required_secret_on_create(self, client):
        headers, _ = await _user(client)
        body = body_for("openconnect")
        body["tunnel"] = {**body["tunnel"], "password": None}
        resp = await client.post("/api/v1/netgw/gateways", json=body, headers=headers)
        assert resp.json()["message_key"] == "netgw.secretRequired"

    async def test_quota_and_name_collisions(self, client, monkeypatch):
        """Fails when the per-user cap or the shared tenant-namespace name space is not enforced."""
        monkeypatch.setattr(get_settings(), "netgw_max_per_user", 2)
        headers, _ = await _user(client)
        gw = await _create(client, headers)
        resp = await client.post("/api/v1/netgw/gateways", json=wg_body(), headers=headers)
        assert resp.json()["message_key"] == "netgw.nameTaken"
        fwd = {
            "kind": "intranet",
            "name": "nas",
            "port": 2049,
            "target_host": "10.1.0.5",
            "target_port": 2049,
        }
        await client.post(
            f"/api/v1/netgw/gateways/{gw['uuid']}/forwards", json=fwd, headers=headers
        )
        resp = await client.post(
            "/api/v1/netgw/gateways", json=wg_body(name="nas"), headers=headers
        )
        assert resp.json()["message_key"] == "netgw.nameTaken"
        await _create(client, headers, wg_body(name="lab"))
        resp = await client.post(
            "/api/v1/netgw/gateways", json=wg_body(name="dc2"), headers=headers
        )
        assert resp.json()["message_key"] == "netgw.gatewayQuota"


async def _dev_instance(sm, user_id: int, status: str = "running") -> str:
    instance_id, _ = await seed_instance(sm, user_id, status=status, wallet_credit=False)
    uuid = uuid4().hex
    async with sm() as session:
        await session.execute(update(Instance).where(Instance.id == instance_id).values(uuid=uuid))
        await session.commit()
    return uuid


@pytest.mark.usefixtures("enabled")
class TestForwards:
    async def test_intranet_and_instance_forwards_render(self, client, sm, fake):
        """Fails when a forward does not reach the K8s spec and the agent spec consistently
        (ExternalName ↔ listen port, instance Service ↔ reverse relay)."""
        headers, user_id = await _user(client)
        gw = await _create(client, headers)
        url = f"/api/v1/netgw/gateways/{gw['uuid']}/forwards"
        nas = {
            "kind": "intranet",
            "name": "nas",
            "port": 2049,
            "target_host": "nas.corp.example",
            "target_port": 2049,
        }
        assert (await client.post(url, json=nas, headers=headers)).status_code == 201
        inst = await _dev_instance(sm, user_id)
        ssh = {
            "kind": "instance",
            "name": "box-ssh",
            "port": 2222,
            "instance_uuid": inst,
            "target_port": 22,
        }
        resp = await client.post(url, json=ssh, headers=headers)
        assert resp.status_code == 201, resp.text
        forwards = resp.json()["forwards"]
        assert [f["name"] for f in forwards] == ["nas", "box-ssh"]
        assert forwards[1]["instance_uuid"] == inst and forwards[1]["available"] is True

        await drain(sm)
        spec = fake.netgw[gw["uuid"]]
        assert [(f.name, f.port, f.listen_port) for f in spec.intranet] == [("nas", 2049, 20000)]
        assert [(f.instance_name, f.port) for f in spec.instance] == [(inst, 22)]
        agent = _agent_spec(fake, gw["uuid"])
        assert agent["forwards"] == [
            {"port": 20000, "host": "nas.corp.example", "target_port": 2049}
        ]
        rev = agent["reverse"][0]
        assert rev["port"] == 2222 and rev["target_port"] == 22
        fid = forwards[1]["id"]
        assert rev["host"] == (f"netgw-{gw['uuid']}-{fid}.tenant-{user_id}.svc.cluster.local.")

        resp = await client.delete(f"{url}/{forwards[0]['id']}", headers=headers)
        assert [f["name"] for f in resp.json()["forwards"]] == ["box-ssh"]
        await drain(sm)
        assert fake.netgw[gw["uuid"]].intranet == ()

    @pytest.mark.parametrize(
        ("fwd", "key"),
        [
            (
                {
                    "kind": "intranet",
                    "name": "x",
                    "port": 80,
                    "target_host": "10.2.0.5",
                    "target_port": 80,
                },
                "netgw.targetOutsideRemote",
            ),
            (
                {
                    "kind": "intranet",
                    "name": "corp",
                    "port": 80,
                    "target_host": "10.1.0.5",
                    "target_port": 80,
                },
                "netgw.nameTaken",
            ),
        ],
    )
    async def test_intranet_forward_rules(self, client, fwd, key):
        headers, _ = await _user(client)
        gw = await _create(client, headers)
        resp = await client.post(
            f"/api/v1/netgw/gateways/{gw['uuid']}/forwards", json=fwd, headers=headers
        )
        assert resp.json()["message_key"] == key, resp.text

    async def test_instance_forward_rules(self, client, sm):
        """Fails when a reverse relay can target another tenant's instance, a released one, a
        reserved port or a port twice."""
        headers, user_id = await _user(client, 1)
        _, other_id = await _user(client, 2)
        gw = await _create(client, headers)
        url = f"/api/v1/netgw/gateways/{gw['uuid']}/forwards"

        def body(name: str, port: int, inst: str) -> dict[str, Any]:
            return {
                "kind": "instance",
                "name": name,
                "port": port,
                "instance_uuid": inst,
                "target_port": 22,
            }

        foreign = await _dev_instance(sm, other_id)
        assert (
            await client.post(url, json=body("a", 2222, foreign), headers=headers)
        ).status_code == 404
        released = await _dev_instance(sm, user_id, status="released")
        resp = await client.post(url, json=body("b", 2222, released), headers=headers)
        assert resp.json()["message_key"] == "netgw.instanceNotEligible"
        mine = await _dev_instance(sm, user_id)
        resp = await client.post(url, json=body("c", 1080, mine), headers=headers)
        assert resp.json()["message_key"] == "netgw.portReserved"
        assert (
            await client.post(url, json=body("d", 2222, mine), headers=headers)
        ).status_code == 201
        resp = await client.post(url, json=body("e", 2222, mine), headers=headers)
        assert resp.json()["message_key"] == "netgw.portTaken"

    async def test_forward_quota(self, client, monkeypatch):
        monkeypatch.setattr(get_settings(), "netgw_max_forwards", 1)
        headers, _ = await _user(client)
        gw = await _create(client, headers)
        url = f"/api/v1/netgw/gateways/{gw['uuid']}/forwards"
        fwd = {
            "kind": "intranet",
            "name": "a",
            "port": 80,
            "target_host": "10.1.0.5",
            "target_port": 80,
        }
        assert (await client.post(url, json=fwd, headers=headers)).status_code == 201
        resp = await client.post(url, json={**fwd, "name": "b"}, headers=headers)
        assert resp.json()["message_key"] == "netgw.forwardQuota"


@pytest.mark.usefixtures("enabled")
class TestPatrol:
    async def test_reapplies_missing_deployment_and_image_drift(
        self, client, sm, fake, monkeypatch
    ):
        """Fails when a deleted gateway Deployment or a new gateway image is not converged."""
        headers, _ = await _user(client)
        gw = await _create(client, headers)
        await drain(sm)
        fake.netgw.pop(gw["uuid"])
        await netgw_patrol(sm)
        await drain(sm)
        assert gw["uuid"] in fake.netgw

        new_image = IMAGE.replace("a" * 64, "b" * 64)
        monkeypatch.setattr(get_settings(), "netgw_image", new_image)
        await netgw_patrol(sm)
        await drain(sm)
        assert fake.netgw[gw["uuid"]].image == new_image

    async def test_pending_apply_is_not_doubled(self, client, sm, fake):
        headers, _ = await _user(client)
        gw = await _create(client, headers)
        await netgw_patrol(sm)
        async with sm() as session:
            row = await session.scalar(select(NetGateway).where(NetGateway.uuid == gw["uuid"]))
            assert row is not None and row.generation == 1

    async def test_orphan_deployment_reclaimed(self, sm, fake):
        """Fails when a gateway Deployment without a row (and its tenant VPN secret) survives."""
        orphan = uuid4().hex
        fake.netgw[orphan] = NetgwSpec(
            uuid=orphan,
            tenant_namespace="tenant-99",
            socks_name="x",
            image=IMAGE,
            spec_json="{}",
            egress_ports=(),
        )
        await netgw_patrol(sm)
        assert orphan not in fake.netgw
        assert fake.netgw_deleted[-1] == (orphan, "tenant-99")

    async def test_deleted_account_tears_down(self, client, sm, fake):
        headers, user_id = await _user(client)
        gw = await _create(client, headers)
        await drain(sm)
        async with sm() as session:
            await session.execute(update(User).where(User.id == user_id).values(status="deleted"))
            await session.commit()
        await netgw_patrol(sm)
        await drain(sm)
        assert gw["uuid"] not in fake.netgw
        async with sm() as session:
            assert (
                await session.scalar(select(NetGateway).where(NetGateway.uuid == gw["uuid"]))
                is None
            )


@pytest.mark.usefixtures("enabled")
class TestLogsAndAdmin:
    async def test_logs(self, client, sm, fake):
        headers, _ = await _user(client)
        gw = await _create(client, headers)
        fake.netgw_logs[gw["uuid"]] = "a\nb\nc"
        resp = await client.get(
            f"/api/v1/netgw/gateways/{gw['uuid']}/logs?tail_lines=2", headers=headers
        )
        assert resp.json() == {"lines": ["b", "c"], "truncated": True}

    async def test_admin_list_and_delete(self, client, sm, fake):
        """Fails when ops cannot tear down a tenant gateway, or readonly can."""
        headers, user_id = await _user(client)
        gw = await _create(client, headers)
        await drain(sm)
        readonly = await admin_headers(sm, client, "readonly")
        rows = (await client.get("/api/admin/v1/netgw/gateways", headers=readonly)).json()
        assert [(r["uuid"], r["user_id"]) for r in rows] == [(gw["uuid"], user_id)]
        assert "private_key" not in json.dumps(rows)
        url = f"/api/admin/v1/netgw/gateways/{gw['uuid']}/delete"
        assert (
            await client.post(url, json={"reason": "abuse"}, headers=readonly)
        ).status_code == 403
        ops = await admin_headers(sm, client, "ops")
        resp = await client.post(url, json={"reason": "abuse report"}, headers=ops)
        assert resp.status_code == 200, resp.text
        assert resp.json()["state"] == "deleting"
        await drain(sm)
        assert gw["uuid"] not in fake.netgw
