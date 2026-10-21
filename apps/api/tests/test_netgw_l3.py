"""Private-network gateway L3 (whole-subnet access): configuration, attachments, peers rendering,
the instance side of an attachment and its cleanup."""

import importlib
import json
import pathlib
import sys
from datetime import timedelta
from types import ModuleType
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select, update

from app.core.config import get_settings
from app.core.crypto import encrypt_str
from app.core.timeutil import now_utc
from app.modules.netgw.models import NetGateway, NetGatewayAttachment
from app.modules.netgw.patrol import netgw_patrol
from app.modules.orchestrator.models import Instance, InstanceEvent
from app.modules.orchestrator.service import build_pod_spec_with_cluster
from tests.helpers import drain, seed_instance, user_headers_with_id
from tests.test_netgw import IMAGE, TUNNELS, enabled, wg_body  # noqa: F401

pytestmark = pytest.mark.usefixtures("fake", "enabled")

AGENT_DIR = pathlib.Path(__file__).resolve().parents[3] / "deploy" / "netgw" / "agent"
L3 = {"cidr": "192.168.250.0/24", "nat": False}


def _agent(name: str) -> ModuleType:
    if str(AGENT_DIR) not in sys.path:
        sys.path.insert(0, str(AGENT_DIR))
    return importlib.import_module(f"netgw_agent.{name}")


async def _user(client, n: int = 1) -> tuple[dict[str, str], int]:
    return await user_headers_with_id(client, f"netgw-l3-{n}@test.local")


async def _gateway(client, headers, **over: Any) -> dict[str, Any]:
    body = wg_body(**{"l3": L3, **over})
    resp = await client.post("/api/v1/netgw/gateways", json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _instance(sm, user_id: int, *, status: str = "running", **kw: Any) -> str:
    instance_id, _ = await seed_instance(sm, user_id, status=status, wallet_credit=False, **kw)
    uuid = uuid4().hex
    async with sm() as session:
        await session.execute(
            update(Instance)
            .where(Instance.id == instance_id)
            .values(
                uuid=uuid,
                ssh_port=30022,
                jupyter_token=encrypt_str("tok", aad=f"jupyter-token:{uuid}"),
            )
        )
        await session.commit()
    return uuid


async def _attach(client, headers, gw: str, instance: str) -> Any:
    return await client.post(
        f"/api/v1/netgw/gateways/{gw}/attachments",
        json={"instance_uuid": instance},
        headers=headers,
    )


async def _view(client, headers, gw: str) -> dict[str, Any]:
    return (await client.get(f"/api/v1/netgw/gateways/{gw}", headers=headers)).json()


def _peers(fake, gw: str) -> list[dict[str, Any]]:
    return json.loads(fake.netgw[gw].peers_json)["peers"]


class TestL3Config:
    async def test_create_renders_hub_and_view(self, client, sm, fake):
        """Fails when an L3 gateway is not rendered with its hub or the view leaks the hub key."""
        headers, _ = await _user(client)
        gw = await _gateway(client, headers)
        assert gw["l3"] == {
            "cidr": "192.168.250.0/24",
            "nat": False,
            "address": "192.168.250.1",
            "max_attachments": 250,
            "attachments": [],
        }
        await drain(sm)
        spec = fake.netgw[gw["uuid"]]
        assert spec.l3 is True
        agent = json.loads(spec.spec_json)
        assert agent["l3"]["cidr"] == "192.168.250.0/24"
        assert len(agent["l3"]["private_key"]) == 44
        assert _peers(fake, gw["uuid"]) == []
        assert agent["l3"]["private_key"] not in json.dumps(
            await _view(client, headers, gw["uuid"])
        )
        parsed = _agent("spec").parse(agent)
        assert str(parsed.l3.address) == "192.168.250.1"

    async def test_relay_only_gateway_has_no_l3(self, client, sm, fake):
        headers, _ = await _user(client)
        resp = await client.post("/api/v1/netgw/gateways", json=wg_body(), headers=headers)
        gw = resp.json()
        assert gw["l3"] is None
        await drain(sm)
        assert fake.netgw[gw["uuid"]].l3 is False
        assert json.loads(fake.netgw[gw["uuid"]].spec_json)["l3"] is None

    @pytest.mark.parametrize(
        ("l3", "over", "key"),
        [
            ({"cidr": "not-a-net"}, {}, "netgw.l3CidrInvalid"),
            ({"cidr": "192.168.250.0/30"}, {}, "netgw.l3CidrSize"),
            ({"cidr": "10.0.0.0/15"}, {}, "netgw.l3CidrSize"),
            ({"cidr": "9.9.9.0/24"}, {}, "netgw.l3CidrNotPrivate"),
            ({"cidr": "10.42.7.0/24"}, {}, "netgw.cidrForbidden"),
            ({"cidr": "10.1.4.0/24"}, {}, "netgw.l3CidrOverlapsRemote"),
            ({"cidr": "10.250.0.0/24"}, {}, "netgw.l3CidrContainsLocal"),
            (
                {"cidr": "192.168.250.0/24", "nat": False},
                {"tunnel": TUNNELS["openconnect"], "local_address": None},
                "netgw.l3NatRequired",
            ),
        ],
    )
    async def test_network_rules(self, client, l3, over, key):
        """Fails when the instance network could collide with routed, cluster or tunnel ranges,
        or an SSL VPN gateway could route a range its server never assigned."""
        headers, _ = await _user(client)
        resp = await client.post(
            "/api/v1/netgw/gateways", json=wg_body(l3={**L3, **l3}, **over), headers=headers
        )
        assert resp.status_code == 400, resp.text
        assert resp.json()["message_key"] == key

    async def test_update_keeps_hub_key_and_guards_attachments(self, client, sm, fake):
        """Fails when an update rotates the hub key (attached instances pin it) or drops / moves
        the instance network under attached instances."""
        headers, user_id = await _user(client)
        gw = await _gateway(client, headers)
        await drain(sm)
        hub_key = json.loads(fake.netgw[gw["uuid"]].spec_json)["l3"]["private_key"]
        url = f"/api/v1/netgw/gateways/{gw['uuid']}"
        resp = await client.put(url, json=wg_body(l3={**L3, "nat": True}), headers=headers)
        assert resp.status_code == 200, resp.text
        await drain(sm)
        assert json.loads(fake.netgw[gw["uuid"]].spec_json)["l3"]["private_key"] == hub_key

        instance = await _instance(sm, user_id)
        assert (await _attach(client, headers, gw["uuid"], instance)).status_code == 201
        for l3 in (None, {"cidr": "192.168.251.0/24", "nat": True}):
            resp = await client.put(url, json=wg_body(l3=l3), headers=headers)
            assert resp.status_code == 409
            assert resp.json()["message_key"] == "netgw.l3InUse"


class TestAttachments:
    async def test_attach_renders_peers_without_rolling_the_gateway(self, client, sm, fake):
        """Fails when attaching an instance restarts the gateway (spec hash changes) or the hub
        cannot find the instance Pod by name."""
        headers, user_id = await _user(client)
        gw = await _gateway(client, headers)
        await drain(sm)
        before = fake.netgw[gw["uuid"]].spec_hash
        a, b = await _instance(sm, user_id), await _instance(sm, user_id)
        assert (await _attach(client, headers, gw["uuid"], a)).status_code == 201
        resp = await _attach(client, headers, gw["uuid"], b)
        assert resp.status_code == 201
        view = resp.json()["l3"]["attachments"]
        assert [(x["instance_uuid"], x["address"]) for x in view] == [
            (a, "192.168.250.2"),
            (b, "192.168.250.3"),
        ]
        await drain(sm)
        assert fake.netgw[gw["uuid"]].spec_hash == before
        peers = _peers(fake, gw["uuid"])
        assert peers[0]["host"] == (
            f"{a}.netgw-{gw['uuid']}-l3.tenant-{user_id}.svc.cluster.local."
        )
        agent = _agent("spec")
        parsed = agent.parse(json.loads(fake.netgw[gw["uuid"]].spec_json))
        assert [str(p.address) for p in agent.parse_peers({"peers": peers}, parsed.l3)] == [
            "192.168.250.2",
            "192.168.250.3",
        ]

    async def test_attach_rules(self, client, sm):
        """Fails when an instance can sit on two gateways, a foreign / released / VM-isolated
        instance can be attached, or a gateway without L3 accepts attachments."""
        headers, user_id = await _user(client)
        other_headers, other_id = await _user(client, 2)
        gw = await _gateway(client, headers)
        gw2 = await _gateway(client, headers, name="lab", l3={"cidr": "192.168.251.0/24"})
        instance = await _instance(sm, user_id)
        assert (await _attach(client, headers, gw["uuid"], instance)).status_code == 201
        assert (await _attach(client, headers, gw["uuid"], instance)).status_code == 201
        resp = await _attach(client, headers, gw2["uuid"], instance)
        assert resp.json()["message_key"] == "netgw.instanceAttached"

        foreign = await _instance(sm, other_id)
        assert (await _attach(client, headers, gw["uuid"], foreign)).status_code == 404
        released = await _instance(sm, user_id, status="released")
        resp = await _attach(client, headers, gw["uuid"], released)
        assert resp.json()["message_key"] == "netgw.instanceNotEligible"
        kata = await _instance(
            sm,
            user_id,
            spec={
                "tier": "dedicated",
                "vram_gb": 80,
                "vcpu": 8,
                "mem_gb": 32,
                "disk_gb": 100,
                "pool_label": "kata",
            },
        )
        resp = await _attach(client, headers, gw["uuid"], kata)
        assert resp.json()["message_key"] == "netgw.l3InstanceUnsupported"

        plain = (
            await client.post(
                "/api/v1/netgw/gateways", json=wg_body(name="plain"), headers=other_headers
            )
        ).json()
        resp = await _attach(client, other_headers, plain["uuid"], foreign)
        assert resp.json()["message_key"] == "netgw.l3Disabled"

    async def test_detach_and_capacity(self, client, sm, fake):
        """Fails when a detached instance stays a hub peer or the instance network
        over-allocates."""
        headers, user_id = await _user(client)
        gw = await _gateway(client, headers, l3={"cidr": "192.168.250.0/29"})
        assert gw["l3"]["max_attachments"] == 5
        instances = [await _instance(sm, user_id) for _ in range(6)]
        for inst in instances[:5]:
            assert (await _attach(client, headers, gw["uuid"], inst)).status_code == 201
        resp = await _attach(client, headers, gw["uuid"], instances[5])
        assert resp.json()["message_key"] == "netgw.l3Full"

        url = f"/api/v1/netgw/gateways/{gw['uuid']}/attachments/{instances[0]}"
        assert (await client.delete(url, headers=headers)).status_code == 200
        assert (await client.delete(url, headers=headers)).json()["message_key"] == (
            "netgw.attachmentNotFound"
        )
        resp = await _attach(client, headers, gw["uuid"], instances[5])
        assert resp.status_code == 201
        assert resp.json()["l3"]["attachments"][-1]["address"] == "192.168.250.2"
        await drain(sm)
        assert instances[0] not in json.dumps(_peers(fake, gw["uuid"]))

    async def test_pending_restart(self, client, sm):
        """Fails when a running instance whose Pod predates its attachment (or the last change of
        the routed networks) is not flagged, or a freshly started one still is."""
        headers, user_id = await _user(client)
        gw = await _gateway(client, headers)
        old = now_utc() - timedelta(hours=1)
        uuid = await _instance(sm, user_id, events=[(old, None, "creating")])
        async with sm() as session:
            instance_id = await session.scalar(select(Instance.id).where(Instance.uuid == uuid))
        resp = await _attach(client, headers, gw["uuid"], uuid)
        assert resp.json()["l3"]["attachments"][0]["pending_restart"] is True

        async with sm() as session:
            session.add(
                InstanceEvent(
                    instance_id=instance_id,
                    from_status="stopped",
                    to_status="starting",
                    reason="seed",
                    actor="user",
                    created_at=now_utc(),
                )
            )
            await session.commit()
        view = await _view(client, headers, gw["uuid"])
        assert view["l3"]["attachments"][0]["pending_restart"] is False

        resp = await client.put(
            f"/api/v1/netgw/gateways/{gw['uuid']}",
            json=wg_body(l3=L3, remote_cidrs=["10.1.0.0/16", "10.9.0.0/16"]),
            headers=headers,
        )
        assert resp.json()["l3"]["attachments"][0]["pending_restart"] is True


class TestInstanceSide:
    async def _attached(self, client, sm) -> tuple[dict[str, Any], Instance, int]:
        headers, user_id = await _user(client)
        gw = await _gateway(client, headers)
        uuid = await _instance(sm, user_id, status="starting")
        assert (await _attach(client, headers, gw["uuid"], uuid)).status_code == 201
        async with sm() as session:
            inst = await session.scalar(select(Instance).where(Instance.uuid == uuid))
            assert inst is not None
        return gw, inst, user_id

    async def test_pod_spec_carries_the_attachment(self, client, sm):
        """Fails when the next Pod of an attached instance lacks its attach material, or the
        agent's attach entry would reject what the platform renders."""
        gw, inst, _ = await self._attached(client, sm)
        async with sm() as session:
            spec = await build_pod_spec_with_cluster(session, inst)
        assert spec.netgw is not None
        assert spec.netgw.gateway_uuid == gw["uuid"]
        assert spec.netgw.image == IMAGE
        config = json.loads(spec.netgw.config_json)
        assert config["routes"] == ["10.1.0.0/16"]
        assert config["endpoint_host"] == (
            f"netgw-{gw['uuid']}-hub.superdl-netgw.svc.cluster.local."
        )
        parsed = _agent("attach").parse(config)
        assert str(parsed.address) == "192.168.250.2"

    async def test_no_attachment_when_gateway_deleting_or_feature_off(
        self, client, sm, monkeypatch
    ):
        """Fails when an instance Pod would wait on a gateway that is going away or a feature
        that is switched off."""
        gw, inst, _ = await self._attached(client, sm)
        monkeypatch.setattr(get_settings(), "netgw_image", "")
        async with sm() as session:
            assert (await build_pod_spec_with_cluster(session, inst)).netgw is None
        monkeypatch.setattr(get_settings(), "netgw_image", IMAGE)
        async with sm() as session:
            await session.execute(
                update(NetGateway).where(NetGateway.uuid == gw["uuid"]).values(status="deleting")
            )
            await session.commit()
        async with sm() as session:
            assert (await build_pod_spec_with_cluster(session, inst)).netgw is None

    async def test_released_instance_pruned_by_patrol(self, client, sm, fake):
        """Fails when a released instance keeps its address and hub peer."""
        gw, inst, _ = await self._attached(client, sm)
        await drain(sm)
        assert len(_peers(fake, gw["uuid"])) == 1
        async with sm() as session:
            await session.execute(
                update(Instance).where(Instance.id == inst.id).values(status="released")
            )
            await session.commit()
        await netgw_patrol(sm)
        await drain(sm)
        assert _peers(fake, gw["uuid"]) == []
        async with sm() as session:
            assert await session.scalar(select(NetGatewayAttachment)) is None
