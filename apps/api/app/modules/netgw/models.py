"""Private-network gateways, their forwards and L3 attachments. Secrets (keys, PSK, passwords, the
OpenVPN profile) live only in `secrets_encrypted`; `config` holds the non-secret protocol
parameters and the L3 range."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

STATUS_ACTIVE = "active"
STATUS_DELETING = "deleting"
KIND_INTRANET = "intranet"
KIND_INSTANCE = "instance"


class NetGateway(Base):
    __tablename__ = "net_gateways"
    __table_args__ = (
        CheckConstraint(
            "protocol IN ('wireguard', 'ipsec', 'openvpn', 'openconnect')", name="protocol"
        ),
        CheckConstraint("status IN ('active', 'deleting')", name="status"),
        UniqueConstraint("user_id", "name"),
        UniqueConstraint("user_id", "idempotency_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    uuid: Mapped[str] = mapped_column(String(32), unique=True)
    user_id: Mapped[int] = mapped_column(index=True)
    name: Mapped[str] = mapped_column(String(40))
    protocol: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default=STATUS_ACTIVE, index=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB)
    secrets_encrypted: Mapped[str] = mapped_column(Text)
    public_key: Mapped[str | None] = mapped_column(String(64))
    generation: Mapped[int] = mapped_column(default=1, server_default="1")
    applied_generation: Mapped[int] = mapped_column(default=0, server_default="0")
    tunnel_up: Mapped[bool] = mapped_column(default=False, server_default="false")
    tunnel_changed_at: Mapped[datetime | None]
    pod_phase: Mapped[str | None] = mapped_column(String(32))
    checked_at: Mapped[datetime | None]
    idempotency_key: Mapped[str | None] = mapped_column(String(64))
    request_fingerprint: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class NetGatewayForward(Base):
    """intranet: instances reach `<name>:<port>` → target_host:target_port in the tenant network;
    instance: the tenant network reaches `<tunnel address>:<port>` → instance:target_port."""

    __tablename__ = "net_gateway_forwards"
    __table_args__ = (
        CheckConstraint("kind IN ('intranet', 'instance')", name="kind"),
        CheckConstraint(
            "(kind = 'intranet') = (target_host IS NOT NULL AND instance_id IS NULL)",
            name="kind_shape",
        ),
        UniqueConstraint("user_id", "name"),
        Index(
            "uq_net_gateway_forwards_listen_port",
            "gateway_id",
            "port",
            unique=True,
            postgresql_where=text("kind = 'instance'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    gateway_id: Mapped[int] = mapped_column(
        ForeignKey("net_gateways.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(index=True)
    kind: Mapped[str] = mapped_column(String(8))
    name: Mapped[str] = mapped_column(String(40))
    port: Mapped[int]
    target_host: Mapped[str | None] = mapped_column(String(253))
    target_port: Mapped[int]
    instance_id: Mapped[int | None] = mapped_column(index=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class NetGatewayAttachment(Base):
    """An instance attached to a gateway's L3 hub: its address in the gateway's L3 range and its
    hub key pair (private half AES-GCM encrypted, AAD bound to the instance uuid). One gateway per
    instance."""

    __tablename__ = "net_gateway_attachments"
    __table_args__ = (UniqueConstraint("gateway_id", "address"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    gateway_id: Mapped[int] = mapped_column(
        ForeignKey("net_gateways.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(index=True)
    instance_id: Mapped[int] = mapped_column(unique=True)
    address: Mapped[str] = mapped_column(String(15))
    public_key: Mapped[str] = mapped_column(String(44))
    private_key_encrypted: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
