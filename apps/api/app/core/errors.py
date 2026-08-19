"""统一错误体 {code, message, detail}。业务错误码集中于 ErrorCode,前端据此分支。"""

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
    PHONE_TAKEN = "PHONE_TAKEN"
    LOGIN_FAILED = "LOGIN_FAILED"
    USER_FROZEN = "USER_FROZEN"
    SSH_KEY_INVALID = "SSH_KEY_INVALID"
    SSH_KEY_DUPLICATE = "SSH_KEY_DUPLICATE"
    # 商品/库存
    SKU_NOT_ON_SALE = "SKU_NOT_ON_SALE"
    # 实例
    INSTANCE_INVALID_TRANSITION = "INSTANCE_INVALID_TRANSITION"
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
        message: str,
        *,
        http_status: int = status.HTTP_400_BAD_REQUEST,
        detail: Any = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.detail = detail


def not_found(message: str = "资源不存在") -> AppError:
    return AppError(ErrorCode.NOT_FOUND, message, http_status=status.HTTP_404_NOT_FOUND)


def unauthorized(message: str = "未登录或凭证已过期") -> AppError:
    return AppError(ErrorCode.UNAUTHORIZED, message, http_status=status.HTTP_401_UNAUTHORIZED)


def forbidden(message: str = "无权访问") -> AppError:
    return AppError(ErrorCode.FORBIDDEN, message, http_status=status.HTTP_403_FORBIDDEN)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content={
                "code": exc.code.value,
                "message": exc.message,
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
                "detail": jsonable_encoder(exc.errors()),
            },
        )
