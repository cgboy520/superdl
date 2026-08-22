from fastapi import APIRouter, Request, Response, status

from app.core.audit import set_audit_target
from app.core.db import DbSession
from app.modules.account import service
from app.modules.account.deps import CurrentUser
from app.modules.account.schemas import (
    LoginRequest,
    PasswordResetRequest,
    RealNameRequest,
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


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.post("/auth/sms-code", status_code=status.HTTP_204_NO_CONTENT)
async def send_sms_code(body: SmsCodeRequest, session: DbSession, request: Request) -> Response:
    await service.send_sms_code(session, body.phone, body.purpose, client_ip=_client_ip(request))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/auth/register", status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, session: DbSession, request: Request) -> TokenPair:
    pair = await service.register(
        session,
        body.phone,
        body.sms_code,
        body.password,
        accept_terms=body.accept_terms,
        client_ip=_client_ip(request),
    )
    set_audit_target(request, f"user:{pair.user.id}")
    return pair


@router.post("/auth/login")
async def login(body: LoginRequest, session: DbSession, request: Request) -> TokenPair:
    pair = await service.login(
        session, body.phone, body.sms_code, body.password, client_ip=_client_ip(request)
    )
    set_audit_target(request, f"user:{pair.user.id}")
    return pair


@router.post("/auth/password/reset")
async def reset_password(
    body: PasswordResetRequest, session: DbSession, request: Request
) -> TokenPair:
    """设置/修改/找回密码(手机号 + 验证码)。成功即撤销全部在外会话并换发新 token。"""
    pair = await service.reset_password(
        session, body.phone, body.sms_code, body.new_password, client_ip=_client_ip(request)
    )
    set_audit_target(request, f"user:{pair.user.id}", detail={"action": "password_reset"})
    return pair


@router.post("/auth/refresh")
async def refresh(body: RefreshRequest, session: DbSession) -> TokenPair:
    return await service.refresh_tokens(session, body.refresh_token)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(body: RefreshRequest, session: DbSession) -> Response:
    """登出当前会话(refresh token 一次性消费位撤销)。token 无效也回 204,防枚举。"""
    await service.logout(session, body.refresh_token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/auth/logout-all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_all(user: CurrentUser, session: DbSession, request: Request) -> Response:
    """登出全部会话:token_version+1,已签发的 access/refresh 即刻全部失效。"""
    await service.logout_all(session, user.id)
    set_audit_target(request, f"user:{user.id}", detail={"action": "logout_all"})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me")
async def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)


@router.patch("/me/warn-threshold")
async def set_warn_threshold(
    body: WarnThresholdUpdate, user: CurrentUser, session: DbSession
) -> UserOut:
    updated = await service.set_warn_threshold(session, user, body.low_balance_warn_hours)
    return UserOut.model_validate(updated)


@router.post("/me/real-name")
async def submit_real_name(
    body: RealNameRequest, user: CurrentUser, session: DbSession, request: Request
) -> UserOut:
    """实名认证(三要素核验;身份证号仅存脱敏串)。"""
    updated = await service.submit_real_name(session, user, body.name, body.id_number)
    set_audit_target(request, f"user:{user.id}", detail={"action": "real_name_verified"})
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
