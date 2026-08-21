"""节点注册匿名侧:脚本下发 + 令牌换参数 + 进度回报。

鉴权模型(同 billing/webhooks_router 的「凭证即鉴权」):
- /script 无鉴权 —— 内容零密钥(静态脚本,仅替换 API 地址占位符),轻限流防刷;
- /bootstrap /progress 走 Bearer 注册令牌(256-bit 只存哈希),
  无效/过期/吊销/终态一律统一 404(service 层保证,防探测),外加按 IP 限流。
join token 明文只出现在 bootstrap 响应体,严禁入日志(本文件不打印响应)。
"""

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Header, Request
from fastapi.responses import PlainTextResponse

from app.core.config import get_settings
from app.core.db import DbSession
from app.core.errors import unauthorized
from app.core.ratelimit import check_rate_limit
from app.modules.nodes import service
from app.modules.nodes.schemas import (
    BootstrapOut,
    BootstrapRequest,
    ProgressAck,
    ProgressRequest,
)

router = APIRouter(tags=["node-enroll"])

_SCRIPT_PATH = Path(__file__).parent / "assets" / "node-join.sh"


@lru_cache(maxsize=1)
def _script_body() -> str:
    return _SCRIPT_PATH.read_text(encoding="utf-8")


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise unauthorized("缺少注册令牌")
    return authorization.removeprefix("Bearer ").strip()


@router.get("/node-enroll/script", response_class=PlainTextResponse)
async def get_join_script(request: Request) -> PlainTextResponse:
    """装机脚本下发(text/x-shellscript)。零密钥;占位符替换为本环境 API 地址。"""
    await check_rate_limit(
        f"node-enroll-script:{_client_ip(request)}", max_attempts=30, window_seconds=60
    )
    # 只替换第一次出现(赋值行);脚本内另一处 __API_BASE__ 是护栏的比较字面量,须原样保留
    body = _script_body().replace("__API_BASE__", get_settings().public_base_url.rstrip("/"), 1)
    return PlainTextResponse(body, media_type="text/x-shellscript")


@router.post("/node-enroll/bootstrap")
async def enroll_bootstrap(
    body: BootstrapRequest,
    session: DbSession,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> BootstrapOut:
    """令牌换装机参数(含 RKE2 join token,仅经本响应体下发)。支持脚本重跑/重启续跑。"""
    await check_rate_limit(f"node-enroll:{_client_ip(request)}", max_attempts=30, window_seconds=60)
    token = _bearer_token(authorization)
    enrollment, cfg = await service.bootstrap(
        session,
        token,
        hostname=body.hostname,
        os_info=body.os_info,
        gpus=body.gpus,
        gpu_details=body.gpu_details,
        client_ip=request.client.host if request.client else None,
    )
    return BootstrapOut(
        pool=enrollment.pool,
        hostname_expected=enrollment.hostname,
        k8s_distro=await service.derive_node_distro(session, cfg),
        rke2_version=cfg["cluster_agent_version"],
        rke2_server_url=cfg["cluster_server_url"],
        rke2_join_token=cfg["cluster_join_token"],
        driver_version=cfg["node_driver_version"],
        nvme_devices=enrollment.nvme_devices or [],
        registries_yaml=service.render_registries_yaml(cfg),
        install_mirror=cfg["node_install_mirror"] or "cn",
    )


@router.post("/node-enroll/progress")
async def enroll_progress(
    body: ProgressRequest,
    session: DbSession,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> ProgressAck:
    await check_rate_limit(f"node-enroll:{_client_ip(request)}", max_attempts=60, window_seconds=60)
    token = _bearer_token(authorization)
    enrollment = await service.report_progress(
        session, token, phase=body.phase, state=body.state, message=body.message
    )
    return ProgressAck(status=enrollment.status)
