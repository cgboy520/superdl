"""运营策略参数:env 默认值 + DB 覆盖。"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from sqlalchemy import String, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import get_settings
from app.core.db import Base


class PolicyOverride(Base):
    __tablename__ = "policy_overrides"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


# key → (类型, 下限, 上限);默认值取 Settings 同名字段
POLICY_SPECS: dict[str, tuple[Literal["decimal", "int"], Decimal, Decimal]] = {
    "disk_price_gb_month": ("decimal", Decimal("0.0010"), Decimal("1.0000")),
    "disk_min_gb": ("int", Decimal(1), Decimal(1024)),
    "disk_max_gb": ("int", Decimal(10), Decimal(65536)),
    "disk_grace_days": ("int", Decimal(1), Decimal(365)),
    "disk_frozen_days": ("int", Decimal(1), Decimal(365)),
    "freeze_grace_hours": ("int", Decimal(1), Decimal(720)),
    "afford_cover_hours": ("int", Decimal(1), Decimal(24)),
    "prewarm_min_coverage_pct": ("int", Decimal(1), Decimal(100)),
    "prewarm_recheck_hours": ("int", Decimal(1), Decimal(168)),
    # 每用户配额:用户级覆盖 → 本层 → env 默认
    "max_instances_per_user": ("int", Decimal(1), Decimal(1000)),
    "max_gpus_per_user": ("int", Decimal(1), Decimal(1024)),
    "max_vcpus_per_user": ("int", Decimal(1), Decimal(4096)),
    "max_disks_per_user": ("int", Decimal(1), Decimal(1000)),
    # 每个 GPU 节点让给 CPU 实例的 vCPU 上限(catalog/service);0 = 不许 CPU 实例落 GPU 节点
    "gpu_node_cpu_instance_vcpu_cap": ("int", Decimal(0), Decimal(1024)),
    # 包周期折扣(百分数,80 = 8 折;100 = 不打折)
    "period_discount_day": ("int", Decimal(50), Decimal(100)),
    "period_discount_week": ("int", Decimal(50), Decimal(100)),
    "period_discount_month": ("int", Decimal(50), Decimal(100)),
    "period_discount_year": ("int", Decimal(50), Decimal(100)),
    # 包周期到期前预警天数(每个到期时刻至多一条)
    "period_expire_warn_days": ("int", Decimal(1), Decimal(30)),
    # 竞价价 = 按量价 × pct/100(上界 90)
    "spot_discount_pct": ("int", Decimal(10), Decimal(90)),
    # 抢占通知到真删 Pod 的宽限窗(秒);与 creating_timeout_seconds 耦合,见 docs/reference/limits.md
    "spot_grace_seconds": ("int", Decimal(30), Decimal(600)),
}


@dataclass(frozen=True)
class EffectivePolicies:
    disk_price_gb_month: Decimal
    disk_min_gb: int
    disk_max_gb: int
    disk_grace_days: int
    disk_frozen_days: int
    freeze_grace_hours: int
    afford_cover_hours: int
    prewarm_min_coverage_pct: int
    prewarm_recheck_hours: int
    max_instances_per_user: int
    max_gpus_per_user: int
    max_vcpus_per_user: int
    max_disks_per_user: int
    gpu_node_cpu_instance_vcpu_cap: int
    period_discount_day: int
    period_discount_week: int
    period_discount_month: int
    period_discount_year: int
    period_expire_warn_days: int
    spot_discount_pct: int
    spot_grace_seconds: int


# creating 超时预算里宽限窗之外须留给「删 Pod → 释放卡 → 调度 → 拉起」的余量(秒)
PREEMPT_TIME_RESERVE_SECONDS = 120


def validate_policy_value(key: str, value: str) -> str:
    """校验并归一化;未知键或越界抛 ValueError。"""
    spec = POLICY_SPECS.get(key)
    if spec is None:
        raise ValueError(f"未知策略键:{key}")
    kind, lo, hi = spec
    try:
        num = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{key} 不是合法数字:{value}") from exc
    if kind == "int" and num != num.to_integral_value():
        raise ValueError(f"{key} 须为整数:{value}")
    if not lo <= num <= hi:
        raise ValueError(f"{key} 取值须在 {lo}~{hi} 之间")
    if key == "spot_grace_seconds":
        # 跨键约束:宽限窗 + 余量 ≤ creating 超时
        budget = get_settings().creating_timeout_seconds - PREEMPT_TIME_RESERVE_SECONDS
        if num > budget:
            raise ValueError(
                f"spot_grace_seconds 不得超过 {budget} 秒"
                f"(creating 超时 {get_settings().creating_timeout_seconds}s 减去"
                f" {PREEMPT_TIME_RESERVE_SECONDS}s 调度余量)"
            )
    return str(int(num)) if kind == "int" else str(num)


async def get_effective_policies(session: AsyncSession) -> EffectivePolicies:
    settings = get_settings()
    eff: dict[str, str] = {k: str(getattr(settings, k)) for k in POLICY_SPECS}
    for row in (await session.execute(select(PolicyOverride))).scalars():
        if row.key in eff:
            eff[row.key] = row.value
    # 按 POLICY_SPECS 的类型列转换
    converted: dict[str, Any] = {
        k: (Decimal(v) if POLICY_SPECS[k][0] == "decimal" else int(v)) for k, v in eff.items()
    }
    return EffectivePolicies(**converted)


async def set_policy_overrides(session: AsyncSession, updates: dict[str, str]) -> None:
    """写覆盖(不 commit)。"""
    for key, raw in updates.items():
        value = validate_policy_value(key, raw)
        await session.execute(
            pg_insert(PolicyOverride)
            .values(key=key, value=value)
            .on_conflict_do_update(index_elements=["key"], set_={"value": value})
        )


async def list_policy_overrides(session: AsyncSession) -> dict[str, str]:
    return {r.key: r.value for r in (await session.execute(select(PolicyOverride))).scalars()}
