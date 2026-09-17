"""HTTP helpers: idempotent replay response convention, direct peer IP, constant-time Bearer
comparison."""

import secrets

from fastapi import Request, Response, status

IDEMPOTENT_REPLAY_HEADER = "X-Idempotent-Replay"


def mark_idempotent_replay(response: Response) -> None:
    """Turn the injected Response into replay form: 200 + marker header (overriding the decorator's
    201/202)."""
    response.status_code = status.HTTP_200_OK
    response.headers[IDEMPOTENT_REPLAY_HEADER] = "true"


def client_ip(request: Request) -> str | None:
    """Client IP (rate-limit keys / audit / consent records). Taken from scope["client"]: uvicorn's
    ProxyHeadersMiddleware has already rewritten it per FORWARDED_ALLOW_IPS, skipping trusted
    proxies right to left; every upstream proxy / CDN origin must be in that range or every public
    request
    collapses onto one IP (deploy/app/k8s/00-namespace-config.yaml)."""
    return request.client.host if request.client else None


def bearer_matches(authorization: str | None, token: str) -> bool:
    """Constant-time comparison of the Authorization header with the expected Bearer token (encoded
    to bytes first)."""
    return secrets.compare_digest((authorization or "").encode(), f"Bearer {token}".encode())
