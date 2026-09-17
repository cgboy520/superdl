"""Aliyun RPC-style OpenAPI: HMAC-SHA1 signing and POST calls (shared by dysmsapi / captcha /
cloudauth)."""

import base64
import hashlib
import hmac
import urllib.parse
from uuid import uuid4

import httpx

from app.core.timeutil import now_utc


def percent_encode(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def rpc_signed_params(
    params: dict[str, str],
    *,
    access_key_id: str,
    access_key_secret: str,
    nonce: str,
    timestamp: str,
) -> dict[str, str]:
    """Add the common parameters to the business parameters and compute the Signature (POST
    form)."""
    query = {
        **params,
        "AccessKeyId": access_key_id,
        "Format": "JSON",
        "SignatureMethod": "HMAC-SHA1",
        "SignatureNonce": nonce,
        "SignatureVersion": "1.0",
        "Timestamp": timestamp,
    }
    canonical = "&".join(
        f"{percent_encode(k)}={percent_encode(v)}" for k, v in sorted(query.items())
    )
    to_sign = f"POST&{percent_encode('/')}&{percent_encode(canonical)}"
    digest = hmac.new((access_key_secret + "&").encode(), to_sign.encode(), hashlib.sha1).digest()
    return {**query, "Signature": base64.b64encode(digest).decode()}


async def rpc_call(
    endpoint: str,
    params: dict[str, str],
    *,
    access_key_id: str,
    access_key_secret: str,
    transport: httpx.AsyncBaseTransport | None,
    error_cls: type[Exception],
) -> dict:
    """Sign and POST the form to endpoint, return the JSON body; transport errors become error_cls,
    the business result code is judged by the channel."""
    signed = rpc_signed_params(
        params,
        access_key_id=access_key_id,
        access_key_secret=access_key_secret,
        nonce=uuid4().hex,
        timestamp=now_utc().strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    try:
        async with httpx.AsyncClient(timeout=10, transport=transport) as client:
            resp = await client.post(endpoint, data=signed)
        return resp.json()
    except Exception as exc:
        raise error_cls(f"{endpoint} request failed: {exc}") from exc
