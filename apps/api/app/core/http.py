"""创建类幂等端点的重放响应约定:重放一律 200 + X-Idempotent-Replay 头(而非 201/202)。"""

from fastapi import Request, Response, status

IDEMPOTENT_REPLAY_HEADER = "X-Idempotent-Replay"


def mark_idempotent_replay(response: Response) -> None:
    """把注入的 Response 改为重放形态:200 + 区分头(覆盖装饰器声明的 201/202)。"""
    response.status_code = status.HTTP_200_OK
    response.headers[IDEMPOTENT_REPLAY_HEADER] = "true"


def client_ip(request: Request) -> str | None:
    """直连对端 IP(单一定义点;用于限流键/合规存证,信任边界为直连,不读 X-Forwarded-For)。"""
    return request.client.host if request.client else None
