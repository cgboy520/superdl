"""路由层共享的查询参数声明。"""

from typing import Annotated

from fastapi import Header, Query

from app.core.pagination import MAX_LIMIT
from app.core.timeutil import BILLING_TZ_OFFSET_MINUTES

# 本地日界偏移量(分):默认北京时间,±720;前端传 -new Date().getTimezoneOffset()
TzOffset = Query(default=BILLING_TZ_OFFSET_MINUTES, ge=-720, le=720)

# 游标分页:cursor 为不透明 base64(pagination.py),limit 封顶 MAX_LIMIT
Cursor = Query(default=None)
Limit = Query(default=None, le=MAX_LIMIT)

# 创建类接口的幂等键头(core/idempotency.py);max_length 与各表 idempotency_key 列宽 varchar(64) 对齐
IDEMPOTENCY_KEY_MAX_LENGTH = 64
IdempotencyKey = Annotated[
    str | None, Header(alias="Idempotency-Key", max_length=IDEMPOTENCY_KEY_MAX_LENGTH)
]
