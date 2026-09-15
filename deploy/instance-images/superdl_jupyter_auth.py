"""SuperDL 平台 Jupyter 鉴权扩展(镜像构建期 COPY,entrypoint 按可导入性启用)。

两件事:
1. `/superdl-bootstrap` handler:一次性票据(code + exp + sig,验签密钥 = Jupyter token
   本体)核销 → Set-Cookie 第一方会话 cookie → 302 进 Jupyter。token 不进 URL;
   票据单次、短 TTL。
2. `SuperDLIdentityProvider`:stock 的 ?token=/Authorization 之外,接受上述 cookie。
   任一环节异常一律回落 stock token 鉴权(?token= 链接依旧可用)。
"""

import hashlib
import hmac
import inspect
import os
import time

from jupyter_server.auth import User
from jupyter_server.base.handlers import JupyterHandler

try:
    from jupyter_server.auth.identity import TokenIdentityProvider as _BaseIdentityProvider
except ImportError:
    from jupyter_server.auth.identity import PasswordIdentityProvider as _BaseIdentityProvider
from jupyter_server.utils import url_path_join
from tornado import web

COOKIE_NAME = "__Host-superdl_jupyter"
_used_codes: dict[str, float] = {}
_USED_CODE_KEEP_SECONDS = 120.0


def _purge_used_codes(now: float) -> None:
    expired = [c for c, ts in _used_codes.items() if now - ts > _USED_CODE_KEEP_SECONDS]
    for c in expired:
        del _used_codes[c]


def _ticket_valid(token: str, code: str, exp: str, sig: str) -> bool:
    if not code or not sig:
        return False
    try:
        deadline = int(exp)
    except ValueError:
        return False
    if time.time() > deadline:
        return False
    expected = hmac.new(token.encode(), f"{code}.{exp}".encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


class SuperDLBootstrapHandler(JupyterHandler):
    """核销一次性票据并设置登录 cookie。"""

    async def get(self) -> None:
        token = os.environ.get("JUPYTER_TOKEN", "")
        code = self.get_argument("code", "")
        exp = self.get_argument("exp", "")
        sig = self.get_argument("sig", "")
        _purge_used_codes(time.time())
        if not token or code in _used_codes or not _ticket_valid(token, code, exp, sig):
            raise web.HTTPError(403)
        _used_codes[code] = time.time()
        self.set_cookie(
            COOKIE_NAME, token, secure=True, httponly=True, samesite="lax", path="/"
        )
        self.redirect(url_path_join(self.base_url, "lab"))


def _user_from_cookie(provider: _BaseIdentityProvider, handler: JupyterHandler):
    """校验 cookie,返回用户身份或 None。"""
    try:
        token = str(provider.token or "")
        cookie = handler.get_cookie(COOKIE_NAME)
        if token and cookie and hmac.compare_digest(cookie, token):
            return User("root")
    except Exception:
        return None
    return None


if inspect.iscoroutinefunction(_BaseIdentityProvider.get_user):

    class SuperDLIdentityProvider(_BaseIdentityProvider):
        async def get_user(self, handler):  # type: ignore[override]
            user = _user_from_cookie(self, handler)
            if user is not None:
                return user
            return await super().get_user(handler)
else:

    class SuperDLIdentityProvider(_BaseIdentityProvider):
        def get_user(self, handler):  # type: ignore[override]
            user = _user_from_cookie(self, handler)
            if user is not None:
                return user
            return super().get_user(handler)


def _jupyter_server_extension_points() -> list[dict]:
    return [{"module": "superdl_jupyter_auth"}]


def _load_jupyter_server_extension(server_app) -> None:
    route = url_path_join(server_app.base_url, "superdl-bootstrap")
    server_app.web_app.add_handlers(".*$", [(route, SuperDLBootstrapHandler)])
    server_app.log.info("superdl bootstrap auth extension loaded")
