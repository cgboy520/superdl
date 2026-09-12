"""节点注册:令牌生命周期 + 加入状态机。
注册令牌 `sdln_` + token_urlsafe(32),库中只存 HMAC-SHA256(core/crypto.hash_node_token);
首次 bootstrap 即消费,换发 progress 令牌 `sdlp_`。令牌绝对过期;无效/过期/吊销/终态统一 404;
状态迁移集中于 transition_enrollment。
"""

import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from fastapi import status as http_status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.core.config import get_settings
from app.core.crypto import hash_node_token, hash_node_token_candidates
from app.core.errors import AppError, ErrorCode, conflict, not_found
from app.core.gpu_models import model_matches
from app.core.idempotency import find_replay
from app.core.k8s.base import (
    INSTANCE_DISK_STORAGE_CLASS,
    JUICEFS_STORAGE_CLASS,
    ClusterProbe,
    derive_distro,
)
from app.core.logging import get_logger
from app.core.outbox import enqueue
from app.core.platform_config import RuntimeConfig, get_runtime_config
from app.core.registry import parse_proxy_projects
from app.core.timeutil import now_utc
from app.modules.nodes.models import ClusterStatus, NodeEnrollment, NodeSpec
from app.modules.nodes.schemas import EnrollmentCreate

logger = get_logger(__name__)

TOKEN_PREFIX = "sdln_"
PROGRESS_TOKEN_PREFIX = "sdlp_"
TERMINAL_STATUSES = frozenset({"joined", "failed", "expired", "revoked"})
REGENERATABLE_STATUSES = frozenset({"pending", "expired", "failed"})

_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"installing", "expired", "revoked", "failed"}),
    "installing": frozenset({"rebooting", "joining", "failed", "revoked", "joined", "expired"}),
    "rebooting": frozenset({"installing", "joining", "failed", "revoked", "joined", "expired"}),
    "joining": frozenset({"joined", "failed", "revoked", "expired"}),
    # 终态 → revoked 只有退役(decommission_node)一条入口;手工吊销对终态仍 409
    "joined": frozenset({"revoked"}),
    "failed": frozenset({"revoked"}),
    "expired": frozenset({"revoked"}),
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
        raise conflict(
            key="nodes.enrollTransition",
            params={"from": enrollment.status, "to": new_status},
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


def _new_token(prefix: str = TOKEN_PREFIX) -> tuple[str, str]:
    token = prefix + secrets.token_urlsafe(32)
    return token, hash_node_token(token)


def enrollment_commands(token: str) -> tuple[str, str]:
    """注册命令两种形态:管道式 / 先下载可审阅式。token 经 stdin 落 0600 文件,不进 argv。"""
    base = get_settings().public_base_url.rstrip("/")
    script_url = f"{base}/api/v1/node-enroll/script"
    token_file = "/run/superdl-join.token"
    # echo 是 shell 内建,不产生含 token 的 argv
    load = f"echo '{token}' | sudo sh -c 'umask 077; cat > {token_file}; "
    cleanup = f"; s=$?; rm -f {token_file}; exit $s'"
    curl_cmd = f"{load}curl -fsSL {script_url} | bash -s -- --token-file {token_file}{cleanup}"
    wget_cmd = (
        f"wget -qO node-join.sh {script_url} && "
        f"{load}bash node-join.sh --token-file {token_file}{cleanup}"
    )
    return curl_cmd, wget_cmd


async def require_cluster_config(session: AsyncSession) -> RuntimeConfig:
    """创建注册令牌的前置:cluster 组必须已配置,否则 409。"""
    cfg = await get_runtime_config(session)
    if not cfg.cluster_server_url or not cfg.cluster_join_token:
        raise conflict(key="nodes.clusterNotConfigured")
    return cfg


async def create_enrollment(
    session: AsyncSession,
    data: EnrollmentCreate,
    *,
    created_by: int,
    idempotency_key: str | None,
) -> tuple[NodeEnrollment, str]:
    """创建注册令牌。Idempotency-Key 重放:不建新行,轮换该行 token 后返回。"""
    await require_cluster_config(session)
    if idempotency_key:
        existing = await find_replay(
            session,
            NodeEnrollment,
            owner_col=NodeEnrollment.created_by,
            owner_id=created_by,
            key=idempotency_key,
        )
        if existing is not None:
            # 与 regenerate 同守卫
            if existing.status not in REGENERATABLE_STATUSES:
                raise conflict(key="nodes.regenerateNotAllowed", params={"status": existing.status})
            token, existing.token_hash = _new_token()
            existing.progress_token_hash = None  # 旧 progress 令牌随注册令牌一并作废
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
        # joined 不进「待加入」视图
        if r.status == "joined":
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
    """换新令牌:仅 pending/expired/failed。"""
    await require_cluster_config(session)
    enrollment = await get_enrollment(session, enrollment_id)
    if enrollment.status not in REGENERATABLE_STATUSES:
        raise conflict(key="nodes.regenerateNotAllowed", params={"status": enrollment.status})
    token, enrollment.token_hash = _new_token()
    enrollment.progress_token_hash = None  # 旧 progress 令牌随注册令牌一并作废
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
        raise conflict(key="nodes.alreadyTerminal", params={"status": enrollment.status})
    transition_enrollment(enrollment, "revoked")
    await session.commit()
    await session.refresh(enrollment)
    return enrollment


async def decommission_node(session: AsyncSession, node_name: str, *, reason: str) -> int:
    """节点退役(不可逆),返回置 revoked 的登记行数。同事务三件事:停调度期望态落台账、
    该主机名全部登记置 revoked、outbox 入队 node.decommission。
    集群 join token 轮换与 kubelet 证书吊销需人工。
    """
    row = (
        await session.execute(select(NodeSpec).where(NodeSpec.node_name == node_name))
    ).scalar_one_or_none()
    if row is None:
        raise not_found(key="nodes.nodeNotFound")
    row.desired_unschedulable = True
    enrollments = list(
        (
            await session.execute(
                select(NodeEnrollment).where(
                    NodeEnrollment.node_name == node_name,
                    NodeEnrollment.status != "revoked",
                )
            )
        ).scalars()
    )
    for enrollment in enrollments:
        transition_enrollment(enrollment, "revoked", error=f"节点已退役:{reason}")
    enqueue(session, "node.decommission", {"node_name": node_name, "reason": reason})
    await session.commit()
    logger.warning(
        "node_decommissioned",
        node=node_name,
        revoked_enrollments=len(enrollments),
        reason=reason,
    )
    return len(enrollments)


async def request_cordon(
    session: AsyncSession, node_name: str, *, unschedulable: bool, reason: str
) -> None:
    """cordon 期望态落台账 + outbox 入队;handler 读期望态而非 payload。"""
    row = (
        await session.execute(select(NodeSpec).where(NodeSpec.node_name == node_name))
    ).scalar_one_or_none()
    if row is not None:
        row.desired_unschedulable = unschedulable
    enqueue(
        session,
        "node.cordon",
        {"node_name": node_name, "unschedulable": unschedulable, "reason": reason},
    )
    await session.commit()


# ---------- 匿名侧(令牌即鉴权;统一 404) ----------


def _check_usable(row: NodeEnrollment | None) -> NodeEnrollment:
    """公共闸门:无效/终态/过期一律 404;请求路径只拒不迁移,落 expired 由对账器做。"""
    if row is None or row.status in TERMINAL_STATUSES or row.expires_at < now_utc():
        raise not_found()
    return row


async def _resolve_by_hash(
    session: AsyncSession, column: InstrumentedAttribute[str | None], token: str
) -> NodeEnrollment | None:
    """按 HMAC candidates(crypto.py)取行。"""
    return (
        await session.execute(
            select(NodeEnrollment).where(column.in_(hash_node_token_candidates(token))).limit(1)
        )
    ).scalar_one_or_none()


async def _resolve_token(session: AsyncSession, token: str) -> NodeEnrollment:
    """注册令牌(bootstrap 用):按哈希取行。"""
    row = await _resolve_by_hash(session, NodeEnrollment.token_hash, token)
    return _check_usable(row)


async def _resolve_progress_token(session: AsyncSession, token: str) -> NodeEnrollment:
    """progress 令牌(进度上报用):只按 progress_token_hash 取行,注册令牌不能上报。"""
    row = await _resolve_by_hash(session, NodeEnrollment.progress_token_hash, token)
    return _check_usable(row)


async def bootstrap(
    session: AsyncSession,
    token: str,
    *,
    hostname: str,
    os_info: dict[str, Any],
    gpu_details: list[dict[str, Any]],
    client_ip: str | None,
) -> tuple[NodeEnrollment, RuntimeConfig, str]:
    """令牌换装机参数,返回 (enrollment, cluster 最小配置, progress 令牌)。
    只有 pending 行能 bootstrap,首跑即消费并迁 installing。
    """
    row = await _resolve_token(session, token)
    if row.status != "pending":
        raise not_found()
    if row.hostname != hostname:
        # 主机名与签发时不符即 failed
        transition_enrollment(
            row, "failed", error=f"主机名不符:期望 {row.hostname},实际上报 {hostname}(防令牌串用)"
        )
        await session.commit()
        raise conflict(key="nodes.hostnameMismatch")
    row.node_name = hostname
    row.reported_ip = client_ip
    row.os_info = os_info
    row.gpu_info = gpu_details
    row.last_report_at = now_utc()
    progress_token, row.progress_token_hash = _new_token(PROGRESS_TOKEN_PREFIX)
    transition_enrollment(row, "installing", phase="bootstrap")
    cfg = await get_runtime_config(session)
    await session.commit()
    await session.refresh(row)
    return row, cfg, progress_token


async def report_progress(
    session: AsyncSession,
    token: str,
    *,
    phase: str,
    state: str,
    message: str | None,
    driver_version: str | None = None,
    cuda_version: str | None = None,
) -> NodeEnrollment:
    row = await _resolve_progress_token(session, token)
    if row.status == "pending":
        raise not_found()  # 未 bootstrap 就上报进度:非法序列,按无效令牌处理
    row.phase = phase
    row.last_report_at = now_utc()
    versions = {
        k: v for k, v in (("driver_version", driver_version), ("cuda_version", cuda_version)) if v
    }
    if versions:
        # 驱动/CUDA 版本并进登记快照,巡检据此填台账
        row.os_info = {**(row.os_info or {}), **versions}
    if state == "failed":
        transition_enrollment(row, "failed", phase=phase, error=message or f"{phase} 失败")
    elif state == "rebooting":
        transition_enrollment(row, "rebooting", phase=phase)
    elif row.status == "rebooting" and state in ("running", "ok"):
        # oneshot 续跑后的第一条进度:回到 installing
        transition_enrollment(row, "installing", phase=phase)
    if phase == "agent_start" and state == "ok" and row.status == "installing":
        transition_enrollment(row, "joining", phase=phase)
    await session.commit()
    await session.refresh(row)
    return row


# ---------- 节点规格台账(巡检写入,业务只读) ----------


async def list_node_specs(session: AsyncSession) -> list[NodeSpec]:
    """全量台账(含 Missing/未打标),管理端节点页数据源。"""
    rows = (await session.execute(select(NodeSpec).order_by(NodeSpec.node_name))).scalars()
    return list(rows)


async def get_node_spec(session: AsyncSession, node_name: str) -> NodeSpec | None:
    rows = (
        await session.execute(select(NodeSpec).where(NodeSpec.node_name == node_name))
    ).scalars()
    return next(iter(rows), None)


async def ready_specs(session: AsyncSession) -> list[NodeSpec]:
    """Ready 节点(上架校验/容量列口径)。"""
    rows = (await session.execute(select(NodeSpec).where(NodeSpec.status == "Ready"))).scalars()
    return list(rows)


def matching_specs(
    specs: Iterable[NodeSpec], pool_label: str, wanted_model: str | None
) -> list[NodeSpec]:
    """台账里「池 × canonical 型号」匹配的行,不看状态(唯一判「同一物理池」处);
    wanted_model None 恒不匹配。"""
    return [
        s for s in specs if s.pool_label == pool_label and model_matches(wanted_model, s.gpu_model)
    ]


def pool_specs(specs: Iterable[NodeSpec], pool_label: str) -> list[NodeSpec]:
    """台账里只按池匹配的行,不看型号与状态(CPU 档口径);不复用 matching_specs。"""
    return [s for s in specs if s.pool_label == pool_label]


async def gpu_model_aggregates(session: AsyncSession) -> list["GpuModelAggregate"]:
    """台账按 canonical×池聚合(SKU「从集群资源创建」数据源);未识别型号归 gpu_model=None 桶。"""
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
                # 整机配比取各节点最小值;0 = 未知
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
    """探测结果 upsert 单行(id=1);调用方 commit。"""
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
    row.nvidia_runtimeclass = probe.nvidia_runtimeclass
    row.gateway_ready = probe.gateway_ready
    row.cert_manager_ready = probe.cert_manager_ready
    row.nodes_ready = probe.nodes_ready
    row.nodes_total = probe.nodes_total
    row.storage_classes = list(probe.storage_classes)
    row.pools = dict(probe.pools)
    row.error = probe.error
    row.probed_at = now_utc()
    return row


async def get_cluster_status(session: AsyncSession) -> ClusterStatus | None:
    return await session.get(ClusterStatus, 1)


# Harbor CA 在节点上的落点占位符,node-join 按本机发行版目录替换
REGISTRY_CA_PATH_TEMPLATE = "__RANCHER_DIR__/harbor-ca.crt"


def render_registries_yaml(cfg: RuntimeConfig) -> str:
    """生成节点 registries.yaml(RKE2 / k3s 同格式):`mirrors "*"` Spegel P2P;
    `registry_proxy_projects` 每行 <上游>=<Harbor 代理项目>;`registry_ca_pem` 非空则配 ca_file。
    不含 auth;`node_registries_yaml` 有值即原样下发。
    """
    override = cfg.node_registries_yaml.strip()
    if override:
        return override
    host = cfg.registry_host.strip()
    lines = ["mirrors:", '  "*": {}']
    if host:
        proxies = parse_proxy_projects(cfg.registry_proxy_projects)
        for upstream, project in proxies.items():
            lines += [
                f"  {upstream}:",
                "    endpoint:",
                f'      - "https://{host}"',
                "    rewrite:",
                f'      "^(.*)$": "{project}/$1"',
            ]
        if cfg.registry_ca_pem.strip():
            lines += [
                "configs:",
                f'  "{host}":',
                "    tls:",
                f'      ca_file: "{REGISTRY_CA_PATH_TEMPLATE}"',
            ]
    return "\n".join(lines) + "\n"


async def derive_node_distro(session: AsyncSession, cfg: RuntimeConfig) -> str:
    """装机发行版派生:探测缓存 > agent 版本后缀 > rke2。"""
    row = await get_cluster_status(session)
    if row and row.distro:
        return row.distro
    return derive_distro(cfg.cluster_agent_version) or "rke2"


HAMI_GATE_MAX_AGE = timedelta(minutes=10)  # 能力缓存陈旧窗:超时视为未知,拒绝下发


async def _fresh_cluster_status(session: AsyncSession) -> ClusterStatus:
    """下发门禁共用的能力缓存读取:缺失/陈旧一律 409。"""
    row = await get_cluster_status(session)
    if row is None or now_utc() - row.probed_at > HAMI_GATE_MAX_AGE:
        raise AppError(
            ErrorCode.CLUSTER_NOT_READY,
            key="nodes.clusterNotReady",
            http_status=http_status.HTTP_409_CONFLICT,
            detail={"reason": "probe_stale" if row else "no_probe"},
        )
    return row


async def require_hami_ready(session: AsyncSession) -> None:
    """shared 档下发门禁:调度器缺位即 409。"""
    row = await _fresh_cluster_status(session)
    if not row.hami_ready:
        raise AppError(
            ErrorCode.CLUSTER_NOT_READY,
            key="nodes.clusterNotReady",
            http_status=http_status.HTTP_409_CONFLICT,
            detail={"reason": "hami_not_ready"},
        )


async def require_kata_runtimeclass(session: AsyncSession) -> None:
    """dedicated 档下发门禁:RuntimeClass kata-qemu 缺位即 409。"""
    row = await _fresh_cluster_status(session)
    if not row.kata_runtimeclass:
        raise AppError(
            ErrorCode.CLUSTER_NOT_READY,
            key="nodes.clusterNotReady",
            http_status=http_status.HTTP_409_CONFLICT,
            detail={"reason": "kata_runtimeclass_missing"},
        )


async def require_storage_classes(session: AsyncSession, *, with_data_disk: bool) -> None:
    """存储下发门禁:实例盘/数据盘 StorageClass 缺位即 409。"""
    row = await _fresh_cluster_status(session)
    present = set(row.storage_classes or ())
    required = [INSTANCE_DISK_STORAGE_CLASS]
    if with_data_disk:
        required.append(JUICEFS_STORAGE_CLASS)
    missing = [sc for sc in required if sc not in present]
    if missing:
        raise AppError(
            ErrorCode.CLUSTER_NOT_READY,
            key="nodes.storageClassMissing",
            params={"names": "、".join(missing)},
            http_status=http_status.HTTP_409_CONFLICT,
            detail={"reason": "storage_class_missing", "missing": missing},
        )
