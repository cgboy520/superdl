"""开发环境种子数据:SKU 四档 + 平台镜像 + 管理员。

用法(需 PG 已迁移):cd apps/api && uv run python scripts/seed_dev.py
幂等:已存在同名数据则跳过。
"""

import asyncio
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 允许 scripts/ 直跑

from sqlalchemy import select

from app.core.db import get_sessionmaker
from app.core.logging import setup_logging
from app.modules.adminapi.service import ensure_bootstrap_admin
from app.modules.catalog.models import PlatformImage, Sku

SKUS = [
    {
        "name": "RTX4090-FULL",
        "gpu_model": "RTX4090",
        "tier": "dedicated",
        "gpu_cores_pct": 100,
        "vram_gb": 24,
        "pool_label": "kata",
        "vcpu": 16,
        "mem_gb": 64,
        "price_hourly": Decimal("3.9900"),
        "max_gpus_per_instance": 8,
        "cuda_max": "12.8",
        "status": "on",
    },
    {
        "name": "H100-MIG-1g.10gb",
        "gpu_model": "H100",
        "tier": "mig",
        "mig_profile": "1g.10gb",
        "gpu_cores_pct": 100,
        "vram_gb": 10,
        "pool_label": "mig",
        "vcpu": 8,
        "mem_gb": 32,
        "price_hourly": Decimal("2.5000"),
        "max_gpus_per_instance": 1,
        "cuda_max": "12.8",
        "status": "on",
    },
    {
        "name": "RTX4090-STD50",
        "gpu_model": "RTX4090",
        "tier": "shared_std",
        "gpu_cores_pct": 50,
        "vram_gb": 8,
        "oversell_cores": Decimal("1.50"),
        "oversell_vram": Decimal("1.10"),
        "pool_label": "hami",
        "vcpu": 8,
        "mem_gb": 32,
        "price_hourly": Decimal("1.6800"),
        "max_gpus_per_instance": 1,
        "cuda_max": "12.8",
        "status": "on",
    },
    {
        "name": "RTX4090-ECO30",
        "gpu_model": "RTX4090",
        "tier": "shared_eco",
        "gpu_cores_pct": 30,
        "vram_gb": 7,
        "oversell_cores": Decimal("2.00"),
        "oversell_vram": Decimal("1.20"),
        "pool_label": "hami",
        "vcpu": 6,
        "mem_gb": 24,
        "price_hourly": Decimal("0.9900"),
        "max_gpus_per_instance": 1,
        "cuda_max": "12.8",
        "status": "on",
    },
]

IMAGES = [
    ("PyTorch", "2.9.0", "3.12", "12.8", "registry.superdl.local/pytorch:2.9.0-cu128"),
    ("PyTorch", "2.7.1", "3.11", "12.4", "registry.superdl.local/pytorch:2.7.1-cu124"),
    ("TensorFlow", "2.20", "3.12", "12.8", "registry.superdl.local/tensorflow:2.20-cu128"),
    ("Miniconda", "24.7", "3.12", "12.8", "registry.superdl.local/miniconda:24.7-cu128"),
]


async def main() -> None:
    setup_logging()
    sm = get_sessionmaker()
    async with sm() as session:
        for data in SKUS:
            exists = (
                await session.execute(select(Sku).where(Sku.name == data["name"]))
            ).scalar_one_or_none()
            if exists is None:
                session.add(Sku(**data))
        for fw, ver, py, cuda, ref in IMAGES:
            exists = (
                await session.execute(select(PlatformImage).where(PlatformImage.image_ref == ref))
            ).scalar_one_or_none()
            if exists is None:
                session.add(
                    PlatformImage(
                        framework=fw,
                        framework_version=ver,
                        python_version=py,
                        cuda_version=cuda,
                        image_ref=ref,
                    )
                )
        await session.commit()
        await ensure_bootstrap_admin(session, "admin123-dev")
    print("seed done: 4 SKU / 4 镜像 / admin(admin123-dev)")  # noqa: T201


if __name__ == "__main__":
    asyncio.run(main())
