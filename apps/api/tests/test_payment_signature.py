"""真实收款渠道的验签路径(/webhooks/wechatpay、/webhooks/alipay),用测试内现生成的密钥真实签名。"""

# 白盒用例:直探模块内部
# pyright: reportPrivateUsage=false

import base64
import json
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.parse import urlencode

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.core.errors import AppError
from app.core.platform_config import runtime_config_from_strings as rc
from app.modules.billing.payment_channels import AlipayChannel, WechatChannel


@pytest.fixture(scope="module")
def keypair() -> tuple[str, str]:
    """(私钥 PEM, 公钥 PEM),module 级复用。"""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    pub = (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    return priv, pub


def _sign(private_pem: str, message: bytes) -> str:
    key = serialization.load_pem_private_key(private_pem.encode(), password=None)
    assert isinstance(key, rsa.RSAPrivateKey)
    return base64.b64encode(key.sign(message, padding.PKCS1v15(), hashes.SHA256())).decode()


APP_ID = "2021000100000001"
SELLER_ID = "2088123412341234"


def _alipay_channel(keypair: tuple[str, str]) -> AlipayChannel:
    priv, pub = keypair
    return AlipayChannel(
        rc(
            {
                "alipay_app_id": APP_ID,
                "alipay_private_key": priv,
                "alipay_public_key": pub,
                "alipay_seller_id": SELLER_ID,
            }
        )
    )


def _alipay_notify(private_pem: str, **overrides: str) -> bytes:
    """按官方加签口径构造通知体:去 sign/sign_type 与空值,按 key 排序,k=v 用 & 连。"""
    notify_time = (datetime.now(UTC) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
    params = {
        "app_id": APP_ID,
        "seller_id": SELLER_ID,
        "out_trade_no": "SDL20260819000001",
        "trade_no": "2026081922001400000001",
        "total_amount": "100.00",
        "trade_status": "TRADE_SUCCESS",
        "notify_id": "abc123",
        "notify_time": notify_time,
        **overrides,
    }
    # 加签口径是解码后的值,发送体须 urlencode
    message = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    params["sign_type"] = "RSA2"
    params["sign"] = _sign(private_pem, message.encode())
    return urlencode(params).encode()


class TestAlipayCallbackSignature:
    async def test_valid_signature_accepted(self, keypair):
        priv, _pub = keypair
        result = await _alipay_channel(keypair).parse_callback({}, _alipay_notify(priv))
        assert result.order_no == "SDL20260819000001"
        assert result.channel_txn_id == "2026081922001400000001"
        assert str(result.amount) == "100.00"
        assert result.success is True

    async def test_tampered_amount_rejected(self, keypair):
        """篡改金额签名对不上 → 拒。"""
        priv, _pub = keypair
        body = _alipay_notify(priv).replace(b"total_amount=100.00", b"total_amount=99999.00")
        assert b"total_amount=99999.00" in body
        with pytest.raises(AppError) as exc:
            await _alipay_channel(keypair).parse_callback({}, body)
        assert exc.value.code.name == "PAYMENT_CHANNEL_ERROR"

    async def test_foreign_app_id_rejected(self, keypair):
        """app_id 核对。"""
        priv, _pub = keypair
        with pytest.raises(AppError) as exc:
            await _alipay_channel(keypair).parse_callback(
                {}, _alipay_notify(priv, app_id="2021000199999999")
            )
        assert exc.value.message_key == "billing.alipayCallbackMerchantMismatch"

    async def test_foreign_seller_id_rejected(self, keypair):
        priv, _pub = keypair
        with pytest.raises(AppError) as exc:
            await _alipay_channel(keypair).parse_callback(
                {}, _alipay_notify(priv, seller_id="2088999999999999")
            )
        assert exc.value.message_key == "billing.alipayCallbackMerchantMismatch"

    async def test_refunded_notification_is_not_success(self, keypair):
        priv, _pub = keypair
        result = await _alipay_channel(keypair).parse_callback(
            {}, _alipay_notify(priv, trade_status="TRADE_CLOSED")
        )
        assert result.success is False

    async def test_partial_refund_is_a_reversal(self, keypair):
        """挂了说明:部分退款通知(trade_status 仍 TRADE_SUCCESS,refund_fee 有值)被当成功回调放过。"""
        priv, _pub = keypair
        result = await _alipay_channel(keypair).parse_callback(
            {}, _alipay_notify(priv, refund_fee="30.00", gmt_refund="2026-08-19 12:00:00")
        )
        assert result.success is False
        assert result.refund_amount == Decimal("30.00")

    async def test_stale_notify_time_rejected(self, keypair):
        """挂了说明:回调没有新鲜度窗口,截获的通知可无限期重放。"""
        priv, _pub = keypair
        stale = (datetime.now(UTC) + timedelta(hours=8) - timedelta(hours=2)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        with pytest.raises(AppError) as exc:
            await _alipay_channel(keypair).parse_callback(
                {}, _alipay_notify(priv, notify_time=stale)
            )
        assert exc.value.message_key == "billing.alipayCallbackVerifyFailed"

    async def test_blank_value_param_still_verifies(self, keypair):
        """通知里带空字段(如 body=)不误判验签失败。"""
        from urllib.parse import parse_qsl

        priv, _pub = keypair
        body = _alipay_notify(priv) + b"&body=&extend="
        # 解析侧丢掉空值参数
        assert dict(parse_qsl(body.decode())).get("body") is None
        result = await _alipay_channel(keypair).parse_callback({}, body)
        assert result.success is True


APIV3_KEY = "0123456789abcdef0123456789abcdef"
MCHID = "1900000001"
WX_APPID = "wx0123456789abcdef"


def _wechat_channel(keypair: tuple[str, str]) -> WechatChannel:
    priv, pub = keypair
    return WechatChannel(
        rc(
            {
                "wechat_mchid": MCHID,
                "wechat_appid": WX_APPID,
                "wechat_private_key": priv,
                "wechat_cert_serial_no": "ABCDEF0123456789ABCDEF0123456789ABCDEF01",
                "wechat_apiv3_key": APIV3_KEY,
                "wechat_public_key": pub,
                "wechat_public_key_id": "PUB_KEY_ID_0000000000000000000000000000",
            }
        )
    )


def _wechat_notify(private_pem: str, resource_plain: dict) -> tuple[dict, bytes]:
    """构造一条 APIv3 通知:资源体 AES-256-GCM 加密,再对 timestamp\\nnonce\\nbody\\n 签名。"""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = "0123456789ab"
    associated = "transaction"
    ciphertext = AESGCM(APIV3_KEY.encode()).encrypt(
        nonce.encode(), json.dumps(resource_plain).encode(), associated.encode()
    )
    body_obj = {
        "id": "evt-1",
        "event_type": "TRANSACTION.SUCCESS",
        "resource_type": "encrypt-resource",
        "resource": {
            "algorithm": "AEAD_AES_256_GCM",
            "ciphertext": base64.b64encode(ciphertext).decode(),
            "nonce": nonce,
            "associated_data": associated,
            "original_type": "transaction",
        },
    }
    body = json.dumps(body_obj).encode()
    timestamp, wx_nonce = str(int(time.time())), "nonce123"
    signed = f"{timestamp}\n{wx_nonce}\n{body.decode()}\n".encode()
    signature = _sign(private_pem, signed)
    headers = {
        "Wechatpay-Timestamp": timestamp,
        "Wechatpay-Nonce": wx_nonce,
        "Wechatpay-Signature": signature,
        "Wechatpay-Serial": "PUB_KEY_ID_0000000000000000000000000000",
        "Wechatpay-Signature-Type": "WECHATPAY2-SHA256-RSA2048",
    }
    return headers, body


def _wx_resource(**overrides) -> dict:
    return {
        "mchid": MCHID,
        "appid": WX_APPID,
        "out_trade_no": "SDL20260819000002",
        "transaction_id": "4200001234202608190000000001",
        "trade_state": "SUCCESS",
        "amount": {"total": 10000, "currency": "CNY"},
        **overrides,
    }


class TestWechatCallbackSignature:
    async def test_missing_public_key_fails_closed(self, keypair):
        """公钥模式是唯一验签模式:公钥/公钥 ID 缺失即渠道不可用。"""
        priv, pub = keypair
        for overrides in (
            {"wechat_public_key": "", "wechat_public_key_id": ""},
            {"wechat_public_key_id": ""},
        ):
            cfg = {
                "wechat_mchid": MCHID,
                "wechat_appid": WX_APPID,
                "wechat_private_key": priv,
                "wechat_cert_serial_no": "ABCDEF0123456789ABCDEF0123456789ABCDEF01",
                "wechat_apiv3_key": APIV3_KEY,
                "wechat_public_key": pub,
                "wechat_public_key_id": "PUB_KEY_ID_0000000000000000000000000000",
                **overrides,
            }
            with pytest.raises(AppError) as exc:
                WechatChannel(rc(cfg))
            assert exc.value.message_key == "billing.wechatCredentialsIncomplete"

    async def test_valid_signature_accepted(self, keypair):
        priv, _pub = keypair
        headers, body = _wechat_notify(priv, _wx_resource())
        result = await _wechat_channel(keypair).parse_callback(headers, body)
        assert result.order_no == "SDL20260819000002"
        assert str(result.amount) == "100"
        assert result.success is True

    async def test_foreign_currency_rejected(self, keypair):
        """外币通知拒收。"""
        priv, _pub = keypair
        headers, body = _wechat_notify(
            priv, _wx_resource(amount={"total": 10000, "currency": "USD"})
        )
        with pytest.raises(AppError) as exc:
            await _wechat_channel(keypair).parse_callback(headers, body)
        assert exc.value.code.name == "PAYMENT_CHANNEL_ERROR"

    async def test_tampered_body_rejected(self, keypair):
        priv, _pub = keypair
        headers, body = _wechat_notify(priv, _wx_resource())
        with pytest.raises(AppError) as exc:
            await _wechat_channel(keypair).parse_callback(headers, body + b" ")
        assert exc.value.code.name == "PAYMENT_CHANNEL_ERROR"

    async def test_unsigned_probe_is_app_error_not_500(self, keypair):
        """未签名请求归一化成 AppError,不是 500。"""
        priv, _pub = keypair
        headers, body = _wechat_notify(priv, _wx_resource())
        headers.pop("Wechatpay-Signature-Type")
        headers.pop("Wechatpay-Signature")
        with pytest.raises(AppError):
            await _wechat_channel(keypair).parse_callback(headers, body)

    async def test_foreign_merchant_rejected(self, keypair):
        """收款商户不符 → 拒。"""
        priv, _pub = keypair
        headers, body = _wechat_notify(priv, _wx_resource(mchid="1900009999"))
        with pytest.raises(AppError) as exc:
            await _wechat_channel(keypair).parse_callback(headers, body)
        assert exc.value.message_key == "billing.wechatCallbackMerchantMismatch"

    async def test_missing_mchid_rejected(self, keypair):
        """通知不带 mchid/appid 即判失败。"""
        priv, _pub = keypair
        resource = _wx_resource()
        del resource["mchid"]
        headers, body = _wechat_notify(priv, resource)
        with pytest.raises(AppError) as exc:
            await _wechat_channel(keypair).parse_callback(headers, body)
        assert exc.value.message_key == "billing.wechatCallbackMerchantMismatch"

    async def test_unknown_serial_never_reaches_sdk(self, keypair, monkeypatch):
        """挂了说明:陌生 Wechatpay-Serial 会让 SDK 去微信拉平台证书,未验签的外部请求触发出网。"""
        priv, _pub = keypair
        channel = _wechat_channel(keypair)
        called = []
        monkeypatch.setattr(channel._wxpay, "callback", lambda *a, **k: called.append(1))
        headers, body = _wechat_notify(priv, _wx_resource())
        headers["Wechatpay-Serial"] = "ABCDEF0123456789ABCDEF0123456789ABCDEF99"
        with pytest.raises(AppError):
            await channel.parse_callback(headers, body)
        assert called == []

    async def test_stale_timestamp_rejected(self, keypair):
        """挂了说明:回调没有新鲜度窗口,截获的通知可无限期重放。"""
        priv, _pub = keypair
        headers, body = _wechat_notify(priv, _wx_resource())
        headers["Wechatpay-Timestamp"] = str(int(time.time()) - 3600)
        with pytest.raises(AppError):
            await _wechat_channel(keypair).parse_callback(headers, body)

    async def test_lowercase_header_names_accepted(self, keypair):
        """头字段读取大小写不敏感。"""
        priv, _pub = keypair
        headers, body = _wechat_notify(priv, _wx_resource())
        lowered = {k.lower(): v for k, v in headers.items()}
        result = await _wechat_channel(keypair).parse_callback(lowered, body)
        assert result.success is True
