from datetime import datetime
from decimal import Decimal

from sqlalchemy import ForeignKey, Index, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Sku(Base):
    """商品规格。超卖参数是 SKU 属性;变更仅影响新实例(实例落库时快照)。"""

    __tablename__ = "skus"
    # 业务唯一键:同一 (型号, 档位, 池, MIG 切片, 算力份额, vCPU, 内存) 只允许一条,
    # mig_profile 为空也算相等(NULLS NOT DISTINCT),防并发/重试建出同义 SKU 把库存口径搅浑。
    # 带上池是因为「共享」档同名却可能落 mig(硬切分)或 hami(软切分)两种池;带上
    # vCPU/内存是为了让同一张卡能出不同配套规格(纯 CPU 规格更是只能靠这两列区分)。
    # 用唯一索引而非 UniqueConstraint:后者的 ADD CONSTRAINT 要全表校验锁,
    # 唯一索引可以 CONCURRENTLY 在线建(见 scripts/check-migration-ddl.py)。
    __table_args__ = (
        Index(
            "uq_skus_business_key",
            "gpu_model",
            "tier",
            "pool_label",
            "mig_profile",
            "gpu_cores_pct",
            "vcpu",
            "mem_gb",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    gpu_model: Mapped[str] = mapped_column(String(32), index=True)  # e.g. RTX4090 / A100
    # dedicated(专用整卡)/ shared(共享切分);标准 vs 经济由 pool_label 派生
    # (mig 池 = 硬切分标准档,hami 池 = 软切分经济档),不再单列枚举值
    tier: Mapped[str] = mapped_column(String(16), index=True)
    mig_profile: Mapped[str | None] = mapped_column(String(32))  # e.g. 1g.10gb(仅 mig 档)
    gpu_cores_pct: Mapped[int] = mapped_column(default=100)  # 算力份额 %(共享档 <100)
    vram_gb: Mapped[int]  # 每实例显存配额
    oversell_cores: Mapped[Decimal] = mapped_column(Numeric(4, 2), default=Decimal("1.00"))
    oversell_vram: Mapped[Decimal] = mapped_column(Numeric(4, 2), default=Decimal("1.00"))
    pool_label: Mapped[str] = mapped_column(String(32))  # 节点池:kata / hami / mig
    vcpu: Mapped[int]
    mem_gb: Mapped[int]
    disk_gb: Mapped[int] = mapped_column(default=100)  # 实例盘(含 100G 免费)
    price_hourly: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    max_gpus_per_instance: Mapped[int] = mapped_column(default=1)
    cuda_max: Mapped[str | None] = mapped_column(String(16))  # 支持的最高 CUDA 版本
    status: Mapped[str] = mapped_column(String(8), default="off", index=True)  # on / off
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    # 变更时间,onupdate 自动刷新;无业务读取,留作审计线索
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class PlatformImage(Base):
    """平台镜像目录:框架→版本→Python→CUDA 级联的数据源。"""

    __tablename__ = "images"

    id: Mapped[int] = mapped_column(primary_key=True)
    framework: Mapped[str] = mapped_column(String(32))  # PyTorch / TensorFlow / Miniconda
    framework_version: Mapped[str] = mapped_column(String(32))
    python_version: Mapped[str] = mapped_column(String(16))
    cuda_version: Mapped[str] = mapped_column(String(16))
    image_ref: Mapped[str] = mapped_column(String(256), unique=True)
    prewarm_enabled: Mapped[bool] = mapped_column(default=True)  # 管理员意图:是否参与全节点预热
    sort: Mapped[int] = mapped_column(default=0)


class ImageNodeCache(Base):
    """每镜像×每节点的缓存状态。由 prewarm_patrol 巡检铺行/收敛,
    image.prewarm outbox handler 置 pulling;是 is_prewarmed 计算值的数据源。"""

    __tablename__ = "image_node_cache"
    __table_args__ = (UniqueConstraint("image_id", "node_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    image_id: Mapped[int] = mapped_column(ForeignKey("images.id", ondelete="CASCADE"), index=True)
    node_name: Mapped[str] = mapped_column(String(255))
    # pending(待预热)/ pulling(Job 进行中)/ cached(已缓存)/ failed(拉取失败)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    # 这一行缓存的是哪个 ref。与镜像当前 image_ref 不符即由巡检作废重拉:
    # admin_update_image 改 ref 时会同事务删行,但 SQL 直改 / 数据修复脚本绕不过这条兜底。
    cached_ref: Mapped[str | None] = mapped_column(String(256))
    last_error: Mapped[str | None] = mapped_column(Text)
    checked_at: Mapped[datetime | None]  # 最近确认 cached 的时刻,复检窗口依据
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
