"""mock 支付回调:仅 dev/test 注册(webhooks_router 条件 include)。

prod 镜像构建期直接删除本文件(Dockerfile 的 EXCLUDE_MOCK=1):
「产物正确」不依赖「配置正确」,误配 SUPERDL_ENVIRONMENT 也注册不出该路由。
"""

from fastapi import APIRouter, Request

from app.core.db import DbSession
from app.modules.billing import payment_service
from app.modules.billing.payment_channels import get_channel

router = APIRouter()


@router.post("/webhooks/mock")
async def mock_webhook(request: Request, session: DbSession) -> dict[str, str]:
    """dev/test 专用:模拟支付成功回调。"""
    channel = await get_channel("mock", session)
    result = await channel.parse_callback(dict(request.headers), await request.body())
    status = await payment_service.handle_callback(session, "mock", result)
    return {"status": status}
