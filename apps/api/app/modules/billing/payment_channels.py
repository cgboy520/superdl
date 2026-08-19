"""支付渠道抽象。

- mock:dev/test 默认,POST /api/v1/webhooks/mock 直接标记支付成功
- wechat:wechatpayv3(社区事实标准,平台证书自动更新/验签齐全)
- alipay:alipay-sdk-python(官方 SDK)

微信/支付宝需要商户资质(人工事项 #6),真实回调联调为人工事项 #7(1 分钱)。
未配置凭据时报 PAYMENT_CHANNEL_ERROR,不影响 mock 渠道与其余功能。
"""

from decimal import Decimal
from typing import TYPE_CHECKING, Protocol

from app.core.config import get_settings
from app.core.errors import AppError, ErrorCode

if TYPE_CHECKING:
    from app.modules.billing.models import Order


class CallbackResult:
    """验签解析后的回调结果。"""

    def __init__(self, order_no: str, channel_txn_id: str, amount: Decimal, success: bool) -> None:
        self.order_no = order_no
        self.channel_txn_id = channel_txn_id
        self.amount = amount
        self.success = success


class PaymentChannel(Protocol):
    name: str

    async def create_payment(self, order: "Order") -> str:
        """发起支付,返回二维码内容 qr_url。"""
        ...

    async def parse_callback(self, headers: dict[str, str], body: bytes) -> CallbackResult:
        """验签并解析回调。验签失败抛 AppError(PAYMENT_CHANNEL_ERROR)。"""
        ...


class MockChannel:
    """dev/test 渠道:qr_url 为占位;回调体 {"order_no", "txn_id", "amount"}。"""

    name = "mock"

    async def create_payment(self, order: "Order") -> str:
        return f"superdl-mock-pay://{order.order_no}?amount={order.amount}"

    async def parse_callback(self, headers: dict[str, str], body: bytes) -> CallbackResult:
        import json

        try:
            data = json.loads(body)
            return CallbackResult(
                order_no=data["order_no"],
                channel_txn_id=data.get("txn_id", f"mock-{data['order_no']}"),
                amount=Decimal(str(data["amount"])),
                success=bool(data.get("success", True)),
            )
        except (ValueError, KeyError) as exc:
            raise AppError(ErrorCode.PAYMENT_CHANNEL_ERROR, "mock 回调解析失败") from exc


class WechatChannel:  # pragma: no cover - 需真实商户凭据,人工事项 #7 联调
    """微信支付 Native(扫码)。凭据经 SUPERDL_WECHAT_* 注入。"""

    name = "wechat"

    def __init__(self) -> None:
        s = get_settings()
        if not (s.wechat_mchid and s.wechat_private_key and s.wechat_cert_serial_no):
            raise AppError(ErrorCode.PAYMENT_CHANNEL_ERROR, "微信支付未配置商户凭据(人工事项 #6)")
        from wechatpayv3 import WeChatPay, WeChatPayType  # type: ignore[import-untyped]

        self._wxpay = WeChatPay(
            wechatpay_type=WeChatPayType.NATIVE,
            mchid=s.wechat_mchid,
            private_key=s.wechat_private_key,
            cert_serial_no=s.wechat_cert_serial_no,
            apiv3_key=s.wechat_apiv3_key,
            appid=s.wechat_appid,
            notify_url=f"{s.public_base_url}/api/v1/webhooks/wechatpay",
        )

    async def create_payment(self, order: "Order") -> str:
        import asyncio

        code, message = await asyncio.to_thread(
            self._wxpay.pay,
            description=f"SuperDL 充值 {order.order_no}",
            out_trade_no=order.order_no,
            amount={"total": int(order.amount * 100)},
        )
        import json

        if code != 200:
            raise AppError(ErrorCode.PAYMENT_CHANNEL_ERROR, f"微信下单失败:{message}")
        return json.loads(message)["code_url"]

    async def parse_callback(self, headers: dict[str, str], body: bytes) -> CallbackResult:
        import asyncio
        from typing import Any

        result: Any = await asyncio.to_thread(self._wxpay.callback, headers, body)
        if not isinstance(result, dict) or result.get("event_type") != "TRANSACTION.SUCCESS":
            raise AppError(ErrorCode.PAYMENT_CHANNEL_ERROR, "微信回调验签失败")
        resource: dict[str, Any] = result["resource"]
        return CallbackResult(
            order_no=resource["out_trade_no"],
            channel_txn_id=resource["transaction_id"],
            amount=Decimal(resource["amount"]["total"]) / 100,
            success=resource["trade_state"] == "SUCCESS",
        )


class AlipayChannel:  # pragma: no cover - 需真实商户凭据,人工事项 #7 联调
    """支付宝当面付(扫码)。W4 联调时按商户参数补全细节。"""

    name = "alipay"

    def __init__(self) -> None:
        s = get_settings()
        if not (s.alipay_app_id and s.alipay_private_key):
            raise AppError(ErrorCode.PAYMENT_CHANNEL_ERROR, "支付宝未配置商户凭据(人工事项 #6)")
        raise AppError(
            ErrorCode.PAYMENT_CHANNEL_ERROR,
            "支付宝渠道待人工事项 #7 联调启用(alipay-sdk-python 已就位)",
        )

    async def create_payment(self, order: "Order") -> str:
        raise NotImplementedError

    async def parse_callback(self, headers: dict[str, str], body: bytes) -> CallbackResult:
        raise NotImplementedError


def get_channel(name: str) -> PaymentChannel:
    settings = get_settings()
    if name == "mock":
        # 双保险:生产环境无条件拒绝 mock(无验签渠道 = 无鉴权入账口)
        if settings.environment == "prod" or not settings.payment_mock:
            raise AppError(ErrorCode.PAYMENT_CHANNEL_ERROR, "mock 渠道仅限开发环境")
        return MockChannel()
    if name == "wechat":
        return WechatChannel()
    if name == "alipay":
        return AlipayChannel()
    raise AppError(ErrorCode.VALIDATION_ERROR, f"未知支付渠道:{name}")
