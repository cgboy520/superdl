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

# uvicorn 日志器:清空自带 handler 交 root 并管
_BRIDGED_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")

_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}

# 敏感字段名(命中即打码)
_SENSITIVE_KEY_RE = re.compile(r"(phone|id_number|token|secret|password|code)", re.IGNORECASE)
_PHONE_VALUE_RE = re.compile(PHONE_RE_LOOSE)


def mask_phone_value(value: str) -> str:
    """手机号打码(前3后4):138****5678。全仓唯一打码实现。"""
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
    logger: WrappedLogger, method: str, event_dict: EventDict
) -> EventDict:
    """按键名打码:phone 前3后4,其余敏感键整体 ******;dict 值逐内层键检查;非字符串不动。"""
    for key, value in event_dict.items():
        if _SENSITIVE_KEY_RE.search(key):
            event_dict[key] = _mask_value(key, value)
        elif isinstance(value, dict):
            event_dict[key] = {
                k: _mask_value(k, v) if _SENSITIVE_KEY_RE.search(k) else v for k, v in value.items()
            }
    return event_dict


def setup_logging() -> None:
    settings = get_settings()
    level = _LEVELS[settings.log_level]
    shared_processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _mask_sensitive_processor,  # 渲染前兜底:structlog 侧与 stdlib 桥接侧共用
    ]
    if settings.environment == "prod":
        renderer: structlog.typing.Processor = structlog.processors.JSONRenderer()
    else:
        # 默认异常渲染在装了 rich 时带局部变量(含凭据,且深栈一次渲染几十秒),铉成纯文本栈
        renderer = structlog.dev.ConsoleRenderer(exception_formatter=structlog.dev.plain_traceback)

    # structlog 侧:处理链末端交回 ProcessorFormatter 渲染
    structlog.configure(
        processors=[*shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # stdlib 侧:外来 LogRecord 过 foreign_pre_chain 再经同一 renderer
    formatter_processors: list[structlog.typing.Processor] = [
        structlog.stdlib.ProcessorFormatter.remove_processors_meta,
    ]
    if settings.environment == "prod":
        # prod 把 exception 渲染成结构化栈帧;show_locals 必须关(局部变量含凭据);dev 的
        # ConsoleRenderer 自己渲染 exc_info,不叠加
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
