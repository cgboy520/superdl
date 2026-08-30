"""路由层共享的查询参数声明(单一定义点,防各路由漂移)。"""

from fastapi import Query

from app.core.pagination import MAX_LIMIT
from app.core.timeutil import BILLING_TZ_OFFSET_MINUTES

# 本地日界偏移量(分):默认北京时间;±720 上下界(覆盖 UTC-12 ~ UTC+12)。
# 前端一律传浏览器真实 offset(-new Date().getTimezoneOffset())。
TzOffset = Query(default=BILLING_TZ_OFFSET_MINUTES, ge=-720, le=720)

# 游标分页两参数:cursor 为不透明 base64(见 pagination.py),limit 封顶 MAX_LIMIT
Cursor = Query(default=None)
Limit = Query(default=None, le=MAX_LIMIT)
