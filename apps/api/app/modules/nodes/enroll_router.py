"""Anonymous side of node enrollment: script download + token exchange + progress reports.
/script has no auth (zero secrets, light rate limit); /bootstrap takes the Bearer enrollment token,
one-off, and issues the narrow progress token;
invalid / expired / revoked / terminal tokens all get 404, rate-limited per IP. The join token
plaintext appears only in the bootstrap response body, never in logs.
"""

import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Header, Request
from fastapi.responses import PlainTextResponse

from app.core.config import get_settings
from app.core.db import DbSession
from app.core.errors import unauthorized
from app.core.http import client_ip
from app.core.ratelimit import check_rate_limit
from app.modules.nodes import service
from app.modules.nodes.schemas import BootstrapOut, BootstrapRequest, ProgressRequest

router = APIRouter(tags=["node-enroll"])

_SCRIPT_PATH = Path(__file__).parent / "assets" / "node-join.sh"


@lru_cache(maxsize=1)
def _script_body() -> str:
    return _SCRIPT_PATH.read_text(encoding="utf-8")


def _served_script() -> str:
    """The script body only replaces the first __API_BASE__."""
    return _script_body().replace("__API_BASE__", get_settings().public_base_url.rstrip("/"), 1)


def _client_ip(request: Request) -> str:
    return client_ip(request) or "unknown"


def _bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise unauthorized("missing enrollment token")
    return authorization.removeprefix("Bearer ").strip()


@router.get("/node-enroll/script", response_class=PlainTextResponse)
async def get_join_script(request: Request) -> PlainTextResponse:
    """Install script download (text/x-shellscript). Zero secrets; the placeholder is replaced with
    this environment's API address."""
    await check_rate_limit(
        f"node-enroll-script:{_client_ip(request)}", max_attempts=30, window_seconds=60
    )
    return PlainTextResponse(_served_script(), media_type="text/x-shellscript")


@router.post("/node-enroll/bootstrap")
async def enroll_bootstrap(
    body: BootstrapRequest,
    session: DbSession,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> BootstrapOut:
    """Exchange the token for install parameters (join token included). The enrollment token is
    one-off: the first run consumes it and issues the progress token."""
    await check_rate_limit(f"node-enroll:{_client_ip(request)}", max_attempts=30, window_seconds=60)
    token = _bearer_token(authorization)
    enrollment, cfg, progress_token = await service.bootstrap(
        session,
        token,
        hostname=body.hostname,
        os_info=body.os_info,
        gpu_details=body.gpu_details,
        client_ip=client_ip(request),
    )
    return BootstrapOut(
        pool=enrollment.pool,
        k8s_distro=await service.derive_node_distro(session, cfg),
        cluster_agent_version=cfg.cluster_agent_version,
        cluster_server_url=cfg.cluster_server_url,
        cluster_join_token=cfg.cluster_join_token,
        driver_version=cfg.node_driver_version,
        nvme_devices=enrollment.nvme_devices or [],
        registries_yaml=service.render_registries_yaml(cfg),
        registry_ca_pem=cfg.registry_ca_pem,
        install_mirror=cfg.node_install_mirror or "official",
        progress_token=progress_token,
        script_sha256=hashlib.sha256(_served_script().encode()).hexdigest(),
    )


@router.post("/node-enroll/progress", status_code=204)
async def enroll_progress(
    body: ProgressRequest,
    session: DbSession,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Progress report, no response body."""
    await check_rate_limit(f"node-enroll:{_client_ip(request)}", max_attempts=60, window_seconds=60)
    token = _bearer_token(authorization)
    await service.report_progress(
        session,
        token,
        phase=body.phase,
        state=body.state,
        message=body.message,
        driver_version=body.driver_version,
        cuda_version=body.cuda_version,
    )
