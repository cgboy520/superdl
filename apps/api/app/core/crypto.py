"""敏感配置落库加密(AES-256-GCM)。

主密钥只走 env(SUPERDL_CONFIG_ENCRYPTION_KEY,urlsafe-base64 的 32 字节),
dev/test 未配置时从 jwt_secret 派生。密文格式 `enc:v1:<b64(nonce+ct)>`,
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
    except ValueError as exc:
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


def hash_sms_code(phone: str, purpose: str, code: str) -> str:
    """短信验证码的**带密钥**摘要(HMAC-SHA256,hex)。

    验证码此前是明文入库,而密码走了 bcrypt:一次只读数据库访问(备份 dump、只读副本、
    DBA 账号、一个 SQL 注入落点)就能 `SELECT phone, code` 拿到当前全部活跃验证码,
    直接登入任意账号,或走改密路径把本人踢下线 —— 从「读到库」一步升级成「成为任何人」。

    必须是**带密钥**的摘要:验证码只有 6 位数字,不加密钥的 sha256 对同一个拿到 dump 的
    攻击者来说是 10^6 次哈希,微秒级就穷举完了,加不加等价;每行加盐也一样(仍是每行
    10^6)。密钥取平台配置主密钥(只走 env、prod 强制配置,不在库里),phone 与 purpose
    混进消息做域分离。
    """
    import hmac

    key = _master_key()
    msg = f"smscode|{phone}|{purpose}|{code}".encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()
