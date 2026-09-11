"""敏感配置加密(AES-256-GCM)与带密钥摘要(HMAC-SHA256)。

主密钥只走 env(SUPERDL_CONFIG_ENCRYPTION_KEY,urlsafe-base64 32 字节),dev/test 未配置时
从 jwt_secret 派生;轮换期旧密钥挂 SUPERDL_CONFIG_ENCRYPTION_KEY_PREVIOUS(只读),
见 deploy/cluster/runbooks/key-rotation.md。
密文 `enc:v2:<kid>:<b64(nonce+ct)>`,kid = 主密钥指纹(SHA-256 前 12 hex),未知 kid 拒;
AAD 绑定配置键名。摘要读路径走 candidates(当前世代在前,previous 派生世代随后),
写路径只写当前世代。
"""

import base64
import hashlib
import hmac
import os

from app.core.config import decode_master_key, get_settings

_PREFIX_V2 = "enc:v2:"

# HKDF 域分离:加密子密钥与摘要子密钥独立派生
_ENC_INFO = b"superdl/enc/v2"
_MAC_INFO = b"superdl/mac/v2"
_KDF_SALT = b"superdl-crypto"


def _decode_key(raw: str, *, env_name: str) -> bytes:
    return decode_master_key(raw, label=env_name)


def _active_key() -> bytes:
    settings = get_settings()
    raw = settings.config_encryption_key
    if not raw:
        # dev/test 兜底派生
        return hashlib.sha256(f"{settings.jwt_secret}:platform-config".encode()).digest()
    return _decode_key(raw, env_name="SUPERDL_CONFIG_ENCRYPTION_KEY")


def _previous_key() -> bytes | None:
    """轮换窗口内的旧主密钥(只读):未配置返回 None。"""
    raw = get_settings().config_encryption_key_previous
    if not raw:
        return None
    return _decode_key(raw, env_name="SUPERDL_CONFIG_ENCRYPTION_KEY_PREVIOUS")


def _kid_of(key: bytes) -> str:
    """密钥指纹(12 hex)。"""
    return hashlib.sha256(key).hexdigest()[:12]


def _derive(key: bytes, info: bytes) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    return HKDF(algorithm=hashes.SHA256(), length=32, salt=_KDF_SALT, info=info).derive(key)


def is_encrypted(value: str) -> bool:
    return value.startswith(_PREFIX_V2)


def encrypt_str(plaintext: str, *, aad: str) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    master = _active_key()
    nonce = os.urandom(12)
    ct = AESGCM(_derive(master, _ENC_INFO)).encrypt(nonce, plaintext.encode(), aad.encode())
    return f"{_PREFIX_V2}{_kid_of(master)}:{base64.b64encode(nonce + ct).decode()}"


def decrypt_str(token: str, *, aad: str) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if not token.startswith(_PREFIX_V2):
        raise ValueError("密文缺少 enc: 版本前缀")
    kid, sep, b64 = token[len(_PREFIX_V2) :].partition(":")
    if not sep:
        raise ValueError("v2 密文缺少 kid 段")
    keys_by_kid: dict[str, bytes] = {}
    for k in (_active_key(), _previous_key()):
        if k is not None:
            keys_by_kid[_kid_of(k)] = k
    master = keys_by_kid.get(kid)
    if master is None:
        raise ValueError(f"v2 密文引用未知 kid:{kid}(主密钥已轮换且未挂 PREVIOUS?)")
    blob = base64.b64decode(b64)
    return AESGCM(_derive(master, _ENC_INFO)).decrypt(blob[:12], blob[12:], aad.encode()).decode()


def _hmac_hex(mac_key: bytes, domain_msg: str) -> str:
    """带密钥摘要(HMAC-SHA256, hex);调用方以固定前缀做域分离。"""
    return hmac.new(mac_key, domain_msg.encode(), hashlib.sha256).hexdigest()


def _mac_candidates() -> list[bytes]:
    """摘要用钥读候选:当前世代在前,轮换窗口内追加 previous 派生;按值去重。"""
    out: list[bytes] = []
    for master in dict.fromkeys(k for k in (_active_key(), _previous_key()) if k is not None):
        mac_key = _derive(master, _MAC_INFO)
        if mac_key not in out:
            out.append(mac_key)
    return out


def _hmac_candidates(domain_msg: str) -> list[str]:
    """读路径候选摘要(写路径只用 [0] = 当前世代)。"""
    return [_hmac_hex(k, domain_msg) for k in _mac_candidates()]


def hash_sms_code(phone: str, purpose: str, code: str) -> str:
    """短信验证码的带密钥摘要(写路径);phone 与 purpose 混进消息做域分离。"""
    return _hmac_candidates(f"smscode|{phone}|{purpose}|{code}")[0]


def hash_sms_code_candidates(phone: str, purpose: str, code: str) -> list[str]:
    """短信验证码的读路径候选(轮换窗口内兼读旧钥匙世代)。"""
    return _hmac_candidates(f"smscode|{phone}|{purpose}|{code}")


def hash_api_key(key: str) -> str:
    """服务端点 API Key 的带密钥摘要(写路径;域分离前缀 service-api-key|)。"""
    return _hmac_candidates(f"service-api-key|{key}")[0]


def hash_api_key_candidates(key: str) -> list[str]:
    """API Key 的读路径候选(轮换窗口内兼读旧世代)。"""
    return _hmac_candidates(f"service-api-key|{key}")


def hash_node_token(token: str) -> str:
    """节点注册/进度令牌的带密钥摘要(写路径;域分离前缀 node-enroll|)。"""
    return _hmac_candidates(f"node-enroll|{token}")[0]


def hash_node_token_candidates(token: str) -> list[str]:
    """节点令牌的读路径候选(轮换窗口内兼读旧钥匙世代)。"""
    return _hmac_candidates(f"node-enroll|{token}")
