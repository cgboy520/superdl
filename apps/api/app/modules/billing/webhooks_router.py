"""支付回调(无用户鉴权,验签即鉴权)。重放安全。"""

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

from app.core.config import get_settings
from app.core.db import DbSession
from app.modules.billing import payment_service
from app.modules.billing.payment_channels import get_channel

router = APIRouter(tags=["webhooks"])

# mock 回调仅在非生产环境注册(生产环境该路由 404;渠道层还有第二道校验)
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
    channel = await get_channel("wechat", session)
    result = await channel.parse_callback(dict(request.headers), await request.body())
    status = await payment_service.handle_callback(session, "wechat", result)
    return {"code": "SUCCESS", "message": status}


@router.post("/webhooks/alipay")
async def alipay_webhook(request: Request, session: DbSession) -> PlainTextResponse:
    channel = await get_channel("alipay", session)
    result = await channel.parse_callback(dict(request.headers), await request.body())
    await payment_service.handle_callback(session, "alipay", result)
    # 支付宝要求应答纯文本 success(非 JSON),否则渠道判通知失败并重试 8 次
    return PlainTextResponse("success")
