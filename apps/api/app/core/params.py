"""路由层共享的查询参数声明(单一定义点,防各路由漂移)。"""

from fastapi import Query

# 本地日界偏移量(分):默认 480 = 东八区;±720 上下界(覆盖 UTC-12 ~ UTC+12)。
# 前端一律传浏览器真实 offset(-new Date().getTimezoneOffset())。
TzOffset = Query(default=480, ge=-720, le=720)
