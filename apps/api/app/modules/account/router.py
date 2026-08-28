from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel

from app.core.audit import set_audit_target
from app.core.config import get_settings
from app.core.db import DbSession
from app.core.errors import AppError, ErrorCode
from app.core.http import client_ip
from app.modules.account import service
from app.modules.account.deps import CurrentUser
from app.modules.account.schemas import (
    DeletionRequestCreate,
    DeletionRequestOut,
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

# refresh token 的 HttpOnly Cookie(web SPA 与 API 同源反代,SameSite=Strict 即可):
# 长期凭据移出 JS 可达面(XSS 偷不走),access token 短 TTL 留前端。
# body 仍回 refresh_token:存量客户端的迁移期旁路,其自然淘汰后可从契约收窄
_REFRESH_COOKIE = "superdl_refresh"


def _set_refresh_cookie(response: Response, refresh_token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        _REFRESH_COOKIE,
        refresh_token,
        max_age=settings.refresh_token_ttl_seconds,
        path="/api/v1/auth",
        httponly=True,
        secure=settings.environment == "prod",  # dev/test 是 http
        samesite="strict",
    )


def _refresh_token_from(request: Request, body: RefreshRequest | None) -> str:
    """body 优先(存量客户端:调用方拿到了 token 值,谈不上 CSRF,不要求自定义头);
    cookie 为浏览器新路径——必须带自定义头做双提交纵深(跨站表单与简单跨域请求
    都造不出自定义头;SameSite=Strict 之上的一道)。"""
    if body is not None:
        return body.refresh_token
    cookie_token = request.cookies.get(_REFRESH_COOKIE)
    if cookie_token is not None:
        if request.headers.get("x-requested-with") != "fetch":
            raise AppError(ErrorCode.FORBIDDEN, key="common.forbidden", http_status=403)
        return cookie_token
    raise AppError(ErrorCode.VALIDATION_ERROR, key="common.validation", http_status=422)


@router.post("/auth/sms-code", status_code=status.HTTP_204_NO_CONTENT)
async def send_sms_code(body: SmsCodeRequest, session: DbSession, request: Request) -> Response:
    await service.send_sms_code(
        session,
        body.phone,
        body.purpose,
        client_ip=client_ip(request),
        captcha_token=body.captcha_token,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class CaptchaConfigOut(BaseModel):
    """前端初始化验证码 SDK 所需的公开信息(身份标/场景非密)。enabled=false(安全策略
    captcha_enabled 关闭)时前端不加载 SDK,发码不带 token。"""

    enabled: bool
    scene_id: str | None
    prefix: str | None


@router.get("/auth/captcha-config")
async def captcha_config(session: DbSession) -> CaptchaConfigOut:
    """验证码 2.0 客户端初始化配置(免鉴权;prefix/scene_id 为公开信息)。"""
    from app.core.platform_config import get_effective_platform_config

    cfg = await get_effective_platform_config(session)
    return CaptchaConfigOut(
        enabled=cfg["captcha_enabled"] == "true",
        scene_id=cfg["captcha_scene_id"] or None,
        prefix=cfg["captcha_prefix"] or None,
    )


@router.post("/auth/register", status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest, session: DbSession, request: Request, response: Response
) -> TokenPair:
    pair = await service.register(
        session,
        body.phone,
        body.sms_code,
        body.password,
        accept_terms=body.accept_terms,
        client_ip=client_ip(request),
    )
    set_audit_target(request, f"user:{pair.user.id}")
    _set_refresh_cookie(response, pair.refresh_token)
    return pair


@router.post("/auth/login")
async def login(
    body: LoginRequest, session: DbSession, request: Request, response: Response
) -> TokenPair:
    pair = await service.login(
        session, body.phone, body.sms_code, body.password, client_ip=client_ip(request)
    )
    set_audit_target(request, f"user:{pair.user.id}")
    _set_refresh_cookie(response, pair.refresh_token)
    return pair


@router.post("/auth/password/reset")
async def reset_password(
    body: PasswordResetRequest, session: DbSession, request: Request, response: Response
) -> TokenPair:
    """设置/修改/找回密码(手机号 + 验证码)。成功即撤销全部在外会话并换发新 token。"""
    pair = await service.reset_password(
        session, body.phone, body.sms_code, body.new_password, client_ip=client_ip(request)
    )
    set_audit_target(request, f"user:{pair.user.id}", detail={"action": "password_reset"})
    _set_refresh_cookie(response, pair.refresh_token)
    return pair


@router.post("/auth/refresh")
async def refresh(
    session: DbSession, request: Request, response: Response, body: RefreshRequest | None = None
) -> TokenPair:
    """轮换刷新:refresh 经 HttpOnly Cookie(首选)或 body(存量旁路)提交;
    成功即轮换 Cookie 与 body 双写。"""
    pair = await service.refresh_tokens(session, _refresh_token_from(request, body))
    _set_refresh_cookie(response, pair.refresh_token)
    return pair


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    session: DbSession, request: Request, body: RefreshRequest | None = None
) -> Response:
    """登出当前会话(refresh token 一次性消费位撤销 + 清 Cookie)。token 无效也回 204,防枚举。"""
    await service.logout(session, _refresh_token_from(request, body))
    resp = Response(status_code=status.HTTP_204_NO_CONTENT)
    resp.delete_cookie(_REFRESH_COOKIE, path="/api/v1/auth")
    return resp


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


@router.post("/me/deletion-request", status_code=status.HTTP_201_CREATED)
async def create_deletion_request(
    body: DeletionRequestCreate, user: CurrentUser, session: DbSession, request: Request
) -> DeletionRequestOut:
    """申请注销(7 天冷静期)。须键入与账号一致的完整手机号;已有 pending 返回既有(幂等)。"""
    req = await service.request_deletion(session, user, phone=body.phone, reason=body.reason)
    set_audit_target(request, f"user:{user.id}", detail={"action": "account_deletion_request"})
    return DeletionRequestOut.model_validate(req)


@router.get("/me/deletion-request")
async def get_deletion_request(user: CurrentUser, session: DbSession) -> DeletionRequestOut | None:
    """当前 pending 申请;无则最近一条(展示驳回原因/冷静期倒计时);从未申请回 null。"""
    req = await service.get_my_deletion_request(session, user.id)
    return DeletionRequestOut.model_validate(req) if req is not None else None


@router.post("/me/deletion-request/cancel")
async def cancel_deletion_request(
    user: CurrentUser, session: DbSession, request: Request
) -> DeletionRequestOut:
    """冷静期内撤销注销申请(仅 pending 可撤)。"""
    req = await service.cancel_deletion_request(session, user.id)
    set_audit_target(request, f"user:{user.id}", detail={"action": "account_deletion_cancel"})
    return DeletionRequestOut.model_validate(req)


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
