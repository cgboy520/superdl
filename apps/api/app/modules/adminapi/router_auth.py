"""Admin routes (auth / MFA / self-service password change)."""

from fastapi import APIRouter, Request, Response, status

from app.core.audit import set_audit_target
from app.core.db import DbSession
from app.core.http import client_ip
from app.modules.adminapi import auth_service
from app.modules.adminapi.deps import CurrentAdmin, require_roles
from app.modules.adminapi.schemas import (
    AdminAccountOut,
    AdminLoginRequest,
    AdminLoginTokenOut,
    AdminOut,
    AdminRefreshOut,
    AdminRefreshRequest,
    AdminSelfPasswordRequest,
    MfaChallengeOut,
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
) -> MfaChallengeOut | AdminLoginTokenOut:
    """Password check. With admin_mfa_enabled only a second-factor challenge ticket is returned
    (enrolment ticket for unenrolled, verification ticket for enrolled accounts),
    the access token is issued by /auth/mfa/setup/confirm or /auth/login/mfa; when off,
    access_token is returned directly."""
    result, admin = await auth_service.login(
        session, body.username, body.password, client_ip=client_ip(request)
    )
    if isinstance(result, AdminLoginTokenOut):
        set_audit_target(request, f"admin:{admin.id}", detail={"action": "login_without_mfa"})
    else:
        set_audit_target(request, f"admin:{admin.id}")
    return result


@router.post("/auth/mfa/setup/begin")
async def mfa_setup_begin(body: MfaTicketRequest, session: DbSession) -> MfaSetupOut:
    """First enrolment: exchange the enrolment ticket for the TOTP secret and otpauth URI (the
    frontend renders the QR code)."""
    secret, uri = await auth_service.begin_totp_setup(session, body.ticket)
    return MfaSetupOut(secret=secret, otpauth_uri=uri)


@router.post("/auth/mfa/setup/confirm")
async def mfa_setup_confirm(
    body: MfaCodeRequest, session: DbSession, request: Request
) -> MfaSetupConfirmOut:
    """Verify the first code to complete enrolment; recovery codes are returned this once."""
    token, admin, codes = await auth_service.confirm_totp_setup(session, body.ticket, body.code)
    set_audit_target(request, f"admin:{admin.id}", detail={"action": "mfa_bind"})
    return MfaSetupConfirmOut(
        access_token=token, admin=AdminOut.model_validate(admin), recovery_codes=codes
    )


@router.post("/auth/login/mfa")
async def mfa_login_verify(
    body: MfaCodeRequest, session: DbSession, request: Request
) -> MfaLoginOut:
    """Second-factor verification (6-digit code or recovery code); a recovery code is void after
    use, ≤ 2 left prompts regeneration."""
    token, admin, left = await auth_service.verify_mfa_login(session, body.ticket, body.code)
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
    """Regenerate the recovery codes (all old ones void). Plaintext returned this once."""
    codes = await auth_service.regenerate_recovery_codes(session, admin)
    set_audit_target(request, f"admin:{admin.id}", detail={"action": "mfa_recovery_regenerated"})
    return RecoveryCodesOut(recovery_codes=codes)


@router.post("/admins/{admin_id}/mfa/reset", dependencies=[require_roles()])
async def mfa_reset(
    admin_id: int, body: MfaResetRequest, admin: CurrentAdmin, session: DbSession, request: Request
) -> AdminAccountOut:
    """Admin resets another admin's TOTP: enrolment cleared and every session revoked, re-enrol at
    the next login."""
    target = await auth_service.reset_totp(session, admin, admin_id)
    set_audit_target(
        request, f"admin:{target.id}", detail={"action": "mfa_reset", "reason": body.reason}
    )
    return AdminAccountOut.model_validate(target)


@router.post("/auth/refresh")
async def admin_refresh(body: AdminRefreshRequest, session: DbSession) -> AdminRefreshOut:
    """Silent renewal: a valid or just-expired (15-minute grace) access token is exchanged for a new
    one; 12-hour absolute cap from iat.
    Not audited."""
    token = await auth_service.renew_access_token(session, body.access_token)
    return AdminRefreshOut(access_token=token)


@router.get("/me")
async def admin_me(admin: CurrentAdmin) -> AdminOut:
    return AdminOut.model_validate(admin)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def admin_logout(admin: CurrentAdmin, session: DbSession, request: Request) -> Response:
    """Server-side logout: token_version+1, every outstanding session of the admin becomes
    invalid."""
    await auth_service.logout(session, admin.id)
    set_audit_target(request, f"admin:{admin.id}", detail={"action": "logout"})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/me/password", status_code=status.HTTP_204_NO_CONTENT)
async def admin_change_own_password(
    body: AdminSelfPasswordRequest, admin: CurrentAdmin, session: DbSession, request: Request
) -> Response:
    """Self-service password change. Success bumps token_version+1 and revokes every outstanding
    session."""
    await auth_service.change_own_password(
        session, admin.id, body.current_password, body.new_password
    )
    set_audit_target(request, f"admin:{admin.id}", detail={"action": "self_password_change"})
    return Response(status_code=status.HTTP_204_NO_CONTENT)
