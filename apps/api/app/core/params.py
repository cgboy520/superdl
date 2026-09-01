"""路由层共享的查询参数声明(单一定义点,防各路由漂移)。"""

from typing import Annotated

from fastapi import Header, Query

from app.core.pagination import MAX_LIMIT
from app.core.timeutil import BILLING_TZ_OFFSET_MINUTES

# 本地日界偏移量(分):默认北京时间;±720 上下界(覆盖 UTC-12 ~ UTC+12)。
# 前端一律传浏览器真实 offset(-new Date().getTimezoneOffset())。
TzOffset = Query(default=BILLING_TZ_OFFSET_MINUTES, ge=-720, le=720)

# 游标分页两参数:cursor 为不透明 base64(见 pagination.py),limit 封顶 MAX_LIMIT
Cursor = Query(default=None)
Limit = Query(default=None, le=MAX_LIMIT)

# 创建类写接口的幂等键头(重放语义见 core/idempotency.py)。
# max_length 与全部承载表的 idempotency_key 列宽(varchar(64))对齐:契约层不挡的话,
# 超长键会在 INSERT 时抛 DataError(StringDataRightTruncation),而 idempotency.py 只接
# IntegrityError —— 任何已登录用户都能把创建类端点变成 500。
IDEMPOTENCY_KEY_MAX_LENGTH = 64
IdempotencyKey = Annotated[
    str | None, Header(alias="Idempotency-Key", max_length=IDEMPOTENCY_KEY_MAX_LENGTH)
]
