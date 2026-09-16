"""Email channel (Protocol + factory): `mock` logs, `smtp` delivers through aiosmtplib. Carries
verification codes; server and sender come from the platform config center."""

from email.message import EmailMessage
from typing import Any, Literal, Protocol

import aiosmtplib
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.platform_config import RuntimeConfig, get_runtime_config
from app.core.ratelimit import check_rate_limit

logger = get_logger(__name__)

EmailQuotaKind = Literal["verify", "notify"]
SmtpSecurity = Literal["starttls", "tls", "none"]

EMAIL_PLATFORM_LIMITS: dict[EmailQuotaKind, tuple[int, int]] = {
    "verify": (2000, 10000),
    "notify": (1000, 5000),
}
SMTP_TIMEOUT_SECONDS = 10.0


async def ensure_email_platform_quota(kind: EmailQuotaKind = "verify") -> None:
    """Platform-wide email budget per kind (hourly / daily), independent of the SMS budget.
    Raises RATE_LIMITED (429); every `channel.send` must pass it first."""
    hourly, daily = EMAIL_PLATFORM_LIMITS[kind]
    await check_rate_limit(
        f"email-platform:{kind}:hourly", max_attempts=hourly, window_seconds=3600.0
    )
    await check_rate_limit(
        f"email-platform:{kind}:daily", max_attempts=daily, window_seconds=86400.0
    )


class EmailError(RuntimeError):
    """The mail server refused or the connection failed."""


class EmailChannel(Protocol):
    async def send(self, to: str, subject: str, text: str, html: str | None = None) -> None:
        """Send one message. Raises EmailError."""
        ...


class MockEmailChannel:
    async def send(self, to: str, subject: str, text: str, html: str | None = None) -> None:
        logger.info("mock_email_sent", to=to, subject=subject, text=text, has_html=html is not None)


class SmtpEmailChannel:
    """SMTP submission: `starttls` (587) upgrades after connecting, `tls` (465) connects over
    TLS, `none` sends in clear (private networks only)."""

    def __init__(
        self,
        host: str,
        port: int,
        security: SmtpSecurity,
        *,
        username: str | None,
        password: str | None,
        sender: str,
        reply_to: str | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._security = security
        self._username = username
        self._password = password
        self._sender = sender
        self._reply_to = reply_to

    def build_message(self, to: str, subject: str, text: str, html: str | None) -> EmailMessage:
        message = EmailMessage()
        message["From"] = self._sender
        message["To"] = to
        message["Subject"] = subject
        if self._reply_to:
            message["Reply-To"] = self._reply_to
        message.set_content(text)
        if html:
            message.add_alternative(html, subtype="html")
        return message

    def connect_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "hostname": self._host,
            "port": self._port,
            "use_tls": self._security == "tls",
            "start_tls": self._security == "starttls",
            "timeout": SMTP_TIMEOUT_SECONDS,
        }
        if self._username:
            kwargs["username"] = self._username
            kwargs["password"] = self._password or ""
        return kwargs

    async def send(self, to: str, subject: str, text: str, html: str | None = None) -> None:
        try:
            await aiosmtplib.send(
                self.build_message(to, subject, text, html), **self.connect_kwargs()
            )
        except (aiosmtplib.SMTPException, OSError) as exc:
            raise EmailError(f"smtp send failed: {exc}") from exc


_channel: EmailChannel | None = None


def set_email_channel(channel: EmailChannel | None) -> None:
    """Test seam; None restores construction from config."""
    global _channel
    _channel = channel


def build_email_channel(cfg: RuntimeConfig) -> EmailChannel:
    """Channel for the effective `email_provider`; a missing server or sender raises EmailError."""
    if cfg.email_provider == "mock":
        return MockEmailChannel()
    if not (cfg.smtp_host and cfg.email_from):
        raise EmailError("SMTP email configuration incomplete (smtp_host / email_from)")
    security: SmtpSecurity = cfg.smtp_security  # type: ignore[assignment]
    return SmtpEmailChannel(
        cfg.smtp_host,
        int(cfg.smtp_port),
        security,
        username=cfg.smtp_username or None,
        password=cfg.smtp_password or None,
        sender=cfg.email_from,
        reply_to=cfg.email_reply_to or None,
    )


async def get_email_channel(session: AsyncSession) -> EmailChannel:
    if _channel is not None:
        return _channel
    return build_email_channel(await get_runtime_config(session))
