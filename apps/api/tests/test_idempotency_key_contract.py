"""Contract-layer length gate of the Idempotency-Key header (every carrier column is
varchar(64))."""

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.params import IDEMPOTENCY_KEY_MAX_LENGTH
from app.modules.tickets.models import Ticket
from tests.helpers import create_user_with_key

pytestmark = pytest.mark.usefixtures("fake")

_BODY = {
    "category": "billing",
    "subject": "bill mismatch",
    "body": "this month's bill does not match the usage, please check.",
}


class TestIdempotencyKeyLength:
    async def test_header_bound_matches_every_backing_column(self):
        """The header cap equals the column width of every carrier table."""
        from sqlalchemy import String

        from app.models_registry import Base

        widths = {
            f"{table.name}.{col.name}": col.type.length
            for table in Base.metadata.tables.values()
            for col in table.columns
            if "idempotency_key" in col.name and isinstance(col.type, String)
        }
        assert widths, "no table with an idempotency key found: the scan is broken"
        assert set(widths.values()) == {IDEMPOTENCY_KEY_MAX_LENGTH}, widths

    async def test_over_long_key_is_422_not_500(self, client: AsyncClient, sm):
        """A 65-character key → 422."""
        headers, user_id, _ = await create_user_with_key(client, "13800000210")
        resp = await client.post(
            "/api/v1/tickets",
            json=_BODY,
            headers={**headers, "Idempotency-Key": "k" * (IDEMPOTENCY_KEY_MAX_LENGTH + 1)},
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["code"] == "VALIDATION_ERROR"
        assert any(e["loc"][-1] == "Idempotency-Key" for e in resp.json()["detail"]), resp.json()[
            "detail"
        ]
        async with sm() as session:
            rows = (
                (await session.execute(select(Ticket).where(Ticket.user_id == user_id)))
                .scalars()
                .all()
            )
        assert rows == []

    async def test_exactly_max_length_key_still_works(self, client: AsyncClient, sm):
        """The boundary value of 64 characters is accepted."""
        headers, _, _ = await create_user_with_key(client, "13800000211")
        key = "k" * IDEMPOTENCY_KEY_MAX_LENGTH
        first = await client.post(
            "/api/v1/tickets", json=_BODY, headers={**headers, "Idempotency-Key": key}
        )
        assert first.status_code == 201, first.text
        replay = await client.post(
            "/api/v1/tickets", json=_BODY, headers={**headers, "Idempotency-Key": key}
        )
        assert replay.status_code == 200
        assert replay.headers.get("X-Idempotent-Replay") == "true"
        assert replay.json()["id"] == first.json()["id"]
