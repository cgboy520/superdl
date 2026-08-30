"""节点注册:令牌生命周期 + 加入状态机。

安全要点:
- 注册令牌 `sdln_` + token_urlsafe(32)(256-bit 熵),库中只存 HMAC-SHA256
  (core/crypto.hash_node_token,域分离前缀 node-enroll|;读路径 dual-read:
  裸 SHA-256 旧行命中即席升级为 HMAC,过渡期后清理裸验分支);
  明文仅在创建/重生成响应出现一次。首次 bootstrap 即消费:换发窄权限
  progress 令牌 `sdlp_`(仅可上报进度,不能再换装机参数)。
- 令牌绝对过期:progress 上报只刷新心跳(last_report_at),不延长 expires_at。
- 无效/过期/吊销/终态令牌一律统一 404(不区分原因,防探测);
  匿名端点的限流在 enroll_router 层。
- 状态迁移集中于 transition_enrollment,非法迁移 409。
"""

import hashlib
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
from app.core.errors import AppError, ErrorCode, not_found
from app.core.gpu_models import model_matches
from app.core.idempotency import find_replay
from app.core.k8s.base import (
    INSTANCE_DISK_STORAGE_CLASS,
    JUICEFS_STORAGE_CLASS,
    ClusterProbe,
    derive_distro,
)
from app.core.logging import get_logger
from app.core.platform_config import get_effective_platform_config
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


def _new_token(prefix: str = TOKEN_PREFIX) -> tuple[str, str]:
    token = prefix + secrets.token_urlsafe(32)
    return token, hash_node_token(token)


def enrollment_commands(token: str) -> tuple[str, str]:
    """注册命令两种形态:管道式 / 先下载可审阅式。

    token 经 stdin 落入 0600 文件(/run 为 tmpfs,重启即消),脚本从文件读取:
    全程不出现在节点任何进程的 argv 里(本地用户 ps 不可见);命令执行完即删。
    """
    base = get_settings().public_base_url.rstrip("/")
    script_url = f"{base}/api/v1/node-enroll/script"
    token_file = "/run/superdl-join.token"
    # echo 是 shell 内建命令,不产生含 token 的进程 argv
    load = f"echo '{token}' | sudo sh -c 'umask 077; cat > {token_file}; "
    cleanup = f"; s=$?; rm -f {token_file}; exit $s'"
    curl_cmd = f"{load}curl -fsSL {script_url} | bash -s -- --token-file {token_file}{cleanup}"
    wget_cmd = (
        f"wget -qO node-join.sh {script_url} && "
        f"{load}bash node-join.sh --token-file {token_file}{cleanup}"
    )
    return curl_cmd, wget_cmd


# bootstrap 下发所需的最小键面。全量生效配置含支付私钥、Harbor 机器人 Secret 等解密敏感项,
# 注册链路只允许这 9 个键出 service 层(防整体漏进响应/日志);镜像仓库三键只用于渲染
# registries.yaml 与落 CA,机器人 Secret 不出注册链路(拉取凭据经 imagePullSecrets 托管)。
_CLUSTER_CONFIG_KEYS = (
    "cluster_server_url",
    "cluster_join_token",
    "cluster_agent_version",
    "node_driver_version",
    "node_install_mirror",
    "node_registries_yaml",
    "registry_host",
    "registry_ca_pem",
    "registry_proxy_projects",
)


def _narrow_cluster_config(cfg: dict[str, str]) -> dict[str, str]:
    return {k: cfg.get(k, "") for k in _CLUSTER_CONFIG_KEYS}


async def require_cluster_config(session: AsyncSession) -> dict[str, str]:
    """创建注册令牌的前置:cluster 组必须已配置,否则 409 引导去平台配置页。"""
    cfg = await get_effective_platform_config(session)
    if not cfg.get("cluster_server_url") or not cfg.get("cluster_join_token"):
        raise AppError(
            ErrorCode.CONFLICT,
            key="nodes.clusterNotConfigured",
            http_status=http_status.HTTP_409_CONFLICT,
        )
    return _narrow_cluster_config(cfg)


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
        existing = await find_replay(
            session,
            NodeEnrollment,
            owner_col=NodeEnrollment.created_by,
            owner_id=created_by,
            key=idempotency_key,
        )
        if existing is not None:
            # 与 regenerate 同守卫:进行中的令牌被重放轮换会掐断正在装机的脚本
            if existing.status not in REGENERATABLE_STATUSES:
                raise AppError(
                    ErrorCode.CONFLICT,
                    key="nodes.regenerateNotAllowed",
                    params={"status": existing.status},
                    http_status=http_status.HTTP_409_CONFLICT,
                )
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
        # joined 已进入正式「节点」列表,不占用「待加入」视图
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
    """换新令牌:仅 pending/expired/failed —— 换令牌会掐断进行中的脚本。"""
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
    """期望态落台账 + outbox 入队:handler 读期望态而非 payload,
    乱序重试(cordon 失败退避 vs 后发 uncordon 成功)不会把旧意图盖回去。"""
    from app.core.outbox import enqueue
    from app.modules.nodes.models import NodeSpec

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


# ---------- 匿名侧(令牌即鉴权;统一 404 防探测) ----------


def _check_usable(row: NodeEnrollment | None) -> NodeEnrollment:
    """公共闸门:无效/终态/过期一律 404。过期为绝对截止(不随心跳续命);
    落 expired 由对账器(30s)清扫,请求路径只拒不迁移。"""
    if row is None or row.status in TERMINAL_STATUSES or row.expires_at < now_utc():
        raise not_found()
    return row


async def _resolve_by_hash(
    session: AsyncSession, column: InstrumentedAttribute[str | None], token: str
) -> NodeEnrollment | None:
    """按摘要取行(dual-read):先按 HMAC candidates(当前/legacy 世代与轮换 previous,
    见 crypto.py)一趟取;miss 再按裸 SHA-256(无密钥摘要迁移前旧行)取,命中即席升级为
    当前世代 HMAC 落库。令牌短 TTL,旧行自然过期后裸验分支可清理。"""
    row = (
        await session.execute(
            select(NodeEnrollment).where(column.in_(hash_node_token_candidates(token))).limit(1)
        )
    ).scalar_one_or_none()
    if row is not None:
        return row
    legacy = hashlib.sha256(token.encode()).hexdigest()
    row = (
        await session.execute(select(NodeEnrollment).where(column == legacy))
    ).scalar_one_or_none()
    if row is not None:
        # 即席升级:旧裸摘要行换当前世代 HMAC,不让无密钥摘要长期留在库里
        upgraded = hash_node_token(token)
        if column is NodeEnrollment.token_hash:
            row.token_hash = upgraded
        else:
            row.progress_token_hash = upgraded
        await session.flush()
    return row


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
) -> tuple[NodeEnrollment, dict[str, str], str]:
    """令牌换装机参数。返回 (enrollment, cluster 最小配置, progress 令牌)。

    注册令牌一次性:只有 pending 行能 bootstrap,首跑即消费(换发仅可上报进度的
    progress 令牌并迁 installing),此后任何令牌都不能再 bootstrap;重跑/重启续跑只用
    progress 令牌上报。
    """
    row = await _resolve_token(session, token)
    if row.status != "pending":
        raise not_found()
    if row.hostname != hostname:
        # 签发时已绑定期望主机名:上报不符即 failed,被盗令牌不能在别的机器换出 join token
        transition_enrollment(
            row, "failed", error=f"主机名不符:期望 {row.hostname},实际上报 {hostname}(防令牌串用)"
        )
        await session.commit()
        raise AppError(
            ErrorCode.CONFLICT,
            key="nodes.hostnameMismatch",
            http_status=http_status.HTTP_409_CONFLICT,
        )
    row.node_name = hostname
    row.reported_ip = client_ip
    row.os_info = os_info
    row.gpu_info = gpu_details
    row.last_report_at = now_utc()
    progress_token, row.progress_token_hash = _new_token(PROGRESS_TOKEN_PREFIX)
    transition_enrollment(row, "installing", phase="bootstrap")
    cfg = _narrow_cluster_config(await get_effective_platform_config(session))
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
        # 脚本只在驱动已加载的收尾上报带版本;并进登记快照,巡检据此填台账 driver/cuda 列
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
    """台账里「池 × canonical 型号」匹配的行,不看状态。

    上架硬校验、市场库存、创建软准入、管理端容量列/容量预览都只经这一处判「同一物理池」;
    wanted_model 为 None(未识别型号)恒不匹配。Ready 口径由调用方决定:
    ready_specs() 只取 Ready,容量预览/软准入还要看非 Ready 行(有节点但不可售)。
    """
    return [
        s for s in specs if s.pool_label == pool_label and model_matches(wanted_model, s.gpu_model)
    ]


def pool_specs(specs: Iterable[NodeSpec], pool_label: str) -> list[NodeSpec]:
    """台账里只按池匹配的行,不看型号也不看状态(CPU 档口径)。

    不复用 matching_specs(..., wanted_model=None):那里 None 表示「型号未识别」并
    刻意恒不匹配(显存/型号不确定不许卖卡)。CPU 规格根本不带型号,是另一件事,
    掺进同一个参数会把「未识别型号也放行」偷偷带给 GPU 路径。
    """
    return [s for s in specs if s.pool_label == pool_label]


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


# Harbor 自签/私有 CA 在节点上的落点:node-join 把 registry_ca_pem 写到 $RANCHER_DIR/harbor-ca.crt,
# 渲染时用占位符,脚本落盘时按本机发行版目录替换(服务端不猜 /etc/rancher/<rke2|k3s>)
REGISTRY_CA_PATH_TEMPLATE = "__RANCHER_DIR__/harbor-ca.crt"


def render_registries_yaml(cfg: dict[str, str]) -> str:
    """平台生成节点 registries.yaml(RKE2 / k3s 同格式),按平台配置·镜像仓库组:

    - `mirrors "*"`:Spegel P2P 覆盖全部仓库(含 Harbor);
    - `registry_proxy_projects` 每行 <上游>=<Harbor 代理项目>:该上游 mirror + rewrite 到 Harbor
      代理缓存项目,拉不到时 containerd 回落上游;
    - `registry_ca_pem` 非空:`configs.<host>.tls.ca_file` 指向 node-join 落盘的 CA。
    不含任何 auth:拉取凭据由平台托管为 imagePullSecrets(core/registry),节点不落凭据。
    `node_registries_yaml` 有值 = 高级覆盖,原样下发(明文落库,不得含凭据)。
    """
    override = (cfg.get("node_registries_yaml") or "").strip()
    if override:
        return override
    host = (cfg.get("registry_host") or "").strip()
    lines = ["mirrors:", '  "*": {}']
    if host:
        proxies = parse_proxy_projects(cfg.get("registry_proxy_projects") or "")
        for upstream, project in proxies.items():
            lines += [
                f"  {upstream}:",
                "    endpoint:",
                f'      - "https://{host}"',
                "    rewrite:",
                f'      "^(.*)$": "{project}/$1"',
            ]
        if (cfg.get("registry_ca_pem") or "").strip():
            lines += [
                "configs:",
                f'  "{host}":',
                "    tls:",
                f'      ca_file: "{REGISTRY_CA_PATH_TEMPLATE}"',
            ]
    return "\n".join(lines) + "\n"


async def derive_node_distro(session: AsyncSession, cfg: dict[str, str]) -> str:
    """装机发行版派生:探测缓存 > agent 版本后缀 > rke2。"""
    row = await get_cluster_status(session)
    if row and row.distro:
        return row.distro
    return derive_distro(cfg.get("cluster_agent_version")) or "rke2"


HAMI_GATE_MAX_AGE = timedelta(minutes=10)  # 能力缓存陈旧窗:超时视为未知,拒绝下发


async def _fresh_cluster_status(session: AsyncSession) -> ClusterStatus:
    """下发门禁共用的能力缓存读取:缺失/陈旧一律 409(巡检 60s 一轮,陈旧即 worker 停摆)。"""
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
    """shared 档下发门禁:调度器缺位即时报错,而非等 Pod Pending 超时。"""
    row = await _fresh_cluster_status(session)
    if not row.hami_ready:
        raise AppError(
            ErrorCode.CLUSTER_NOT_READY,
            key="nodes.clusterNotReady",
            http_status=http_status.HTTP_409_CONFLICT,
            detail={"reason": "hami_not_ready"},
        )


async def require_kata_runtimeclass(session: AsyncSession) -> None:
    """dedicated 档下发门禁:RuntimeClass kata-qemu 缺位即时 409。

    没有它,Pod 带着 runtimeClassName: kata-qemu 下发会被 kubelet 直接拒掉,
    用户侧表现为开机后几十秒转 failed(force 上架的 dedicated SKU 是唯一入口)。
    """
    row = await _fresh_cluster_status(session)
    if not row.kata_runtimeclass:
        raise AppError(
            ErrorCode.CLUSTER_NOT_READY,
            key="nodes.clusterNotReady",
            http_status=http_status.HTTP_409_CONFLICT,
            detail={"reason": "kata_runtimeclass_missing"},
        )


async def require_storage_classes(session: AsyncSession, *, with_data_disk: bool) -> None:
    """存储下发门禁:StorageClass 缺位即时 409,而非等 Pod Pending 超时。

    按名核对 ClusterStatus.storage_classes 里实例盘/数据盘各自的 SC。
    """
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
