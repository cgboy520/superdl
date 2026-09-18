"""Signature, freshness and merchant checks of WeChat Pay and Alipay callbacks."""

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
    """(private key PEM, public key PEM), reused at module level."""
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
    """Build a URL-encoded signed Alipay notice body."""
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
        """A tampered amount breaks the signature → refused."""
        priv, _pub = keypair
        body = _alipay_notify(priv).replace(b"total_amount=100.00", b"total_amount=99999.00")
        assert b"total_amount=99999.00" in body
        with pytest.raises(AppError) as exc:
            await _alipay_channel(keypair).parse_callback({}, body)
        assert exc.value.code.name == "PAYMENT_CHANNEL_ERROR"

    async def test_foreign_app_id_rejected(self, keypair):
        """app_id check."""
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
        priv, _pub = keypair
        result = await _alipay_channel(keypair).parse_callback(
            {}, _alipay_notify(priv, refund_fee="30.00", gmt_refund="2026-08-19 12:00:00")
        )
        assert result.success is False
        assert result.refund_amount == Decimal("30.00")

    async def test_stale_notify_time_rejected(self, keypair):
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
        """Empty fields in the notice (e.g. body=) do not fail verification."""
        from urllib.parse import parse_qsl

        priv, _pub = keypair
        body = _alipay_notify(priv) + b"&body=&extend="
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
    """Build an APIv3 notice: the resource encrypted with AES-256-GCM, then
    timestamp\\nnonce\\nbody\\n
    signed."""
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
        """Public-key mode is the only verification mode: a missing key / key id makes the channel
        unavailable."""
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

    async def test_wire_currency_passed_through(self, keypair):
        """The wire currency rides on the result; handle_callback compares it with the order."""
        priv, _pub = keypair
        headers, body = _wechat_notify(
            priv, _wx_resource(amount={"total": 10000, "currency": "USD"})
        )
        result = await _wechat_channel(keypair).parse_callback(headers, body)
        assert result.currency == "USD" and result.amount == Decimal("100.00")

    async def test_missing_currency_rejected(self, keypair):
        """`amount.currency` is mandatory on the wire; without it the callback is not trusted on
        amount alone."""
        priv, _pub = keypair
        headers, body = _wechat_notify(priv, _wx_resource(amount={"total": 10000}))
        with pytest.raises(AppError) as exc:
            await _wechat_channel(keypair).parse_callback(headers, body)
        assert exc.value.message_key == "billing.wechatCallbackVerifyFailed"

    async def test_tampered_body_rejected(self, keypair):
        priv, _pub = keypair
        headers, body = _wechat_notify(priv, _wx_resource())
        with pytest.raises(AppError) as exc:
            await _wechat_channel(keypair).parse_callback(headers, body + b" ")
        assert exc.value.code.name == "PAYMENT_CHANNEL_ERROR"

    async def test_unsigned_probe_is_app_error_not_500(self, keypair):
        """An unsigned request normalises to AppError, not 500."""
        priv, _pub = keypair
        headers, body = _wechat_notify(priv, _wx_resource())
        headers.pop("Wechatpay-Signature-Type")
        headers.pop("Wechatpay-Signature")
        with pytest.raises(AppError):
            await _wechat_channel(keypair).parse_callback(headers, body)

    async def test_foreign_merchant_rejected(self, keypair):
        """Payee merchant mismatch → refused."""
        priv, _pub = keypair
        headers, body = _wechat_notify(priv, _wx_resource(mchid="1900009999"))
        with pytest.raises(AppError) as exc:
            await _wechat_channel(keypair).parse_callback(headers, body)
        assert exc.value.message_key == "billing.wechatCallbackMerchantMismatch"

    async def test_missing_mchid_rejected(self, keypair):
        """A notice without mchid/appid fails."""
        priv, _pub = keypair
        resource = _wx_resource()
        del resource["mchid"]
        headers, body = _wechat_notify(priv, resource)
        with pytest.raises(AppError) as exc:
            await _wechat_channel(keypair).parse_callback(headers, body)
        assert exc.value.message_key == "billing.wechatCallbackMerchantMismatch"

    async def test_unknown_serial_never_reaches_sdk(self, keypair, monkeypatch):
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
        priv, _pub = keypair
        headers, body = _wechat_notify(priv, _wx_resource())
        headers["Wechatpay-Timestamp"] = str(int(time.time()) - 3600)
        with pytest.raises(AppError):
            await _wechat_channel(keypair).parse_callback(headers, body)

    async def test_lowercase_header_names_accepted(self, keypair):
        """Header reads are case-insensitive."""
        priv, _pub = keypair
        headers, body = _wechat_notify(priv, _wx_resource())
        lowered = {k.lower(): v for k, v in headers.items()}
        result = await _wechat_channel(keypair).parse_callback(lowered, body)
        assert result.success is True
