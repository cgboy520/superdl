"""结构化日志:structlog 管道 + stdlib 桥接(ProcessorFormatter 模式)。

- 业务代码用 get_logger()(structlog);第三方库(uvicorn/sqlalchemy/kubernetes 等)
  走 stdlib logging,经 root logger 上挂的 ProcessorFormatter 进入同一渲染管道,
  两端输出格式一致(prod=JSON,dev/test=Console),且同样合并 contextvars
  (request_id 绑定见 observability 中间件)。
- 级别统一由 SUPERDL_LOG_LEVEL 控制(默认 INFO;structlog 过滤与 root level 同源)。
- PII/凭据全局兜底:_mask_sensitive_processor 按字段名打码
  (phone/id_number/token/secret/password/code),防新增日志点漏脱敏。
"""

import logging
import re
import sys

import structlog
import structlog.tracebacks
from structlog.typing import EventDict, WrappedLogger

from app.core.config import get_settings
from app.core.regex import PHONE_RE_LOOSE

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

# 敏感字段名(命中即打码):手机号/证件号/令牌/密钥/口令/验证码
_SENSITIVE_KEY_RE = re.compile(r"(phone|id_number|token|secret|password|code)", re.IGNORECASE)
_PHONE_VALUE_RE = re.compile(PHONE_RE_LOOSE)


def mask_phone_value(value: str) -> str:
    """手机号打码(前3后4):138****5678。全仓唯一的打码实现:日志处理器、core/sms
    与 modules 层(实名、管理端租户列表)都直接用它。"""
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
    """PII/凭据全局兜底打码(命名约定防线):键名命中 phone/id_number/token/secret/
    password/code 的值——phone 按前3后4打码,其余整体 ******(防长凭据部分可辨)。
    dict 值(如 params)外层键名不参与判定,逐内层键同款检查;非字符串值不动。"""
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
        # prod JSON 里把 logger.exception 渲染成结构化栈帧(否则只剩一行 event,无栈无行号)。
        # show_locals 必须关(ExceptionDictTransformer 默认为 True):异步栈帧握着 Settings /
        # session,局部变量入日志即把 database_url 口令与 jwt_secret 写成明文,而
        # _mask_sensitive_processor 拦不住(它先于本处理器跑,栈帧字典在其后才生成)。
        # 只能加在 prod:ConsoleRenderer 自己渲染 exc_info,叠加本处理器会 TypeError(list 拼 str)。
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
