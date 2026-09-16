"""Unified error body {code, message, message_key, params, detail, request_id}; `message` is the
rendered English text, `message_key` / `params` let clients render their own locale
(core/messages.py)."""

from collections.abc import Mapping
from enum import StrEnum
from typing import Any

import structlog
from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.logging import get_logger
from app.core.messages import render_message


class ErrorCode(StrEnum):
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    CONFLICT = "CONFLICT"
    RATE_LIMITED = "RATE_LIMITED"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
    AUDIT_UNAVAILABLE = "AUDIT_UNAVAILABLE"
    INTERNAL = "INTERNAL"
    CODE_INVALID = "CODE_INVALID"
    CODE_TOO_FREQUENT = "CODE_TOO_FREQUENT"
    CODE_SEND_FAILED = "CODE_SEND_FAILED"
    CAPTCHA_REQUIRED = "CAPTCHA_REQUIRED"
    CAPTCHA_VERIFY_FAILED = "CAPTCHA_VERIFY_FAILED"
    CAPTCHA_CHANNEL_ERROR = "CAPTCHA_CHANNEL_ERROR"
    HANDLE_TAKEN = "HANDLE_TAKEN"
    LOGIN_FAILED = "LOGIN_FAILED"
    USER_FROZEN = "USER_FROZEN"
    SSH_KEY_INVALID = "SSH_KEY_INVALID"
    SSH_KEY_DUPLICATE = "SSH_KEY_DUPLICATE"
    REAL_NAME_MISMATCH = "REAL_NAME_MISMATCH"
    REAL_NAME_REQUIRED = "REAL_NAME_REQUIRED"
    REAL_NAME_CHANNEL_ERROR = "REAL_NAME_CHANNEL_ERROR"
    REAL_NAME_DISABLED = "REAL_NAME_DISABLED"
    TERMS_NOT_ACCEPTED = "TERMS_NOT_ACCEPTED"
    SKU_NOT_ON_SALE = "SKU_NOT_ON_SALE"
    SKU_NOT_SELLABLE = "SKU_NOT_SELLABLE"
    INSTANCE_INVALID_TRANSITION = "INSTANCE_INVALID_TRANSITION"
    CLUSTER_NOT_READY = "CLUSTER_NOT_READY"
    INSTANCE_NOT_STOPPED = "INSTANCE_NOT_STOPPED"
    INSTANCE_FROZEN = "INSTANCE_FROZEN"
    NO_CAPACITY = "NO_CAPACITY"
    API_KEY_INVALID = "API_KEY_INVALID"
    INSUFFICIENT_BALANCE = "INSUFFICIENT_BALANCE"
    SUBSCRIPTION_EXPIRED = "SUBSCRIPTION_EXPIRED"
    SUBSCRIPTION_NOT_RENEWABLE = "SUBSCRIPTION_NOT_RENEWABLE"
    DISK_IN_USE = "DISK_IN_USE"
    DISK_SHRINK_FORBIDDEN = "DISK_SHRINK_FORBIDDEN"
    ORDER_NOT_FOUND = "ORDER_NOT_FOUND"
    PAYMENT_CHANNEL_ERROR = "PAYMENT_CHANNEL_ERROR"
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


def _make(
    code: ErrorCode,
    http_status: int,
    default_key: str,
    message: str | None,
    key: str | None,
    params: Mapping[str, Any] | None = None,
    detail: Any = None,
) -> AppError:
    """给定 message 时不带文案键;否则用 key 或该状态的默认键。"""
    key = None if message is not None else (key or default_key)
    return AppError(code, message, key=key, params=params, http_status=http_status, detail=detail)


def not_found(message: str | None = None, *, key: str | None = None) -> AppError:
    return _make(ErrorCode.NOT_FOUND, status.HTTP_404_NOT_FOUND, "common.notFound", message, key)


def unauthorized(message: str | None = None, *, key: str | None = None) -> AppError:
    return _make(
        ErrorCode.UNAUTHORIZED, status.HTTP_401_UNAUTHORIZED, "common.unauthorized", message, key
    )


def forbidden(
    message: str | None = None,
    *,
    key: str | None = None,
    params: Mapping[str, Any] | None = None,
) -> AppError:
    return _make(
        ErrorCode.FORBIDDEN, status.HTTP_403_FORBIDDEN, "common.forbidden", message, key, params
    )


def conflict(
    message: str | None = None,
    *,
    key: str | None = None,
    params: Mapping[str, Any] | None = None,
    detail: Any = None,
) -> AppError:
    return _make(
        ErrorCode.CONFLICT,
        status.HTTP_409_CONFLICT,
        "common.retryableConflict",
        message,
        key,
        params,
        detail,
    )


def _error_headers(http_status: int, headers: Mapping[str, str] | None) -> dict[str, str]:
    """统一响应头:401 一律带 WWW-Authenticate(RFC 6750),调用方自定义头合并保留。"""
    out = dict(headers or {})
    if http_status == status.HTTP_401_UNAUTHORIZED:
        out.setdefault("WWW-Authenticate", "Bearer")
    return out


def current_request_id() -> str | None:
    """读取 structlog 上下文的 request_id;缺失或为空时返回 None。"""
    value = structlog.contextvars.get_contextvars().get("request_id")
    return str(value) if value else None


def _error_body(
    code: ErrorCode, message: str, message_key: str | None, params: Any, detail: Any
) -> dict[str, Any]:
    """统一错误体六键结构的单一定义点(全部 exception handler 与中间件直渲响应共用)。"""
    return {
        "code": code.value,
        "message": message,
        "message_key": message_key,
        "params": params,
        "detail": detail,
        "request_id": current_request_id(),
    }


_HTTP_STATUS_MAP: dict[int, tuple[ErrorCode, str]] = {
    status.HTTP_404_NOT_FOUND: (ErrorCode.NOT_FOUND, "common.notFound"),
    status.HTTP_405_METHOD_NOT_ALLOWED: (ErrorCode.METHOD_NOT_ALLOWED, "common.methodNotAllowed"),
}


def _unhandled_response(exc: Exception, *, path: str, method: str) -> JSONResponse:  # noqa: ARG001
    """未捕获异常的统一渲染(structlog 留痕 + 统一错误体)。"""
    get_logger("app.errors").exception("unhandled_exception", path=path, method=method)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=_error_body(
            ErrorCode.INTERNAL,
            render_message("common.internal", None),
            "common.internal",
            None,
            None,
        ),
    )


class Uniform500Middleware:
    """捕获 HTTP 请求下游的未处理异常并渲染统一 500 响应。"""

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


def payload_too_large_response() -> JSONResponse:
    """返回统一的 413 错误响应。"""
    return JSONResponse(
        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
        content=_error_body(
            ErrorCode.PAYLOAD_TOO_LARGE,
            render_message("common.payloadTooLarge", None),
            "common.payloadTooLarge",
            None,
            None,
        ),
    )


def audit_unavailable_response() -> JSONResponse:
    """返回审计不可用的统一 503 错误响应。"""
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content=_error_body(
            ErrorCode.AUDIT_UNAVAILABLE,
            render_message("common.auditUnavailable", None),
            "common.auditUnavailable",
            None,
            None,
        ),
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content=_error_body(
                exc.code,
                exc.message,
                exc.message_key,
                jsonable_encoder(exc.params),
                jsonable_encoder(exc.detail),
            ),
            headers=_error_headers(exc.http_status, exc.headers),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        _request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        """路由层 404/405 等框架异常渲染统一错误体。"""
        code, key = _HTTP_STATUS_MAP.get(
            exc.status_code,
            (ErrorCode.VALIDATION_ERROR, "common.validation")
            if exc.status_code < 500
            else (ErrorCode.INTERNAL, "common.internal"),
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(
                code, render_message(key, None), key, None, jsonable_encoder(exc.detail)
            ),
            headers=_error_headers(exc.status_code, exc.headers),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
        """返回校验错误的位置、原因和类型,不回显 input。"""
        detail = [
            {"loc": e.get("loc"), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()
        ]
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=_error_body(
                ErrorCode.VALIDATION_ERROR,
                render_message("common.validation", None),
                "common.validation",
                None,
                jsonable_encoder(detail),
            ),
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        """最外层兜底,只兜中间件自身的异常(正常路径 500 由 Uniform500Middleware 渲染)。"""
        return _unhandled_response(exc, path=request.url.path, method=request.method)
