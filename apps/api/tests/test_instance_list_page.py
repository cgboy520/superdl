"""用户端实例列表:游标分页 + status/name 过滤。"""

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.helpers import create_user_with_key, seed_instance


async def _insert_instance(
    sm: async_sessionmaker[AsyncSession],
    user_id: int,
    *,
    name: str,
    status: str = "running",
) -> str:
    _id, uuid = await seed_instance(
        sm, user_id, name=name, status=status, spec={"gpu_model": "RTX4090"}, wallet_credit=False
    )
    return uuid


class TestInstanceListPage:
    async def test_status_filter(self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]):
        headers, user_id, _ = await create_user_with_key(client, "13900000202")
        await _insert_instance(sm, user_id, name="run-1", status="running")
        await _insert_instance(sm, user_id, name="stop-1", status="stopped")
        await _insert_instance(sm, user_id, name="gone", status="released")

        items = (
            await client.get("/api/v1/instances", params={"status": "stopped"}, headers=headers)
        ).json()["items"]
        assert [i["name"] for i in items] == ["stop-1"]
        # released 永不出列表(不带过滤时也不出现)
        all_items = (await client.get("/api/v1/instances", headers=headers)).json()["items"]
        assert "gone" not in [i["name"] for i in all_items]

    async def test_name_filter_matches_name_or_uuid_prefix(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        headers, user_id, _ = await create_user_with_key(client, "13900000203")
        target = await _insert_instance(sm, user_id, name="Train-Job")
        await _insert_instance(sm, user_id, name="other")

        by_name = (
            await client.get("/api/v1/instances", params={"name": "train"}, headers=headers)
        ).json()["items"]
        assert [i["name"] for i in by_name] == ["Train-Job"]  # 大小写不敏感

        by_uuid = (
            await client.get("/api/v1/instances", params={"name": target[:12]}, headers=headers)
        ).json()["items"]
        assert [i["uuid"] for i in by_uuid] == [target]

        none = (
            await client.get("/api/v1/instances", params={"name": "不存在"}, headers=headers)
        ).json()["items"]
        assert none == []

    async def test_name_filter_like_metachars_are_literal(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        """name/q 的 LIKE 元字符转义:"%" / "_" 按字面匹配,不当通配符(防一个 % 拖全表)。"""
        headers, user_id, _ = await create_user_with_key(client, "13900000208")
        await _insert_instance(sm, user_id, name="100%cotton")
        await _insert_instance(sm, user_id, name="1000jobs")

        literal = (
            await client.get("/api/v1/instances", params={"name": "100%"}, headers=headers)
        ).json()["items"]
        assert [i["name"] for i in literal] == ["100%cotton"]  # % 通配则 "1000jobs" 也会命中

        underscore = (
            await client.get("/api/v1/instances", params={"name": "100_cotton"}, headers=headers)
        ).json()["items"]
        assert underscore == []  # _ 通配则会命中 "100%cotton"

    async def test_filter_composes_with_cursor(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        headers, user_id, _ = await create_user_with_key(client, "13900000204")
        for i in range(3):
            await _insert_instance(sm, user_id, name=f"job-{i}", status="stopped")
        await _insert_instance(sm, user_id, name="job-x", status="running")

        page1 = (
            await client.get(
                "/api/v1/instances",
                params={"status": "stopped", "name": "job", "limit": 2},
                headers=headers,
            )
        ).json()
        assert [i["name"] for i in page1["items"]] == ["job-2", "job-1"]
        page2 = (
            await client.get(
                "/api/v1/instances",
                params={
                    "status": "stopped",
                    "name": "job",
                    "limit": 2,
                    "cursor": page1["next_cursor"],
                },
                headers=headers,
            )
        ).json()
        assert [i["name"] for i in page2["items"]] == ["job-0"]
        assert page2["next_cursor"] is None

    async def test_other_users_instances_invisible(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        headers, user_id, _ = await create_user_with_key(client, "13900000206")
        _headers2, user_id2, _ = await create_user_with_key(client, "13900000207")
        await _insert_instance(sm, user_id, name="mine")
        await _insert_instance(sm, user_id2, name="theirs")
        items = (await client.get("/api/v1/instances", headers=headers)).json()["items"]
        assert [i["name"] for i in items] == ["mine"]
