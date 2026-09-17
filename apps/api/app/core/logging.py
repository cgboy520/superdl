"""结构化日志:structlog + stdlib 桥接(ProcessorFormatter),prod=JSON,dev/test=Console,
合并 contextvars;级别由 SUPERDL_LOG_LEVEL 控制;_mask_sensitive_processor 按字段名打码。"""

import logging
import re
import sys

import structlog
import structlog.tracebacks
from structlog.typing import EventDict, WrappedLogger

from app.core.config import get_settings
from app.core.handles import mask_handle

_BRIDGED_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")

_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}

_SENSITIVE_KEY_RE = re.compile(
    r"(phone|email|handle|id_number|token|secret|password|passwd|code|api_key|apikey|jwt"
    r"|authorization|credential|private_key|cookie|session|totp|recovery)",
    re.IGNORECASE,
)
_MASK_MAX_DEPTH = 4


def _mask_value(key: str, value: object) -> object:
    if not isinstance(value, str):
        return value
    if any(marker in key.lower() for marker in ("phone", "email", "handle")):
        return mask_handle(value)
    return "******"


def _mask_all_strings(key: str, value: object, depth: int) -> object:
    """敏感键下的容器:所有字符串叶子一律打码,深度封顶。"""
    if isinstance(value, str):
        return _mask_value(key, value)
    if depth >= _MASK_MAX_DEPTH:
        return value
    if isinstance(value, dict):
        return {k: _mask_all_strings(key, v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask_all_strings(key, v, depth + 1) for v in value]
    return value


def _mask_field(key: str, value: object, depth: int) -> object:
    """按键名遮蔽;非敏感键的 dict / list 递归检查内层键,深度封顶 4。"""
    if _SENSITIVE_KEY_RE.search(key):
        return _mask_all_strings(key, value, depth)
    if depth >= _MASK_MAX_DEPTH:
        return value
    if isinstance(value, dict):
        return {k: _mask_field(str(k), v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask_field("", v, depth + 1) for v in value]
    return value


def _mask_sensitive_processor(
    logger: WrappedLogger,  # noqa: ARG001
    method: str,  # noqa: ARG001
    event_dict: EventDict,
) -> EventDict:
    """按敏感键名遮蔽字符串,嵌套 dict / list 递归到第 4 层。"""
    for key, value in event_dict.items():
        event_dict[key] = _mask_field(key, value, 0)
    return event_dict


def setup_logging() -> None:
    """配置 structlog 和 stdlib 日志;异常栈不得包含局部变量。"""
    settings = get_settings()
    level = _LEVELS[settings.log_level]
    shared_processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _mask_sensitive_processor,
    ]
    if settings.environment == "prod":
        renderer: structlog.typing.Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(exception_formatter=structlog.dev.plain_traceback)

    structlog.configure(
        processors=[*shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter_processors: list[structlog.typing.Processor] = [
        structlog.stdlib.ProcessorFormatter.remove_processors_meta,
    ]
    if settings.environment == "prod":
        formatter_processors.append(
            structlog.processors.ExceptionRenderer(
                structlog.tracebacks.ExceptionDictTransformer(show_locals=False)
            )
        )
    formatter_processors.append(renderer)
    formatter = structlog.stdlib.ProcessorFormatter(
        processors=formatter_processors,
        foreign_pre_chain=shared_processors,
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    for name in _BRIDGED_LOGGERS:
        third_party = logging.getLogger(name)
        third_party.handlers.clear()
        third_party.propagate = True


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
