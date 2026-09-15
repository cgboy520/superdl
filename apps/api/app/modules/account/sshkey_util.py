"""OpenSSH 公钥解析与指纹计算(SHA256,与 `ssh-keygen -lf` 一致)。"""

import base64
import binascii
import hashlib
import struct

ALLOWED_KEY_TYPES = frozenset(
    {
        "ssh-ed25519",
        "ssh-rsa",
        "ecdsa-sha2-nistp256",
        "ecdsa-sha2-nistp384",
        "ecdsa-sha2-nistp521",
    }
)


def parse_public_key(text: str) -> tuple[str, str]:
    """校验并规整公钥。返回 (规整后的公钥, 指纹)。格式非法抛 ValueError。"""
    parts = text.strip().split()
    if len(parts) < 2:
        raise ValueError("公钥格式非法:应为 '<type> <base64> [comment]'")
    key_type, b64 = parts[0], parts[1]
    comment = " ".join(parts[2:]) if len(parts) > 2 else ""
    if key_type not in ALLOWED_KEY_TYPES:
        raise ValueError(f"不支持的公钥类型:{key_type}")
    try:
        blob = base64.b64decode(b64, validate=True)
    except binascii.Error as exc:
        raise ValueError("公钥 base64 解码失败") from exc
    if len(blob) < 4:
        raise ValueError("公钥内容非法")
    (type_len,) = struct.unpack(">I", blob[:4])
    embedded = blob[4 : 4 + type_len].decode(errors="replace")
    if embedded != key_type:
        raise ValueError("公钥类型与内容不一致")
    digest = hashlib.sha256(blob).digest()
    fingerprint = "SHA256:" + base64.b64encode(digest).decode().rstrip("=")
    normalized = f"{key_type} {b64}" + (f" {comment}" if comment else "")
    return normalized, fingerprint
