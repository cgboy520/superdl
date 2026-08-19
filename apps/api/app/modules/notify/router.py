from datetime import datetime

from fastapi import APIRouter, Header, Request, Response, status
from pydantic import BaseModel

from app.core.config import get_settings
from app.core.db import DbSession
from app.core.errors import unauthorized
from app.modules.account.deps import CurrentUser
from app.modules.notify import service

router = APIRouter(tags=["notify"])


class NotificationOut(BaseModel):
    id: int
    type: str
    title: str
    content: str
    severity: str
    read_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


@router.get("/notifications")
async def list_notifications(
    user: CurrentUser, session: DbSession, unread: bool = False
) -> list[NotificationOut]:
    rows = await service.list_notifications(session, user.id, unread_only=unread)
    return [NotificationOut.model_validate(r) for r in rows]


@router.post("/notifications/{notification_id}/read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_read(notification_id: int, user: CurrentUser, session: DbSession) -> Response:
    await service.mark_read(session, user.id, notification_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/webhooks/alertmanager")
async def alertmanager_webhook(
    request: Request,
    session: DbSession,
    authorization: str | None = Header(default=None),
) -> dict[str, int]:
    """Alertmanager 告警接入。配置了 token 则必须携带 Bearer;生产未配 token 拒绝。"""
    settings = get_settings()
    if settings.alertmanager_token:
        if authorization != f"Bearer {settings.alertmanager_token}":
            raise unauthorized("告警 token 无效")
    elif settings.environment == "prod":
        raise unauthorized("生产环境必须配置 SUPERDL_ALERTMANAGER_TOKEN")
    payload = await request.json()
    written = await service.ingest_alertmanager(session, payload)
    return {"ingested": written}
