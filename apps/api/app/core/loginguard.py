"""Login rate-limit buckets: IP / IP+account / account 15-minute window / account daily window, one
mechanism shared by the user and admin sides, each declaring only its thresholds. `preflight`
buckets "count first, then decide" before bcrypt (atomic under concurrency) and refund this hit or
reset on valid credentials (`clear_on_success`); `preflight=False` buckets count only after a
failure and never gate admission."""

from collections.abc import Sequence
from dataclasses import dataclass

from app.core.ratelimit import check_rate_limit, clear_rate_limit, refund_hit


@dataclass(frozen=True)
class LoginBucket:
    key: str
    max_attempts: int
    window_seconds: float
    clear_on_success: bool = False
    preflight: bool = True


async def login_attempt(buckets: Sequence[LoginBucket]) -> None:
    """Before checking credentials: count and judge each preflight bucket, 429 when over (this
    attempt is already counted)."""
    for b in buckets:
        if b.preflight:
            await check_rate_limit(
                b.key, max_attempts=b.max_attempts, window_seconds=b.window_seconds
            )


async def login_failed(buckets: Sequence[LoginBucket], *, precounted: bool = False) -> None:
    """Wrong credentials: with precounted=True the preflight buckets were counted in login_attempt,
    so only the remaining buckets are counted."""
    for b in buckets:
        if precounted and b.preflight:
            continue
        await check_rate_limit(b.key, max_attempts=b.max_attempts, window_seconds=b.window_seconds)


async def login_succeeded(buckets: Sequence[LoginBucket], *, precounted: bool = False) -> None:
    """Valid credentials: clear_on_success buckets reset; the other pre-counted preflight buckets
    refund this hit."""
    for b in buckets:
        if b.clear_on_success:
            await clear_rate_limit(b.key)
        elif precounted and b.preflight:
            await refund_hit(b.key, window_seconds=b.window_seconds)
