"""Container log endpoint: 200 / truncation / status gate / IDOR / rate limit / fault injection."""

import pytest
from httpx import AsyncClient

from app.modules.orchestrator.reconciler import reconcile_once
from tests.helpers import drain, provision_running

pytestmark = pytest.mark.usefixtures("fake")


async def _get_logs(client: AsyncClient, headers: dict, uuid: str, **params: int):
    return await client.get(f"/api/v1/instances/{uuid}/logs", headers=headers, params=params)


class TestInstanceLogs:
    async def test_running_200(self, client, sm, fake):
        headers, uuid, _user_id = await provision_running(client, sm, fake)

        resp = await _get_logs(client, headers, uuid)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["truncated"] is False
        assert len(body["lines"]) > 0

    async def test_stopping_state_also_allowed(self, client, sm, fake):
        """While stopping the Pod may still be Terminating, the log is still readable (200)."""
        headers, uuid, _user_id = await provision_running(client, sm, fake)
        resp = await client.post(f"/api/v1/instances/{uuid}/stop", headers=headers)
        assert resp.json()["status"] == "stopping"
        resp = await _get_logs(client, headers, uuid)
        assert resp.status_code == 200, resp.text

    async def test_tail_lines_clamped_to_2000(self, client, sm, fake):
        """tail_lines above 2000 is capped; one extra line is fetched to detect truncation."""
        headers, uuid, _user_id = await provision_running(client, sm, fake)
        resp = await _get_logs(client, headers, uuid, tail_lines=5000)
        assert resp.status_code == 200, resp.text
        assert fake.log_calls[-1] == (f"tenant-{_user_id}", uuid, 2001)

    async def test_partial_tail_sets_truncated(self, client, sm, fake):
        """Synthetic 8-line log, tail_lines=3 → the last 3 lines with truncated=True."""
        headers, uuid, _user_id = await provision_running(client, sm, fake)
        resp = await _get_logs(client, headers, uuid, tail_lines=3)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body["lines"]) == 3
        assert body["truncated"] is True

    async def test_stopped_409(self, client, sm, fake):
        """Reading logs outside running/stopping → 409."""
        headers, uuid, _user_id = await provision_running(client, sm, fake)
        await client.post(f"/api/v1/instances/{uuid}/stop", headers=headers)
        await drain(sm)
        await reconcile_once(sm)
        resp = await _get_logs(client, headers, uuid)
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "orchestrator.logsNeedsRunning"

    @pytest.mark.parametrize("kind", ["missing", "idor"])
    async def test_404_not_found_and_not_owner(self, client, sm, fake, kind: str):
        """Unknown and someone else's instances are both 404."""
        headers, uuid, _user_id = await provision_running(client, sm, fake)
        if kind == "missing":
            resp = await _get_logs(client, headers, "0" * 32)
        else:
            other_headers, _other_uuid, _ = await provision_running(
                client, sm, fake, phone="13900000011"
            )
            resp = await _get_logs(client, other_headers, uuid)
        assert resp.status_code == 404
        assert resp.json()["code"] == "NOT_FOUND"

    async def test_rate_limit_20_per_hour(self, client, sm, fake):
        """20/h/user: the first 20 are 200, the 21st is 429."""
        headers, uuid, _user_id = await provision_running(client, sm, fake)
        for _ in range(20):
            resp = await _get_logs(client, headers, uuid)
            assert resp.status_code == 200, resp.text
        resp = await _get_logs(client, headers, uuid)
        assert resp.status_code == 429
        assert resp.json()["code"] == "RATE_LIMITED"

    async def test_k8s_failure_uniform_error(self, client, sm, fake):
        """A K8s-side read error → unified error body (503)."""
        headers, uuid, _user_id = await provision_running(client, sm, fake)
        fake.fail_next_logs = True

        resp = await _get_logs(client, headers, uuid)
        assert resp.status_code == 503
        body = resp.json()
        assert body["code"] == "INTERNAL"
        assert body["message_key"] == "orchestrator.logsUnavailable"
