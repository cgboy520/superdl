"""结构化日志:structlog + stdlib 桥接(ProcessorFormatter),prod=JSON,dev/test=Console,
合并 contextvars;级别由 SUPERDL_LOG_LEVEL 控制;_mask_sensitive_processor 按字段名打码。"""

import logging
import re
import sys

import structlog
import structlog.tracebacks
from structlog.typing import EventDict, WrappedLogger

from app.core.config import get_settings
from app.core.regex import PHONE_RE_LOOSE

_BRIDGED_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")

_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}

_SENSITIVE_KEY_RE = re.compile(r"(phone|id_number|token|secret|password|code)", re.IGNORECASE)
_PHONE_VALUE_RE = re.compile(PHONE_RE_LOOSE)


def mask_phone_value(value: str) -> str:
    """匹配手机号时保留前 3 后 4 位,否则返回六个星号。"""
    if _PHONE_VALUE_RE.match(value):
        return value[:3] + "****" + value[-4:]
    return "******"


def _mask_value(key: str, value: object) -> object:
    if not isinstance(value, str):
        return value
    if "phone" in key.lower():
        return mask_phone_value(value)
    return "******"


def _mask_sensitive_processor(
    logger: WrappedLogger,  # noqa: ARG001
    method: str,  # noqa: ARG001
    event_dict: EventDict,
) -> EventDict:
    """按敏感键名遮蔽字符串;非敏感顶层键的 dict 值只检查下一层,不递归。"""
    for key, value in event_dict.items():
        if _SENSITIVE_KEY_RE.search(key):
            event_dict[key] = _mask_value(key, value)
        elif isinstance(value, dict):
            event_dict[key] = {
                k: _mask_value(k, v) if _SENSITIVE_KEY_RE.search(k) else v for k, v in value.items()
            }
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
