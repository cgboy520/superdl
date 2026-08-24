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

# 回调端点无鉴权且每次都要 RSA 验签 + 订单行 FOR UPDATE(微信侧未知 Wechatpay-Serial
# 还会触发 SDK 外拉平台证书),故限流。阈值取在渠道重试节奏之上(微信 15 次、支付宝 8 次)。
CALLBACK_RATE_LIMIT = 120
CALLBACK_RATE_WINDOW = 60.0


async def _guard(request: Request) -> None:
    ip = client_ip(request)
    await check_rate_limit(
        f"pay-callback:{ip or '-'}",
        max_attempts=CALLBACK_RATE_LIMIT,
        window_seconds=CALLBACK_RATE_WINDOW,
    )


# mock 回调仅在非生产环境注册(生产环境该路由 404;渠道层还有第二道校验);
# prod 镜像构建期再直接剔除 webhooks_mock 模块(Dockerfile EXCLUDE_MOCK=1),
# 配置(环境变量)与产物(文件不存在)双重保障,互不依赖
if get_settings().environment != "prod":
    try:
        from app.modules.billing import webhooks_mock

        router.include_router(webhooks_mock.router)
    except ImportError:
        pass  # prod 镜像已剔除 mock 模块


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
