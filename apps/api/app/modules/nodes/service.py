"""节点注册(WP23):令牌生命周期 + 加入状态机。

安全要点:
- 令牌 `sdln_` + token_urlsafe(32)(256-bit 熵),库中只存 sha256;
  明文仅在创建/重生成响应出现一次。
- 无效/过期/吊销/终态令牌一律统一 404(不区分原因,防探测);
  匿名端点的限流在 enroll_router 层。
- 状态迁移集中于 transition_enrollment(对齐铁律 #10 精神),非法迁移 409。
"""

import hashlib
import secrets
from datetime import timedelta
from typing import Any

from fastapi import status as http_status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AppError, ErrorCode, not_found
from app.core.logging import get_logger
from app.core.platform_config import get_effective_platform_config
from app.core.timeutil import now_utc
from app.modules.nodes.models import NodeEnrollment
from app.modules.nodes.schemas import EnrollmentCreate

logger = get_logger(__name__)

TOKEN_PREFIX = "sdln_"
TERMINAL_STATUSES = frozenset({"joined", "failed", "expired", "revoked"})
# 允许 bootstrap 的状态:pending 首跑;installing/rebooting 支持脚本重跑与重启续跑
BOOTSTRAP_STATUSES = frozenset({"pending", "installing", "rebooting"})
REGENERATABLE_STATUSES = frozenset({"pending", "expired", "failed"})

_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"installing", "expired", "revoked", "failed"}),
    "installing": frozenset({"rebooting", "joining", "failed", "revoked", "joined"}),
    "rebooting": frozenset({"installing", "joining", "failed", "revoked", "joined"}),
    "joining": frozenset({"joined", "failed", "revoked"}),
}


def transition_enrollment(
    enrollment: NodeEnrollment,
    new_status: str,
    *,
    phase: str | None = None,
    error: str | None = None,
) -> None:
    """唯一状态迁移入口(不 commit)。非法迁移 409。"""
    if enrollment.status == new_status:
        return
    allowed = _ALLOWED_TRANSITIONS.get(enrollment.status, frozenset())
    if new_status not in allowed:
        raise AppError(
            ErrorCode.CONFLICT,
            f"注册状态不允许 {enrollment.status} → {new_status}",
            http_status=http_status.HTTP_409_CONFLICT,
        )
    enrollment.status = new_status
    if phase is not None:
        enrollment.phase = phase
    if error is not None:
        enrollment.error = error
    if new_status == "joined":
        enrollment.joined_at = now_utc()
    logger.info(
        "node_enrollment_transition",
        enrollment_id=enrollment.id,
        to=new_status,
        phase=enrollment.phase,
    )


def _new_token() -> tuple[str, str]:
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    return token, hashlib.sha256(token.encode()).hexdigest()


def enrollment_commands(token: str) -> tuple[str, str]:
    """注册命令两种形态:管道式 / 先下载可审阅式。token 走参数,不进 URL。"""
    base = get_settings().public_base_url.rstrip("/")
    script_url = f"{base}/api/v1/node-enroll/script"
    curl_cmd = f"curl -fsSL {script_url} | sudo bash -s -- --token {token}"
    wget_cmd = f"wget -qO node-join.sh {script_url} && sudo bash node-join.sh --token {token}"
    return curl_cmd, wget_cmd


async def require_cluster_config(session: AsyncSession) -> dict[str, str]:
    """创建注册令牌的前置:cluster 组必须已配置,否则 409 引导去平台配置页。"""
    cfg = await get_effective_platform_config(session)
    if not cfg.get("rke2_server_url") or not cfg.get("rke2_join_token"):
        raise AppError(
            ErrorCode.CONFLICT,
            "集群接入参数未配置:请超管在「平台配置 · 集群接入」录入 RKE2 Server 地址与 join token",
            http_status=http_status.HTTP_409_CONFLICT,
        )
    return cfg


async def create_enrollment(
    session: AsyncSession,
    data: EnrollmentCreate,
    *,
    created_by: int,
    idempotency_key: str | None,
) -> tuple[NodeEnrollment, str]:
    """创建注册令牌。Idempotency-Key 重放:不建新行,轮换该行 token 后原样返回
    (token 只存哈希无法复读,轮换是幂等重放下唯一能再给出可用命令的做法)。"""
    await require_cluster_config(session)
    if idempotency_key:
        existing = (
            await session.execute(
                select(NodeEnrollment).where(
                    NodeEnrollment.created_by == created_by,
                    NodeEnrollment.idempotency_key == idempotency_key,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            token, existing.token_hash = _new_token()
            await session.commit()
            await session.refresh(existing)
            return existing, token

    token, token_hash = _new_token()
    enrollment = NodeEnrollment(
        token_hash=token_hash,
        pool=data.pool,
        hostname=data.hostname,
        note=data.note,
        nvme_devices=data.nvme_devices,
        expires_at=now_utc() + timedelta(hours=data.ttl_hours),
        created_by=created_by,
        idempotency_key=idempotency_key,
    )
    session.add(enrollment)
    await session.commit()
    await session.refresh(enrollment)
    return enrollment, token


async def list_enrollments(
    session: AsyncSession, *, active_only: bool = False
) -> list[NodeEnrollment]:
    rows = list(
        (await session.execute(select(NodeEnrollment).order_by(NodeEnrollment.id.desc()))).scalars()
    )
    if not active_only:
        return rows
    now = now_utc()
    out: list[NodeEnrollment] = []
    for r in rows:
        if r.status == "revoked":
            continue
        if r.status == "joined" and r.joined_at and r.joined_at < now - timedelta(hours=24):
            continue
        if r.status == "expired" and r.updated_at < now - timedelta(days=7):
            continue
        out.append(r)
    return out


async def get_enrollment(session: AsyncSession, enrollment_id: int) -> NodeEnrollment:
    row = await session.get(NodeEnrollment, enrollment_id)
    if row is None:
        raise not_found("注册记录不存在")
    return row


async def regenerate_enrollment(
    session: AsyncSession, enrollment_id: int, *, ttl_hours: int = 24
) -> tuple[NodeEnrollment, str]:
    """换新令牌:仅 pending/expired/failed(installing 等进行中状态换令牌会掐断在跑的脚本)。"""
    await require_cluster_config(session)
    enrollment = await get_enrollment(session, enrollment_id)
    if enrollment.status not in REGENERATABLE_STATUSES:
        raise AppError(
            ErrorCode.CONFLICT,
            f"状态 {enrollment.status} 不允许重新生成(仅 待执行/已过期/已失败)",
            http_status=http_status.HTTP_409_CONFLICT,
        )
    token, enrollment.token_hash = _new_token()
    enrollment.status = "pending"
    enrollment.phase = None
    enrollment.error = None
    enrollment.expires_at = now_utc() + timedelta(hours=ttl_hours)
    await session.commit()
    await session.refresh(enrollment)
    return enrollment, token


async def revoke_enrollment(session: AsyncSession, enrollment_id: int) -> NodeEnrollment:
    enrollment = await get_enrollment(session, enrollment_id)
    if enrollment.status in TERMINAL_STATUSES:
        raise AppError(
            ErrorCode.CONFLICT,
            f"状态 {enrollment.status} 已是终态,无需吊销",
            http_status=http_status.HTTP_409_CONFLICT,
        )
    transition_enrollment(enrollment, "revoked")
    await session.commit()
    await session.refresh(enrollment)
    return enrollment


# ---------- 匿名侧(令牌即鉴权;统一 404 防探测) ----------


async def _resolve_token(session: AsyncSession, token: str) -> NodeEnrollment:
    """按哈希取行;无效/终态/过期一律 404。pending 过期顺带落 expired。"""
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    row = (
        await session.execute(select(NodeEnrollment).where(NodeEnrollment.token_hash == token_hash))
    ).scalar_one_or_none()
    if row is None or row.status in TERMINAL_STATUSES:
        raise not_found()
    if row.status == "pending" and row.expires_at < now_utc():
        transition_enrollment(row, "expired")
        await session.commit()
        raise not_found()
    return row


async def bootstrap(
    session: AsyncSession,
    token: str,
    *,
    hostname: str,
    os_info: dict[str, Any],
    gpus: list[str],
    client_ip: str | None,
) -> tuple[NodeEnrollment, dict[str, str]]:
    """令牌换装机参数。支持重复调用(脚本重跑/重启续跑);返回 (enrollment, cluster 配置)。"""
    row = await _resolve_token(session, token)
    if row.status not in BOOTSTRAP_STATUSES:
        raise not_found()
    if row.hostname and row.hostname != hostname:
        transition_enrollment(
            row, "failed", error=f"主机名不符:期望 {row.hostname},实际上报 {hostname}(防令牌串用)"
        )
        await session.commit()
        raise AppError(
            ErrorCode.CONFLICT,
            "主机名与登记不符,令牌已作废,请在管理端核对后重新生成",
            http_status=http_status.HTTP_409_CONFLICT,
        )
    if row.reported_ip and client_ip and row.reported_ip != client_ip:
        # 换 IP 重跑常见(多网卡/NAT),不硬拒,但留审计信号
        logger.warning(
            "node_enrollment_ip_changed",
            enrollment_id=row.id,
            old=row.reported_ip,
            new=client_ip,
        )
    row.node_name = hostname
    row.reported_ip = client_ip
    row.os_info = os_info
    row.gpu_info = gpus
    row.last_report_at = now_utc()
    if row.status == "pending":
        transition_enrollment(row, "installing", phase="bootstrap")
    cfg = await get_effective_platform_config(session)
    await session.commit()
    await session.refresh(row)
    return row, cfg


async def report_progress(
    session: AsyncSession, token: str, *, phase: str, state: str, message: str | None
) -> NodeEnrollment:
    row = await _resolve_token(session, token)
    if row.status == "pending":
        raise not_found()  # 未 bootstrap 就上报进度:非法序列,按无效令牌处理
    row.phase = phase
    row.last_report_at = now_utc()
    if state == "failed":
        transition_enrollment(row, "failed", phase=phase, error=message or f"{phase} 失败")
    elif state == "rebooting":
        transition_enrollment(row, "rebooting", phase=phase)
    elif row.status == "rebooting" and state in ("running", "ok"):
        # oneshot 续跑后的第一条进度:回到 installing
        transition_enrollment(row, "installing", phase=phase)
    if phase == "rke2_start" and state == "ok" and row.status == "installing":
        transition_enrollment(row, "joining", phase=phase)
    await session.commit()
    await session.refresh(row)
    return row
