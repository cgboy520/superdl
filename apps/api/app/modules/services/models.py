"""在线服务:实例之上的产品聚合根。只存身份与网关侧属性;状态由当前 / 候选实例派生(state.py),
不另立状态机。每次部署 = 一台新实例(`instances.service_id` 反指),计费 / 配额 / 回收主体仍是实例。"""

from datetime import datetime

from sqlalchemy import CheckConstraint, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

DESIRED_RUNNING = "running"
DESIRED_STOPPED = "stopped"


class Service(Base):
    __tablename__ = "services"
    __table_args__ = (
        CheckConstraint("protocol IN ('http')", name="protocol"),
        CheckConstraint("desired_state IN ('running', 'stopped')", name="desired_state"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # 公网域名左标签(svc-<10 位 base32>),兼作 API 路径标识。不用实例 uuid:内部主键不该出现在
    # 公网域名、TLS SNI、访问日志与第三方 Referer 里
    public_slug: Mapped[str] = mapped_column(String(32), unique=True)
    user_id: Mapped[int] = mapped_column(index=True)
    name: Mapped[str] = mapped_column(String(64))
    protocol: Mapped[str] = mapped_column(String(8), default="http", server_default="http")
    # False = 公开端点:网关回调匿名放行(extAuth 策略仍挂在 listener 上,改它不动 K8s)
    require_api_key: Mapped[bool] = mapped_column(default=True, server_default="true")
    # 用户意图(stop / start / 部署时写);状态派生不读它,前端据此区分「用户停的」与「平台停的」
    desired_state: Mapped[str] = mapped_column(
        String(8), default=DESIRED_RUNNING, server_default=DESIRED_RUNNING
    )
    # 对外流量指向的版本;候选版本只在版本更新在途时非空
    current_instance_id: Mapped[int | None]
    rollout_instance_id: Mapped[int | None]
    revision: Mapped[int] = mapped_column(default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
    # 当前实例 released 时由迁移监听器写入;非空即终态,列表默认不列
    released_at: Mapped[datetime | None]


class ServiceApiKey(Base):
    """服务的访问密钥。明文只在创建响应里出现一次,库里只存带密钥摘要;归属服务而非实例,
    版本更新换实例后照常有效。

    必须存 HMAC 而非裸 sha256:无密钥的摘要拿到库 dump 即可离线比对。
    吊销写 revoked_at 不删行,谁在什么时候吊销了哪把是审计事实。
    """

    __tablename__ = "service_api_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(index=True)
    service_id: Mapped[int] = mapped_column(index=True)
    name: Mapped[str] = mapped_column(String(64))
    # HMAC-SHA256 hex(crypto.hash_api_key);唯一索引即鉴权回源的查询路径
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    key_prefix: Mapped[str] = mapped_column(String(16))  # sk-a1b2c3d4,列表页回显用
    # 最近使用(「钥匙还被谁用」的排查线索,分钟级粒度足够):
    # 每 key 每 60s 至多一写(见 service._touch_key_last_used)
    last_used_at: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
