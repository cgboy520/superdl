"""支付回调(无用户鉴权,验签即鉴权)。重放安全。"""

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

from app.core.config import get_settings
from app.core.db import DbSession
from app.core.ratelimit import check_rate_limit
from app.modules.billing import payment_service
from app.modules.billing.payment_channels import get_channel

router = APIRouter(tags=["webhooks"])

# 两条端点无鉴权、无配额,每次调用都要做 RSA 验签、对订单行取 FOR UPDATE;微信侧遇到未知
# 的 Wechatpay-Serial 还会触发 SDK 向外拉取平台证书 —— 廉价的 DoS / 出站放大杠杆。
# 阈值取在渠道正常重试节奏之上:微信最多重试 15 次、支付宝 8 次,单 IP 每分钟 120 次
# 对真实渠道绰绰有余,对扫描器则是硬顶。
CALLBACK_RATE_LIMIT = 120
CALLBACK_RATE_WINDOW = 60.0


async def _guard(request: Request) -> None:
    client_ip = request.client.host if request.client else None
    await check_rate_limit(
        f"pay-callback:{client_ip or '-'}",
        max_attempts=CALLBACK_RATE_LIMIT,
        window_seconds=CALLBACK_RATE_WINDOW,
    )


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
    # 支付宝要求应答纯文本 success(非 JSON),否则渠道判通知失败并重试 8 次
    return PlainTextResponse("success")
