"""Per-user quotas: instance count / total GPU caps; released slots are returned."""

import pytest
from httpx import AsyncClient

from app.core.config import get_settings
from app.modules.orchestrator.reconciler import reconcile_once
from tests.helpers import (
    IMAGE_PYTORCH,
    create_test_sku,
    drain,
    funded_user,
)


@pytest.fixture
def _tight_quota():
    settings = get_settings()
    old = (settings.max_instances_per_user, settings.max_gpus_per_user)
    settings.max_instances_per_user = 1
    settings.max_gpus_per_user = 1
    yield
    settings.max_instances_per_user, settings.max_gpus_per_user = old


async def _create(client: AsyncClient, headers: dict, sku_id: int, key_id: int):
    return await client.post(
        "/api/v1/instances",
        json={
            "sku_id": sku_id,
            "gpu_count": 1,
            "image_ref": IMAGE_PYTORCH,
            "ssh_key_ids": [key_id],
        },
        headers=headers,
    )


class TestUserQuota:
    async def test_count_capped_and_freed_on_release(
        self, client: AsyncClient, sm, fake, _tight_quota
    ):
        headers, user_id, key_id = await funded_user(client, sm, "13900000071")
        sku_id = await create_test_sku(sm)

        first = await _create(client, headers, sku_id, key_id)
        assert first.status_code == 202, first.text
        uuid = first.json()["uuid"]

        second = await _create(client, headers, sku_id, key_id)
        assert second.status_code == 400
        assert second.json()["message_key"] == "orchestrator.instanceQuota"

        await drain(sm)
        fake.mark_ready(f"tenant-{user_id}", uuid)
        await reconcile_once(sm)
        await client.post(f"/api/v1/instances/{uuid}/stop", headers=headers)
        await drain(sm)
        await reconcile_once(sm)
        await client.delete(f"/api/v1/instances/{uuid}", headers=headers)
        await drain(sm)
        await reconcile_once(sm)

        third = await _create(client, headers, sku_id, key_id)
        assert third.status_code == 202, third.text
