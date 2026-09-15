"""GET /api/v1/metrics/instances:实例列表 sparkline 批量摘要。"""

import pytest

from app.modules.metering import prom
from tests.helpers import prom_mock, provision_running

pytestmark = pytest.mark.usefixtures("fake")


class TestMetricsSummary:
    async def test_running_instance_series_and_last(self, client, sm, fake):
        headers, uuid, _user_id = await provision_running(client, sm, fake)
        prom.set_client(prom_mock([(1e9, 40.0), (1e9 + 300, 82.5)]))
        resp = await client.get("/api/v1/metrics/instances", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        assert len(body["items"]) == 1
        item = body["items"][0]
        assert item["uuid"] == uuid
        assert item["points"] == [[1e9, 40.0], [1e9 + 300, 82.5]]
        assert item["last"] == 82.5

    async def test_prom_down_returns_200_unavailable(self, client, sm, fake):
        headers, _uuid, _user_id = await provision_running(client, sm, fake)
        prom.set_client(prom_mock(fail=True))
        resp = await client.get("/api/v1/metrics/instances", headers=headers)
        assert resp.status_code == 200
        assert resp.json() == {"available": False, "items": []}

    async def test_non_running_excluded(self, client, sm, fake):
        headers, uuid, _user_id = await provision_running(client, sm, fake)
        stop = await client.post(f"/api/v1/instances/{uuid}/stop", headers=headers)
        assert stop.json()["status"] == "stopping", stop.text
        prom.set_client(prom_mock([(1e9, 50.0)]))
        resp = await client.get("/api/v1/metrics/instances", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["items"] == []

    async def test_tenant_isolation(self, client, sm, fake):
        _headers_a, uuid_a, _ = await provision_running(client, sm, fake, phone="13900020001")
        headers_b, uuid_b, _ = await provision_running(client, sm, fake, phone="13900020002")
        prom.set_client(prom_mock([(1e9, 10.0)]))
        resp = await client.get("/api/v1/metrics/instances", headers=headers_b)
        uuids = {i["uuid"] for i in resp.json()["items"]}
        assert uuids == {uuid_b}
        assert uuid_a not in uuids
