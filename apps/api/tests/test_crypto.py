"""主密钥版本化:v2 密文带 kid、HKDF 子密钥分离、双密钥读迁移、解密 fail-closed。"""

# pyright: reportPrivateUsage=false

import base64
import hashlib
import hmac
from types import SimpleNamespace

import pytest
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core import crypto

_KEY_A = base64.urlsafe_b64encode(bytes(range(32))).decode()
_KEY_B = base64.urlsafe_b64encode(bytes(range(32, 64))).decode()


def _settings(key: str | None = _KEY_A, prev: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        config_encryption_key=key,
        config_encryption_key_previous=prev,
        jwt_secret="test-jwt-secret-32-bytes-minimum!!",
    )


@pytest.fixture
def set_keys(monkeypatch: pytest.MonkeyPatch):
    """提供 crypto 当前及旧主密钥的配置替换函数。"""

    def _set(key: str | None = _KEY_A, prev: str | None = None) -> None:
        monkeypatch.setattr(crypto, "get_settings", lambda: _settings(key, prev))

    return _set


def _raw_key(b64: str) -> bytes:
    return base64.urlsafe_b64decode(b64)


class TestV2Format:
    def test_kid_segment_matches_active_key_fingerprint(self, set_keys):
        set_keys()
        token = crypto.encrypt_str("plain", aad="k")
        parts = token.split(":", 3)
        assert parts[0] == "enc" and parts[1] == "v2"
        assert parts[2] == hashlib.sha256(_raw_key(_KEY_A)).hexdigest()[:12]

    def test_ciphertext_key_differs_from_master(self, set_keys):
        """v2 密文不能用裸主密钥直接解开(HKDF 派生)。"""
        set_keys()
        token = crypto.encrypt_str("plain", aad="k")
        blob = base64.b64decode(token.split(":", 3)[3])
        with pytest.raises(InvalidTag):
            AESGCM(_raw_key(_KEY_A)).decrypt(blob[:12], blob[12:], b"k")

    def test_wrong_aad_rejected(self, set_keys):
        """AAD 绑定键名:密文不可跨字段搬运。"""
        set_keys()
        token = crypto.encrypt_str("secret", aad="sms_access_key_secret")
        with pytest.raises(InvalidTag):
            crypto.decrypt_str(token, aad="wechat_apiv3_key")


class TestDecryptDualRead:
    def test_v2_readable_via_previous_during_rotation(self, set_keys):
        """轮换窗口:旧钥匙写的 v2 密文经 PREVIOUS 可读,新写入只认新钥匙。"""
        set_keys(_KEY_B)
        rotated_v2 = crypto.encrypt_str("v2-secret", aad="k")
        set_keys(_KEY_A, prev=_KEY_B)
        assert crypto.decrypt_str(rotated_v2, aad="k") == "v2-secret"
        fresh = crypto.encrypt_str("new-secret", aad="k")
        assert fresh.split(":", 3)[2] == hashlib.sha256(_raw_key(_KEY_A)).hexdigest()[:12]

    def test_unknown_kid_rejected_without_key_scan(self, set_keys):
        set_keys()
        token = crypto.encrypt_str("plain", aad="k")
        forged = token.replace(token.split(":")[2], "0" * 12, 1)
        with pytest.raises(ValueError, match="未知 kid"):
            crypto.decrypt_str(forged, aad="k")

    def test_malformed_tokens_rejected(self, set_keys):
        set_keys()
        with pytest.raises(ValueError, match="版本前缀"):
            crypto.decrypt_str("not-a-token", aad="k")
        with pytest.raises(ValueError, match="版本前缀"):
            crypto.decrypt_str("enc:v9:AAAA", aad="k")
        with pytest.raises(ValueError, match="kid"):
            crypto.decrypt_str("enc:v2:no-kid-separator", aad="k")


class TestDigestGenerations:
    def test_write_uses_hkdf_current_generation(self, set_keys):
        """写入世代 = HKDF(当前主密钥),与裸主密钥 HMAC 输出不同。"""
        set_keys()
        digest = crypto.hash_api_key("sk-test")
        hkdf_gen = hmac.new(
            crypto._derive(_raw_key(_KEY_A), crypto._MAC_INFO),
            b"service-api-key|sk-test",
            hashlib.sha256,
        ).hexdigest()
        bare_gen = hmac.new(
            _raw_key(_KEY_A), b"service-api-key|sk-test", hashlib.sha256
        ).hexdigest()
        assert digest == hkdf_gen
        assert digest != bare_gen

    def test_candidates_cover_previous(self, set_keys):
        """读路径 candidates:[当前HKDF];挂 previous 追加旧钥匙的派生世代。"""
        set_keys()
        single = crypto.hash_api_key_candidates("sk-test")
        assert len(single) == 1

        set_keys(_KEY_A, prev=_KEY_B)
        rotated = crypto.hash_api_key_candidates("sk-test")
        assert len(rotated) == 2
        assert rotated[0] == single[0]
        prev_gen = hmac.new(
            crypto._derive(_raw_key(_KEY_B), crypto._MAC_INFO),
            b"service-api-key|sk-test",
            hashlib.sha256,
        ).hexdigest()
        assert prev_gen in rotated

    def test_dev_fallback_key_derivation_still_works(self, set_keys):
        """dev/test 未配主密钥时从 jwt_secret 派生。"""
        set_keys(None)
        token = crypto.encrypt_str("plain", aad="k")
        assert crypto.decrypt_str(token, aad="k") == "plain"
        assert crypto.hash_api_key("sk-x")
