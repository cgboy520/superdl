"""运营策略参数:env 默认值 + DB 覆盖,管理端在线调整免重启发版。"""

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
    # 每用户配额:校验链 用户级覆盖 → 本层 → env 默认(Settings 同名字段)
    "max_instances_per_user": ("int", Decimal(1), Decimal(1000)),
    "max_gpus_per_user": ("int", Decimal(1), Decimal(1024)),
    "max_vcpus_per_user": ("int", Decimal(1), Decimal(4096)),
    "max_disks_per_user": ("int", Decimal(1), Decimal(1000)),
    # 每个 GPU 节点最多让 CPU 实例吃掉多少 vCPU(近似库存口径,见 catalog/service)。
    # 0 = 不许 CPU 实例落 GPU 节点:pool != cpu 的 CPU SKU 一律判无容量。
    "gpu_node_cpu_instance_vcpu_cap": ("int", Decimal(0), Decimal(1024)),
    # 对外服务端点的边缘限流(每端点每秒请求数)。在网关本地桶生效,不回源平台
    "service_endpoint_rps": ("int", Decimal(1), Decimal(1000)),
    # 包周期折扣(百分数,80 = 8 折)。上界 100 = 不打折,不设 >100 的「加价」档
    "period_discount_day": ("int", Decimal(50), Decimal(100)),
    "period_discount_week": ("int", Decimal(50), Decimal(100)),
    "period_discount_month": ("int", Decimal(50), Decimal(100)),
    "period_discount_year": ("int", Decimal(50), Decimal(100)),
    # 包周期到期前多少天开始预警(短信 + 站内信,每天至多一条)
    "period_expire_warn_days": ("int", Decimal(1), Decimal(30)),
    # 竞价价 = 按量价 × pct/100。上界 90:竞价的对价是「可被回收」,必须让出折扣
    "spot_discount_pct": ("int", Decimal(10), Decimal(90)),
    # 抢占通知到真删 Pod 的宽限窗(秒)。与 creating_timeout_seconds 有时间预算耦合,
    # 调之前先读 docs/reference/limits.md 那一段
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
    service_endpoint_rps: int
    period_discount_day: int
    period_discount_week: int
    period_discount_month: int
    period_discount_year: int
    period_expire_warn_days: int
    spot_discount_pct: int
    spot_grace_seconds: int


# 抢占宽限窗之外必须留给「删 Pod → 释放卡 → 调度请求方 → 拉起」的时间余量(秒):
# 宽限窗吃掉整个 creating 超时预算时,请求方会在 victim 的 Pod 删完之前就超时转 failed
PREEMPT_TIME_RESERVE_SECONDS = 120


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
    if key == "spot_grace_seconds":
        # 跨键约束,静态区间表达不了:宽限窗必须给「删 Pod + 调度请求方」留够余量
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
    # 按 POLICY_SPECS 的类型列统一转换:新增策略键只需动 SPECS 与 dataclass 两处
    converted: dict[str, Any] = {
        k: (Decimal(v) if POLICY_SPECS[k][0] == "decimal" else int(v)) for k, v in eff.items()
    }
    return EffectivePolicies(**converted)


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
