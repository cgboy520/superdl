from decimal import Decimal
from typing import Any

from fastapi import status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_sessionmaker
from app.core.errors import AppError, ErrorCode, not_found
from app.core.logging import get_logger
from app.core.ratelimit import RateLimitCounter, check_rate_limit
from app.core.security import (
    create_token,
    hash_password,
    hash_password_sync,
    verify_password,
)
from app.modules.adminapi.models import AdminUser

logger = get_logger(__name__)

# 不存在的用户名也走一次哈希校验,拉平时间侧信道
_DUMMY_HASH = hash_password_sync("dummy-timing-equalizer")

LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 300.0
# 纯 IP 桶(只计失败):换用户名不换桶,兜住遍历账号的口令喷洒;
# 阈值放宽到 30/时,别误伤办公网 NAT 出口共享同一 IP 的多名管理员
LOGIN_IP_MAX_ATTEMPTS = 30
LOGIN_IP_WINDOW_SECONDS = 3600.0

# 与 AdminCreateRequest.password 的 min_length 对齐(引导口令不经 schema,需自查)
PASSWORD_MIN_LENGTH = 12
# bcrypt 上限 72 字节;schema 的 max_length 按字符计,多字节口令会绕过
PASSWORD_MAX_BYTES = 72

# 单笔调账绝对值上限:超出走对公/线下流程,不进双人复核(防手滑多敲零)
ADJUST_MAX_ABS = Decimal("100000.00")

# 总览「实例分状态计数」覆盖的非终态(released 历史行无界,不装全表)。
# 口径唯一事实源是 orchestrator/statemachine.py;模块边界只放行 service/schemas,此处按值对齐。
OVERVIEW_INSTANCE_STATUSES = (
    "creating",
    "starting",
    "running",
    "stopping",
    "stopped",
    "frozen",
    "releasing",
    "failed",
)


def _check_password_bytes(password: str) -> None:
    """哈希前按字节数拦截超长口令,否则 bcrypt 5.x 在哈希层抛 ValueError 变 500。"""
    if len(password.encode()) > PASSWORD_MAX_BYTES:
        raise AppError(ErrorCode.VALIDATION_ERROR, key="common.validation")


async def _clear_login_failures(key: str) -> None:
    """登录成功清零该桶的失败计数(独立事务,不随业务 session 回滚)。"""
    async with get_sessionmaker()() as session:
        await session.execute(delete(RateLimitCounter).where(RateLimitCounter.key == key))
        await session.commit()


async def login(
    session: AsyncSession, username: str, password: str, *, client_ip: str | None = None
) -> tuple[str, AdminUser]:
    admin = (
        await session.execute(select(AdminUser).where(AdminUser.username == username))
    ).scalar_one_or_none()
    password_ok = await verify_password(password, admin.password_hash if admin else _DUMMY_HASH)
    if admin is None or not password_ok:
        # 只在失败后计数:成功登录不消耗配额(此前连成功也计数,连登 5 次即被 429)
        await check_rate_limit(
            f"admin-login-ip:{client_ip or '-'}",
            max_attempts=LOGIN_IP_MAX_ATTEMPTS,
            window_seconds=LOGIN_IP_WINDOW_SECONDS,
        )
        await check_rate_limit(
            f"admin-login:{client_ip or '-'}:{username}",
            max_attempts=LOGIN_MAX_ATTEMPTS,
            window_seconds=LOGIN_WINDOW_SECONDS,
        )
        logger.warning("admin_login_failed", username=username, ip=client_ip)
        raise AppError(ErrorCode.LOGIN_FAILED, key="adminapi.loginFailed")
    if admin.status != "active":
        raise AppError(
            ErrorCode.USER_FROZEN,
            key="adminapi.userDisabled",
            http_status=status.HTTP_403_FORBIDDEN,
        )
    # 凭据正确即清零该账号桶的失败计数(IP 桶不清:口令喷洒不会产生成功登录)
    await _clear_login_failures(f"admin-login:{client_ip or '-'}:{username}")
    token = create_token(
        str(admin.id), "admin", token_type="access", extra={"ver": admin.token_version}
    )
    return token, admin


async def create_admin(session: AsyncSession, username: str, password: str, role: str) -> AdminUser:
    from sqlalchemy.exc import IntegrityError

    _check_password_bytes(password)
    admin = AdminUser(username=username, password_hash=await hash_password(password), role=role)
    session.add(admin)
    try:
        await session.commit()
    except IntegrityError as exc:  # username 唯一
        await session.rollback()
        raise AppError(
            ErrorCode.CONFLICT,
            key="adminapi.adminUsernameTaken",
            http_status=status.HTTP_409_CONFLICT,
        ) from exc
    await session.refresh(admin)
    return admin


async def ensure_bootstrap_admin(session: AsyncSession, password: str) -> None:
    """启动引导:**表为空时**创建首个 admin 账号,之后自动失效。"""
    existing = (await session.execute(select(AdminUser).limit(1))).scalar_one_or_none()
    if existing is not None:
        return
    # 引导口令不经请求 schema,长度须与 AdminCreateRequest 同标准;不合规宁可启动失败
    if len(password) < PASSWORD_MIN_LENGTH or len(password.encode()) > PASSWORD_MAX_BYTES:
        raise RuntimeError(
            f"引导口令不合规:须 ≥{PASSWORD_MIN_LENGTH} 字符且 UTF-8 编码后 "
            f"≤{PASSWORD_MAX_BYTES} 字节,请修正 SUPERDL_BOOTSTRAP_ADMIN_PASSWORD"
        )
    await create_admin(session, "admin", password, "admin")
    logger.warning(
        "bootstrap_admin_created",
        username="admin",
        hint="首个管理员已创建;请立即登录改密并建出第二个 admin(调账双人复核需要两个人)",
    )


async def list_admins(session: AsyncSession) -> list[AdminUser]:
    return list((await session.execute(select(AdminUser).order_by(AdminUser.id))).scalars().all())


async def _get_admin(session: AsyncSession, admin_id: int) -> AdminUser:
    admin = await session.get(AdminUser, admin_id, with_for_update=True)
    if admin is None:
        raise not_found()
    return admin


async def update_admin(
    session: AsyncSession,
    admin_id: int,
    *,
    role: str | None,
    new_status: str | None,
    actor_id: int,
) -> tuple[AdminUser, dict[str, Any]]:
    """改角色/停用。返回 (账号, 旧值快照),旧值交调用方落审计。"""
    admin = await _get_admin(session, admin_id)
    if admin.id == actor_id and (new_status == "disabled" or (role and role != admin.role)):
        # 禁止自停用/自降权
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            key="adminapi.cannotChangeSelf",
            http_status=status.HTTP_409_CONFLICT,
        )
    before: dict[str, Any] = {}
    if role is not None and role != admin.role:
        before["role"] = admin.role
        admin.role = role
    if new_status is not None and new_status != admin.status:
        before["status"] = admin.status
        admin.status = new_status
    if before:
        # 角色变更/停用立即失效已签发的 token
        admin.token_version += 1
    await session.commit()
    await session.refresh(admin)
    return admin, before


async def reset_admin_password(session: AsyncSession, admin_id: int, password: str) -> AdminUser:
    admin = await _get_admin(session, admin_id)
    _check_password_bytes(password)
    admin.password_hash = await hash_password(password)
    admin.token_version += 1  # 改密即踢掉全部在外会话
    await session.commit()
    await session.refresh(admin)
    return admin


async def change_own_password(
    session: AsyncSession, admin_id: int, current_password: str, new_password: str
) -> None:
    admin = await _get_admin(session, admin_id)
    if not await verify_password(current_password, admin.password_hash):
        raise AppError(ErrorCode.LOGIN_FAILED, key="adminapi.loginFailed")
    _check_password_bytes(new_password)
    admin.password_hash = await hash_password(new_password)
    admin.token_version += 1
    await session.commit()


async def create_adjustment(
    session: AsyncSession,
    *,
    user_id: int,
    amount,
    reason: str,
    created_by: int,
    idempotency_key: str | None = None,
):
    from sqlalchemy.exc import IntegrityError

    from app.core.errors import AppError, ErrorCode
    from app.core.money import as_amount
    from app.modules.account import service as account_service
    from app.modules.adminapi.models import AdminAdjustment

    if idempotency_key:
        existing = (
            await session.execute(
                select(AdminAdjustment).where(
                    AdminAdjustment.created_by == created_by,
                    AdminAdjustment.idempotency_key == idempotency_key,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing  # 幂等重放:返回已受理的调账单,不重复开单

    # 用户必须存在:否则复核通过时 wallet 会为幽灵 user_id 凭空建钱包并入账
    await account_service.get_user(session, user_id)
    amount = as_amount(Decimal(str(amount)))
    if amount == 0:
        raise AppError(ErrorCode.VALIDATION_ERROR, key="adminapi.adjustNotZero")
    if abs(amount) > ADJUST_MAX_ABS:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            key="common.validation",
            detail={"field": "amount", "max_abs": format(ADJUST_MAX_ABS, "f")},
        )
    adj = AdminAdjustment(
        user_id=user_id,
        amount=amount,
        reason=reason,
        created_by=created_by,
        idempotency_key=idempotency_key,
    )
    session.add(adj)
    try:
        await session.commit()
    except IntegrityError:
        # 并发同幂等键:唯一约束兜底,回滚后回查胜出方按重放返回(参照充值订单同款写法)
        await session.rollback()
        if idempotency_key is None:
            raise  # 无幂等键不会撞 (created_by, idempotency_key) 约束,原样上抛
        raced = (
            await session.execute(
                select(AdminAdjustment).where(
                    AdminAdjustment.created_by == created_by,
                    AdminAdjustment.idempotency_key == idempotency_key,
                )
            )
        ).scalar_one_or_none()
        if raced is None:
            raise  # 撞的是别的约束(理论不到达),原样上抛
        return raced
    await session.refresh(adj)
    return adj


async def review_adjustment(
    session: AsyncSession,
    adjustment_id: int,
    *,
    approve: bool,
    reviewer_id: int,
    comment: str | None,
):
    """双人复核:复核人不得是发起人,且须为调账发起前已存在的账号;通过即生效(钱包+流水,同事务)。"""
    from app.core.errors import AppError, ErrorCode, not_found
    from app.core.timeutil import ensure_utc, now_utc
    from app.modules.adminapi.models import AdminAdjustment
    from app.modules.billing import service as billing_service

    # 行锁:并发复核时后到者等锁,看到非 pending 即 409
    adj = await session.get(AdminAdjustment, adjustment_id, with_for_update=True)
    if adj is None:
        raise not_found()
    if adj.status != "pending":
        raise AppError(ErrorCode.CONFLICT, key="adminapi.adjustAlreadyProcessed", http_status=409)
    if adj.created_by == reviewer_id:
        raise AppError(
            ErrorCode.ADMIN_SECOND_REVIEW_REQUIRED,
            key="adminapi.adjustSecondReviewer",
            http_status=403,
        )
    reviewer = await session.get(AdminUser, reviewer_id)
    if reviewer is None or ensure_utc(reviewer.created_at) >= ensure_utc(adj.created_at):
        # 防自建第二账号绕复核:发起后才创建的账号不构成独立的第二人
        raise AppError(
            ErrorCode.ADMIN_SECOND_REVIEW_REQUIRED,
            key="adminapi.adjustReviewerTooNew",
            http_status=403,
        )
    adj.reviewed_by = reviewer_id
    adj.review_comment = comment
    adj.reviewed_at = now_utc()
    if not approve:
        adj.status = "rejected"
        await session.commit()
        return adj
    adj.status = "approved"
    if adj.amount > 0:
        await billing_service.credit(
            session,
            adj.user_id,
            adj.amount,
            type_="adjust",
            ref_type="adjustment",
            ref_id=str(adj.id),
            remark=f"调账:{adj.reason}",
        )
    else:
        await billing_service.debit(
            session,
            adj.user_id,
            -adj.amount,
            type_="adjust",
            ref_type="adjustment",
            ref_id=str(adj.id),
            remark=f"调账:{adj.reason}",
            allow_negative=True,  # 冲正金额不受当前余额封顶
        )
    await session.commit()
    return adj


async def list_adjustments(session: AsyncSession):
    from app.modules.adminapi.models import AdminAdjustment

    return list(
        (
            await session.execute(
                # 固定截断,与 admin 的 LIST_CAPS.adjustments 对齐
                select(AdminAdjustment).order_by(AdminAdjustment.id.desc()).limit(200)
            )
        ).scalars()
    )


# ---------- 只读聚合(总览/调账上下文/改价影响面;均不碰写路径) ----------


async def overview(session: AsyncSession) -> dict[str, Any]:
    """运营总览聚合:精确 COUNT 口径,替代前端在截断列表里数数的做法。

    组成受模块边界约束(只许调对方 service):
    - 实例分状态计数:list_instances_by_status 逐状态装载计数;released 终态不统计
      (历史行无界)。规模上来后应下沉为 orchestrator 的 count 聚合函数。
    - 付费租户:ledger consume 全表聚合精确计数;租户总数取 active 用户口径。
    - 节点/GPU:台账全量(含 NotReady/Missing,前端据此画非 Ready 段)。
    """
    from app.modules.account import service as account_service
    from app.modules.billing import service as billing_service
    from app.modules.nodes import service as nodes_service
    from app.modules.orchestrator import service as orchestrator_service

    status_counts: dict[str, int] = {}
    for st in OVERVIEW_INSTANCE_STATUSES:
        status_counts[st] = len(await orchestrator_service.list_instances_by_status(session, st))

    consumed = await billing_service.consumed_by_user(session)
    active_user_ids = await account_service.list_active_user_ids(session)

    pools: dict[str, dict[str, int]] = {}
    nodes_ready = nodes_missing = 0
    specs = await nodes_service.list_node_specs(session)
    for r in specs:
        if r.status == "Ready":
            nodes_ready += 1
        elif r.status == "Missing":
            nodes_missing += 1
        pool = pools.setdefault(r.pool_label or "", {"gpu_total": 0, "gpu_used": 0, "ready": 0})
        pool["gpu_total"] += r.gpu_count
        pool["gpu_used"] += r.gpu_used
        if r.status == "Ready":
            pool["ready"] += r.gpu_count

    return {
        "instances_by_status": status_counts,
        "tenants_total": len(active_user_ids),
        "paying_tenants": sum(1 for v in consumed.values() if v > 0),
        "nodes_total": len(specs),
        "nodes_ready": nodes_ready,
        "nodes_missing": nodes_missing,
        "pools": [
            {
                "pool": name or "unlabeled",
                "gpu_total": p["gpu_total"],
                "gpu_used": p["gpu_used"],
                "ready_gpu_total": p["ready"],
            }
            for name, p in sorted(pools.items())
        ],
    }


async def adjust_context(session: AsyncSession, user_id: int) -> dict[str, Any]:
    """调账前置上下文(只读):确认租户身份 + 当前余额 + 近 3 条流水。用户不存在 → 404。"""
    from app.modules.account import service as account_service
    from app.modules.billing import service as billing_service
    from app.modules.orchestrator import service as orchestrator_service

    user = await account_service.get_user(session, user_id)
    balance = await billing_service.get_balance(session, user_id)
    recent = await billing_service.ledger_page(session, user_id, limit=3)
    running_by_user = await orchestrator_service.list_running_instances_by_user(session)
    return {
        "user_id": user.id,
        "phone_masked": user.phone[:3] + "****" + user.phone[-4:],
        "status": user.status,
        "balance": format(balance, "f"),
        "running_instances": len(running_by_user.get(user_id, [])),
        "recent_ledger": recent.items,
    }


async def sku_impact(session: AsyncSession, sku_id: int) -> dict[str, Any]:
    """改价影响面(只读):该 SKU 当前活跃(creating/starting/running)实例数/用户数/卡数。"""
    from app.modules.orchestrator import service as orchestrator_service

    active = []
    for st in ("creating", "starting", "running"):
        active.extend(await orchestrator_service.list_instances_by_status(session, st))
    mine = [i for i in active if i.sku_id == sku_id]
    return {
        "sku_id": sku_id,
        "active_instances": len(mine),
        "active_users": len({i.user_id for i in mine}),
        "active_gpus": sum(i.gpu_count for i in mine),
    }
