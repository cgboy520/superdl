"""阿里云 RPC 风格 OpenAPI 签名(HMAC-SHA1)。dysmsapi(短信)与 cloudauth(实名)共用。

只实现服务端 POST 调用所需最小面;SDK 依赖零增加。
"""

import base64
import hashlib
import hmac
import urllib.parse


def percent_encode(value: str) -> str:
    # 阿里云 RPC 签名要求 RFC3986:仅 A-Za-z0-9-_.~ 不编码,空格 %20,* %2A
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
