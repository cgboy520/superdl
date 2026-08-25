"""支付渠道抽象。

- mock:dev/test 默认,POST /api/v1/webhooks/mock 直接标记支付成功
- wechat:wechatpayv3(平台证书自动更新 + 验签)
- alipay:alipay-sdk-python

微信/支付宝需真实商户凭据;未配置时报 PAYMENT_CHANNEL_ERROR。
"""

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, ClassVar, Literal, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AppError, ErrorCode
from app.core.platform_config import get_effective_platform_config
from app.core.timeutil import ensure_utc, now_utc

if TYPE_CHECKING:
    from app.modules.billing.models import Order


# 回调时间戳新鲜度窗口(重放本身已由 handle_callback 的幂等挡住)。
# 放宽到 24h 以覆盖渠道一天量级的重试与关单后迟到回调的 rescue 入账(见 payment_service)。
CALLBACK_MAX_AGE_SECONDS = 24 * 3600


def _check_freshness(callback_at: datetime, *, key: str) -> None:
    """回调时间戳超出 ±24h 即拒(判验签失败,不单独造错误码)。"""
    if abs((now_utc() - ensure_utc(callback_at)).total_seconds()) > CALLBACK_MAX_AGE_SECONDS:
        raise AppError(ErrorCode.PAYMENT_CHANNEL_ERROR, key=key)


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
        # InvalidOperation:amount 非数值;TypeError:报文不是 JSON 对象(数组/标量)
        except (ValueError, KeyError, TypeError, InvalidOperation) as exc:
            raise AppError(
                ErrorCode.PAYMENT_CHANNEL_ERROR, key="billing.mockCallbackParseFailed"
            ) from exc
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
ALIPAY_CFG_KEYS = (
    "alipay_app_id",
    "alipay_private_key",
    "alipay_public_key",
    "alipay_seller_id",
)


class WechatChannel:
    """微信支付 Native(扫码),APIv3。

    验签双模式(wechatpayv3 原生支持):配置了微信支付公钥 + 公钥 ID(PUB_KEY_ID_*)
    走公钥模式(新商户唯一模式);否则回退平台证书模式(SDK 自动拉取轮换)。
    """

    name = "wechat"

    def __init__(self, cfg: Mapping[str, str]) -> None:  # pragma: no cover - 需真实商户凭据
        required = (
            "wechat_mchid",
            "wechat_appid",
            "wechat_private_key",
            "wechat_cert_serial_no",
            "wechat_apiv3_key",
        )
        if not all(cfg[k] for k in required):
            raise AppError(
                ErrorCode.PAYMENT_CHANNEL_ERROR, key="billing.wechatCredentialsIncomplete"
            )
        public_key = cfg["wechat_public_key"] or None
        public_key_id = cfg["wechat_public_key_id"] or None
        if bool(public_key) != bool(public_key_id):
            raise AppError(ErrorCode.PAYMENT_CHANNEL_ERROR, key="billing.wechatPublicKeyPair")
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
        self._mchid = cfg["wechat_mchid"]
        self._appid = cfg["wechat_appid"]

    async def create_payment(self, order: "Order") -> str:  # pragma: no cover - 需真实商户凭据
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
            raise AppError(
                ErrorCode.PAYMENT_CHANNEL_ERROR,
                key="billing.wechatCreateFailed",
                params={"message": message},
            )
        return json.loads(message)["code_url"]

    async def parse_callback(self, headers: dict[str, str], body: bytes) -> CallbackResult:
        """验签 + 时间戳新鲜度 + AES-GCM 解密 + 核对商户身份。

        SDK 的失败路径不都是返回值(未签名探测请求会直接抛裸 Exception),在此统一归一化。
        """
        import asyncio
        from typing import Any

        # 头部键大小写不敏感:路由层 dict(request.headers) 是小写,测试直传是原样大小写
        lowered = {k.lower(): v for k, v in headers.items()}
        try:
            callback_at = datetime.fromtimestamp(
                int(lowered.get("wechatpay-timestamp", "")), tz=UTC
            )
        except (ValueError, OverflowError, OSError) as exc:
            raise AppError(
                ErrorCode.PAYMENT_CHANNEL_ERROR, key="billing.wechatCallbackVerifyFailed"
            ) from exc
        _check_freshness(callback_at, key="billing.wechatCallbackVerifyFailed")
        try:
            result: Any = await asyncio.to_thread(self._wxpay.callback, headers, body)
        except AppError:
            raise
        except Exception as exc:
            raise AppError(
                ErrorCode.PAYMENT_CHANNEL_ERROR, key="billing.wechatCallbackVerifyFailed"
            ) from exc
        if not isinstance(result, dict) or result.get("event_type") != "TRANSACTION.SUCCESS":
            raise AppError(
                ErrorCode.PAYMENT_CHANNEL_ERROR, key="billing.wechatCallbackVerifyFailed"
            )
        resource = result.get("resource")
        if not isinstance(resource, dict):
            raise AppError(
                ErrorCode.PAYMENT_CHANNEL_ERROR, key="billing.wechatCallbackVerifyFailed"
            )
        # 除验签外还要核对通知里的商户号/应用号确为已方;缺失即判失败,不按缺失放行。
        if resource.get("mchid") != self._mchid or resource.get("appid") != self._appid:
            raise AppError(
                ErrorCode.PAYMENT_CHANNEL_ERROR, key="billing.wechatCallbackMerchantMismatch"
            )
        # 缺字段显式 4xx(裸 [] 索引的 KeyError 会漏成 500,微信对非 SUCCESS 应答重试 15 次)
        out_trade_no = resource.get("out_trade_no")
        transaction_id = resource.get("transaction_id")
        trade_state = resource.get("trade_state")
        amount_obj = resource.get("amount")
        total = amount_obj.get("total") if isinstance(amount_obj, dict) else None
        currency = amount_obj.get("currency") if isinstance(amount_obj, dict) else None
        if not out_trade_no or not transaction_id or not trade_state or total is None:
            raise AppError(
                ErrorCode.PAYMENT_CHANNEL_ERROR, key="billing.wechatCallbackVerifyFailed"
            )
        # 官方验证清单:币种必须是人民币——商户号/appid 都校了,币种是同一清单上的一行,
        # 漏校则外币通知的金额会被按 CNY 入账
        if currency != "CNY":
            raise AppError(
                ErrorCode.PAYMENT_CHANNEL_ERROR, key="billing.wechatCallbackMerchantMismatch"
            )
        return CallbackResult(
            order_no=out_trade_no,
            channel_txn_id=transaction_id,
            amount=Decimal(total) / 100,
            success=trade_state == "SUCCESS",
        )

    async def query_order(self, order: "Order") -> QueryResult:  # pragma: no cover - 需真实商户
        import asyncio
        import json

        code, message = await asyncio.to_thread(self._wxpay.query, out_trade_no=order.order_no)
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
    """支付宝当面付(precreate 扫码 + 异步通知 RSA2 验签 + 主动查单)。

    凭据(应用私钥 + 支付宝公钥)取自平台配置中心,env SUPERDL_ALIPAY_* 为默认值层。
    """

    name = "alipay"

    GATEWAY = "https://openapi.alipay.com/gateway.do"

    def __init__(self, cfg: Mapping[str, str]) -> None:  # pragma: no cover - 需真实商户凭据
        if not (cfg["alipay_app_id"] and cfg["alipay_private_key"] and cfg["alipay_public_key"]):
            raise AppError(
                ErrorCode.PAYMENT_CHANNEL_ERROR, key="billing.alipayCredentialsIncomplete"
            )
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
        self._app_id = cfg["alipay_app_id"]
        self._seller_id = cfg.get("alipay_seller_id") or ""
        # prod 强制 seller_id:回调除验签外必须核对收款方身份,留空等于收款账号不校验
        if get_settings().environment == "prod" and not self._seller_id:
            raise AppError(ErrorCode.PAYMENT_CHANNEL_ERROR, key="billing.alipaySellerIdRequired")
        self._notify_url = f"{get_settings().public_base_url}/api/v1/webhooks/alipay"

    async def create_payment(self, order: "Order") -> str:  # pragma: no cover - 需真实商户凭据
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

    async def parse_callback(self, headers: dict[str, str], body: bytes) -> CallbackResult:
        from urllib.parse import parse_qsl

        from alipay.aop.api.util.SignatureUtils import (  # type: ignore[import-untyped]
            verify_with_rsa,
        )

        # 官方验签口径:剔除空值参数(parse_qsl 默认 keep_blank_values=False)。
        # 保留空值会把官方未参与加签的空字段拼进验签串,合法通知会被误判失败。
        params = dict(parse_qsl(body.decode("utf-8")))
        sign = params.pop("sign", "")
        params.pop("sign_type", None)
        message = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        try:
            ok = verify_with_rsa(self._public_key, message.encode("utf-8"), sign)
        except Exception as exc:
            raise AppError(
                ErrorCode.PAYMENT_CHANNEL_ERROR, key="billing.alipayCallbackVerifyFailed"
            ) from exc
        if not ok:
            raise AppError(
                ErrorCode.PAYMENT_CHANNEL_ERROR, key="billing.alipayCallbackVerifyFailed"
            )
        # 新鲜度窗口:notify_time 是加签参数(北京时间,秒级),篡改即验签失败
        try:
            notify_at = datetime.strptime(
                params.get("notify_time", ""), "%Y-%m-%d %H:%M:%S"
            ).replace(tzinfo=timezone(timedelta(hours=8)))
        except ValueError as exc:
            raise AppError(
                ErrorCode.PAYMENT_CHANNEL_ERROR, key="billing.alipayCallbackVerifyFailed"
            ) from exc
        _check_freshness(notify_at, key="billing.alipayCallbackVerifyFailed")
        # 官方通知校验清单的另外两条:app_id 必须是自己的应用,seller_id 必须是自己的收款账号
        if params.get("app_id") != self._app_id:
            raise AppError(
                ErrorCode.PAYMENT_CHANNEL_ERROR, key="billing.alipayCallbackMerchantMismatch"
            )
        # seller_id 缺失或不符一律拒收(当面付通知必带 seller_id;缺失即视为伪造/串号)
        if params.get("seller_id") != self._seller_id:
            raise AppError(
                ErrorCode.PAYMENT_CHANNEL_ERROR, key="billing.alipayCallbackMerchantMismatch"
            )
        return CallbackResult(
            order_no=params.get("out_trade_no", ""),
            channel_txn_id=params.get("trade_no", ""),
            amount=Decimal(params.get("total_amount", "0")),
            success=params.get("trade_status") in ("TRADE_SUCCESS", "TRADE_FINISHED"),
        )

    async def query_order(self, order: "Order") -> QueryResult:  # pragma: no cover - 需真实商户
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
            return QueryResult("pending")  # 用户未扫码,渠道侧尚无单
        raise AppError(
            ErrorCode.PAYMENT_CHANNEL_ERROR,
            key="billing.alipayQueryFailed",
            params={"message": resp.get("sub_msg") or resp.get("msg")},
        )


# 真实渠道实例缓存:按配置指纹缓存,配置变更即重建(免重启生效)
_real_channel_cache: dict[str, tuple[tuple[str, ...], PaymentChannel]] = {}


async def get_channel(name: str, session: AsyncSession) -> PaymentChannel:
    settings = get_settings()
    if name == "mock":
        # 无验签渠道只在显式开启时可用;prod 下 payment_mock 必为 false 由 Settings 校验保证
        if not settings.payment_mock:
            raise AppError(ErrorCode.PAYMENT_CHANNEL_ERROR, key="billing.mockDevOnly")
        return MockChannel()
    if name not in ("wechat", "alipay"):
        raise AppError(
            ErrorCode.VALIDATION_ERROR, key="billing.unknownChannel", params={"name": name}
        )
    cfg = await get_effective_platform_config(session)
    keys = WECHAT_CFG_KEYS if name == "wechat" else ALIPAY_CFG_KEYS
    fingerprint = tuple(cfg[k] for k in keys)
    cached = _real_channel_cache.get(name)
    if cached is not None and cached[0] == fingerprint:
        return cached[1]
    # 构造函数同步解析 PEM(RSA 密钥加载是阻塞 CPU 操作),出让事件循环
    channel: PaymentChannel = await asyncio.to_thread(
        WechatChannel if name == "wechat" else AlipayChannel, cfg
    )
    _real_channel_cache[name] = (fingerprint, channel)
    return channel
