"""Alipay face-to-face payment (precreate QR), RSA2 notify verification and order query."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

from app.core.config import get_settings
from app.core.errors import AppError, ErrorCode
from app.core.platform_config import RuntimeConfig
from app.modules.billing.payment_channels.base import (
    SDK_TIMEOUT_SECONDS,
    CallbackResult,
    PaymentInit,
    QueryResult,
    assert_callback_fresh,
    channel_error,
    run_in_sdk_pool,
)

if TYPE_CHECKING:
    from app.modules.billing.models import Order


ALIPAY_CFG_KEYS = (
    "alipay_app_id",
    "alipay_private_key",
    "alipay_public_key",
    "alipay_seller_id",
)


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

    async def create_payment(  # pragma: no cover
        self,
        order: "Order",
        *,
        return_url: str,  # noqa: ARG002
        cancel_url: str,  # noqa: ARG002
    ) -> PaymentInit:
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
        return PaymentInit(resp["qr_code"])

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
            currency="CNY",
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
                    currency="CNY",
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
