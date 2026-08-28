"""HTTP 层小工具:幂等重放响应约定、直连对端 IP、Bearer 常量时间比较。"""

import secrets

from fastapi import Request, Response, status

IDEMPOTENT_REPLAY_HEADER = "X-Idempotent-Replay"


def mark_idempotent_replay(response: Response) -> None:
    """把注入的 Response 改为重放形态:200 + 区分头(覆盖装饰器声明的 201/202)。"""
    response.status_code = status.HTTP_200_OK
    response.headers[IDEMPOTENT_REPLAY_HEADER] = "true"


def client_ip(request: Request) -> str | None:
    """直连对端 IP(单一定义点;用于限流键/合规存证,信任边界为直连,不读 X-Forwarded-For)。"""
    return request.client.host if request.client else None


def bearer_matches(authorization: str | None, token: str) -> bool:
    """Authorization 头与期望 Bearer token 的常量时间比较(防计时探测出 token 前缀)。

    必须先 encode 成 bytes:compare_digest 的 str 入参遇非 ASCII(畸形头)会抛 TypeError。"""
    return secrets.compare_digest((authorization or "").encode(), f"Bearer {token}".encode())
