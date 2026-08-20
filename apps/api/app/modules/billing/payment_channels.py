"""支付渠道抽象。

- mock:dev/test 默认,POST /api/v1/webhooks/mock 直接标记支付成功
- wechat:wechatpayv3(平台证书自动更新 + 验签)
- alipay:alipay-sdk-python

微信/支付宝需要商户资质(人工事项 #6),真实回调联调为人工事项 #7(1 分钱)。
未配置凭据时报 PAYMENT_CHANNEL_ERROR。
"""

from collections.abc import Mapping
from decimal import Decimal
from typing import TYPE_CHECKING, ClassVar, Literal, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AppError, ErrorCode
from app.core.platform_config import get_effective_platform_config

if TYPE_CHECKING:
    from app.modules.billing.models import Order


class CallbackResult:
    """验签解析后的回调结果。"""

    def __init__(self, order_no: str, channel_txn_id: str, amount: Decimal, success: bool) -> None:
        self.order_no = order_no
        self.channel_txn_id = channel_txn_id
        self.amount = amount
        self.success = success


class QueryResult:
    """主动查单结果(丢回调收敛/人工补单核验的唯一事实源)。"""

    def __init__(
        self,
        status: Literal["paid", "pending", "closed", "unknown"],
        channel_txn_id: str | None = None,
        amount: Decimal | None = None,
    ) -> None:
        self.status = status
        self.channel_txn_id = channel_txn_id
        self.amount = amount


class PaymentChannel(Protocol):
    name: str

    async def create_payment(self, order: "Order") -> str:
        """发起支付,返回二维码内容 qr_url。"""
        ...

    async def parse_callback(self, headers: dict[str, str], body: bytes) -> CallbackResult:
        """验签并解析回调。验签失败抛 AppError(PAYMENT_CHANNEL_ERROR)。"""
        ...

    async def query_order(self, order: "Order") -> QueryResult:
        """向渠道主动查单。渠道不可达抛 AppError(PAYMENT_CHANNEL_ERROR)。"""
        ...


class MockChannel:
    """dev/test 渠道:qr_url 为占位;回调体 {"order_no", "txn_id", "amount"}。

    类级 `_channel_side` 模拟渠道侧账本,支撑查单 poller 与人工补单的测试/演示:
    mock webhook 入账时同步记录;也可用 mark_paid() 只造"渠道已付但回调丢失"的场景。
    """

    name = "mock"

    _channel_side: ClassVar[dict[str, tuple[str, Decimal]]] = {}

    @classmethod
    def mark_paid(cls, order_no: str, txn_id: str, amount: Decimal | str) -> None:
        cls._channel_side[order_no] = (txn_id, Decimal(str(amount)))

    @classmethod
    def reset(cls) -> None:
        cls._channel_side.clear()

    async def create_payment(self, order: "Order") -> str:
        return f"superdl-mock-pay://{order.order_no}?amount={order.amount}"

    async def parse_callback(self, headers: dict[str, str], body: bytes) -> CallbackResult:
        import json

        try:
            data = json.loads(body)
            result = CallbackResult(
                order_no=data["order_no"],
                channel_txn_id=data.get("txn_id", f"mock-{data['order_no']}"),
                amount=Decimal(str(data["amount"])),
                success=bool(data.get("success", True)),
            )
        except (ValueError, KeyError) as exc:
            raise AppError(ErrorCode.PAYMENT_CHANNEL_ERROR, "mock 回调解析失败") from exc
        if result.success:
            self.mark_paid(result.order_no, result.channel_txn_id, result.amount)
        return result

    async def query_order(self, order: "Order") -> QueryResult:
        hit = self._channel_side.get(order.order_no)
        if hit is None:
            return QueryResult("pending")
        return QueryResult("paid", channel_txn_id=hit[0], amount=hit[1])


# 参与渠道构造的配置键(工厂据此做实例缓存指纹;配置变更即重建,免重启生效)
WECHAT_CFG_KEYS = (
    "wechat_mchid",
    "wechat_appid",
    "wechat_private_key",
    "wechat_cert_serial_no",
    "wechat_apiv3_key",
    "wechat_public_key",
    "wechat_public_key_id",
)
ALIPAY_CFG_KEYS = ("alipay_app_id", "alipay_private_key", "alipay_public_key")


class WechatChannel:  # pragma: no cover - 需真实商户凭据,人工事项 #7 联调
    """微信支付 Native(扫码),APIv3。

    验签双模式(wechatpayv3 原生支持):配置了微信支付公钥 + 公钥 ID(PUB_KEY_ID_*)
    走公钥模式(2024-10 后新商户唯一模式);否则回退平台证书模式(SDK 自动拉取轮换)。
    """

    name = "wechat"

    def __init__(self, cfg: Mapping[str, str]) -> None:
        required = (
            "wechat_mchid",
            "wechat_appid",
            "wechat_private_key",
            "wechat_cert_serial_no",
            "wechat_apiv3_key",
        )
        if not all(cfg[k] for k in required):
            raise AppError(
                ErrorCode.PAYMENT_CHANNEL_ERROR, "微信支付商户凭据不完整(管理端·平台配置)"
            )
        public_key = cfg["wechat_public_key"] or None
        public_key_id = cfg["wechat_public_key_id"] or None
        if bool(public_key) != bool(public_key_id):
            raise AppError(
                ErrorCode.PAYMENT_CHANNEL_ERROR, "微信支付公钥模式需同时配置公钥与公钥 ID"
            )
        from wechatpayv3 import WeChatPay, WeChatPayType  # type: ignore[import-untyped]

        self._wxpay = WeChatPay(
            wechatpay_type=WeChatPayType.NATIVE,
            mchid=cfg["wechat_mchid"],
            private_key=cfg["wechat_private_key"],
            cert_serial_no=cfg["wechat_cert_serial_no"],
            apiv3_key=cfg["wechat_apiv3_key"],
            appid=cfg["wechat_appid"],
            notify_url=f"{get_settings().public_base_url}/api/v1/webhooks/wechatpay",
            public_key=public_key,
            public_key_id=public_key_id,
        )

    async def create_payment(self, order: "Order") -> str:
        import asyncio

        # time_expire:渠道侧与本地 expires_at 同步过期(RFC3339,aware-UTC 直接序列化)
        code, message = await asyncio.to_thread(
            self._wxpay.pay,
            description=f"SuperDL 充值 {order.order_no}",
            out_trade_no=order.order_no,
            amount={"total": int(order.amount * 100)},
            time_expire=order.expires_at.isoformat(timespec="seconds"),
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

    async def query_order(self, order: "Order") -> QueryResult:
        import asyncio
        import json

        code, message = await asyncio.to_thread(self._wxpay.query, out_trade_no=order.order_no)
        if code != 200:
            raise AppError(ErrorCode.PAYMENT_CHANNEL_ERROR, f"微信查单失败:{message}")
        data = json.loads(message)
        state = data.get("trade_state")
        if state == "SUCCESS":
            return QueryResult(
                "paid",
                channel_txn_id=data["transaction_id"],
                amount=Decimal(data["amount"]["total"]) / 100,
            )
        if state in ("CLOSED", "REVOKED", "PAYERROR"):
            return QueryResult("closed")
        if state in ("NOTPAY", "USERPAYING"):
            return QueryResult("pending")
        return QueryResult("unknown")


class AlipayChannel:  # pragma: no cover - 需真实商户凭据,人工事项 #7 联调(1 分钱)
    """支付宝当面付(precreate 扫码 + 异步通知 RSA2 验签 + 主动查单)。

    凭据经 SUPERDL_ALIPAY_* 注入(应用私钥 + 支付宝公钥)。
    """

    name = "alipay"

    GATEWAY = "https://openapi.alipay.com/gateway.do"

    def __init__(self, cfg: Mapping[str, str]) -> None:
        if not (cfg["alipay_app_id"] and cfg["alipay_private_key"] and cfg["alipay_public_key"]):
            raise AppError(ErrorCode.PAYMENT_CHANNEL_ERROR, "支付宝商户凭据不完整(管理端·平台配置)")
        from alipay.aop.api.AlipayClientConfig import (
            AlipayClientConfig,  # type: ignore[import-untyped]
        )
        from alipay.aop.api.DefaultAlipayClient import (
            DefaultAlipayClient,  # type: ignore[import-untyped]
        )

        client_cfg = AlipayClientConfig()
        client_cfg.server_url = self.GATEWAY
        client_cfg.app_id = cfg["alipay_app_id"]
        client_cfg.app_private_key = cfg["alipay_private_key"]
        client_cfg.alipay_public_key = cfg["alipay_public_key"]
        self._client = DefaultAlipayClient(alipay_client_config=client_cfg)
        self._public_key = cfg["alipay_public_key"]
        self._notify_url = f"{get_settings().public_base_url}/api/v1/webhooks/alipay"

    async def create_payment(self, order: "Order") -> str:
        import asyncio
        import json

        from alipay.aop.api.domain.AlipayTradePrecreateModel import (  # type: ignore[import-untyped]
            AlipayTradePrecreateModel,
        )
        from alipay.aop.api.request.AlipayTradePrecreateRequest import (  # type: ignore[import-untyped]
            AlipayTradePrecreateRequest,
        )

        from app.core.timeutil import now_utc

        model = AlipayTradePrecreateModel()
        model.out_trade_no = order.order_no
        model.total_amount = str(order.amount)
        model.subject = f"SuperDL 充值 {order.order_no}"
        # 渠道侧与本地 expires_at 同步过期(相对分钟数,至少 1m)
        remaining_min = int((order.expires_at - now_utc()).total_seconds() // 60)
        model.timeout_express = f"{max(1, remaining_min)}m"
        req = AlipayTradePrecreateRequest(biz_model=model)
        req.notify_url = self._notify_url
        try:
            resp = json.loads(await asyncio.to_thread(self._client.execute, req))
        except Exception as exc:
            raise AppError(ErrorCode.PAYMENT_CHANNEL_ERROR, f"支付宝下单失败:{exc}") from exc
        if resp.get("code") != "10000":
            raise AppError(
                ErrorCode.PAYMENT_CHANNEL_ERROR,
                f"支付宝下单失败:{resp.get('sub_msg') or resp.get('msg')}",
            )
        return resp["qr_code"]

    async def parse_callback(self, headers: dict[str, str], body: bytes) -> CallbackResult:
        from urllib.parse import parse_qsl

        from alipay.aop.api.util.SignatureUtils import (  # type: ignore[import-untyped]
            verify_with_rsa,
        )

        params = dict(parse_qsl(body.decode("utf-8"), keep_blank_values=True))
        sign = params.pop("sign", "")
        params.pop("sign_type", None)
        message = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        try:
            ok = verify_with_rsa(self._public_key, message.encode("utf-8"), sign)
        except Exception as exc:
            raise AppError(ErrorCode.PAYMENT_CHANNEL_ERROR, "支付宝回调验签失败") from exc
        if not ok:
            raise AppError(ErrorCode.PAYMENT_CHANNEL_ERROR, "支付宝回调验签失败")
        return CallbackResult(
            order_no=params.get("out_trade_no", ""),
            channel_txn_id=params.get("trade_no", ""),
            amount=Decimal(params.get("total_amount", "0")),
            success=params.get("trade_status") in ("TRADE_SUCCESS", "TRADE_FINISHED"),
        )

    async def query_order(self, order: "Order") -> QueryResult:
        import asyncio
        import json

        from alipay.aop.api.domain.AlipayTradeQueryModel import (  # type: ignore[import-untyped]
            AlipayTradeQueryModel,
        )
        from alipay.aop.api.request.AlipayTradeQueryRequest import (  # type: ignore[import-untyped]
            AlipayTradeQueryRequest,
        )

        model = AlipayTradeQueryModel()
        model.out_trade_no = order.order_no
        req = AlipayTradeQueryRequest(biz_model=model)
        try:
            resp = json.loads(await asyncio.to_thread(self._client.execute, req))
        except Exception as exc:
            raise AppError(ErrorCode.PAYMENT_CHANNEL_ERROR, f"支付宝查单失败:{exc}") from exc
        if resp.get("code") == "10000":
            status = resp.get("trade_status")
            if status in ("TRADE_SUCCESS", "TRADE_FINISHED"):
                return QueryResult(
                    "paid",
                    channel_txn_id=resp.get("trade_no"),
                    amount=Decimal(resp["total_amount"]),
                )
            if status == "TRADE_CLOSED":
                return QueryResult("closed")
            return QueryResult("pending")
        if resp.get("sub_code") == "ACQ.TRADE_NOT_EXIST":
            return QueryResult("pending")  # 用户未扫码,渠道侧尚无单
        raise AppError(
            ErrorCode.PAYMENT_CHANNEL_ERROR,
            f"支付宝查单失败:{resp.get('sub_msg') or resp.get('msg')}",
        )


# 真实渠道实例缓存:按配置指纹缓存,配置变更即重建(免重启生效)
_real_channel_cache: dict[str, tuple[tuple[str, ...], PaymentChannel]] = {}


async def get_channel(name: str, session: AsyncSession) -> PaymentChannel:
    settings = get_settings()
    if name == "mock":
        # 生产环境无条件拒绝 mock(无验签渠道)
        if settings.environment == "prod" or not settings.payment_mock:
            raise AppError(ErrorCode.PAYMENT_CHANNEL_ERROR, "mock 渠道仅限开发环境")
        return MockChannel()
    if name not in ("wechat", "alipay"):
        raise AppError(ErrorCode.VALIDATION_ERROR, f"未知支付渠道:{name}")
    cfg = await get_effective_platform_config(session)
    keys = WECHAT_CFG_KEYS if name == "wechat" else ALIPAY_CFG_KEYS
    fingerprint = tuple(cfg[k] for k in keys)
    cached = _real_channel_cache.get(name)
    if cached is not None and cached[0] == fingerprint:
        return cached[1]
    channel: PaymentChannel = WechatChannel(cfg) if name == "wechat" else AlipayChannel(cfg)
    _real_channel_cache[name] = (fingerprint, channel)
    return channel
