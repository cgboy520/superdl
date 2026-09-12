"""登录限流桶:IP / IP+账号 / 账号 15 分钟窗 / 账号日窗四层,用户端与管理端共用同一套机制,
各自只声明阈值表。全部桶只计失败;`preflight` 桶在 bcrypt 前做只读准入预检,
`clear_on_success` 桶在凭据正确后清零。"""

from collections.abc import Sequence
from dataclasses import dataclass

from app.core.ratelimit import check_rate_limit, clear_rate_limit, ensure_not_rate_limited


@dataclass(frozen=True)
class LoginBucket:
    key: str
    max_attempts: int
    window_seconds: float
    clear_on_success: bool = False
    preflight: bool = True


async def login_preflight(buckets: Sequence[LoginBucket]) -> None:
    """已封禁的桶在查库与 bcrypt 之前拦下(只读,不计数)。"""
    for b in buckets:
        if b.preflight:
            await ensure_not_rate_limited(
                b.key, max_attempts=b.max_attempts, window_seconds=b.window_seconds
            )


async def login_failed(buckets: Sequence[LoginBucket]) -> None:
    """凭据错误:四层同计一次。"""
    for b in buckets:
        await check_rate_limit(b.key, max_attempts=b.max_attempts, window_seconds=b.window_seconds)


async def login_succeeded(buckets: Sequence[LoginBucket]) -> None:
    """凭据正确:清零 clear_on_success 的桶。"""
    for b in buckets:
        if b.clear_on_success:
            await clear_rate_limit(b.key)
