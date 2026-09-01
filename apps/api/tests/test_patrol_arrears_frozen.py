"""余额巡检对「渠道冲正冻结」钱包的判据:一律走可用余额(balance − frozen)。

它挂了说明:被渠道冲正(信用卡拒付/商户后台退款)的账号裸余额还挂着钱,巡检就当他
还买得起算力——在跑的实例不停机、已冻结的实例还被解冻取消回收倒计时,而结算侧
`allow_frozen=True` 一路照扣,冲正金额全变成白送的 GPU 时。
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, update

from app.core.timeutil import now_utc
from app.modules.billing import wallet
from app.modules.billing.models import Wallet
from app.modules.billing.patrol import balance_patrol
from app.modules.orchestrator.models import Instance
from app.modules.orchestrator.reconciler import reconcile_once
from tests.helpers import drain, get_instance, provision_running

pytestmark = pytest.mark.usefixtures("fake")


async def _freeze(sm, user_id: int, amount: str) -> None:
    """等额冻结(等价 payment_service.handle_callback 收到已入账订单的冲正通知)。"""
    async with sm() as session:
        await wallet.freeze(
            session, user_id, Decimal(amount), ref_id="ord-reversed", remark="渠道冲正冻结"
        )
        await session.commit()


async def _wallet_of(sm, user_id: int) -> Wallet:
    async with sm() as session:
        return (await session.execute(select(Wallet).where(Wallet.user_id == user_id))).scalar_one()


async def _drive_to_frozen(client, sm, fake) -> tuple[dict, str, int]:
    """全额冲正 → 停机 → 冻结。返回 (headers, uuid, user_id),实例停在 frozen。"""
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
        """裸余额 100 但全额冻结:实例必须被停机。

        挂了 = 冲正后的账号继续跑 GPU(结算照扣、可用余额早已 ≤ 0),直到人工核销。
        """
        headers, uuid, user_id = await provision_running(client, sm, fake)
        await _freeze(sm, user_id, "100.00")

        counts = await balance_patrol(sm)
        assert counts["stopped"] == 1
        assert (await get_instance(client, headers, uuid))["status"] == "stopping"
        # 判据是可用余额而非裸余额:冻结不动 balance,钱还挂在账上
        w = await _wallet_of(sm, user_id)
        assert w.balance == Decimal("100.00") and w.frozen == Decimal("100.00")
        # 停机边留痕(结算据此停费)
        events = (await client.get(f"/api/v1/instances/{uuid}/events", headers=headers)).json()[
            "items"
        ]
        assert events[0]["reason"] == "arrears_stop"

    async def test_fully_frozen_wallet_does_not_unfreeze_instance(self, client, sm, fake):
        """已冻结实例遇上「裸余额 > 0 但全额冻结」:不解冻,回收倒计时照走到底。

        挂了 = 冲正金额把实例从回收队列里救回来,等于用一笔正在被追回的充值续命。
        """
        headers, uuid, _user_id = await _drive_to_frozen(client, sm, fake)

        counts = await balance_patrol(sm)
        assert counts["unfrozen"] == 0
        data = await get_instance(client, headers, uuid)
        assert data["status"] == "frozen"
        assert data["frozen_deadline"] is not None

        # 倒计时没被取消:到期照常回收
        async with sm() as session:
            await session.execute(
                update(Instance)
                .where(Instance.uuid == uuid)
                .values(frozen_deadline=now_utc() - timedelta(hours=1))
            )
            await session.commit()
        assert (await balance_patrol(sm))["reclaimed"] == 1
        assert (await get_instance(client, headers, uuid))["status"] == "releasing"

    async def test_released_freeze_unfreezes_instance(self, client, sm, fake):
        """对照:人工核销解冻后可用余额回正,实例照常解冻回 stopped。

        挂了 = 判据从「可用余额」滑成了「永不解冻」,核销完的用户开不了机。
        """
        headers, uuid, user_id = await _drive_to_frozen(client, sm, fake)
        async with sm() as session:
            await wallet.release_freeze(session, user_id, Decimal("100.00"))
            await session.commit()

        assert (await balance_patrol(sm))["unfrozen"] == 1
        data = await get_instance(client, headers, uuid)
        assert data["status"] == "stopped"
        assert data["frozen_deadline"] is None

    async def test_partial_freeze_warns_on_available_and_keeps_running(self, client, sm, fake):
        """部分冻结仍有可用额度:不停机,但预警按可用余额算(裸余额算会漏掉预警)。

        挂了 = 要么把还买得起算力的用户误停机,要么预警对着一笔已被冻结的钱报「还能跑很久」。
        """
        headers, uuid, user_id = await provision_running(client, sm, fake)
        # 100 − 70 = 30 可用,单价 1.68/h ≈ 17.9h < 默认预警阈值 24h
        await _freeze(sm, user_id, "70.00")

        counts = await balance_patrol(sm)
        assert counts["stopped"] == 0
        assert counts["warned"] == 1
        assert (await get_instance(client, headers, uuid))["status"] == "running"
        notes = (await client.get("/api/v1/notifications", headers=headers)).json()["items"]
        warn = next(n for n in notes if n["title"] == "余额不足预警")
        assert "¥30.00" in warn["content"]
