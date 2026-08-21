"""真实收款渠道的验签路径。

`/webhooks/wechatpay` 与 `/webhooks/alipay` 无 JWT、无用户鉴权,设计前提是「验签即鉴权」——
而在此之前 AlipayChannel / WechatChannel / verify_with_rsa 在整个测试树里字面出现 0 次:
两条 webhook 用例把 get_channel monkeypatch 成 MockChannel,它的 parse_callback 只做
json.loads,不验任何签名。于是这条无鉴权加钱接口唯一的防线零覆盖,而 94.6% 的 billing
覆盖率是在**不含真实收款验签**的基础上算出来的(整个类被 pragma: no cover 摘出了分母)。

「需要真实商户凭据」对 parse_callback 并不成立:验一条支付宝通知只需要在测试里现生成一对
RSA 密钥;验一条微信通知只需要一对合成密钥 + 任意 32 字节 apiv3 key。凭据只有下单和查单
才真的需要。
"""

import base64
import json
from urllib.parse import urlencode

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.core.errors import AppError
from app.modules.billing.payment_channels import AlipayChannel, WechatChannel


@pytest.fixture(scope="module")
def keypair() -> tuple[str, str]:
    """(私钥 PEM, 公钥 PEM)。2048 位生成一次,module 级复用。"""
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


def _alipay_channel(keypair: tuple[str, str], *, seller_id: str = SELLER_ID) -> AlipayChannel:
    priv, pub = keypair
    return AlipayChannel(
        {
            "alipay_app_id": APP_ID,
            "alipay_private_key": priv,
            "alipay_public_key": pub,
            "alipay_seller_id": seller_id,
        }
    )


def _alipay_notify(private_pem: str, **overrides: str) -> bytes:
    """按官方异步通知的加签口径构造一条通知体:去 sign/sign_type,按 key 排序,k=v 用 & 连。"""
    params = {
        "app_id": APP_ID,
        "seller_id": SELLER_ID,
        "out_trade_no": "SDL20260819000001",
        "trade_no": "2026081922001400000001",
        "total_amount": "100.00",
        "trade_status": "TRADE_SUCCESS",
        "notify_id": "abc123",
        **overrides,
    }
    # 加签口径是**解码后**的值;而真实通知是 application/x-www-form-urlencoded,
    # 签名里的 + / = 会被转义成 %2B 等 —— 不 urlencode 的话 parse_qsl 会把 + 解成空格,
    # 一条完全合法的通知会验签失败(这正是最容易在联调时踩到的一步)。
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
        """改金额 = 最直接的攻击:签名对不上必须拒,而不是按篡改后的金额入账。"""
        priv, _pub = keypair
        body = _alipay_notify(priv).replace(b"total_amount=100.00", b"total_amount=99999.00")
        assert b"total_amount=99999.00" in body
        with pytest.raises(AppError) as exc:
            await _alipay_channel(keypair).parse_callback({}, body)
        assert exc.value.code.name == "PAYMENT_CHANNEL_ERROR"

    async def test_missing_signature_rejected(self, keypair):
        body = b"app_id=%s&out_trade_no=X&total_amount=1.00&trade_status=TRADE_SUCCESS" % (
            APP_ID.encode()
        )
        with pytest.raises(AppError):
            await _alipay_channel(keypair).parse_callback({}, body)

    async def test_signature_from_another_key_rejected(self, keypair):
        """攻击者用自己的密钥签一条格式完全正确的通知 —— 必须被我方公钥挡下。"""
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        other_pem = other.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()
        with pytest.raises(AppError):
            await _alipay_channel(keypair).parse_callback({}, _alipay_notify(other_pem))

    async def test_foreign_app_id_rejected(self, keypair):
        """官方通知校验清单里的 app_id 核对(纵深防御)。"""
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

    async def test_seller_id_check_optional(self, keypair):
        """未配置收款 PID 时只核 app_id —— 存量商户不会因为没填这一项而收不到款。"""
        priv, _pub = keypair
        channel = _alipay_channel(keypair, seller_id="")
        result = await channel.parse_callback(
            {}, _alipay_notify(priv, seller_id="2088999999999999")
        )
        assert result.success is True

    async def test_refunded_notification_is_not_success(self, keypair):
        priv, _pub = keypair
        result = await _alipay_channel(keypair).parse_callback(
            {}, _alipay_notify(priv, trade_status="TRADE_CLOSED")
        )
        assert result.success is False


APIV3_KEY = "0123456789abcdef0123456789abcdef"
MCHID = "1900000001"
WX_APPID = "wx0123456789abcdef"


def _wechat_channel(keypair: tuple[str, str]) -> WechatChannel:
    priv, pub = keypair
    return WechatChannel(
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


def _wechat_notify(
    private_pem: str, resource_plain: dict, *, sign_ok: bool = True
) -> tuple[dict, bytes]:
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
    timestamp, wx_nonce = "1787000000", "nonce123"
    signed = f"{timestamp}\n{wx_nonce}\n{body.decode()}\n".encode()
    signature = _sign(private_pem, signed if sign_ok else signed + b"tamper")
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
        "amount": {"total": 10000},
        **overrides,
    }


class TestWechatCallbackSignature:
    async def test_valid_signature_accepted(self, keypair):
        priv, _pub = keypair
        headers, body = _wechat_notify(priv, _wx_resource())
        result = await _wechat_channel(keypair).parse_callback(headers, body)
        assert result.order_no == "SDL20260819000002"
        assert str(result.amount) == "100"
        assert result.success is True

    async def test_tampered_body_rejected(self, keypair):
        priv, _pub = keypair
        headers, body = _wechat_notify(priv, _wx_resource())
        with pytest.raises(AppError) as exc:
            await _wechat_channel(keypair).parse_callback(headers, body + b" ")
        assert exc.value.code.name == "PAYMENT_CHANNEL_ERROR"

    async def test_bad_signature_rejected(self, keypair):
        priv, _pub = keypair
        headers, body = _wechat_notify(priv, _wx_resource(), sign_ok=False)
        with pytest.raises(AppError):
            await _wechat_channel(keypair).parse_callback(headers, body)

    async def test_unsigned_probe_is_app_error_not_500(self, keypair):
        """未签名的探测请求会让 SDK 抛裸 Exception —— 必须归一化成 AppError,而不是 500。"""
        priv, _pub = keypair
        headers, body = _wechat_notify(priv, _wx_resource())
        headers.pop("Wechatpay-Signature-Type")
        headers.pop("Wechatpay-Signature")
        with pytest.raises(AppError):
            await _wechat_channel(keypair).parse_callback(headers, body)

    async def test_foreign_merchant_rejected(self, keypair):
        """签名可能来自一条合法的微信通知,但收款商户不是我们 —— 官方清单要求核对。"""
        priv, _pub = keypair
        headers, body = _wechat_notify(priv, _wx_resource(mchid="1900009999"))
        with pytest.raises(AppError) as exc:
            await _wechat_channel(keypair).parse_callback(headers, body)
        assert exc.value.message_key == "billing.wechatCallbackMerchantMismatch"
