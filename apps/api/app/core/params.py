"""路由层共享的查询参数和幂等请求头声明。"""

from typing import Annotated

from fastapi import Header, Query

from app.core.pagination import MAX_LIMIT
from app.core.timeutil import BILLING_TZ_OFFSET_MINUTES

TzOffset = Query(default=BILLING_TZ_OFFSET_MINUTES, ge=-720, le=720)

Cursor = Query(default=None)
Limit = Query(default=None, le=MAX_LIMIT)

IDEMPOTENCY_KEY_MAX_LENGTH = 64
IdempotencyKey = Annotated[
    str | None, Header(alias="Idempotency-Key", max_length=IDEMPOTENCY_KEY_MAX_LENGTH)
]
