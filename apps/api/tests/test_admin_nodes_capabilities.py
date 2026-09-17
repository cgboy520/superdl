"""Admin node list carries the pool-switch capability flags computed by core/gpu_models, so the
consoles never keep their own GPU family tables (unrecognised models are fail-closed)."""

from httpx import AsyncClient

from tests.helpers import admin_headers, seed_node_spec


async def test_node_list_reports_capability_flags(client: AsyncClient, sm):
    await seed_node_spec(sm, node_name="gb10-1", gpu_model="GB10", pool_label="hami")
    await seed_node_spec(sm, node_name="h100-1", gpu_model="H100-80G", pool_label="kata")
    await seed_node_spec(sm, node_name="rtx-1", gpu_model="RTX4090", pool_label="hami")
    await seed_node_spec(sm, node_name="mystery-1", gpu_model=None, pool_label="hami")
    ah = await admin_headers(sm, client, role="ops")
    nodes = {n["name"]: n for n in (await client.get("/api/admin/v1/nodes", headers=ah)).json()}
    assert (nodes["gb10-1"]["supports_mig"], nodes["gb10-1"]["supports_passthrough"]) == (
        False,
        False,
    )
    assert (nodes["h100-1"]["supports_mig"], nodes["h100-1"]["supports_passthrough"]) == (
        True,
        True,
    )
    assert (nodes["rtx-1"]["supports_mig"], nodes["rtx-1"]["supports_passthrough"]) == (
        False,
        True,
    )
    assert (nodes["mystery-1"]["supports_mig"], nodes["mystery-1"]["supports_passthrough"]) == (
        False,
        False,
    )
