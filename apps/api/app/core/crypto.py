"""Encryption of sensitive settings (AES-256-GCM) and keyed digests (HMAC-SHA256).

The master key comes only from env (SUPERDL_CONFIG_ENCRYPTION_KEY, urlsafe-base64, 32 bytes); when
unset in dev/test it is derived from jwt_secret; during rotation the old key is mounted as
SUPERDL_CONFIG_ENCRYPTION_KEY_PREVIOUS (read-only), see deploy/cluster/runbooks/key-rotation.md.
Ciphertext `enc:v2:<kid>:<b64(nonce+ct)>`, kid = master key fingerprint (first 12 hex of SHA-256),
unknown kids are rejected; the AAD is bound to the setting key. Digest reads try the candidates
(current generation first, then the previous-derived one); writes use the current generation only.
"""

import base64
import hashlib
import hmac
import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.core.config import decode_master_key, get_settings

_PREFIX_V2 = "enc:v2:"

_ENC_INFO = b"superdl/enc/v2"
_MAC_INFO = b"superdl/mac/v2"
_KDF_SALT = b"superdl-crypto"


def _active_key() -> bytes:
    settings = get_settings()
    raw = settings.config_encryption_key
    if not raw:
        return hashlib.sha256(f"{settings.jwt_secret}:platform-config".encode()).digest()
    return decode_master_key(raw, label="SUPERDL_CONFIG_ENCRYPTION_KEY")


def _previous_key() -> bytes | None:
    """Old master key within the rotation window (read-only): None when unset."""
    raw = get_settings().config_encryption_key_previous
    if not raw:
        return None
    return decode_master_key(raw, label="SUPERDL_CONFIG_ENCRYPTION_KEY_PREVIOUS")


def _kid_of(key: bytes) -> str:
    """Key fingerprint (12 hex)."""
    return hashlib.sha256(key).hexdigest()[:12]


def _derive(key: bytes, info: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=_KDF_SALT, info=info).derive(key)


def encrypt_str(plaintext: str, *, aad: str) -> str:
    master = _active_key()
    nonce = os.urandom(12)
    ct = AESGCM(_derive(master, _ENC_INFO)).encrypt(nonce, plaintext.encode(), aad.encode())
    return f"{_PREFIX_V2}{_kid_of(master)}:{base64.b64encode(nonce + ct).decode()}"


def is_encrypted(value: str) -> bool:
    """Whether value has the `enc:v2:<kid>:<b64>` shape (12-hex kid, non-empty b64); no key or
    integrity check."""
    if not value.startswith(_PREFIX_V2):
        return False
    kid, sep, b64 = value[len(_PREFIX_V2) :].partition(":")
    if not sep or len(kid) != 12 or not b64:
        return False
    return all(c in "0123456789abcdef" for c in kid)


def decrypt_str(token: str, *, aad: str) -> str:
    if not token.startswith(_PREFIX_V2):
        raise ValueError("ciphertext lacks the enc: version prefix")
    kid, sep, b64 = token[len(_PREFIX_V2) :].partition(":")
    if not sep:
        raise ValueError("v2 ciphertext lacks the kid segment")
    keys_by_kid: dict[str, bytes] = {}
    for k in (_active_key(), _previous_key()):
        if k is not None:
            keys_by_kid[_kid_of(k)] = k
    master = keys_by_kid.get(kid)
    if master is None:
        raise ValueError(
            f"v2 ciphertext references an unknown kid: {kid} (master key rotated without PREVIOUS?)"
        )
    blob = base64.b64decode(b64)
    return AESGCM(_derive(master, _ENC_INFO)).decrypt(blob[:12], blob[12:], aad.encode()).decode()


def _hmac_hex(mac_key: bytes, domain_msg: str) -> str:
    """Keyed digest (HMAC-SHA256, hex); callers separate domains with a fixed prefix."""
    return hmac.new(mac_key, domain_msg.encode(), hashlib.sha256).hexdigest()


def _mac_candidates() -> list[bytes]:
    """Master keys deduplicated in current, previous order, derived into digest sub-keys."""
    masters = dict.fromkeys(k for k in (_active_key(), _previous_key()) if k is not None)
    return [_derive(master, _MAC_INFO) for master in masters]


def _hmac_candidates(domain_msg: str) -> list[str]:
    """Candidate digests for the read path (the write path uses only [0] = current generation)."""
    return [_hmac_hex(k, domain_msg) for k in _mac_candidates()]


def hash_verification_code(channel: str, target: str, purpose: str, code: str) -> str:
    """Keyed digest of a verification code (write path); channel, target and purpose separate
    the domains."""
    return hash_verification_code_candidates(channel, target, purpose, code)[0]


def hash_verification_code_candidates(
    channel: str, target: str, purpose: str, code: str
) -> list[str]:
    """Read-path candidates (the previous key generation is accepted inside the rotation window)."""
    return _hmac_candidates(f"vcode|{channel}|{target}|{purpose}|{code}")


def hash_api_key(key: str) -> str:
    """Keyed digest of a service endpoint API key (write path; domain prefix service-api-key|)."""
    return hash_api_key_candidates(key)[0]


def hash_api_key_candidates(key: str) -> list[str]:
    return _hmac_candidates(f"service-api-key|{key}")


def hash_kyc_identity(identity: str) -> str:
    """Keyed digest of a KYC identity number (write path). The `id-number|` domain prefix is kept
    so digests stored before the KYC rename still match; used only for cross-account dedup, the
    plaintext is never stored."""
    return hash_kyc_identity_candidates(identity)[0]


def hash_kyc_identity_candidates(identity: str) -> list[str]:
    return _hmac_candidates(f"id-number|{identity.strip().upper()}")


def hash_node_token(token: str) -> str:
    """Keyed digest of a node enrollment / progress token (write path; domain prefix
    node-enroll|)."""
    return hash_node_token_candidates(token)[0]


def hash_node_token_candidates(token: str) -> list[str]:
    return _hmac_candidates(f"node-enroll|{token}")
