from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.pricing import MARKET_ON_DEMAND


class Instance(Base):
    __tablename__ = "instances"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key"),
        # 状态枚举兜底(手工 SQL 旁路防护);合法迁移见 statemachine.TRANSITIONS。
        # 约束名声明短名,naming convention 自动补 ck_<表>_ 前缀
        CheckConstraint(
            "status IN ('creating', 'running', 'stopping', 'stopped', 'starting', 'frozen',"
            " 'releasing', 'released', 'failed')",
            name="status",
        ),
        # CPU 实例 gpu_count=0 合法,负数会算出负账单(计费份数 = billing_units(gpu_count));
        # 应用层已拦,这里兜住手工 SQL
        CheckConstraint("gpu_count >= 0", name="gpu_count_nonneg"),
        # 形态枚举兜底:dev = SSH + JupyterLab 开发机,service = 对外 HTTP 服务容器。
        # Pod spec 分叉在 build_pod_spec,写错值会落到「跑着、计费照走、没有任何入口」的哑状态
        CheckConstraint("workload_type IN ('dev', 'service')", name="workload_type"),
        # 购买模式枚举兜底:小时结算按 `market != 'subscription'` 挑候选,拼错的值会让
        # 预付过的实例再被按小时扣一遍
        CheckConstraint(
            "market IN ('on_demand', 'spot', 'subscription')",
            name="market",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    uuid: Mapped[str] = mapped_column(String(32), unique=True)  # k8s 对象名
    user_id: Mapped[int] = mapped_column(index=True)
    name: Mapped[str] = mapped_column(String(64))
    sku_id: Mapped[int]
    # SKU 快照:变更 SKU 仅影响新实例
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB)
    price_hourly: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    gpu_count: Mapped[int] = mapped_column(default=1)
    image_ref: Mapped[str] = mapped_column(String(256))
    # 购买模式:on_demand(按量)/ subscription(包周期,已预付)/ spot(竞价);
    # 与 skus.tier 正交(tier 是买什么档,market 是怎么买)。不建索引:没有查询按它单独过滤
    market: Mapped[str] = mapped_column(
        String(16), default=MARKET_ON_DEMAND, server_default=MARKET_ON_DEMAND
    )
    # 实例形态:dev(SSH + JupyterLab)/ service(对外 HTTP 服务);
    # 不另立实体,状态机、计费、配额、回收、reconciler、监控全套复用
    workload_type: Mapped[str] = mapped_column(String(8), default="dev", server_default="dev")
    # 用户覆盖镜像 ENTRYPOINT/CMD;None = 用镜像自带的(dev 形态恒为 None)
    container_command: Mapped[list[str] | None] = mapped_column(JSONB)
    container_args: Mapped[list[str] | None] = mapped_column(JSONB)
    # 是否给这台开 SSH。dev 恒 True,service 默认 False(端口池只有 30000–32767 一段,是硬上限)。
    # 独立成列而不是从 ssh_port 反推:端口是 outbox handler 建 Pod 时才分配的
    with_ssh: Mapped[bool] = mapped_column(default=True, server_default="true")
    # 用户环境变量整包密文(AES-GCM,AAD 绑实例 uuid),明文形如
    # {"plain": {...}, "secret": {...}};明文项一起加密,分列存会泄漏「哪些键是密文」
    env_encrypted: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), index=True)
    version: Mapped[int] = mapped_column(default=0)  # 乐观锁
    k8s_namespace: Mapped[str] = mapped_column(String(64))
    node_name: Mapped[str | None] = mapped_column(String(253))  # 与 node_specs 同宽(K8s 上限 253)
    ssh_port: Mapped[int | None]
    # AES-GCM 密文(enc:v1: 前缀,约 90 字符),AAD 绑定实例 uuid;重置后随重启轮换
    jupyter_token: Mapped[str] = mapped_column(String(160))
    authorized_keys: Mapped[list[str]] = mapped_column(JSONB, default=list)
    data_disk_id: Mapped[int | None]
    idempotency_key: Mapped[str | None] = mapped_column(String(64))
    # 冻结回收截止:进入 frozen 时按策略 freeze_grace_hours 写入(见 billing/patrol.py)
    frozen_deadline: Mapped[datetime | None]
    # running 实例 Pod 首次 not-ready 的时刻,持续超过宽限即判节点失联(见 reconciler);
    # 节点失联时 Pod 停在 phase=Running 而 Ready 转 False,只能看这个字段
    unready_since: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class InstanceEvent(Base):
    """状态迁移流水:计费主依据 + 用户可见时间线。追加式不可改。"""

    __tablename__ = "instance_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    instance_id: Mapped[int] = mapped_column(index=True)
    from_status: Mapped[str | None] = mapped_column(String(16))
    to_status: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(String(64))
    actor: Mapped[str] = mapped_column(String(16))  # user / system / admin
    event_metadata: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)


class PortAllocation(Base):
    """SSH 端口池。instance_id 为空即空闲;blocked=True 表示该端口被集群其它对象占用。

    端口池 30000–32767 与 K8s NodePort 同段,被占端口须标 blocked 让分配器跳过。
    """

    __tablename__ = "port_allocations"
    # 一台实例至多占一个端口(部分唯一:空闲行 instance_id 为 NULL,不参与约束)
    __table_args__ = (
        Index(
            "uq_port_allocations_instance",
            "instance_id",
            unique=True,
            postgresql_where=text("instance_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    port: Mapped[int] = mapped_column(unique=True)
    instance_id: Mapped[int | None] = mapped_column(index=True)
    blocked: Mapped[bool] = mapped_column(default=False, server_default="false")
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class DataDisk(Base):
    """数据盘:独立于实例生命周期(留存抓手)。JuiceFS 子路径,挂载点 /root/data。"""

    __tablename__ = "data_disks"
    # 幂等键:响应丢失后重试不会开出第二块盘;
    # 部分唯一索引:一台实例至多挂一块盘(与 instances.data_disk_id 的 1:1 模型一致)
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key"),
        Index(
            "uq_data_disks_mounted_instance",
            "mounted_instance_id",
            unique=True,
            postgresql_where=text("mounted_instance_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    uuid: Mapped[str] = mapped_column(String(32), unique=True)
    user_id: Mapped[int] = mapped_column(index=True)
    name: Mapped[str] = mapped_column(String(64))
    size_gb: Mapped[int]
    juicefs_subpath: Mapped[str] = mapped_column(String(128), unique=True)
    price_gb_month: Mapped[Decimal] = mapped_column(Numeric(12, 4))  # 创建时快照
    idempotency_key: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    # active / grace(欠费宽限,只读) / frozen / deleting / deleted
    mounted_instance_id: Mapped[int | None] = mapped_column(index=True)
    grace_started_at: Mapped[datetime | None]
    # 最近一次回款恢复(离开 grace/frozen)的时刻:日结追平按 [grace_started, grace_ended)
    # 区间判定宽限日;grace_started_at 为欠费倒计时语义保持 sticky,不混用
    grace_ended_at: Mapped[datetime | None]
    frozen_started_at: Mapped[datetime | None]
    # JuiceFS 目录硬配额是否已按 size_gb 下发(创建/扩容后置 false 并同事务入队 disk.quota,
    # handler 成功才置 true;死信由 reconciler 重派)。false ≠ 不可用,仅配额未强制、不可挂载
    quota_synced: Mapped[bool] = mapped_column(default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ServiceEndpoint(Base):
    """对外服务端点:服务型实例的公网 HTTPS 入口(<slug>.svc.<域名>)。
    一实例一端点(instance_id UNIQUE):多端点会把「端点 ↔ API Key 归属」变成多对多。"""

    __tablename__ = "service_endpoints"
    __table_args__ = (
        # 平台占用端口:22 = sshd,8888 = JupyterLab,用户服务落上去会与其 targetPort 撞车。
        # 应用层已拦,这里兜手工 SQL
        CheckConstraint(
            "container_port BETWEEN 1 AND 65535 AND container_port NOT IN (22, 8888)",
            name="container_port",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    instance_id: Mapped[int] = mapped_column(unique=True)
    # 公网域名左标签(svc-<10 位 base32>);不用 instance.uuid,内部主键不该出现在公网域名、
    # TLS SNI、访问日志与第三方 Referer 里
    public_slug: Mapped[str] = mapped_column(String(32), unique=True)
    container_port: Mapped[int]
    protocol: Mapped[str] = mapped_column(String(8), default="http", server_default="http")
    # 非空 → Pod 上挂 readinessProbe + startupProbe;空 = 容器起来即就绪
    health_path: Mapped[str | None] = mapped_column(String(128))
    # False = 公开端点,网关侧不挂 extAuth,不依赖控制面
    require_api_key: Mapped[bool] = mapped_column(default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ServiceApiKey(Base):
    """服务端点的访问密钥。明文只在创建响应里出现一次,库里只存带密钥摘要。

    必须存 HMAC 而非裸 sha256:无密钥的摘要拿到库 dump 即可离线比对。
    吊销写 revoked_at 不删行,谁在什么时候吊销了哪把是审计事实。
    """

    __tablename__ = "service_api_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(index=True)
    instance_id: Mapped[int] = mapped_column(index=True)
    name: Mapped[str] = mapped_column(String(64))
    # HMAC-SHA256 hex(crypto.hash_api_key);唯一索引即鉴权回源的查询路径
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    key_prefix: Mapped[str] = mapped_column(String(16))  # sk-a1b2c3d4,列表页回显用
    # 鉴权回源时直写不节流:代价是同一行的一次 UPDATE,节流会让「最近使用」失去排查价值
    last_used_at: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
