"""结构化日志:structlog 管道 + stdlib 桥接(ProcessorFormatter 模式)。

- 业务代码用 get_logger()(structlog);第三方库(uvicorn/sqlalchemy/kubernetes 等)
  走 stdlib logging,经 root logger 上挂的 ProcessorFormatter 进入同一渲染管道,
  两端输出格式一致(prod=JSON,dev/test=Console),且同样合并 contextvars
  (request_id 绑定见 observability 中间件)。
- 级别统一由 SUPERDL_LOG_LEVEL 控制(默认 INFO;structlog 过滤与 root level 同源)。
"""

import logging
import sys

import structlog

from app.core.config import get_settings

# uvicorn 自带 handler 的日志器:清空其 handler 交给 root 并管,避免一行两格式。
# access 日志保留(经桥接进同一管道),不静默。
_BRIDGED_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")

_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


def setup_logging() -> None:
    settings = get_settings()
    level = _LEVELS[settings.log_level]
    shared_processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    if settings.environment == "prod":
        renderer: structlog.typing.Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    # structlog 侧:处理链末端交回 ProcessorFormatter(wrap_for_formatter),
    # 由它完成渲染 —— 与 stdlib 侧同一 formatter,输出零差异。
    structlog.configure(
        processors=[*shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # stdlib 侧:外来 LogRecord 先过 foreign_pre_chain(合并 contextvars/级别/时间戳),
    # 再经同一 renderer;remove_processors_meta 剥掉桥接内部键。
    formatter_processors: list[structlog.typing.Processor] = [
        structlog.stdlib.ProcessorFormatter.remove_processors_meta,
    ]
    if settings.environment == "prod":
        # dict_tracebacks:prod JSON 里 logger.exception 渲染成结构化栈帧
        # (否则生产 traceback 只剩一行 event,排障无栈无行号)。
        # dev 的 ConsoleRenderer 自己渲染 exc_info,不能加(dict_tracebacks 产 list,
        # ConsoleRenderer 按 str 拼接会 TypeError)
        formatter_processors.append(structlog.processors.dict_tracebacks)
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
