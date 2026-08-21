"""节点注册:令牌生命周期 + 加入状态机。

安全要点:
- 令牌 `sdln_` + token_urlsafe(32)(256-bit 熵),库中只存 sha256;
  明文仅在创建/重生成响应出现一次。
- 无效/过期/吊销/终态令牌一律统一 404(不区分原因,防探测);
  匿名端点的限流在 enroll_router 层。
- 状态迁移集中于 transition_enrollment(对齐铁律 #10 精神),非法迁移 409。
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from fastapi import status as http_status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AppError, ErrorCode, not_found
from app.core.k8s.base import ClusterProbe
from app.core.logging import get_logger
from app.core.platform_config import get_effective_platform_config
from app.core.timeutil import now_utc
from app.modules.nodes.models import ClusterStatus, NodeEnrollment, NodeSpec
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
            key="nodes.enrollTransition",
            params={"from": enrollment.status, "to": new_status},
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
            key="nodes.clusterNotConfigured",
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
    (token 只存哈希,无法复读原值)。"""
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
        # joined 已毕业到正式「节点」列表,不再占用「待加入」视图(避免与节点表重复)
        if r.status == "joined":
            continue
        if r.status == "expired" and r.updated_at < now - timedelta(days=7):
            continue
        out.append(r)
    return out


async def joined_node_specs(session: AsyncSession) -> dict[str, dict[str, str]]:
    """已加入节点的登记规格(按 node_name 索引),供管理端节点卡补充展示:
    型号(nvidia-smi 上报,取首卡)/驱动版本/CUDA 版本。K8s 侧不带这些标签时用它兜底。"""
    rows = (
        await session.execute(select(NodeEnrollment).where(NodeEnrollment.status == "joined"))
    ).scalars()
    out: dict[str, dict[str, str]] = {}
    for r in rows:
        if not r.node_name:
            continue
        os_info = r.os_info or {}
        gpu_info = r.gpu_info or []
        first = gpu_info[0] if gpu_info else None
        first_name = first.get("name", "") if isinstance(first, dict) else (first or "")
        out[r.node_name] = {
            "gpu_model": str(first_name),
            "driver_version": str(os_info.get("driver_version") or ""),
            "cuda_version": str(os_info.get("cuda_version") or ""),
        }
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
            key="nodes.regenerateNotAllowed",
            params={"status": enrollment.status},
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
            key="nodes.alreadyTerminal",
            params={"status": enrollment.status},
            http_status=http_status.HTTP_409_CONFLICT,
        )
    transition_enrollment(enrollment, "revoked")
    await session.commit()
    await session.refresh(enrollment)
    return enrollment


async def request_cordon(
    session: AsyncSession, node_name: str, *, unschedulable: bool, reason: str
) -> None:
    """cordon/uncordon 只入队不直接动 K8s(硬规范 #3),handler 幂等执行。"""
    from app.core.outbox import enqueue

    enqueue(
        session,
        "node.cordon",
        {"node_name": node_name, "unschedulable": unschedulable, "reason": reason},
    )
    await session.commit()


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
    gpu_details: list[dict[str, Any]] | None = None,
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
            key="nodes.hostnameMismatch",
            http_status=http_status.HTTP_409_CONFLICT,
        )
    if row.reported_ip and client_ip and row.reported_ip != client_ip:
        # 换 IP 重跑常见(多网卡/NAT):不硬拒,留审计信号
        logger.warning(
            "node_enrollment_ip_changed",
            enrollment_id=row.id,
            old=row.reported_ip,
            new=client_ip,
        )
    row.node_name = hostname
    row.reported_ip = client_ip
    row.os_info = os_info
    row.gpu_info = gpu_details if gpu_details else gpus  # 新脚本全卡清单优先,旧脚本回落名称列表
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


# ---------- 节点规格台账(巡检写入,业务只读;WP26) ----------


async def list_node_specs(session: AsyncSession) -> list[NodeSpec]:
    """全量台账(含 Missing/未打标),管理端节点页数据源。"""
    rows = (await session.execute(select(NodeSpec).order_by(NodeSpec.node_name))).scalars()
    return list(rows)


async def ready_specs(session: AsyncSession) -> list[NodeSpec]:
    """Ready 节点(上架校验/容量预览口径)。"""
    rows = (await session.execute(select(NodeSpec).where(NodeSpec.status == "Ready"))).scalars()
    return list(rows)


async def gpu_model_aggregates(session: AsyncSession) -> list["GpuModelAggregate"]:
    """台账按 canonical×池聚合(SKU「从集群资源创建」下拉数据源)。

    未识别型号归入 gpu_model=None 桶(前端标 unrecognized,不可被 SKU 选中)。
    """
    rows = await list_node_specs(session)
    agg: dict[tuple[str | None, str | None], GpuModelAggregate] = {}
    for r in rows:
        key = (r.gpu_model, r.pool_label)
        item = agg.get(key)
        if item is None:
            item = GpuModelAggregate(
                gpu_model=r.gpu_model, gpu_model_raw=r.gpu_model_raw, pool_label=r.pool_label
            )
            agg[key] = item
        item.node_count += 1
        item.gpu_total += r.gpu_count
        if r.status == "Ready":
            item.ready_gpu_total += r.gpu_count
            item.ready_gpu_free += max(0, r.gpu_count - r.gpu_used)
            if r.gpu_count > 0:
                # 整机配比(vCPU/内存 ÷ 卡数),取各节点最小值 = 保守推荐;0 = 未知
                per_vcpu, per_mem = r.vcpu // r.gpu_count, r.mem_gb // r.gpu_count
                item.vcpu_per_gpu = min(item.vcpu_per_gpu or per_vcpu, per_vcpu)
                item.mem_gb_per_gpu = min(item.mem_gb_per_gpu or per_mem, per_mem)
        if r.vram_gb:
            item.vram_gb = max(item.vram_gb, r.vram_gb)
    return sorted(
        agg.values(), key=lambda x: (x.gpu_model is None, str(x.gpu_model), str(x.pool_label))
    )


@dataclass
class GpuModelAggregate:
    gpu_model: str | None
    gpu_model_raw: str | None
    pool_label: str | None
    node_count: int = 0
    gpu_total: int = 0
    ready_gpu_total: int = 0
    ready_gpu_free: int = 0
    vram_gb: int = 0
    vcpu_per_gpu: int = 0
    mem_gb_per_gpu: int = 0


# ---------- 集群能力缓存 ----------


async def save_cluster_probe(session: AsyncSession, probe: ClusterProbe) -> ClusterStatus:
    """探测结果 upsert 单行(id=1),probed_at=当次时间。调用方负责 commit。"""
    row = await session.get(ClusterStatus, 1)
    if row is None:
        row = ClusterStatus(id=1)
        session.add(row)
    row.api_reachable = probe.api_reachable
    row.k8s_version = probe.k8s_version
    row.distro = probe.distro
    row.hami_ready = probe.hami_ready
    row.dcgm_present = probe.dcgm_present
    row.kps_present = probe.kps_present
    row.gpu_operator_present = probe.gpu_operator_present
    row.kata_runtimeclass = probe.kata_runtimeclass
    row.storage_classes = list(probe.storage_classes)
    row.pools = dict(probe.pools)
    row.detail = {"runtime_classes": list(probe.runtime_classes)}
    row.error = probe.error
    row.probed_at = now_utc()
    return row


async def get_cluster_status(session: AsyncSession) -> ClusterStatus | None:
    return await session.get(ClusterStatus, 1)
