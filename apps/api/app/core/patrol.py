"""巡检公用骨架:逐项独立执行,单项失败计指标 + 留痕,不拖垮整轮。"""

from collections.abc import Awaitable, Callable, Iterable, Mapping

from app.core.logging import get_logger
from app.core.metrics import PATROL_FAILED_TOTAL

logger = get_logger(__name__)


async def for_each[T](
    items: Iterable[T],
    handler: Callable[[T], Awaitable[None]],
    *,
    stage: str,
    ident: Callable[[T], Mapping[str, object]],
) -> None:
    """对每一项调用 handler(通常自开事务);异常 → `superdl_patrol_failed_total{stage}` +1,
    日志事件 `patrol_<stage>_failed` 带 ident(item) 的定位字段,继续下一项。"""
    for item in items:
        try:
            await handler(item)
        except Exception:
            PATROL_FAILED_TOTAL.labels(stage=stage).inc()
            logger.exception(f"patrol_{stage}_failed", **ident(item))
