"""HTTP 层小工具:幂等重放响应约定、直连对端 IP、Bearer 常量时间比较。"""

import secrets

from fastapi import Request, Response, status

IDEMPOTENT_REPLAY_HEADER = "X-Idempotent-Replay"


def mark_idempotent_replay(response: Response) -> None:
    """把注入的 Response 改为重放形态:200 + 区分头(覆盖装饰器声明的 201/202)。"""
    response.status_code = status.HTTP_200_OK
    response.headers[IDEMPOTENT_REPLAY_HEADER] = "true"


def client_ip(request: Request) -> str | None:
    """客户端 IP(限流键 / 审计 / 合规存证)。取 scope["client"]:uvicorn 的 ProxyHeadersMiddleware
    已按 FORWARDED_ALLOW_IPS 自右向左跳过可信代理改写它;每一跳前置代理 / CDN 回源地址都必须列入
    该网段,否则全部公网请求坍缩成同一个 IP(deploy/app/k8s/00-namespace-config.yaml)。"""
    return request.client.host if request.client else None


def bearer_matches(authorization: str | None, token: str) -> bool:
    """Authorization 头与期望 Bearer token 的常量时间比较(先 encode 成 bytes)。"""
    return secrets.compare_digest((authorization or "").encode(), f"Bearer {token}".encode())
