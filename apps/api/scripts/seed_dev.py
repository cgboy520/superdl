"""dev 种子:五条 SKU(整卡 / MIG / HAMi 两档 / 纯 CPU)+ 平台镜像 + 管理员。幂等;仅 dev/test 可跑
(prod 用 scripts/bootstrap_admin.py)。管理员口令随机生成只打印一次,或取 SUPERDL_SEED_ADMIN_PASSWORD

用法:cd apps/api && uv run python scripts/seed_dev.py
"""

import asyncio
import os
import secrets
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 允许 scripts/ 直跑

from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.core.logging import setup_logging
from app.modules.adminapi.models import AdminUser
from app.modules.adminapi.service import ensure_bootstrap_admin
from app.modules.catalog.models import PlatformImage, Sku

# dev 种子全部 SKU 开竞价档(生产默认关,见 catalog/models.spot_enabled)
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
        "cuda_max": "13.2",
        "spot_enabled": True,
        "status": "on",
    },
    {
        "name": "H100-MIG-1g.10gb",
        "gpu_model": "H100",
        "tier": "shared",
        "mig_profile": "1g.10gb",
        "gpu_cores_pct": 100,
        "vram_gb": 10,
        "pool_label": "mig",
        "vcpu": 8,
        "mem_gb": 32,
        "price_hourly": Decimal("2.5000"),
        "max_gpus_per_instance": 1,
        "cuda_max": "13.2",
        "spot_enabled": True,
        "status": "on",
    },
    {
        "name": "RTX4090-SHARED50",
        "gpu_model": "RTX4090",
        "tier": "shared",
        "gpu_cores_pct": 50,
        "vram_gb": 8,
        "oversell_cores": Decimal("1.50"),
        "pool_label": "hami",
        "vcpu": 8,
        "mem_gb": 32,
        "price_hourly": Decimal("1.6800"),
        "max_gpus_per_instance": 1,
        "cuda_max": "13.2",
        "spot_enabled": True,
        "status": "on",
    },
    {
        # 纯 CPU 规格:gpu_model 空串,算力/显存/卡数全 0,price_hourly 为整机时价
        "name": "CPU-8C16G",
        "gpu_model": "",
        "tier": "cpu",
        "gpu_cores_pct": 0,
        "vram_gb": 0,
        "pool_label": "cpu",
        "vcpu": 8,
        "mem_gb": 16,
        "price_hourly": Decimal("0.4900"),
        "max_gpus_per_instance": 0,
        "cuda_max": None,
        "spot_enabled": True,
        "status": "on",
    },
    {
        "name": "RTX4090-SHARED30",
        "gpu_model": "RTX4090",
        "tier": "shared",
        "gpu_cores_pct": 30,
        "vram_gb": 7,
        "oversell_cores": Decimal("2.00"),
        "pool_label": "hami",
        "vcpu": 6,
        "mem_gb": 24,
        "price_hourly": Decimal("0.9900"),
        "max_gpus_per_instance": 1,
        "cuda_max": "13.2",
        "spot_enabled": True,
        "status": "on",
    },
]

IMAGES = [
    # 平台默认镜像目录(见 deploy/instance-images/README.md);dev 只写 tag,生产 image_ref 须钉 digest
    # (framework, framework_version, python, cuda, image_ref, sort)
    (
        "PyTorch",
        "2.13.0",
        "3.13",
        "13.2",
        "harbor.example.com/superdl/pytorch:2.13.0-cu132-py313",
        0,
    ),
    (
        "PyTorch",
        "2.13.0",
        "3.13",
        "12.9",
        "harbor.example.com/superdl/pytorch:2.13.0-cu129-py313",
        1,
    ),
    ("PyTorch", "2.7.1", "3.13", "11.8", "harbor.example.com/superdl/pytorch:2.7.1-cu118-py313", 2),
    (
        "TensorFlow",
        "2.21.0",
        "3.13",
        "12.9",
        "harbor.example.com/superdl/tensorflow:2.21.0-cu129-py313",
        0,
    ),
    (
        "TensorFlow",
        "2.14.1",
        "3.11",
        "11.8",
        "harbor.example.com/superdl/tensorflow:2.14.1-cu118-py311",
        1,
    ),
    (
        "Miniconda",
        "26.5.3",
        "3.13",
        "13.2",
        "harbor.example.com/superdl/miniconda:26.5.3-cu132-py313",
        0,
    ),
    (
        "Miniconda",
        "26.5.3",
        "3.13",
        "12.9",
        "harbor.example.com/superdl/miniconda:26.5.3-cu129-py313",
        1,
    ),
    (
        "Miniconda",
        "26.5.3",
        "3.13",
        "11.8",
        "harbor.example.com/superdl/miniconda:26.5.3-cu118-py313",
        2,
    ),
    (
        "DataScience",
        "2026.08",
        "3.13",
        "CPU",
        "harbor.example.com/superdl/datascience:2026.08-py313",
        0,
    ),
    (
        "PaddlePaddle",
        "3.3.1",
        "3.10",
        "13.0",
        "harbor.example.com/superdl/paddle:3.3.1-cu130-py310",
        0,
    ),
    (
        "PaddlePaddle",
        "3.3.1",
        "3.10",
        "12.9",
        "harbor.example.com/superdl/paddle:3.3.1-cu129-py310",
        1,
    ),
    (
        "PaddlePaddle",
        "3.3.1",
        "3.10",
        "11.8",
        "harbor.example.com/superdl/paddle:3.3.1-cu118-py310",
        2,
    ),
]


async def main() -> None:
    setup_logging()
    settings = get_settings()
    if settings.environment not in ("dev", "test"):
        print(  # noqa: T201
            "refused: seed_dev 仅允许 dev/test 环境"
            f"(当前 SUPERDL_ENVIRONMENT={settings.environment})",
            file=sys.stderr,
        )
        sys.exit(1)
    sm = get_sessionmaker()
    async with sm() as session:
        for data in SKUS:
            exists = (
                await session.execute(select(Sku).where(Sku.name == data["name"]))
            ).scalar_one_or_none()
            if exists is None:
                session.add(Sku(**data))
        for fw, ver, py, cuda, ref, sort in IMAGES:
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
                        sort=sort,
                    )
                )
        await session.commit()
        has_admin = (
            await session.execute(select(AdminUser.id).limit(1))
        ).scalar_one_or_none() is not None
        if has_admin:
            print(  # noqa: T201
                f"seed done: {len(SKUS)} SKU / {len(IMAGES)} 镜像 / admin 已存在(未改动)"
            )
            return
        password = os.environ.get("SUPERDL_SEED_ADMIN_PASSWORD") or secrets.token_urlsafe(18)
        await ensure_bootstrap_admin(session, password)
        print(  # noqa: T201
            f"seed done: {len(SKUS)} SKU / {len(IMAGES)} 镜像 / "
            f"admin({password})——口令仅本次显示,请立即保存"
        )


if __name__ == "__main__":
    asyncio.run(main())
