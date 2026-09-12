"""管理端只读聚合:运营总览、调账上下文、SKU 改价影响面。"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger, mask_phone_value
from app.core.money import money_str
from app.modules.account import service as account_service
from app.modules.billing import service as billing_service
from app.modules.nodes import service as nodes_service
from app.modules.orchestrator import queries as orchestrator_queries
from app.modules.orchestrator.schemas import NON_TERMINAL_STATUSES

logger = get_logger(__name__)


async def overview(session: AsyncSession) -> dict[str, Any]:
    """运营总览聚合:精确 COUNT 口径。

    实例分状态计数不含 released;付费租户 = ledger consume 全表聚合;租户总数取 active 用户;
    包周期在保数 = 未到期的订阅行数;节点/GPU 取台账全量(含 NotReady/Missing),
    竞价占用按台账 gpu_used 截断。
    """
    counted = await orchestrator_queries.count_instances_by_status(session)
    status_counts: dict[str, int] = {st: counted.get(st, 0) for st in NON_TERMINAL_STATUSES}

    consumed = await billing_service.consumed_by_user(session)
    active_user_ids = await account_service.list_active_user_ids(session)

    pools: dict[str, dict[str, int]] = {}
    nodes_ready = nodes_missing = 0
    specs = await nodes_service.list_node_specs(session)
    spot_by_pool = await orchestrator_queries.running_spot_gpus_by_pool(session)
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
        "subscriptions_active": len(
            await billing_service.reserved_subscription_instance_ids(session)
        ),
        "nodes_total": len(specs),
        "nodes_ready": nodes_ready,
        "nodes_missing": nodes_missing,
        "pools": [
            {
                "pool": name or "unlabeled",
                "gpu_total": p["gpu_total"],
                "gpu_used": p["gpu_used"],
                "gpu_spot_used": min(spot_by_pool.get(name, 0), p["gpu_used"]),
                "ready_gpu_total": p["ready"],
            }
            for name, p in sorted(pools.items())
        ],
    }


async def adjust_context(session: AsyncSession, user_id: int) -> dict[str, Any]:
    """调账前置上下文(只读):租户身份 + 当前余额 + 近 3 条流水。用户不存在 → 404。"""
    user = await account_service.get_user(session, user_id)
    balance = await billing_service.get_balance(session, user_id)
    recent = await billing_service.ledger_page(session, user_id, limit=3)
    running_by_user = await orchestrator_queries.list_running_instances_by_user(session)
    return {
        "user_id": user.id,
        "phone_masked": mask_phone_value(user.phone),
        "status": user.status,
        "balance": money_str(balance),
        "running_instances": len(running_by_user.get(user_id, [])),
        "recent_ledger": recent.items,
    }


async def sku_impact(session: AsyncSession, sku_id: int) -> dict[str, Any]:
    """改价影响面(只读):该 SKU 当前活跃(creating/starting/running)实例数/用户数/卡数。"""
    active = []
    for st in ("creating", "starting", "running"):
        active.extend(await orchestrator_queries.list_instances_by_status(session, st))
    mine = [i for i in active if i.sku_id == sku_id]
    return {
        "sku_id": sku_id,
        "active_instances": len(mine),
        "active_users": len({i.user_id for i in mine}),
        "active_gpus": sum(i.gpu_count for i in mine),
    }
