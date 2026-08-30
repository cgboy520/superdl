"""主密钥版本化(审计 #18):v2 密文带 kid、HKDF 子密钥分离、双密钥读迁移、
摘要 candidates 兼容 legacy 世代、解密 fail-closed。"""

import base64
import hashlib
import hmac
import os
from types import SimpleNamespace

import pytest
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core import crypto

_KEY_A = base64.urlsafe_b64encode(bytes(range(32))).decode()  # 当前主密钥
_KEY_B = base64.urlsafe_b64encode(bytes(range(32, 64))).decode()  # 轮换前旧主密钥


def _settings(key: str | None = _KEY_A, prev: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        config_encryption_key=key,
        config_encryption_key_previous=prev,
        jwt_secret="test-jwt-secret-32-bytes-minimum!!",
    )


@pytest.fixture
def set_keys(monkeypatch: pytest.MonkeyPatch):
    """钉住 crypto 看到的钥匙串(绕过 lru_cache 的全局 Settings)。"""

    def _set(key: str | None = _KEY_A, prev: str | None = None) -> None:
        monkeypatch.setattr(crypto, "get_settings", lambda: _settings(key, prev))

    return _set


def _raw_key(b64: str) -> bytes:
    return base64.urlsafe_b64decode(b64)


def _encrypt_v1(plaintext: str, raw_b64: str, *, aad: str) -> str:
    """手工构造 legacy v1 密文(裸主密钥,无 HKDF、无 kid)。"""
    nonce = os.urandom(12)
    ct = AESGCM(_raw_key(raw_b64)).encrypt(nonce, plaintext.encode(), aad.encode())
    return "enc:v1:" + base64.b64encode(nonce + ct).decode()


class TestV2Format:
    def test_kid_segment_matches_active_key_fingerprint(self, set_keys):
        set_keys()
        token = crypto.encrypt_str("plain", aad="k")
        parts = token.split(":", 3)  # enc | v2 | kid | b64(nonce+ct)
        assert parts[0] == "enc" and parts[1] == "v2"
        assert parts[2] == hashlib.sha256(_raw_key(_KEY_A)).hexdigest()[:12]

    def test_ciphertext_key_differs_from_master(self, set_keys):
        """HKDF 派生:v2 密文不能用裸主密钥直接解开(用钥分离)。"""
        set_keys()
        token = crypto.encrypt_str("plain", aad="k")
        blob = base64.b64decode(token.split(":", 3)[3])
        with pytest.raises(InvalidTag):
            AESGCM(_raw_key(_KEY_A)).decrypt(blob[:12], blob[12:], b"k")


class TestDecryptDualRead:
    def test_v1_readable_with_same_key(self, set_keys):
        """存量 v1 密文(升级前写入)在新代码下可读。"""
        set_keys()
        legacy = _encrypt_v1("old-secret", _KEY_A, aad="k")
        assert crypto.decrypt_str(legacy, aad="k") == "old-secret"

    def test_v1_and_v2_readable_via_previous_during_rotation(self, set_keys):
        """轮换窗口:旧钥匙写的 v1/v2 密文经 PREVIOUS 可读,新写入只认新钥匙。"""
        # 先以旧钥匙为 active 造密文
        set_keys(_KEY_B)
        legacy_v1 = _encrypt_v1("v1-secret", _KEY_B, aad="k")
        rotated_v2 = crypto.encrypt_str("v2-secret", aad="k")
        # 轮换:新钥匙上位,旧钥匙挂 previous
        set_keys(_KEY_A, prev=_KEY_B)
        assert crypto.decrypt_str(legacy_v1, aad="k") == "v1-secret"
        assert crypto.decrypt_str(rotated_v2, aad="k") == "v2-secret"
        fresh = crypto.encrypt_str("new-secret", aad="k")
        assert fresh.split(":", 3)[2] == hashlib.sha256(_raw_key(_KEY_A)).hexdigest()[:12]

    def test_previous_alone_cannot_encrypt(self, set_keys):
        """previous 只读:轮换后新密文 kid 必须指向当前钥匙。"""
        set_keys(_KEY_A, prev=_KEY_B)
        token = crypto.encrypt_str("x", aad="k")
        assert token.split(":", 3)[2] != hashlib.sha256(_raw_key(_KEY_B)).hexdigest()[:12]

    def test_unknown_kid_rejected_without_key_scan(self, set_keys):
        set_keys()
        token = crypto.encrypt_str("plain", aad="k")
        forged = token.replace(token.split(":")[2], "0" * 12, 1)
        with pytest.raises(ValueError, match="未知 kid"):
            crypto.decrypt_str(forged, aad="k")

    def test_v1_wrong_key_raises_no_silent_fallback(self, set_keys):
        """v1 密文换错钥匙串:双读均失败即抛,不得静默出值。"""
        legacy = _encrypt_v1("secret", _KEY_B, aad="k")
        set_keys()  # 只挂 _KEY_A
        with pytest.raises(InvalidTag):
            crypto.decrypt_str(legacy, aad="k")

    def test_unprefixed_and_truncated_rejected(self, set_keys):
        set_keys()
        with pytest.raises(ValueError, match="版本前缀"):
            crypto.decrypt_str("not-a-token", aad="k")
        with pytest.raises(ValueError, match="kid"):
            crypto.decrypt_str("enc:v2:no-kid-separator", aad="k")


class TestDigestGenerations:
    def test_write_uses_hkdf_current_generation(self, set_keys):
        """写入世代 = HKDF(当前主密钥);不再是裸主密钥 HMAC(legacy)。"""
        set_keys()
        digest = crypto.hash_api_key("sk-test")
        hkdf_gen = hmac.new(
            crypto._derive(_raw_key(_KEY_A), crypto._MAC_INFO),
            b"service-api-key|sk-test",
            hashlib.sha256,
        ).hexdigest()
        legacy_gen = hmac.new(
            _raw_key(_KEY_A), b"service-api-key|sk-test", hashlib.sha256
        ).hexdigest()
        assert digest == hkdf_gen
        assert digest != legacy_gen

    def test_candidates_cover_legacy_and_previous(self, set_keys):
        """读路径 candidates:[当前HKDF, 当前裸(legacy)];挂 previous 追加旧钥匙两世代。"""
        set_keys()
        single = crypto.hash_api_key_candidates("sk-test")
        assert len(single) == 2
        legacy_gen = hmac.new(
            _raw_key(_KEY_A), b"service-api-key|sk-test", hashlib.sha256
        ).hexdigest()
        assert legacy_gen in single  # legacy 世代仍在,旧 API Key 不失效

        set_keys(_KEY_A, prev=_KEY_B)
        rotated = crypto.hash_api_key_candidates("sk-test")
        assert len(rotated) == 4
        assert rotated[:2] == single  # 当前世代恒在最前(写路径同序)
        prev_legacy = hmac.new(
            _raw_key(_KEY_B), b"service-api-key|sk-test", hashlib.sha256
        ).hexdigest()
        assert prev_legacy in rotated

    def test_domain_separation_holds_per_generation(self, set_keys):
        """同一明文不同域的摘要每个世代都不同(域分离不被 candidates 稀释)。"""
        set_keys()
        assert crypto.hash_sms_code("13800000000", "login", "123456") != crypto.hash_node_token(
            "13800000000|login|123456"
        )

    def test_dev_fallback_key_derivation_still_works(self, set_keys):
        """dev/test 未配主密钥时从 jwt_secret 派生(与生产格式无关,行为不回退)。"""
        set_keys(None)
        token = crypto.encrypt_str("plain", aad="k")
        assert crypto.decrypt_str(token, aad="k") == "plain"
        assert crypto.hash_api_key("sk-x")
