"""WeChat Pay Native (QR) over APIv3; verification in public-key mode only."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from app.core.config import get_settings
from app.core.errors import AppError, ErrorCode
from app.core.platform_config import RuntimeConfig
from app.core.servercopy import copy as server_copy
from app.modules.billing.payment_channels.base import (
    SDK_TIMEOUT,
    CallbackResult,
    PaymentInit,
    QueryResult,
    assert_callback_fresh,
    channel_error,
    header_value,
    run_in_sdk_pool,
)

if TYPE_CHECKING:
    from app.modules.billing.models import Order


WECHAT_CFG_KEYS = (
    "wechat_mchid",
    "wechat_appid",
    "wechat_private_key",
    "wechat_cert_serial_no",
    "wechat_apiv3_key",
    "wechat_public_key",
    "wechat_public_key_id",
)


class WechatChannel:
    """WeChat Pay Native (QR), APIv3. Public-key mode only: wechat_public_key and
    wechat_public_key_id are required."""

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

    async def create_payment(  # pragma: no cover
        self,
        order: "Order",
        *,
        return_url: str,  # noqa: ARG002
        cancel_url: str,  # noqa: ARG002
    ) -> PaymentInit:
        code, message = await run_in_sdk_pool(
            self._wxpay.pay,
            description=server_copy("billing.recharge.subject", order_no=order.order_no),
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
        return PaymentInit(json.loads(message)["code_url"])

    async def parse_callback(self, headers: dict[str, str], body: bytes) -> CallbackResult:
        """Check the public key id and timestamp first, then let the SDK verify and decrypt;
        unverified requests must never trigger a certificate download.

        Requires TRANSACTION.SUCCESS, the matching merchant and app, complete transaction fields
        and the CNY currency.
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
        return CallbackResult(
            order_no=out_trade_no,
            channel_txn_id=transaction_id,
            amount=Decimal(total) / 100,
            success=trade_state == "SUCCESS",
            currency=currency,
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
                currency=data["amount"].get("currency"),
            )
        if state in ("CLOSED", "REVOKED", "PAYERROR"):
            return QueryResult("closed")
        if state in ("NOTPAY", "USERPAYING"):
            return QueryResult("pending")
        return QueryResult("unknown")
