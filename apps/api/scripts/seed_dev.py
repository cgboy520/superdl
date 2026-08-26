"""开发环境种子数据:SKU 四档 + 平台镜像 + 管理员。

用法(需 PG 已迁移):cd apps/api && uv run python scripts/seed_dev.py
幂等:已存在同名数据则跳过。
环境闸:仅 dev/test 可跑——本脚本直调 ensure_bootstrap_admin,绕过生产配置校验,
误指向生产库会创建弱/随机口令 admin,非 dev/test 一律拒绝执行。
管理员口令:默认 secrets 随机生成且仅本次打印;CI/演示需固定口令时显式设
SUPERDL_SEED_ADMIN_PASSWORD(CI 一次性隔离环境,弱口令可接受)。
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
    # image_ref 存 Harbor 全限定名(<host>/<项目>/<名>:<tag>);dev 用 Fake 编排不真拉取
    ("PyTorch", "2.9.0", "3.12", "12.8", "harbor.example.com/superdl/pytorch:2.9.0-cu128"),
    ("PyTorch", "2.7.1", "3.11", "12.4", "harbor.example.com/superdl/pytorch:2.7.1-cu124"),
    ("TensorFlow", "2.20", "3.12", "12.8", "harbor.example.com/superdl/tensorflow:2.20-cu128"),
    ("Miniconda", "24.7", "3.12", "12.8", "harbor.example.com/superdl/miniconda:24.7-cu128"),
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
        has_admin = (
            await session.execute(select(AdminUser.id).limit(1))
        ).scalar_one_or_none() is not None
        if has_admin:
            print("seed done: 4 SKU / 4 镜像 / admin 已存在(未改动)")  # noqa: T201
            return
        password = os.environ.get("SUPERDL_SEED_ADMIN_PASSWORD") or secrets.token_urlsafe(18)
        await ensure_bootstrap_admin(session, password)
        print(  # noqa: T201
            f"seed done: 4 SKU / 4 镜像 / admin({password})——口令仅本次显示,请立即保存"
        )


if __name__ == "__main__":
    asyncio.run(main())
