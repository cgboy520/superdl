"""统一错误体 {code, message, message_key, params, detail}。

message 恒为渲染后的中文(旧客户端兜底);message_key/params 供前端查多语言目录
(core/messages.py 为单一事实源)。ErrorCode 仍是程序化分支依据。"""

from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class ErrorCode(StrEnum):
    # 通用
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    CONFLICT = "CONFLICT"
    RATE_LIMITED = "RATE_LIMITED"
    INTERNAL = "INTERNAL"
    # 账户
    SMS_CODE_INVALID = "SMS_CODE_INVALID"
    SMS_TOO_FREQUENT = "SMS_TOO_FREQUENT"
    SMS_SEND_FAILED = "SMS_SEND_FAILED"
    PHONE_TAKEN = "PHONE_TAKEN"
    LOGIN_FAILED = "LOGIN_FAILED"
    USER_FROZEN = "USER_FROZEN"
    SSH_KEY_INVALID = "SSH_KEY_INVALID"
    SSH_KEY_DUPLICATE = "SSH_KEY_DUPLICATE"
    REAL_NAME_MISMATCH = "REAL_NAME_MISMATCH"
    REAL_NAME_REQUIRED = "REAL_NAME_REQUIRED"
    REAL_NAME_CHANNEL_ERROR = "REAL_NAME_CHANNEL_ERROR"
    TERMS_NOT_ACCEPTED = "TERMS_NOT_ACCEPTED"
    # 商品/库存
    SKU_NOT_ON_SALE = "SKU_NOT_ON_SALE"
    SKU_NOT_SELLABLE = "SKU_NOT_SELLABLE"
    # 实例
    INSTANCE_INVALID_TRANSITION = "INSTANCE_INVALID_TRANSITION"
    CLUSTER_NOT_READY = "CLUSTER_NOT_READY"
    INSTANCE_NOT_STOPPED = "INSTANCE_NOT_STOPPED"
    INSTANCE_FROZEN = "INSTANCE_FROZEN"
    NO_CAPACITY = "NO_CAPACITY"
    # 计费
    INSUFFICIENT_BALANCE = "INSUFFICIENT_BALANCE"
    WALLET_FROZEN = "WALLET_FROZEN"
    # 存储
    DISK_IN_USE = "DISK_IN_USE"
    DISK_SHRINK_FORBIDDEN = "DISK_SHRINK_FORBIDDEN"
    # 支付
    ORDER_NOT_FOUND = "ORDER_NOT_FOUND"
    PAYMENT_CHANNEL_ERROR = "PAYMENT_CHANNEL_ERROR"
    # 管理端
    ADMIN_SECOND_REVIEW_REQUIRED = "ADMIN_SECOND_REVIEW_REQUIRED"


class AppError(Exception):
    """业务异常。service 层抛出,统一 handler 转 HTTP 响应。"""

    def __init__(
        self,
        code: ErrorCode,
        message: str | None = None,
        *,
        key: str | None = None,
        params: Mapping[str, Any] | None = None,
        http_status: int = status.HTTP_400_BAD_REQUEST,
        detail: Any = None,
    ) -> None:
        if key is not None:
            from app.core.messages import render_message

            message = render_message(key, params)
        elif message is None:
            raise ValueError("AppError 需要 message 或 key 之一")
        super().__init__(message)
        self.code = code
        self.message = message
        self.message_key = key
        self.params = dict(params) if params else None
        self.http_status = http_status
        self.detail = detail


def not_found(message: str | None = None, *, key: str | None = None) -> AppError:
    key = None if message is not None else (key or "common.notFound")
    return AppError(ErrorCode.NOT_FOUND, message, key=key, http_status=status.HTTP_404_NOT_FOUND)


def unauthorized(message: str | None = None, *, key: str | None = None) -> AppError:
    key = None if message is not None else (key or "common.unauthorized")
    return AppError(
        ErrorCode.UNAUTHORIZED, message, key=key, http_status=status.HTTP_401_UNAUTHORIZED
    )


def forbidden(message: str | None = None, *, key: str | None = None) -> AppError:
    key = None if message is not None else (key or "common.forbidden")
    return AppError(ErrorCode.FORBIDDEN, message, key=key, http_status=status.HTTP_403_FORBIDDEN)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content={
                "code": exc.code.value,
                "message": exc.message,
                "message_key": exc.message_key,
                "params": jsonable_encoder(exc.params),
                "detail": jsonable_encoder(exc.detail),
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "code": ErrorCode.VALIDATION_ERROR.value,
                "message": "参数校验失败",
                "message_key": "common.validation",
                "params": None,
                "detail": jsonable_encoder(exc.errors()),
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        """未捕获异常兜底:结构化留痕 + 上报 + 统一错误体。"""
        from app.core.logging import get_logger

        get_logger("app.errors").exception(
            "unhandled_exception", path=request.url.path, method=request.method
        )
        _capture_exception(exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "code": ErrorCode.INTERNAL.value,
                "message": "服务器内部错误,请稍后重试",
                "message_key": "common.internal",
                "params": None,
                "detail": None,
            },
        )


try:  # pragma: no cover - 可选依赖,缺失即降级为 no-op
    import sentry_sdk  # type: ignore[import-not-found]
except ImportError:
    sentry_sdk = None


def _capture_exception(exc: Exception) -> None:
    """Sentry seam:配置 SUPERDL_SENTRY_DSN 且安装 sentry-sdk 才生效,否则静默跳过。"""
    from app.core.config import get_settings

    if sentry_sdk is not None and get_settings().sentry_dsn:
        sentry_sdk.capture_exception(exc)


def init_sentry() -> None:
    """启动时初始化 Sentry(可选依赖,未安装仅告警一次)。"""
    from app.core.config import get_settings
    from app.core.logging import get_logger

    dsn = get_settings().sentry_dsn
    if not dsn:
        return
    if sentry_sdk is None:
        get_logger("app.errors").warning("sentry_dsn_set_but_sdk_missing")
        return
    sentry_sdk.init(dsn=dsn, environment=get_settings().environment)
