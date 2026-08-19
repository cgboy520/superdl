"""进程内滑动窗口限流。MVP 单实例足够;多副本部署时换 PG/Redis 计数(接口不变)。"""

import time
from collections import deque

from fastapi import status

from app.core.errors import AppError, ErrorCode

_buckets: dict[str, deque[float]] = {}


def check_rate_limit(key: str, *, max_attempts: int, window_seconds: float) -> None:
    """超限抛 RATE_LIMITED。key 建议含维度前缀,如 'admin-login:1.2.3.4:alice'。"""
    now = time.monotonic()
    bucket = _buckets.setdefault(key, deque())
    while bucket and now - bucket[0] > window_seconds:
        bucket.popleft()
    if len(bucket) >= max_attempts:
        raise AppError(
            ErrorCode.RATE_LIMITED,
            "尝试过于频繁,请稍后再试",
            http_status=status.HTTP_429_TOO_MANY_REQUESTS,
        )
    bucket.append(now)


def reset(key_prefix: str = "") -> None:
    """测试辅助:清空计数。"""
    if not key_prefix:
        _buckets.clear()
        return
    for k in list(_buckets):
        if k.startswith(key_prefix):
            del _buckets[k]
