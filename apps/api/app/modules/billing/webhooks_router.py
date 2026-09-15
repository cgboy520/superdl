"""支付回调(无用户鉴权,验签即鉴权)。重放安全。"""

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

from app.core.config import get_settings
from app.core.db import DbSession
from app.core.http import client_ip
from app.core.ratelimit import check_rate_limit
from app.modules.billing import payment_service
from app.modules.billing.payment_channels import get_channel

router = APIRouter(tags=["webhooks"])

CALLBACK_RATE_LIMIT = 120
CALLBACK_RATE_WINDOW = 60.0


async def _guard(request: Request) -> None:
    ip = client_ip(request)
    await check_rate_limit(
        f"pay-callback:{ip or '-'}",
        max_attempts=CALLBACK_RATE_LIMIT,
        window_seconds=CALLBACK_RATE_WINDOW,
    )


if get_settings().environment != "prod":

    @router.post("/webhooks/mock")
    async def mock_webhook(request: Request, session: DbSession) -> dict[str, str]:
        """dev/test 专用:模拟支付成功回调。"""
        channel = await get_channel("mock", session)
        result = await channel.parse_callback(dict(request.headers), await request.body())
        status = await payment_service.handle_callback(session, "mock", result)
        return {"status": status}


@router.post("/webhooks/wechatpay")
async def wechatpay_webhook(request: Request, session: DbSession) -> dict[str, str]:
    await _guard(request)
    channel = await get_channel("wechat", session)
    result = await channel.parse_callback(dict(request.headers), await request.body())
    status = await payment_service.handle_callback(session, "wechat", result)
    return {"code": "SUCCESS", "message": status}


@router.post("/webhooks/alipay")
async def alipay_webhook(request: Request, session: DbSession) -> PlainTextResponse:
    await _guard(request)
    channel = await get_channel("alipay", session)
    result = await channel.parse_callback(dict(request.headers), await request.body())
    await payment_service.handle_callback(session, "alipay", result)
    return PlainTextResponse("success")
