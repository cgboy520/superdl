"""The balance patrol always uses the available balance (balance − frozen) for wallets frozen by a
channel reversal;
the data-disk frozen-deletion clock is not reset by payment, the next freeze continues it."""

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, update

from app.core.money import money_label
from app.core.timeutil import now_utc
from app.modules.billing import wallet
from app.modules.billing.models import Wallet
from app.modules.billing.patrol import balance_patrol
from app.modules.orchestrator.models import DataDisk, Instance
from app.modules.orchestrator.reconciler import reconcile_once
from tests.helpers import create_disk, drain, funded_user, get_instance, provision_running

pytestmark = pytest.mark.usefixtures("fake")


async def _freeze(sm, user_id: int, amount: str) -> None:
    """Freeze the given amount."""
    async with sm() as session:
        await wallet.freeze(
            session,
            user_id,
            Decimal(amount),
            ref_id="ord-reversed",
            remark="channel reversal freeze",
        )
        await session.commit()


async def _wallet_of(sm, user_id: int) -> Wallet:
    async with sm() as session:
        return (await session.execute(select(Wallet).where(Wallet.user_id == user_id))).scalar_one()


async def _drive_to_frozen(client, sm, fake) -> tuple[dict, str, int]:
    """Full reversal → stop → freeze. Returns (headers, uuid, user_id), the instance ends frozen."""
    headers, uuid, user_id = await provision_running(client, sm, fake)
    await _freeze(sm, user_id, "100.00")
    assert (await balance_patrol(sm))["stopped"] == 1
    await drain(sm)
    await reconcile_once(sm)
    assert (await balance_patrol(sm))["frozen"] == 1
    assert (await get_instance(client, headers, uuid))["status"] == "frozen"
    return headers, uuid, user_id


class TestFrozenWalletArrears:
    async def test_fully_frozen_wallet_stops_running_instance(self, client, sm, fake):
        """Raw balance 100 but fully frozen: the instance stops."""
        headers, uuid, user_id = await provision_running(client, sm, fake)
        await _freeze(sm, user_id, "100.00")

        counts = await balance_patrol(sm)
        assert counts["stopped"] == 1
        assert (await get_instance(client, headers, uuid))["status"] == "stopping"
        w = await _wallet_of(sm, user_id)
        assert w.balance == Decimal("100.00") and w.frozen == Decimal("100.00")
        events = (await client.get(f"/api/v1/instances/{uuid}/events", headers=headers)).json()[
            "items"
        ]
        assert events[0]["reason"] == "arrears_stop"

    async def test_fully_frozen_wallet_does_not_unfreeze_instance(self, client, sm, fake):
        """A frozen instance with "raw balance > 0 but fully frozen": no unfreeze, the reclamation
        countdown continues."""
        headers, uuid, _user_id = await _drive_to_frozen(client, sm, fake)

        counts = await balance_patrol(sm)
        assert counts["unfrozen"] == 0
        data = await get_instance(client, headers, uuid)
        assert data["status"] == "frozen"
        assert data["frozen_deadline"] is not None

        async with sm() as session:
            await session.execute(
                update(Instance)
                .where(Instance.uuid == uuid)
                .values(frozen_deadline=now_utc() - timedelta(hours=1))
            )
            await session.commit()
        assert (await balance_patrol(sm))["reclaimed"] == 1
        assert (await get_instance(client, headers, uuid))["status"] == "releasing"

    async def test_partial_freeze_warns_on_available_and_keeps_running(self, client, sm, fake):
        """Partially frozen with available room: no stop, the warning uses the available balance."""
        headers, uuid, user_id = await provision_running(client, sm, fake)
        await _freeze(sm, user_id, "70.00")

        counts = await balance_patrol(sm)
        assert counts["stopped"] == 0
        assert counts["warned"] == 1
        assert (await get_instance(client, headers, uuid))["status"] == "running"
        notes = (await client.get("/api/v1/notifications", headers=headers)).json()["items"]
        warn = next(n for n in notes if n["title"] == "Low balance warning")
        assert money_label("30.00") in warn["content"]


async def _drain_wallet(sm, user_id: int) -> None:
    async with sm() as session:
        balance = await wallet.get_balance(session, user_id)
        await wallet.debit(
            session, user_id, balance, type_="adjust", remark="drain", allow_negative=True
        )
        await session.commit()


async def _disk_row(sm, uuid: str) -> DataDisk:
    async with sm() as session:
        return (await session.execute(select(DataDisk).where(DataDisk.uuid == uuid))).scalar_one()


async def _set_disk(sm, uuid: str, **values) -> None:
    async with sm() as session:
        await session.execute(update(DataDisk).where(DataDisk.uuid == uuid).values(**values))
        await session.commit()


async def _freeze_disk(client, sm, phone: str) -> tuple[str, int]:
    """Create a disk → arrears → grace → frozen. Returns (disk uuid, user_id)."""
    headers, user_id, _key = await funded_user(client, sm, phone)
    disk = await create_disk(client, headers)
    await _drain_wallet(sm, user_id)
    await balance_patrol(sm)
    await _set_disk(sm, disk["uuid"], grace_started_at=now_utc() - timedelta(days=8))
    await balance_patrol(sm)
    assert (await _disk_row(sm, disk["uuid"])).status == "frozen"
    return disk["uuid"], user_id


class TestFrozenDiskClock:
    async def test_refreeze_resumes_original_deadline(self, client, sm, fake):
        """Top up on day 29 of the freeze, then arrears again: frozen_started_at is kept, deleted on
        the original deadline instead of waiting another 30 days."""
        uuid, user_id = await _freeze_disk(client, sm, "13500000060")
        t0 = now_utc() - timedelta(days=29)
        await _set_disk(sm, uuid, frozen_started_at=t0)

        async with sm() as session:
            await wallet.credit(session, user_id, Decimal("10.00"), type_="recharge")
            await session.commit()
        await balance_patrol(sm)
        row = await _disk_row(sm, uuid)
        assert row.status == "active"
        assert row.frozen_started_at == t0 and row.grace_ended_at is not None

        await _drain_wallet(sm, user_id)
        await balance_patrol(sm)
        assert (await _disk_row(sm, uuid)).status == "grace"
        await balance_patrol(sm)
        row = await _disk_row(sm, uuid)
        assert row.status == "frozen" and row.frozen_started_at == t0

        await balance_patrol(sm)
        assert (await _disk_row(sm, uuid)).status == "frozen"
        await _set_disk(sm, uuid, frozen_started_at=t0 - timedelta(days=2))
        await balance_patrol(sm)
        assert (await _disk_row(sm, uuid)).status == "deleting"

    async def test_long_paid_up_period_resets_frozen_clock(self, client, sm, fake):
        """Staying in good standing beyond disk_frozen_days after payment: the next freeze starts
        from scratch."""
        uuid, user_id = await _freeze_disk(client, sm, "13500000061")
        t0 = now_utc() - timedelta(days=29)
        await _set_disk(sm, uuid, frozen_started_at=t0)
        async with sm() as session:
            await wallet.credit(session, user_id, Decimal("10.00"), type_="recharge")
            await session.commit()
        await balance_patrol(sm)
        await _set_disk(sm, uuid, grace_ended_at=now_utc() - timedelta(days=31))

        await _drain_wallet(sm, user_id)
        await balance_patrol(sm)
        assert (await _disk_row(sm, uuid)).frozen_started_at is None
        await balance_patrol(sm)
        row = await _disk_row(sm, uuid)
        assert row.status == "frozen"
        assert row.frozen_started_at is not None and row.frozen_started_at > t0 + timedelta(days=28)
