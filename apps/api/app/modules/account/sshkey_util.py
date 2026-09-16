"""OpenSSH public key parsing and fingerprints (SHA256, matching `ssh-keygen -lf`)."""

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
    """Validate and normalise the public key. Returns (normalised key, fingerprint). A malformed
    key raises ValueError."""
    parts = text.strip().split()
    if len(parts) < 2:
        raise ValueError("malformed public key: expected '<type> <base64> [comment]'")
    key_type, b64 = parts[0], parts[1]
    comment = " ".join(parts[2:]) if len(parts) > 2 else ""
    if key_type not in ALLOWED_KEY_TYPES:
        raise ValueError(f"unsupported public key type: {key_type}")
    try:
        blob = base64.b64decode(b64, validate=True)
    except binascii.Error as exc:
        raise ValueError("public key base64 decoding failed") from exc
    if len(blob) < 4:
        raise ValueError("invalid public key body")
    (type_len,) = struct.unpack(">I", blob[:4])
    embedded = blob[4 : 4 + type_len].decode(errors="replace")
    if embedded != key_type:
        raise ValueError("public key type does not match its body")
    digest = hashlib.sha256(blob).digest()
    fingerprint = "SHA256:" + base64.b64encode(digest).decode().rstrip("=")
    normalized = f"{key_type} {b64}" + (f" {comment}" if comment else "")
    return normalized, fingerprint
