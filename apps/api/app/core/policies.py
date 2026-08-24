"""运营策略参数:env 默认值 + DB 覆盖,管理端在线调整免重启发版。"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Literal

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
    "low_balance_warn_hours": ("int", Decimal(1), Decimal(168)),
    "afford_cover_hours": ("int", Decimal(1), Decimal(24)),
    "prewarm_min_coverage_pct": ("int", Decimal(1), Decimal(100)),
    "prewarm_recheck_hours": ("int", Decimal(1), Decimal(168)),
    # 每用户配额(F9):校验链 用户级覆盖 → 本层 → env 默认(Settings 同名字段)
    "max_instances_per_user": ("int", Decimal(1), Decimal(1000)),
    "max_gpus_per_user": ("int", Decimal(1), Decimal(1024)),
    "max_disks_per_user": ("int", Decimal(1), Decimal(1000)),
}


@dataclass(frozen=True)
class EffectivePolicies:
    disk_price_gb_month: Decimal
    disk_min_gb: int
    disk_max_gb: int
    disk_grace_days: int
    disk_frozen_days: int
    freeze_grace_hours: int
    low_balance_warn_hours: int
    afford_cover_hours: int
    prewarm_min_coverage_pct: int
    prewarm_recheck_hours: int
    max_instances_per_user: int
    max_gpus_per_user: int
    max_disks_per_user: int


def validate_policy_value(key: str, value: str) -> str:
    """校验并归一化。未知键或越界抛 ValueError(调用方转 AppError)。"""
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
    return str(int(num)) if kind == "int" else str(num)


async def get_effective_policies(session: AsyncSession) -> EffectivePolicies:
    settings = get_settings()
    eff: dict[str, str] = {k: str(getattr(settings, k)) for k in POLICY_SPECS}
    for row in (await session.execute(select(PolicyOverride))).scalars():
        if row.key in eff:
            eff[row.key] = row.value
    return EffectivePolicies(
        disk_price_gb_month=Decimal(eff["disk_price_gb_month"]),
        disk_min_gb=int(eff["disk_min_gb"]),
        disk_max_gb=int(eff["disk_max_gb"]),
        disk_grace_days=int(eff["disk_grace_days"]),
        disk_frozen_days=int(eff["disk_frozen_days"]),
        freeze_grace_hours=int(eff["freeze_grace_hours"]),
        low_balance_warn_hours=int(eff["low_balance_warn_hours"]),
        afford_cover_hours=int(eff["afford_cover_hours"]),
        prewarm_min_coverage_pct=int(eff["prewarm_min_coverage_pct"]),
        prewarm_recheck_hours=int(eff["prewarm_recheck_hours"]),
        max_instances_per_user=int(eff["max_instances_per_user"]),
        max_gpus_per_user=int(eff["max_gpus_per_user"]),
        max_disks_per_user=int(eff["max_disks_per_user"]),
    )


async def set_policy_overrides(session: AsyncSession, updates: dict[str, str]) -> None:
    """写覆盖(不 commit,由调用方与审计同事务提交)。"""
    for key, raw in updates.items():
        value = validate_policy_value(key, raw)
        await session.execute(
            pg_insert(PolicyOverride)
            .values(key=key, value=value)
            .on_conflict_do_update(index_elements=["key"], set_={"value": value})
        )


async def list_policy_overrides(session: AsyncSession) -> dict[str, str]:
    return {r.key: r.value for r in (await session.execute(select(PolicyOverride))).scalars()}
