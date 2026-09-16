"""Payment callbacks (no user auth: a valid signature is the authentication; replay-safe). One
route per registered channel (`payment_channels.CHANNELS`), dev-only channels skipped in prod."""

from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Request, Response

from app.core.config import Settings, get_settings
from app.core.db import DbSession
from app.core.http import client_ip
from app.core.ratelimit import check_rate_limit
from app.modules.billing import payment_service
from app.modules.billing.payment_channels import CHANNELS, ChannelSpec, get_channel

CALLBACK_RATE_LIMIT = 120
CALLBACK_RATE_WINDOW = 60.0


async def _guard(request: Request) -> None:
    ip = client_ip(request)
    await check_rate_limit(
        f"pay-callback:{ip or '-'}",
        max_attempts=CALLBACK_RATE_LIMIT,
        window_seconds=CALLBACK_RATE_WINDOW,
    )


def _handler(spec: ChannelSpec) -> Callable[..., Awaitable[Response]]:
    async def webhook(request: Request, session: DbSession) -> Response:
        if not spec.dev_only:
            await _guard(request)
        channel = await get_channel(spec.name, session)
        result = await channel.parse_callback(dict(request.headers), await request.body())
        status = (
            "ok"
            if result is None
            else await payment_service.handle_callback(session, spec.name, result)
        )
        return spec.ack(status)

    webhook.__name__ = f"{spec.webhook_path}_webhook"
    webhook.__doc__ = (
        "dev/test only: simulate a payment callback."
        if spec.dev_only
        else f"{spec.name} callback: verify → parse → credit once (channel_txn_id idempotency)."
    )
    return webhook


def build_router(settings: Settings) -> APIRouter:
    """`POST /webhooks/{spec.webhook_path}` for every registered channel; dev-only channels are
    not mounted in prod."""
    router = APIRouter(tags=["webhooks"])
    for spec in CHANNELS.values():
        if spec.dev_only and settings.environment == "prod":
            continue
        router.add_api_route(
            f"/webhooks/{spec.webhook_path}",
            _handler(spec),
            methods=["POST"],
            name=f"{spec.webhook_path}_webhook",
        )
    return router


router = build_router(get_settings())
