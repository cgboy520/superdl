"""SuperDL platform Jupyter auth extension (COPYed at image build time, enabled by the entrypoint when importable).

Two things:
1. The `/superdl-bootstrap` handler: a one-time ticket (code + exp + sig, signing key = the Jupyter token
   itself) is redeemed → Set-Cookie of a first-party session cookie → 302 into Jupyter. The token never enters the URL;
   the ticket is single-use with a short TTL.
2. `SuperDLIdentityProvider`: accepts the cookie above in addition to the stock ?token= / Authorization.
   Any failure falls back to stock token auth (?token= links keep working).
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
    """Redeem the one-time ticket and set the login cookie."""

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
    """Validate the cookie and return the user identity or None."""
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
