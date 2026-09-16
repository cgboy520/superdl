"""Deployment billing identity: currency and billing timezone are locked in a single row on first
boot so a later env change cannot silently re-key ledgers, bills and invoice periods."""

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.timeutil import now_utc
from app.modules.billing.models import BillingIdentity

logger = get_logger(__name__)

_MONEY_ROWS_SQL = text(
    "SELECT EXISTS (SELECT 1 FROM balance_ledger) OR EXISTS (SELECT 1 FROM orders)"
    " OR EXISTS (SELECT 1 FROM bills_hourly) OR EXISTS (SELECT 1 FROM bills_daily_disk)"
)


async def assert_billing_identity(session: AsyncSession) -> BillingIdentity:
    """Lock (first boot), accept (match) or refuse (mismatch with money rows) the env identity.

    A mismatch is re-keyed only while no ledger / order / bill row exists, or when
    `SUPERDL_BILLING_IDENTITY_REKEY=true` is set explicitly; both cases log an error.
    """
    settings = get_settings()
    want_currency, want_tz = settings.platform_currency, settings.billing_timezone
    await session.execute(
        pg_insert(BillingIdentity)
        .values(id=1, currency=want_currency, timezone=want_tz, locked_at=now_utc())
        .on_conflict_do_nothing(index_elements=["id"])
    )
    await session.commit()
    row = await session.get(BillingIdentity, 1, populate_existing=True)
    assert row is not None
    if (row.currency, row.timezone) == (want_currency, want_tz):
        return row
    has_money = bool((await session.execute(_MONEY_ROWS_SQL)).scalar())
    if has_money and not settings.billing_identity_rekey:
        raise RuntimeError(
            f"billing identity mismatch: the database is locked to {row.currency}/{row.timezone} "
            f"but SUPERDL_PLATFORM_CURRENCY / SUPERDL_BILLING_TIMEZONE say "
            f"{want_currency}/{want_tz}; "
            "set the env to match, or SUPERDL_BILLING_IDENTITY_REKEY=true to relock an already "
            "settled database (existing amounts and day/period keys are NOT converted)"
        )
    logger.error(
        "billing_identity_rekeyed",
        previous_currency=row.currency,
        previous_timezone=row.timezone,
        currency=want_currency,
        timezone=want_tz,
        forced=has_money,
    )
    row.currency, row.timezone, row.locked_at = want_currency, want_tz, now_utc()
    await session.commit()
    return row
