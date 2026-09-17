from typing import Literal

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel

from app.core.audit import set_audit_target
from app.core.config import get_settings
from app.core.db import DbSession
from app.core.errors import AppError, ErrorCode
from app.core.handles import mask_handle, parse_handle
from app.core.http import client_ip
from app.core.locale import negotiate_locale
from app.core.platform_config import get_runtime_config
from app.modules.account import deletion, service, sshkeys
from app.modules.account.deps import CurrentUser
from app.modules.account.schemas import (
    DeletionRequestCreate,
    DeletionRequestOut,
    HandleCodeRequest,
    HandleConfirmRequest,
    KycSubmitRequest,
    LoginRequest,
    PasswordResetRequest,
    RegisterRequest,
    SshKeyCreate,
    SshKeyOut,
    TokenPair,
    TokenPairOut,
    UserOut,
    VerificationCodeRequest,
    WarnThresholdUpdate,
)

router = APIRouter(tags=["account"])

_REFRESH_COOKIE_PROD = "__Host-superdl_refresh"
_REFRESH_COOKIE_DEV = "superdl_refresh"


def _refresh_cookie_name() -> str:
    return _REFRESH_COOKIE_PROD if get_settings().environment == "prod" else _REFRESH_COOKIE_DEV


def _set_refresh_cookie(response: Response, refresh_token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        _refresh_cookie_name(),
        refresh_token,
        max_age=settings.refresh_token_ttl_seconds,
        path="/",
        httponly=True,
        secure=settings.environment == "prod",
        samesite="strict",
    )


def _refresh_token_from(request: Request) -> str:
    """Read the refresh token from the cookie, requiring X-Requested-With: fetch; missing cookie →
    422, wrong header → 403."""
    cookie_token = request.cookies.get(_refresh_cookie_name())
    if cookie_token is not None:
        if request.headers.get("x-requested-with") != "fetch":
            raise AppError(ErrorCode.FORBIDDEN, key="common.forbidden", http_status=403)
        return cookie_token
    raise AppError(ErrorCode.VALIDATION_ERROR, key="common.validation", http_status=422)


def _token_pair_out(pair: TokenPair) -> TokenPairOut:
    return TokenPairOut(access_token=pair.access_token, user=pair.user)


def _locale_of(request: Request):
    return negotiate_locale(request.headers.get("accept-language"))


@router.post("/auth/verification-code", status_code=status.HTTP_204_NO_CONTENT)
async def send_verification_code(
    body: VerificationCodeRequest, session: DbSession, request: Request
) -> Response:
    """Send a sign-up / sign-in / reset code to an email address or E.164 phone number."""
    await service.send_verification_code(
        session,
        parse_handle(body.handle),
        body.purpose,
        client_ip=client_ip(request),
        captcha_token=body.captcha_token,
        locale=_locale_of(request),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class CaptchaConfigOut(BaseModel):
    """Public CAPTCHA bootstrap: which SDK the web app loads and its public keys. enabled=false →
    no SDK is loaded and codes are requested without a token."""

    enabled: bool
    provider: Literal["aliyun", "turnstile"]
    site_key: str | None
    scene_id: str | None
    prefix: str | None


@router.get("/auth/captcha-config")
async def captcha_config(session: DbSession) -> CaptchaConfigOut:
    """CAPTCHA client bootstrap (unauthenticated)."""
    cfg = await get_runtime_config(session)
    provider: Literal["aliyun", "turnstile"] = (
        "aliyun" if cfg.captcha_provider == "aliyun" else "turnstile"
    )
    return CaptchaConfigOut(
        enabled=cfg.captcha_enabled,
        provider=provider,
        site_key=cfg.captcha_turnstile_site_key or None,
        scene_id=cfg.captcha_scene_id or None,
        prefix=cfg.captcha_prefix or None,
    )


def _mark_credential_attempt(request: Request, handle: str, action: str) -> None:
    set_audit_target(request, f"handle:{mask_handle(handle)}", detail={"action": action})


@router.post("/auth/register", status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest, session: DbSession, request: Request, response: Response
) -> TokenPairOut:
    _mark_credential_attempt(request, body.email, "register")
    pair = await service.register(
        session,
        email=body.email,
        email_code=body.email_code,
        password=body.password,
        phone=body.phone,
        phone_code=body.phone_code,
        accept_terms=body.accept_terms,
        client_ip=client_ip(request),
    )
    set_audit_target(request, f"user:{pair.user.id}", detail={"action": "register"})
    _set_refresh_cookie(response, pair.refresh_token)
    return _token_pair_out(pair)


@router.post("/auth/login")
async def login(
    body: LoginRequest, session: DbSession, request: Request, response: Response
) -> TokenPairOut:
    _mark_credential_attempt(request, body.handle, "login")
    pair = await service.login(
        session, parse_handle(body.handle), body.code, body.password, client_ip=client_ip(request)
    )
    set_audit_target(request, f"user:{pair.user.id}", detail={"action": "login"})
    _set_refresh_cookie(response, pair.refresh_token)
    return _token_pair_out(pair)


@router.post("/auth/password/reset")
async def reset_password(
    body: PasswordResetRequest, session: DbSession, request: Request, response: Response
) -> TokenPairOut:
    """Set / change / recover the password with a verification code; every other session is
    revoked and a fresh token pair is issued."""
    _mark_credential_attempt(request, body.handle, "password_reset")
    pair = await service.reset_password(
        session,
        parse_handle(body.handle),
        body.code,
        body.new_password,
        client_ip=client_ip(request),
    )
    set_audit_target(request, f"user:{pair.user.id}", detail={"action": "password_reset"})
    _set_refresh_cookie(response, pair.refresh_token)
    return _token_pair_out(pair)


@router.post("/auth/refresh")
async def refresh(session: DbSession, request: Request, response: Response) -> TokenPairOut:
    """Rotating refresh: the refresh token travels only in the HttpOnly cookie + X-Requested-With
    header; success writes the new cookie."""
    pair = await service.refresh_tokens(session, _refresh_token_from(request))
    _set_refresh_cookie(response, pair.refresh_token)
    return _token_pair_out(pair)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(session: DbSession, request: Request) -> Response:
    """Log out the current session (consume the refresh token + clear the cookie). 204 even for an
    invalid token."""
    await service.logout(session, _refresh_token_from(request))
    resp = Response(status_code=status.HTTP_204_NO_CONTENT)
    resp.delete_cookie(_refresh_cookie_name(), path="/")
    return resp


@router.post("/auth/logout-all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_all(user: CurrentUser, session: DbSession, request: Request) -> Response:
    """Log out everywhere: token_version+1, every issued access/refresh token is invalid at once."""
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


@router.post("/me/handles/code", status_code=status.HTTP_204_NO_CONTENT)
async def request_handle_code(
    body: HandleCodeRequest, user: CurrentUser, session: DbSession, request: Request
) -> Response:
    """Send a code to a new email / phone before binding it (no CAPTCHA: caller is signed in)."""
    await service.request_handle_code(
        session,
        user,
        parse_handle(body.handle),
        client_ip=client_ip(request),
        locale=_locale_of(request),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/me/handles/confirm")
async def confirm_handle(
    body: HandleConfirmRequest, user: CurrentUser, session: DbSession, request: Request
) -> UserOut:
    """Bind or replace the email / phone after the code check; a handle bound to another account
    is refused (409)."""
    handle = parse_handle(body.handle)
    updated = await service.confirm_handle(session, user, handle, body.code)
    set_audit_target(
        request, f"user:{user.id}", detail={"action": "handle_bound", "kind": handle.kind}
    )
    return UserOut.model_validate(updated)


@router.delete("/me/handles/phone")
async def remove_phone(user: CurrentUser, session: DbSession, request: Request) -> UserOut:
    """Unbind the phone; 409 when the compliance profile requires one or no email is bound."""
    updated = await service.remove_phone(session, user)
    set_audit_target(request, f"user:{user.id}", detail={"action": "phone_removed"})
    return UserOut.model_validate(updated)


@router.post("/me/kyc")
async def submit_kyc(
    body: KycSubmitRequest, user: CurrentUser, session: DbSession, request: Request
) -> UserOut:
    """Identity verification through the configured provider; the identity number is stored
    masked."""
    updated = await service.submit_kyc(session, user, body.full_name, body.identity_number)
    set_audit_target(request, f"user:{user.id}", detail={"action": "kyc_verified"})
    return UserOut.model_validate(updated)


@router.post("/me/deletion-request", status_code=status.HTTP_201_CREATED)
async def create_deletion_request(
    body: DeletionRequestCreate, user: CurrentUser, session: DbSession, request: Request
) -> DeletionRequestOut:
    """Request deletion (7-day cooling-off); one of the account's handles must be retyped.
    An existing pending request is returned unchanged (idempotent)."""
    req = await deletion.request_deletion(session, user, handle=body.handle, reason=body.reason)
    set_audit_target(request, f"user:{user.id}", detail={"action": "account_deletion_request"})
    return DeletionRequestOut.model_validate(req)


@router.get("/me/deletion-request")
async def get_deletion_request(user: CurrentUser, session: DbSession) -> DeletionRequestOut | None:
    """The current pending request; otherwise the most recent one; null when never requested."""
    req = await deletion.get_my_deletion_request(session, user.id)
    return DeletionRequestOut.model_validate(req) if req is not None else None


@router.post("/me/deletion-request/cancel")
async def cancel_deletion_request(
    user: CurrentUser, session: DbSession, request: Request
) -> DeletionRequestOut:
    """Cancel the deletion request within the cooling-off period (pending only)."""
    req = await deletion.cancel_deletion_request(session, user.id)
    set_audit_target(request, f"user:{user.id}", detail={"action": "account_deletion_cancel"})
    return DeletionRequestOut.model_validate(req)


@router.get("/ssh-keys")
async def list_ssh_keys(user: CurrentUser, session: DbSession) -> list[SshKeyOut]:
    keys = await sshkeys.list_ssh_keys(session, user.id)
    return [SshKeyOut.model_validate(k) for k in keys]


@router.post("/ssh-keys", status_code=status.HTTP_201_CREATED)
async def add_ssh_key(
    body: SshKeyCreate, user: CurrentUser, session: DbSession, request: Request
) -> SshKeyOut:
    key = await sshkeys.add_ssh_key(session, user.id, body.name, body.public_key)
    set_audit_target(request, f"ssh_key:{key.id}")
    return SshKeyOut.model_validate(key)


@router.delete("/ssh-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_ssh_key(
    key_id: int, user: CurrentUser, session: DbSession, request: Request
) -> Response:
    await sshkeys.delete_ssh_key(session, user.id, key_id)
    set_audit_target(request, f"ssh_key:{key_id}")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
