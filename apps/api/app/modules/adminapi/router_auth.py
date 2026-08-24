"""管理端路由(认证/MFA/自助改密,自 router.py 拆分)。"""

from fastapi import APIRouter, Request, Response, status

from app.core.audit import set_audit_target
from app.core.db import DbSession
from app.core.http import client_ip
from app.modules.adminapi import service
from app.modules.adminapi.deps import CurrentAdmin, require_roles
from app.modules.adminapi.schemas import (
    AdminAccountOut,
    AdminLoginOut,
    AdminLoginRequest,
    AdminOut,
    AdminRefreshOut,
    AdminRefreshRequest,
    AdminSelfPasswordRequest,
    MfaCodeRequest,
    MfaLoginOut,
    MfaResetRequest,
    MfaSetupConfirmOut,
    MfaSetupOut,
    MfaTicketRequest,
    RecoveryCodesOut,
)

router = APIRouter(tags=["admin"])


@router.post("/auth/login")
async def admin_login(
    body: AdminLoginRequest, session: DbSession, request: Request
) -> AdminLoginOut:
    result, admin = await service.login(
        session, body.username, body.password, client_ip=client_ip(request)
    )
    set_audit_target(request, f"admin:{admin.id}")
    return result


@router.post("/auth/mfa/setup/begin")
async def mfa_setup_begin(body: MfaTicketRequest, session: DbSession) -> MfaSetupOut:
    """首次绑定:凭绑定票换 TOTP 密钥与 otpauth URI(前端渲染二维码)。"""
    secret, uri = await service.begin_totp_setup(session, body.ticket)
    return MfaSetupOut(secret=secret, otpauth_uri=uri)


@router.post("/auth/mfa/setup/confirm")
async def mfa_setup_confirm(
    body: MfaCodeRequest, session: DbSession, request: Request
) -> MfaSetupConfirmOut:
    """校验首个动态码完成绑定;恢复码仅此一次返回。"""
    token, admin, codes = await service.confirm_totp_setup(session, body.ticket, body.code)
    set_audit_target(request, f"admin:{admin.id}", detail={"action": "mfa_bind"})
    return MfaSetupConfirmOut(
        access_token=token, admin=AdminOut.model_validate(admin), recovery_codes=codes
    )


@router.post("/auth/login/mfa")
async def mfa_login_verify(
    body: MfaCodeRequest, session: DbSession, request: Request
) -> MfaLoginOut:
    """二要素验证(6 位动态码或恢复码);恢复码用后作废,剩余 ≤2 提示重新生成。"""
    token, admin, left = await service.verify_mfa_login(session, body.ticket, body.code)
    set_audit_target(
        request,
        f"admin:{admin.id}",
        detail={"action": "mfa_recovery_used" if left is not None else "mfa_success"},
    )
    return MfaLoginOut(
        access_token=token, admin=AdminOut.model_validate(admin), recovery_codes_left=left
    )


@router.post("/me/mfa/recovery-codes")
async def mfa_regenerate_recovery_codes(
    admin: CurrentAdmin, session: DbSession, request: Request
) -> RecoveryCodesOut:
    """重新生成恢复码(旧的全作废)。明文仅此一次返回。"""
    codes = await service.regenerate_recovery_codes(session, admin)
    set_audit_target(request, f"admin:{admin.id}", detail={"action": "mfa_recovery_regenerated"})
    return RecoveryCodesOut(recovery_codes=codes)


@router.post("/admins/{admin_id}/mfa/reset", dependencies=[require_roles()])
async def mfa_reset(
    admin_id: int, body: MfaResetRequest, admin: CurrentAdmin, session: DbSession, request: Request
) -> AdminAccountOut:
    """超管为他人重置 TOTP(锁死救援):清空绑定并踢掉全部会话,下次登录重新强制绑定。"""
    target = await service.reset_totp(session, admin, admin_id)
    set_audit_target(
        request, f"admin:{target.id}", detail={"action": "mfa_reset", "reason": body.reason}
    )
    return AdminAccountOut.model_validate(target)


@router.post("/auth/refresh")
async def admin_refresh(body: AdminRefreshRequest, session: DbSession) -> AdminRefreshOut:
    """静默续期:有效或刚过期(15 分钟宽限)的 access token 换新;
    自首次签发(iat)起 12 小时绝对会话上限,到点须重新登录。高频自动调用,不落审计。"""
    token = await service.renew_access_token(session, body.access_token)
    return AdminRefreshOut(access_token=token)


@router.get("/me")
async def admin_me(admin: CurrentAdmin) -> AdminOut:
    return AdminOut.model_validate(admin)


@router.post("/me/password", status_code=status.HTTP_204_NO_CONTENT)
async def admin_change_own_password(
    body: AdminSelfPasswordRequest, admin: CurrentAdmin, session: DbSession, request: Request
) -> Response:
    """自助改密。成功即 token_version+1,踢掉全部在外会话。"""
    await service.change_own_password(session, admin.id, body.current_password, body.new_password)
    set_audit_target(request, f"admin:{admin.id}", detail={"action": "self_password_change"})
    return Response(status_code=status.HTTP_204_NO_CONTENT)
