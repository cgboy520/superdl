"""敏感配置落库加密(AES-256-GCM)。

主密钥只走 env(SUPERDL_CONFIG_ENCRYPTION_KEY,urlsafe-base64 编码的 32 字节),
不落库、不进 DB 备份;dev/test 未配置时从 jwt_secret 派生,本地零配置可用,
prod 由 Settings fail-fast 强制显式配置。密文格式 `enc:v1:<b64(nonce+ct)>`,
AAD 绑定配置键名,防止密文在字段间搬运复用。
"""

import base64
import hashlib
import os

from app.core.config import get_settings

_PREFIX = "enc:v1:"


def _master_key() -> bytes:
    settings = get_settings()
    raw = settings.config_encryption_key
    if not raw:
        # dev/test 兜底派生;prod 下 Settings 校验已拒绝缺省
        return hashlib.sha256(f"{settings.jwt_secret}:platform-config".encode()).digest()
    try:
        key = base64.urlsafe_b64decode(raw)
    except Exception as exc:
        raise ValueError("SUPERDL_CONFIG_ENCRYPTION_KEY 不是合法 urlsafe-base64") from exc
    if len(key) != 32:
        raise ValueError("SUPERDL_CONFIG_ENCRYPTION_KEY 解码后须为 32 字节")
    return key


def is_encrypted(value: str) -> bool:
    return value.startswith(_PREFIX)


def encrypt_str(plaintext: str, *, aad: str) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = os.urandom(12)
    ct = AESGCM(_master_key()).encrypt(nonce, plaintext.encode(), aad.encode())
    return _PREFIX + base64.b64encode(nonce + ct).decode()


def decrypt_str(token: str, *, aad: str) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if not is_encrypted(token):
        raise ValueError("密文缺少 enc:v1: 前缀")
    blob = base64.b64decode(token[len(_PREFIX) :])
    return AESGCM(_master_key()).decrypt(blob[:12], blob[12:], aad.encode()).decode()
