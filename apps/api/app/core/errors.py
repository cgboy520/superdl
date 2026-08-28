"""统一错误体 {code, message, message_key, params, detail}。

message 恒为渲染后的中文;message_key/params 供前端查多语言目录
(core/messages.py 为单一事实源)。ErrorCode 是程序化分支依据。"""

from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Receive, Scope, Send


class ErrorCode(StrEnum):
    # 通用
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    CONFLICT = "CONFLICT"
    RATE_LIMITED = "RATE_LIMITED"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    INTERNAL = "INTERNAL"
    # 账户
    SMS_CODE_INVALID = "SMS_CODE_INVALID"
    SMS_TOO_FREQUENT = "SMS_TOO_FREQUENT"
    SMS_SEND_FAILED = "SMS_SEND_FAILED"
    CAPTCHA_REQUIRED = "CAPTCHA_REQUIRED"
    CAPTCHA_VERIFY_FAILED = "CAPTCHA_VERIFY_FAILED"
    CAPTCHA_CHANNEL_ERROR = "CAPTCHA_CHANNEL_ERROR"
    PHONE_TAKEN = "PHONE_TAKEN"
    LOGIN_FAILED = "LOGIN_FAILED"
    USER_FROZEN = "USER_FROZEN"
    SSH_KEY_INVALID = "SSH_KEY_INVALID"
    SSH_KEY_DUPLICATE = "SSH_KEY_DUPLICATE"
    REAL_NAME_MISMATCH = "REAL_NAME_MISMATCH"
    REAL_NAME_REQUIRED = "REAL_NAME_REQUIRED"
    REAL_NAME_CHANNEL_ERROR = "REAL_NAME_CHANNEL_ERROR"
    REAL_NAME_DISABLED = "REAL_NAME_DISABLED"
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
    # 服务型实例(对外 HTTPS 端点)
    SERVICE_ENDPOINT_NOT_FOUND = "SERVICE_ENDPOINT_NOT_FOUND"
    # 网关 extAuth 回调的唯一拒绝码:密钥错/已吊销/不属该端点/实例未运行一律同码同文案,
    # 调用方(可能是任意第三方)据此分不出被拒的具体原因
    API_KEY_INVALID = "API_KEY_INVALID"
    # 计费
    INSUFFICIENT_BALANCE = "INSUFFICIENT_BALANCE"
    # 包周期(预付订阅)
    SUBSCRIPTION_EXPIRED = "SUBSCRIPTION_EXPIRED"
    SUBSCRIPTION_NOT_RENEWABLE = "SUBSCRIPTION_NOT_RENEWABLE"
    # 存储
    DISK_IN_USE = "DISK_IN_USE"
    DISK_SHRINK_FORBIDDEN = "DISK_SHRINK_FORBIDDEN"
    # 支付
    ORDER_NOT_FOUND = "ORDER_NOT_FOUND"
    PAYMENT_CHANNEL_ERROR = "PAYMENT_CHANNEL_ERROR"
    # 管理端
    ADMIN_SECOND_REVIEW_REQUIRED = "ADMIN_SECOND_REVIEW_REQUIRED"
    MFA_TICKET_INVALID = "MFA_TICKET_INVALID"
    MFA_CODE_INVALID = "MFA_CODE_INVALID"
    MFA_NOT_BOUND = "MFA_NOT_BOUND"
    MFA_RESET_SELF_FORBIDDEN = "MFA_RESET_SELF_FORBIDDEN"


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
        headers: Mapping[str, str] | None = None,
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
        self.headers = dict(headers) if headers else None


def not_found(message: str | None = None, *, key: str | None = None) -> AppError:
    key = None if message is not None else (key or "common.notFound")
    return AppError(ErrorCode.NOT_FOUND, message, key=key, http_status=status.HTTP_404_NOT_FOUND)


def unauthorized(message: str | None = None, *, key: str | None = None) -> AppError:
    key = None if message is not None else (key or "common.unauthorized")
    return AppError(
        ErrorCode.UNAUTHORIZED, message, key=key, http_status=status.HTTP_401_UNAUTHORIZED
    )


def forbidden(
    message: str | None = None,
    *,
    key: str | None = None,
    params: Mapping[str, Any] | None = None,
) -> AppError:
    key = None if message is not None else (key or "common.forbidden")
    return AppError(
        ErrorCode.FORBIDDEN, message, key=key, params=params, http_status=status.HTTP_403_FORBIDDEN
    )


def _error_headers(http_status: int, headers: Mapping[str, str] | None) -> dict[str, str]:
    """统一响应头:401 一律带 WWW-Authenticate(RFC 6750),调用方自定义头合并保留。"""
    out = dict(headers or {})
    if http_status == status.HTTP_401_UNAUTHORIZED:
        out.setdefault("WWW-Authenticate", "Bearer")
    return out


def current_request_id() -> str | None:
    """错误体回带 request_id(observability 中间件绑定的 contextvar),便于凭单排障。"""
    import structlog

    value = structlog.contextvars.get_contextvars().get("request_id")
    return str(value) if value else None


# 框架层 HTTPException → 统一错误体的状态码映射;业务侧只抛 AppError,
# 框架自身只产生路由 404 与方法 405
_HTTP_STATUS_MAP: dict[int, tuple[ErrorCode, str]] = {
    status.HTTP_404_NOT_FOUND: (ErrorCode.NOT_FOUND, "common.notFound"),
    status.HTTP_405_METHOD_NOT_ALLOWED: (ErrorCode.METHOD_NOT_ALLOWED, "common.methodNotAllowed"),
}


def _unhandled_response(exc: Exception, *, path: str, method: str) -> JSONResponse:
    """未捕获异常的统一渲染(结构化留痕 + 统一错误体);exception handler 与
    Uniform500Middleware 共用同一出口。留痕经 structlog 进 Loki
    (见 deploy/cluster/runbooks/loki-logging.md),异常告警由 Loki 侧规则承接。"""
    from app.core.logging import get_logger

    get_logger("app.errors").exception("unhandled_exception", path=path, method=method)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "code": ErrorCode.INTERNAL.value,
            "message": "服务器内部错误,请稍后重试",
            "message_key": "common.internal",
            "params": None,
            "detail": None,
            "request_id": current_request_id(),
        },
    )


class Uniform500Middleware:
    """中间件链内层的未捕获异常兜底:500 必须在此渲染并沿链返回,才保得住安全响应头
    (SecurityHeaders)与 request_id(Observability)—— @app.exception_handler(Exception)
    由最外层 ServerErrorMiddleware 承接,跑在两者之外。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        try:
            await self.app(scope, receive, send)
        except Exception as exc:
            response = _unhandled_response(
                exc, path=scope.get("path", ""), method=scope.get("method", "")
            )
            await response(scope, receive, send)


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
                "request_id": current_request_id(),
            },
            headers=_error_headers(exc.http_status, exc.headers),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        _request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        """路由层 404/405 等框架异常也渲染统一错误体(否则前端拿到的是另一种形状)。"""
        from app.core.messages import render_message

        code, key = _HTTP_STATUS_MAP.get(
            exc.status_code,
            (ErrorCode.VALIDATION_ERROR, "common.validation")
            if exc.status_code < 500
            else (ErrorCode.INTERNAL, "common.internal"),
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "code": code.value,
                "message": render_message(key, None),
                "message_key": key,
                "params": None,
                "detail": jsonable_encoder(exc.detail),
                "request_id": current_request_id(),
            },
            headers=_error_headers(exc.status_code, exc.headers),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
        # 只回位置/原因/类型:pydantic errors() 的 input 是提交原值,回显即泄露凭据
        detail = [
            {"loc": e.get("loc"), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()
        ]
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={
                "code": ErrorCode.VALIDATION_ERROR.value,
                "message": "参数校验失败",
                "message_key": "common.validation",
                "params": None,
                "detail": jsonable_encoder(detail),
                "request_id": current_request_id(),
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        """未捕获异常兜底:结构化留痕 + 统一错误体。
        正常路径的 500 已被 Uniform500Middleware 在内层渲染(安全头/request_id 不丢);
        本 handler 只兜中间件自身的异常。"""
        return _unhandled_response(exc, path=request.url.path, method=request.method)
