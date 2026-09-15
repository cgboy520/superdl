"""微信公钥验签、支付宝 RSA2 验签与开发环境 mock 支付渠道。"""

import asyncio
import functools
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, ClassVar, Literal, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AppError, ErrorCode
from app.core.platform_config import RuntimeConfig, get_runtime_config
from app.core.timeutil import now_utc

if TYPE_CHECKING:
    from app.modules.billing.models import Order


class CallbackResult:
    """解析后的支付回调;success 表示支付成功,refund_amount 为渠道退款额。"""

    def __init__(
        self,
        order_no: str,
        channel_txn_id: str,
        amount: Decimal,
        success: bool,
        refund_amount: Decimal | None = None,
    ) -> None:
        self.order_no = order_no
        self.channel_txn_id = channel_txn_id
        self.amount = amount
        self.success = success
        self.refund_amount = refund_amount


SDK_TIMEOUT = (5, 10)
SDK_TIMEOUT_SECONDS = 10
CALLBACK_FRESHNESS_SECONDS = 15 * 60
_SDK_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="payment-sdk")


async def run_in_sdk_pool(fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_SDK_EXECUTOR, functools.partial(fn, *args, **kwargs))


def header_value(headers: Mapping[str, str], name: str) -> str:
    """忽略大小写读取首个同名头,缺失时返回空串。"""
    lowered = name.lower()
    for k, v in headers.items():
        if k.lower() == lowered:
            return v
    return ""


def channel_error(key: str) -> AppError:
    """渠道侧错误(凭据不全 / 验签失败 / 商户不符 / 回调过期等)的统一形态。"""
    return AppError(ErrorCode.PAYMENT_CHANNEL_ERROR, key=key)


def assert_callback_fresh(ts: datetime | None, *, key: str) -> None:
    """拒绝缺失或超出 ±CALLBACK_FRESHNESS_SECONDS 的回调时间戳。"""
    if ts is None or abs((now_utc() - ts).total_seconds()) > CALLBACK_FRESHNESS_SECONDS:
        raise channel_error(key)


class QueryResult:
    """渠道查单状态、交易号与金额。"""

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
        """查询渠道订单状态、交易号与金额;失败抛异常。"""
        ...


class MockChannel:
    """无验签的开发测试渠道;成功回调记录到类级内存账本,查单读取该账本。"""

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

    async def parse_callback(self, headers: dict[str, str], body: bytes) -> CallbackResult:  # noqa: ARG002
        import json

        try:
            data = json.loads(body)
            result = CallbackResult(
                order_no=data["order_no"],
                channel_txn_id=data.get("txn_id", f"mock-{data['order_no']}"),
                amount=Decimal(str(data["amount"])),
                success=bool(data.get("success", True)),
            )
        except (ValueError, KeyError, TypeError, InvalidOperation) as exc:
            raise channel_error("billing.mockCallbackParseFailed") from exc
        if result.success:
            self.mark_paid(result.order_no, result.channel_txn_id, result.amount)
        return result

    async def query_order(self, order: "Order") -> QueryResult:
        hit = self._channel_side.get(order.order_no)
        if hit is None:
            return QueryResult("pending")
        return QueryResult("paid", channel_txn_id=hit[0], amount=hit[1])


WECHAT_CFG_KEYS = (
    "wechat_mchid",
    "wechat_appid",
    "wechat_private_key",
    "wechat_cert_serial_no",
    "wechat_apiv3_key",
    "wechat_public_key",
    "wechat_public_key_id",
)
ALIPAY_CFG_KEYS = (
    "alipay_app_id",
    "alipay_private_key",
    "alipay_public_key",
    "alipay_seller_id",
)


class WechatChannel:
    """微信支付 Native(扫码),APIv3。验签仅公钥模式:wechat_public_key 与
    wechat_public_key_id 必填。"""

    name = "wechat"

    def __init__(self, cfg: RuntimeConfig) -> None:  # pragma: no cover
        if not all(getattr(cfg, k) for k in WECHAT_CFG_KEYS):
            raise channel_error("billing.wechatCredentialsIncomplete")
        from wechatpayv3 import WeChatPay, WeChatPayType

        self._wxpay = WeChatPay(
            wechatpay_type=WeChatPayType.NATIVE,
            mchid=cfg.wechat_mchid,
            private_key=cfg.wechat_private_key,
            cert_serial_no=cfg.wechat_cert_serial_no,
            apiv3_key=cfg.wechat_apiv3_key,
            appid=cfg.wechat_appid,
            notify_url=f"{get_settings().public_base_url}/api/v1/webhooks/wechatpay",
            public_key=cfg.wechat_public_key,
            public_key_id=cfg.wechat_public_key_id,
            timeout=SDK_TIMEOUT,
        )
        self._mchid = cfg.wechat_mchid
        self._appid = cfg.wechat_appid
        self._public_key_id = cfg.wechat_public_key_id

    async def create_payment(self, order: "Order") -> str:  # pragma: no cover
        code, message = await run_in_sdk_pool(
            self._wxpay.pay,
            description=f"SuperDL 充值 {order.order_no}",
            out_trade_no=order.order_no,
            amount={"total": int(order.amount * 100)},
            time_expire=order.expires_at.isoformat(timespec="seconds"),
        )
        import json

        if code != 200:
            raise AppError(
                ErrorCode.PAYMENT_CHANNEL_ERROR,
                key="billing.wechatCreateFailed",
                params={"message": message},
            )
        return json.loads(message)["code_url"]

    async def parse_callback(self, headers: dict[str, str], body: bytes) -> CallbackResult:
        """先核对公钥 ID 与时间戳,再由 SDK 验签解密;未验签请求不得触发证书下载。

        要求 TRANSACTION.SUCCESS、匹配的商户与应用、完整交易字段及 CNY 币种。
        """
        if header_value(headers, "Wechatpay-Serial") != self._public_key_id:
            raise channel_error("billing.wechatCallbackVerifyFailed")
        try:
            ts = datetime.fromtimestamp(int(header_value(headers, "Wechatpay-Timestamp")), UTC)
        except (ValueError, OverflowError, OSError):
            ts = None
        assert_callback_fresh(ts, key="billing.wechatCallbackVerifyFailed")
        try:
            result: Any = await run_in_sdk_pool(self._wxpay.callback, headers, body)
        except AppError:
            raise
        except Exception as exc:
            raise channel_error("billing.wechatCallbackVerifyFailed") from exc
        if not isinstance(result, dict) or result.get("event_type") != "TRANSACTION.SUCCESS":
            raise channel_error("billing.wechatCallbackVerifyFailed")
        resource = result.get("resource")
        if not isinstance(resource, dict):
            raise channel_error("billing.wechatCallbackVerifyFailed")
        if resource.get("mchid") != self._mchid or resource.get("appid") != self._appid:
            raise channel_error("billing.wechatCallbackMerchantMismatch")
        out_trade_no = resource.get("out_trade_no")
        transaction_id = resource.get("transaction_id")
        trade_state = resource.get("trade_state")
        amount_obj = resource.get("amount")
        total = amount_obj.get("total") if isinstance(amount_obj, dict) else None
        currency = amount_obj.get("currency") if isinstance(amount_obj, dict) else None
        if not out_trade_no or not transaction_id or not trade_state or total is None:
            raise channel_error("billing.wechatCallbackVerifyFailed")
        if currency != "CNY":
            raise channel_error("billing.wechatCallbackMerchantMismatch")
        return CallbackResult(
            order_no=out_trade_no,
            channel_txn_id=transaction_id,
            amount=Decimal(total) / 100,
            success=trade_state == "SUCCESS",
        )

    async def query_order(self, order: "Order") -> QueryResult:  # pragma: no cover
        import json

        code, message = await run_in_sdk_pool(self._wxpay.query, out_trade_no=order.order_no)
        if code != 200:
            raise AppError(
                ErrorCode.PAYMENT_CHANNEL_ERROR,
                key="billing.wechatQueryFailed",
                params={"message": message},
            )
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


class AlipayChannel:
    """支付宝当面付、异步通知 RSA2 验签与主动查单;生产环境须配置收款方 seller_id。"""

    name = "alipay"

    GATEWAY = "https://openapi.alipay.com/gateway.do"

    def __init__(self, cfg: RuntimeConfig) -> None:  # pragma: no cover
        if not (cfg.alipay_app_id and cfg.alipay_private_key and cfg.alipay_public_key):
            raise channel_error("billing.alipayCredentialsIncomplete")
        from alipay.aop.api.AlipayClientConfig import (
            AlipayClientConfig,
        )
        from alipay.aop.api.DefaultAlipayClient import (
            DefaultAlipayClient,
        )

        client_cfg = AlipayClientConfig()
        client_cfg.server_url = self.GATEWAY
        client_cfg.app_id = cfg.alipay_app_id
        client_cfg.app_private_key = cfg.alipay_private_key
        client_cfg.alipay_public_key = cfg.alipay_public_key
        client_cfg.timeout = SDK_TIMEOUT_SECONDS
        self._client = DefaultAlipayClient(alipay_client_config=client_cfg)
        self._public_key = cfg.alipay_public_key
        self._app_id = cfg.alipay_app_id
        self._seller_id = cfg.alipay_seller_id or ""
        if get_settings().environment == "prod" and not self._seller_id:
            raise channel_error("billing.alipaySellerIdRequired")
        self._notify_url = f"{get_settings().public_base_url}/api/v1/webhooks/alipay"

    async def create_payment(self, order: "Order") -> str:  # pragma: no cover
        import json

        from alipay.aop.api.domain.AlipayTradePrecreateModel import (
            AlipayTradePrecreateModel,
        )
        from alipay.aop.api.request.AlipayTradePrecreateRequest import (
            AlipayTradePrecreateRequest,
        )

        from app.core.timeutil import now_utc

        model = AlipayTradePrecreateModel()
        model.out_trade_no = order.order_no
        model.total_amount = str(order.amount)
        model.subject = f"SuperDL 充值 {order.order_no}"
        remaining_min = int((order.expires_at - now_utc()).total_seconds() // 60)
        model.timeout_express = f"{max(1, remaining_min)}m"
        req = AlipayTradePrecreateRequest(biz_model=model)
        req.notify_url = self._notify_url
        try:
            resp = json.loads(await run_in_sdk_pool(self._client.execute, req))
        except Exception as exc:
            raise AppError(
                ErrorCode.PAYMENT_CHANNEL_ERROR,
                key="billing.alipayCreateFailed",
                params={"message": str(exc)},
            ) from exc
        if resp.get("code") != "10000":
            raise AppError(
                ErrorCode.PAYMENT_CHANNEL_ERROR,
                key="billing.alipayCreateFailed",
                params={"message": resp.get("sub_msg") or resp.get("msg")},
            )
        return resp["qr_code"]

    async def parse_callback(self, headers: dict[str, str], body: bytes) -> CallbackResult:  # noqa: ARG002
        """验签并核对 app_id、seller_id 与北京时间通知时效;正退款额视为反向通知。"""
        from urllib.parse import parse_qsl

        from alipay.aop.api.util.SignatureUtils import (
            verify_with_rsa,
        )

        params = dict(parse_qsl(body.decode("utf-8")))
        sign = params.pop("sign", "")
        params.pop("sign_type", None)
        message = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        try:
            ok = verify_with_rsa(self._public_key, message.encode("utf-8"), sign)
        except Exception as exc:
            raise channel_error("billing.alipayCallbackVerifyFailed") from exc
        if not ok:
            raise channel_error("billing.alipayCallbackVerifyFailed")
        if params.get("app_id") != self._app_id:
            raise channel_error("billing.alipayCallbackMerchantMismatch")
        if params.get("seller_id") != self._seller_id:
            raise channel_error("billing.alipayCallbackMerchantMismatch")
        try:
            ts: datetime | None = datetime.strptime(
                params.get("notify_time", ""), "%Y-%m-%d %H:%M:%S"
            ).replace(tzinfo=timezone(timedelta(hours=8)))
        except ValueError:
            ts = None
        assert_callback_fresh(ts, key="billing.alipayCallbackVerifyFailed")
        refund_amount: Decimal | None = None
        refund_fee = params.get("refund_fee", "")
        if refund_fee:
            try:
                refund_amount = Decimal(refund_fee)
            except InvalidOperation as exc:
                raise channel_error("billing.alipayCallbackVerifyFailed") from exc
            if refund_amount <= 0:
                refund_amount = None
        return CallbackResult(
            order_no=params.get("out_trade_no", ""),
            channel_txn_id=params.get("trade_no", ""),
            amount=Decimal(params.get("total_amount", "0")),
            success=refund_amount is None
            and params.get("trade_status") in ("TRADE_SUCCESS", "TRADE_FINISHED"),
            refund_amount=refund_amount,
        )

    async def query_order(self, order: "Order") -> QueryResult:  # pragma: no cover
        import json

        from alipay.aop.api.domain.AlipayTradeQueryModel import (
            AlipayTradeQueryModel,
        )
        from alipay.aop.api.request.AlipayTradeQueryRequest import (
            AlipayTradeQueryRequest,
        )

        model = AlipayTradeQueryModel()
        model.out_trade_no = order.order_no
        req = AlipayTradeQueryRequest(biz_model=model)
        try:
            resp = json.loads(await run_in_sdk_pool(self._client.execute, req))
        except Exception as exc:
            raise AppError(
                ErrorCode.PAYMENT_CHANNEL_ERROR,
                key="billing.alipayQueryFailed",
                params={"message": str(exc)},
            ) from exc
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
            return QueryResult("pending")
        raise AppError(
            ErrorCode.PAYMENT_CHANNEL_ERROR,
            key="billing.alipayQueryFailed",
            params={"message": resp.get("sub_msg") or resp.get("msg")},
        )


_real_channel_cache: dict[str, tuple[tuple[str, ...], PaymentChannel]] = {}


async def get_channel(name: str, session: AsyncSession) -> PaymentChannel:
    """按配置指纹缓存真实渠道;mock 仅在 payment_mock 开启时可用。"""
    settings = get_settings()
    if name == "mock":
        if not settings.payment_mock:
            raise channel_error("billing.mockDevOnly")
        return MockChannel()
    if name not in ("wechat", "alipay"):
        raise AppError(
            ErrorCode.VALIDATION_ERROR, key="billing.unknownChannel", params={"name": name}
        )
    cfg = await get_runtime_config(session)
    keys = WECHAT_CFG_KEYS if name == "wechat" else ALIPAY_CFG_KEYS
    fingerprint = tuple(getattr(cfg, k) for k in keys)
    cached = _real_channel_cache.get(name)
    if cached is not None and cached[0] == fingerprint:
        return cached[1]
    channel: PaymentChannel = await run_in_sdk_pool(
        WechatChannel if name == "wechat" else AlipayChannel, cfg
    )
    _real_channel_cache[name] = (fingerprint, channel)
    return channel
