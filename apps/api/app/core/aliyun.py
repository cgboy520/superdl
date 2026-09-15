"""阿里云 RPC 风格 OpenAPI:HMAC-SHA1 签名与 POST 调用(dysmsapi / captcha / cloudauth 共用)。"""

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
    """业务参数补齐公共参数并计算 Signature(POST form)。"""
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
    """签名并 POST form 到 endpoint,返回 JSON 体;请求层异常转 error_cls,业务结果码由渠道判定。"""
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
