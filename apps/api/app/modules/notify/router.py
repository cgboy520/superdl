from typing import Any

from fastapi import APIRouter, Header, Query, Request, Response, status

from app.core.config import get_settings
from app.core.db import DbSession
from app.core.errors import AppError, ErrorCode, unauthorized
from app.core.http import bearer_matches
from app.core.http import client_ip as http_client_ip
from app.core.pagination import Page
from app.core.ratelimit import check_rate_limit
from app.modules.account.deps import CurrentUser
from app.modules.notify import service
from app.modules.notify.schemas import NotificationOut, UnreadCountOut

router = APIRouter(tags=["notify"])

# 告警接入端点无用户鉴权(Bearer token 即鉴权)且报文驱动写库,故加固:
# IP 限流 + 体积与字段长度上限。
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
    cursor: str | None = None,
    limit: int | None = Query(default=None, le=100),
) -> Page[NotificationOut]:
    """站内信:降序(最新在前)游标分页。"""
    return await service.list_notifications(
        session, user.id, unread_only=unread, cursor=cursor, limit=limit
    )


@router.get("/notifications/unread-count")
async def unread_count(user: CurrentUser, session: DbSession) -> UnreadCountOut:
    """未读数轻端点(顶栏角标轮询):DB count,与列表分页解耦。"""
    return UnreadCountOut(unread_count=await service.unread_count(session, user.id))


@router.post("/notifications/read-all", status_code=status.HTTP_204_NO_CONTENT)
async def mark_all_read(user: CurrentUser, session: DbSession) -> Response:
    """全部已读(幂等)。注意须注册在 {notification_id} 之前,避免 read-all 被当 id 解析。"""
    await service.mark_all_read(session, user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/notifications/{notification_id}/read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_read(notification_id: int, user: CurrentUser, session: DbSession) -> Response:
    await service.mark_read(session, user.id, notification_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _truncate_strings(value: Any) -> Any:
    """递归截断报文里的字符串值:告警 summary/labels 由外部提交,长度不受信。"""
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
    """Alertmanager 告警接入。除 test 环境外必须配置并携带 Bearer token。"""
    import json

    client_ip = http_client_ip(request)
    await check_rate_limit(
        f"am-webhook:{client_ip or '-'}",
        max_attempts=ALERT_RATE_LIMIT,
        window_seconds=ALERT_RATE_WINDOW,
    )
    settings = get_settings()
    if settings.alertmanager_token:
        if not bearer_matches(authorization, settings.alertmanager_token):
            raise unauthorized("告警 token 无效")
    elif settings.environment != "test":
        raise unauthorized("必须配置 SUPERDL_ALERTMANAGER_TOKEN 后才能接入告警")
    body = await request.body()
    if len(body) > ALERT_MAX_BODY_BYTES:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            f"告警报文过大(上限 {ALERT_MAX_BODY_BYTES // 1024} KiB)",
            http_status=status.HTTP_413_CONTENT_TOO_LARGE,
        )
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise AppError(ErrorCode.VALIDATION_ERROR, "告警报文不是合法 JSON") from exc
    if not isinstance(payload, dict):
        raise AppError(ErrorCode.VALIDATION_ERROR, "告警报文须为 JSON 对象")
    payload = _truncate_strings(payload)
    alerts = payload.get("alerts")
    if isinstance(alerts, list):
        payload["alerts"] = alerts[:ALERT_MAX_ALERTS]
    written = await service.ingest_alertmanager(session, payload)
    return {"ingested": written}
