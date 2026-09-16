"""Shared patrol skeleton: items run independently, a failure counts a metric + leaves a trace and
never takes the round down."""

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
    """Call handler for each item (usually in its own transaction); an exception →
    `superdl_patrol_failed_total{stage}` +1 and the log event `patrol_<stage>_failed` with the
    ident(item) fields, then continue with the next item."""
    for item in items:
        try:
            await handler(item)
        except Exception:
            PATROL_FAILED_TOTAL.labels(stage=stage).inc()
            logger.exception(f"patrol_{stage}_failed", **ident(item))
