import json
from typing import Any

from fastapi import APIRouter, Header, Request, Response, status

from app.core.config import get_settings
from app.core.db import DbSession
from app.core.errors import AppError, ErrorCode, unauthorized
from app.core.http import bearer_matches, client_ip as http_client_ip
from app.core.pagination import Page
from app.core.params import Cursor, Limit
from app.core.ratelimit import check_rate_limit
from app.modules.account.deps import CurrentUser
from app.modules.notify import service
from app.modules.notify.schemas import NotificationOut, UnreadCountOut

router = APIRouter(tags=["notify"])

ALERT_RATE_LIMIT = 120
ALERT_RATE_WINDOW = 60.0
ALERT_MAX_BODY_BYTES = 1024 * 1024
ALERT_MAX_ALERTS = 500
ALERT_MAX_STRING_LEN = 1024


@router.get("/notifications")
async def list_notifications(
    user: CurrentUser,
    session: DbSession,
    unread: bool = False,
    cursor: str | None = Cursor,
    limit: int | None = Limit,
) -> Page[NotificationOut]:
    """In-app notifications: descending (newest first) cursor pagination."""
    return await service.list_notifications(
        session, user.id, unread_only=unread, cursor=cursor, limit=limit
    )


@router.get("/notifications/unread-count")
async def unread_count(user: CurrentUser, session: DbSession) -> UnreadCountOut:
    """Lightweight unread count (top-bar badge polling)."""
    return UnreadCountOut(unread_count=await service.unread_count(session, user.id))


@router.post("/notifications/read-all", status_code=status.HTTP_204_NO_CONTENT)
async def mark_all_read(user: CurrentUser, session: DbSession) -> Response:
    """Mark all read (idempotent); must be registered before {notification_id}."""
    await service.mark_all_read(session, user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/notifications/{notification_id}/read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_read(notification_id: int, user: CurrentUser, session: DbSession) -> Response:
    await service.mark_read(session, user.id, notification_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _truncate_strings(value: Any) -> Any:
    """Recursively truncate the string values of the payload."""
    if isinstance(value, str):
        return value[:ALERT_MAX_STRING_LEN]
    if isinstance(value, dict):
        return {k: _truncate_strings(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_truncate_strings(v) for v in value]
    return value


@router.post("/webhooks/alertmanager")
async def alertmanager_webhook(
    request: Request,
    session: DbSession,
    authorization: str | None = Header(default=None),
) -> dict[str, int]:
    """Alertmanager alert ingestion: SUPERDL_ALERTMANAGER_TOKEN must be configured and sent as a
    Bearer token."""
    client_ip = http_client_ip(request)
    await check_rate_limit(
        f"am-webhook:{client_ip or '-'}",
        max_attempts=ALERT_RATE_LIMIT,
        window_seconds=ALERT_RATE_WINDOW,
    )
    token = get_settings().alertmanager_token
    if not token:
        raise unauthorized(
            "SUPERDL_ALERTMANAGER_TOKEN must be configured before alerts can be ingested"
        )
    if not bearer_matches(authorization, token):
        raise unauthorized("invalid alert token")
    body = await request.body()
    if len(body) > ALERT_MAX_BODY_BYTES:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            f"alert payload too large (cap {ALERT_MAX_BODY_BYTES // 1024} KiB)",
            http_status=status.HTTP_413_CONTENT_TOO_LARGE,
        )
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise AppError(ErrorCode.VALIDATION_ERROR, "alert payload is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise AppError(ErrorCode.VALIDATION_ERROR, "alert payload must be a JSON object")
    payload = _truncate_strings(payload)
    alerts = payload.get("alerts")
    if isinstance(alerts, list):
        payload["alerts"] = alerts[:ALERT_MAX_ALERTS]
    written = await service.ingest_alertmanager(session, payload)
    return {"ingested": written}
