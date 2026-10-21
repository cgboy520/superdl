# pyright: reportPrivateUsage=false
"""Organization gateways: L3 only, the owner scope (a creator never reaches one through the
personal endpoints), member-only attachment by owners and admins, self-detach, the membership
listeners, the patrol, rendering into members' namespaces and the narrowed network policies."""

import json
from typing import Any
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.k8s.base import NETGW_HUB_PORT, NETGW_L3_LABEL, NETGW_LABEL, NETGW_TENANT_LABEL
from app.core.k8s.netgw import (
    build_netgw_deployment,
    build_netgw_netpol,
    build_netgw_services,
    build_netgw_tenant_netpol,
)
from app.core.k8s.real import PRIVATE_CIDRS
from app.modules.account.models import User
from app.modules.netgw import service as netgw_service
from app.modules.netgw.models import NetGateway, NetGatewayAttachment
from app.modules.netgw.patrol import netgw_patrol
from app.modules.notify.models import Notification
from app.modules.orchestrator.models import Instance
from app.modules.orgs.models import Organization, OrgMember
from tests.helpers import admin_headers, drain, user_headers_with_id
from tests.test_netgw import IMAGE, enabled, wg_body  # noqa: F401
from tests.test_netgw_l3 import L3, _instance
from tests.test_orgs import create_org, join_org, org_user

pytestmark = pytest.mark.usefixtures("fake", "enabled")

ORG_GW = "/api/v1/org/netgw/gateways"
KATA_SPEC = {
    "tier": "dedicated",
    "vram_gb": 80,
    "vcpu": 8,
    "mem_gb": 32,
    "disk_gb": 100,
    "pool_label": "kata",
}


async def _org_gateway(client: AsyncClient, headers: dict[str, str], **over: Any) -> Any:
    resp = await client.post(ORG_GW, json=wg_body(**{"l3": L3, **over}), headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _attach(client: AsyncClient, headers: dict[str, str], gw: str, instance: str) -> Any:
    return await client.post(
        f"{ORG_GW}/{gw}/attachments", json={"instance_uuid": instance}, headers=headers
    )


async def _setup(
    client: AsyncClient, sm: async_sessionmaker[AsyncSession]
) -> tuple[dict[str, str], dict[str, str], int, int, dict[str, Any], str]:
    """Owner + member, an organization gateway and a running member instance; (owner headers,
    member headers, member user id, member id, gateway view, instance uuid)."""
    owner, _ = await org_user(client, 1)
    await create_org(client, owner)
    member, member_uid, member_mid = await join_org(client, owner, 2)
    gw = await _org_gateway(client, owner)
    instance = await _instance(sm, member_uid)
    return owner, member, member_uid, member_mid, gw, instance


async def _gateway_row(sm: async_sessionmaker[AsyncSession], uuid: str) -> NetGateway:
    async with sm() as session:
        gw = await session.scalar(select(NetGateway).where(NetGateway.uuid == uuid))
    assert gw is not None
    return gw


async def _attachments(sm: async_sessionmaker[AsyncSession]) -> list[NetGatewayAttachment]:
    async with sm() as session:
        return list((await session.scalars(select(NetGatewayAttachment))).all())


class TestScope:
    async def test_l3_is_required(self, client: AsyncClient):
        owner, _ = await org_user(client, 1)
        await create_org(client, owner)
        resp = await client.post(ORG_GW, json=wg_body(), headers=owner)
        assert resp.status_code == 400
        assert resp.json()["message_key"] == "netgw.orgL3Required"

    async def test_creator_never_reaches_it_through_personal_endpoints(self, client: AsyncClient):
        """Fails when an admin who created an organization gateway keeps control of it after
        demotion or removal, through the personal gateway endpoints."""
        owner, _ = await org_user(client, 1)
        await create_org(client, owner)
        admin, _, admin_mid = await join_org(client, owner, 2, role="admin")
        gw = (await _org_gateway(client, admin))["uuid"]
        personal = [
            ("GET", f"/api/v1/netgw/gateways/{gw}", None),
            ("PUT", f"/api/v1/netgw/gateways/{gw}", wg_body(l3=L3)),
            ("DELETE", f"/api/v1/netgw/gateways/{gw}", None),
            (
                "POST",
                f"/api/v1/netgw/gateways/{gw}/forwards",
                {
                    "kind": "intranet",
                    "name": "nas",
                    "port": 2049,
                    "target_host": "10.1.0.5",
                    "target_port": 2049,
                },
            ),
            ("POST", f"/api/v1/netgw/gateways/{gw}/attachments", {"instance_uuid": uuid4().hex}),
            ("GET", f"/api/v1/netgw/gateways/{gw}/logs", None),
        ]

        async def personal_all_404() -> None:
            listed = (await client.get("/api/v1/netgw/gateways", headers=admin)).json()
            assert listed == []
            for method, path, body in personal:
                resp = await client.request(method, path, json=body, headers=admin)
                assert resp.status_code == 404, (method, path, resp.text)

        await personal_all_404()
        await client.patch(
            f"/api/v1/org/members/{admin_mid}", json={"role": "member"}, headers=owner
        )
        assert (await client.get(f"{ORG_GW}/{gw}", headers=admin)).status_code == 403
        await personal_all_404()
        await client.delete(f"/api/v1/org/members/{admin_mid}", headers=owner)
        assert (await client.get(f"{ORG_GW}/{gw}", headers=admin)).status_code == 404
        await personal_all_404()

    async def test_personal_quota_and_names_ignore_org_gateways(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(get_settings(), "netgw_max_per_user", 1)
        owner, _ = await org_user(client, 1)
        await create_org(client, owner)
        await _org_gateway(client, owner, name="corp")
        resp = await client.post("/api/v1/netgw/gateways", json=wg_body(name="corp"), headers=owner)
        assert resp.status_code == 201, resp.text
        resp = await client.post(ORG_GW, json=wg_body(name="corp", l3=L3), headers=owner)
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "netgw.orgNameTaken"

    async def test_one_idempotency_key_never_replays_across_scopes(self, client: AsyncClient):
        """Fails when the organization endpoint replays a personal gateway created under the same
        key (or the other way round)."""
        owner, _ = await org_user(client, 1)
        await create_org(client, owner)
        headers = {**owner, "Idempotency-Key": "gw-1"}
        body = wg_body(l3=L3)
        first = await client.post("/api/v1/netgw/gateways", json=body, headers=headers)
        assert first.status_code == 201
        resp = await client.post(ORG_GW, json=body, headers=headers)
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "common.idempotencyKeyMismatch"

    async def test_tunnelled_networks_must_be_private(self, client: AsyncClient):
        """Fails when an organization could route members' traffic for public ranges into its
        VPN."""
        owner, _ = await org_user(client, 1)
        await create_org(client, owner)
        resp = await client.post(
            ORG_GW, json=wg_body(l3=L3, remote_cidrs=["13.0.0.0/8"]), headers=owner
        )
        assert resp.status_code == 400
        assert resp.json()["message_key"] == "netgw.orgRemoteNotPrivate"
        gw = await _org_gateway(client, owner)
        resp = await client.put(
            f"{ORG_GW}/{gw['uuid']}",
            json=wg_body(l3=L3, remote_cidrs=["10.1.0.0/16", "140.82.0.0/16"]),
            headers=owner,
        )
        assert resp.status_code == 400
        assert resp.json()["message_key"] == "netgw.orgRemoteNotPrivate"

    async def test_members_cannot_see_or_manage(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        _, member, _, _, gw, instance = await _setup(client, sm)
        assert (await client.get(ORG_GW, headers=member)).status_code == 403
        assert (await client.get("/api/v1/org/netgw/instances", headers=member)).status_code == 403
        assert (await _attach(client, member, gw["uuid"], instance)).status_code == 403


class TestAttach:
    async def test_member_instance_renders_into_its_namespace(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession], fake: Any
    ):
        """Fails when the hub dials an attached member's Pod in the wrong namespace, or the
        member is not told."""
        owner, _, member_uid, member_mid, gw, instance = await _setup(client, sm)
        resp = await _attach(client, owner, gw["uuid"], instance)
        assert resp.status_code == 201, resp.text
        (att,) = resp.json()["l3"]["attachments"]
        assert att["member_id"] == member_mid
        await drain(sm)
        spec = fake.netgw[gw["uuid"]]
        assert spec.tenant_namespace is None
        assert spec.l3_namespaces == (f"tenant-{member_uid}",)
        (peer,) = json.loads(spec.peers_json)["peers"]
        assert f".netgw-{gw['uuid']}-l3.tenant-{member_uid}.svc." in peer["host"]
        async with sm() as session:
            notices = (
                await session.scalars(
                    select(Notification.target_id).where(Notification.user_id == member_uid)
                )
            ).all()
        assert instance in notices

    async def test_apply_ensures_the_member_namespace(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession], fake: Any
    ):
        """Fails when an organization gateway apply depends on a member namespace that an
        early-failed instance never created."""
        owner, _, member_uid, _, gw, instance = await _setup(client, sm)
        fake.namespaces.discard(f"tenant-{member_uid}")
        await _attach(client, owner, gw["uuid"], instance)
        await drain(sm)
        assert f"tenant-{member_uid}" in fake.namespaces
        assert (await _gateway_row(sm, gw["uuid"])).applied_generation == (
            await _gateway_row(sm, gw["uuid"])
        ).generation

    async def test_non_member_instance_looks_missing(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        """Fails when an organization admin can attach (or probe) an outsider's instance."""
        owner, _, _, _, gw, _ = await _setup(client, sm)
        _, outsider_uid = await user_headers_with_id(client, "outsider@test.local")
        foreign = await _instance(sm, outsider_uid)
        for target in (foreign, uuid4().hex):
            resp = await _attach(client, owner, gw["uuid"], target)
            assert resp.status_code == 404
            assert resp.json()["message_key"] == "orchestrator.instanceNotFound"

    async def test_member_detaches_own_instance(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        owner, member, _, _, gw, instance = await _setup(client, sm)
        await _attach(client, owner, gw["uuid"], instance)
        before = (await _gateway_row(sm, gw["uuid"])).generation
        mine = (await client.get("/api/v1/netgw/attachments", headers=member)).json()
        assert [(a["scope"], a["instance_uuid"]) for a in mine] == [("org", instance)]
        resp = await client.delete(f"/api/v1/netgw/attachments/{instance}", headers=member)
        assert resp.status_code == 204
        assert await _attachments(sm) == []
        assert (await _gateway_row(sm, gw["uuid"])).generation == before + 1
        resp = await client.delete(f"/api/v1/netgw/attachments/{instance}", headers=owner)
        assert resp.status_code == 404

    async def test_candidates_show_eligibility_without_access_data(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        owner, _, member_uid, _, gw, instance = await _setup(client, sm)
        kata = await _instance(sm, member_uid, spec=KATA_SPEC)
        await _attach(client, owner, gw["uuid"], instance)
        rows = (await client.get("/api/v1/org/netgw/instances", headers=owner)).json()
        by_uuid = {r["instance_uuid"]: r for r in rows}
        assert by_uuid[instance]["attached_to"] == gw["uuid"]
        assert by_uuid[instance]["reason"] == "attached"
        assert by_uuid[kata]["reason"] == "kata_tier" and by_uuid[kata]["attachable"] is False
        assert not {"ssh_port", "jupyter_url", "node_name", "ssh_host"} & set(rows[0])


class TestMembership:
    async def test_removal_detaches_in_the_same_transaction(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession], fake: Any
    ):
        """Fails when a removed member's instance stays on the organization's gateway."""
        owner, _, _, member_mid, gw, instance = await _setup(client, sm)
        await _attach(client, owner, gw["uuid"], instance)
        await drain(sm)
        before = (await _gateway_row(sm, gw["uuid"])).generation
        resp = await client.delete(f"/api/v1/org/members/{member_mid}", headers=owner)
        assert resp.status_code == 204
        assert await _attachments(sm) == []
        assert (await _gateway_row(sm, gw["uuid"])).generation == before + 1
        await drain(sm)
        assert fake.netgw[gw["uuid"]].l3_namespaces == ()
        assert json.loads(fake.netgw[gw["uuid"]].peers_json)["peers"] == []

    async def test_pod_build_refuses_a_non_member(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        """Fails when an instance whose owner left (listener bypassed) still gets its tunnel
        interface at the next start."""
        owner, _, member_uid, _, gw, instance = await _setup(client, sm)
        await _attach(client, owner, gw["uuid"], instance)
        async with sm() as session:
            inst = await session.scalar(select(Instance).where(Instance.uuid == instance))
            assert inst is not None
            assert await netgw_service.instance_attach(session, inst) is not None
            await session.execute(delete(OrgMember).where(OrgMember.user_id == member_uid))
            await session.commit()
            assert await netgw_service.instance_attach(session, inst) is None

    async def test_patrol_prunes_non_members_and_keeps_creator_independent(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        owner, _, member_uid, _, gw, instance = await _setup(client, sm)
        await _attach(client, owner, gw["uuid"], instance)
        async with sm() as session:
            await session.execute(delete(OrgMember).where(OrgMember.user_id == member_uid))
            await session.execute(
                update(User)
                .where(User.id == (await _gateway_row(sm, gw["uuid"])).user_id)
                .values(status="deleted")
            )
            await session.commit()
        await netgw_patrol(sm)
        assert await _attachments(sm) == []
        assert (await _gateway_row(sm, gw["uuid"])).status == "active"

    async def test_dissolution_tears_gateways_down(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession], fake: Any
    ):
        owner, _ = await org_user(client, 1)
        await create_org(client, owner, "Acme Lab")
        first = await _org_gateway(client, owner, name="corp")
        second = await _org_gateway(client, owner, name="lab")
        await drain(sm)
        resp = await client.post(
            "/api/v1/org/dissolve", json={"confirm_name": "Acme Lab"}, headers=owner
        )
        assert resp.status_code == 200
        assert (await _gateway_row(sm, first["uuid"])).status == "deleting"
        await drain(sm)
        assert {first["uuid"], second["uuid"]} <= set(fake.netgw_deleted)

    async def test_patrol_tears_down_gateways_of_a_dissolved_org(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        """Fails when a gateway outlives its organization because the listener was missed."""
        owner, _ = await org_user(client, 1)
        await create_org(client, owner)
        gw = await _org_gateway(client, owner)
        async with sm() as session:
            await session.execute(update(Organization).values(status="dissolved"))
            await session.commit()
        await netgw_patrol(sm)
        assert (await _gateway_row(sm, gw["uuid"])).status == "deleting"


class TestAdmin:
    async def test_admin_list_marks_scope(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        owner, _ = await org_user(client, 1)
        org = (await create_org(client, owner, "Acme Lab"))["org"]
        gw = await _org_gateway(client, owner)
        await client.post("/api/v1/netgw/gateways", json=wg_body(name="mine"), headers=owner)
        admin = await admin_headers(sm, client, "readonly")
        rows = (
            await client.get(
                "/api/admin/v1/netgw/gateways", params={"org_uuid": org["uuid"]}, headers=admin
            )
        ).json()
        assert [(r["uuid"], r["scope"], r["org_name"]) for r in rows] == [
            (gw["uuid"], "org", "Acme Lab")
        ]
        assert rows[0]["socks_url"] is None and rows[0]["forwards"] == []


def _org_spec(namespaces: tuple[str, ...]) -> Any:
    from app.core.k8s.base import NetgwSpec

    return NetgwSpec(
        uuid="0123456789abcdef0123456789abcdef",
        tenant_namespace=None,
        socks_name="corp",
        image=IMAGE,
        spec_json='{"version": 1}',
        egress_ports=(("udp", 51820),),
        l3=True,
        l3_namespaces=namespaces,
    )


class TestObjects:
    def test_pod_never_follows_membership(self):
        """Fails when attaching or removing members would restart the gateway Pod, or the Pod
        carries a tenant label that a tenant-default policy would admit everywhere."""
        a: Any = build_netgw_deployment(_org_spec(("tenant-1", "tenant-2")))
        b: Any = build_netgw_deployment(_org_spec(()))
        assert a.spec.template.to_dict() == b.spec.template.to_dict()
        assert NETGW_TENANT_LABEL not in a.spec.template.metadata.labels

    def test_policy_admits_attached_pods_on_the_hub_port_only(self):
        """Fails when an organization gateway can be reached from, or reach, anything but the
        attached Pods of the listed namespaces on the hub port."""
        pol: Any = build_netgw_netpol(
            _org_spec(("tenant-1", "tenant-2")), except_cidrs=PRIVATE_CIDRS
        ).spec
        (rule,) = pol.ingress
        (peer,) = rule._from
        (expr,) = peer.namespace_selector.match_expressions
        assert (expr.operator, expr.values) == ("In", ["tenant-1", "tenant-2"])
        assert peer.pod_selector.match_labels == {
            NETGW_L3_LABEL: "0123456789abcdef0123456789abcdef"
        }
        assert [(p.protocol, p.port) for p in rule.ports] == [("UDP", NETGW_HUB_PORT)]
        assert pol.egress[-1].to == [peer]
        empty: Any = build_netgw_netpol(_org_spec(()), except_cidrs=PRIVATE_CIDRS).spec
        assert empty.ingress == [] and len(empty.egress) == 2

    def test_no_socks_and_a_tenant_policy_per_namespace(self):
        spec = _org_spec(("tenant-1",))
        services: list[Any] = build_netgw_services(spec)
        assert [s.metadata.name for s in services] == ["netgw-0123456789abcdef0123456789abcdef-hub"]
        pol: Any = build_netgw_tenant_netpol(spec, "tenant-1")
        assert pol.metadata.namespace == "tenant-1"
        assert pol.spec.pod_selector.match_labels == {NETGW_L3_LABEL: spec.uuid}
        (rule,) = pol.spec.ingress
        assert rule._from[0].pod_selector.match_labels == {NETGW_LABEL: spec.uuid}
        assert [(p.protocol, p.port) for p in rule.ports] == [("UDP", NETGW_HUB_PORT)]
