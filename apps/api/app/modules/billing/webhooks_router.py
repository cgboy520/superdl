"""支付回调(无用户鉴权,验签即鉴权)。重放安全。"""

from fastapi import APIRouter, Request

from app.core.db import DbSession
from app.modules.billing import payment_service
from app.modules.billing.payment_channels import get_channel

router = APIRouter(tags=["webhooks"])


@router.post("/webhooks/mock")
async def mock_webhook(request: Request, session: DbSession) -> dict[str, str]:
    """dev/test 专用:模拟支付成功回调。"""
    channel = get_channel("mock")
    result = await channel.parse_callback(dict(request.headers), await request.body())
    status = await payment_service.handle_callback(session, "mock", result)
    return {"status": status}


@router.post("/webhooks/wechatpay")
async def wechatpay_webhook(request: Request, session: DbSession) -> dict[str, str]:
    channel = get_channel("wechat")
    result = await channel.parse_callback(dict(request.headers), await request.body())
    status = await payment_service.handle_callback(session, "wechat", result)
    return {"code": "SUCCESS", "message": status}


@router.post("/webhooks/alipay")
async def alipay_webhook(request: Request, session: DbSession) -> dict[str, str]:
    channel = get_channel("alipay")
    result = await channel.parse_callback(dict(request.headers), await request.body())
    status = await payment_service.handle_callback(session, "alipay", result)
    return {"status": status}
