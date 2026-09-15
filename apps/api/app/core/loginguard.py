"""登录限流桶:IP / IP+账号 / 账号 15 分钟窗 / 账号日窗四层,用户端与管理端共用同一套机制,
各自只声明阈值表。`preflight` 桶在 bcrypt 前「先计数再判定」(并发下原子),凭据正确后退还本次
计数或清零(`clear_on_success`);`preflight=False` 的桶只在失败后计数、不做准入。"""

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
    """凭据校验前:preflight 桶逐个计数并判定,超限 429(本次尝试已计入)。"""
    for b in buckets:
        if b.preflight:
            await check_rate_limit(
                b.key, max_attempts=b.max_attempts, window_seconds=b.window_seconds
            )


async def login_failed(buckets: Sequence[LoginBucket], *, precounted: bool = False) -> None:
    """凭据错误:precounted=True 时 preflight 桶已在 login_attempt 计过,只补计其余桶。"""
    for b in buckets:
        if precounted and b.preflight:
            continue
        await check_rate_limit(b.key, max_attempts=b.max_attempts, window_seconds=b.window_seconds)


async def login_succeeded(buckets: Sequence[LoginBucket], *, precounted: bool = False) -> None:
    """凭据正确:clear_on_success 桶清零;其余已预计数的 preflight 桶退还本次命中。"""
    for b in buckets:
        if b.clear_on_success:
            await clear_rate_limit(b.key)
        elif precounted and b.preflight:
            await refund_hit(b.key, window_seconds=b.window_seconds)
