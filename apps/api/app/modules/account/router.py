from fastapi import APIRouter, Request, Response, status

from app.core.audit import set_audit_target
from app.core.db import DbSession
from app.modules.account import service
from app.modules.account.deps import CurrentUser
from app.modules.account.schemas import (
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    SmsCodeRequest,
    SshKeyCreate,
    SshKeyOut,
    TokenPair,
    UserOut,
    WarnThresholdUpdate,
)

router = APIRouter(tags=["account"])


@router.post("/auth/sms-code", status_code=status.HTTP_204_NO_CONTENT)
async def send_sms_code(body: SmsCodeRequest, session: DbSession) -> Response:
    await service.send_sms_code(session, body.phone, body.purpose)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/auth/register", status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, session: DbSession, request: Request) -> TokenPair:
    pair = await service.register(session, body.phone, body.sms_code, body.password)
    set_audit_target(request, f"user:{pair.user.id}")
    return pair


@router.post("/auth/login")
async def login(body: LoginRequest, session: DbSession, request: Request) -> TokenPair:
    client_ip = request.client.host if request.client else None
    pair = await service.login(
        session, body.phone, body.sms_code, body.password, client_ip=client_ip
    )
    set_audit_target(request, f"user:{pair.user.id}")
    return pair


@router.post("/auth/refresh")
async def refresh(body: RefreshRequest, session: DbSession) -> TokenPair:
    return await service.refresh_tokens(session, body.refresh_token)


@router.get("/me")
async def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)


@router.patch("/me/warn-threshold")
async def set_warn_threshold(
    body: WarnThresholdUpdate, user: CurrentUser, session: DbSession
) -> UserOut:
    updated = await service.set_warn_threshold(session, user, body.low_balance_warn_hours)
    return UserOut.model_validate(updated)


@router.get("/ssh-keys")
async def list_ssh_keys(user: CurrentUser, session: DbSession) -> list[SshKeyOut]:
    keys = await service.list_ssh_keys(session, user.id)
    return [SshKeyOut.model_validate(k) for k in keys]


@router.post("/ssh-keys", status_code=status.HTTP_201_CREATED)
async def add_ssh_key(
    body: SshKeyCreate, user: CurrentUser, session: DbSession, request: Request
) -> SshKeyOut:
    key = await service.add_ssh_key(session, user.id, body.name, body.public_key)
    set_audit_target(request, f"ssh_key:{key.id}")
    return SshKeyOut.model_validate(key)


@router.delete("/ssh-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_ssh_key(
    key_id: int, user: CurrentUser, session: DbSession, request: Request
) -> Response:
    await service.delete_ssh_key(session, user.id, key_id)
    set_audit_target(request, f"ssh_key:{key_id}")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
