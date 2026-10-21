"""Organizations: members (one organization per user, exactly one owner) and email invitations.
Funds stay per user; the organization only scopes visibility and shared gateways."""

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

ORG_ACTIVE = "active"
ORG_DISSOLVED = "dissolved"
ROLE_OWNER = "owner"
ROLE_ADMIN = "admin"
ROLE_MEMBER = "member"
INVITE_PENDING = "pending"
INVITE_ACCEPTED = "accepted"
INVITE_DECLINED = "declined"
INVITE_REVOKED = "revoked"
INVITE_EXPIRED = "expired"


class Organization(Base):
    """A dissolved organization keeps its row (history); it has no members or pending
    invitations."""

    __tablename__ = "organizations"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'dissolved')", name="status"),
        UniqueConstraint("created_by", "idempotency_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    uuid: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(16), default=ORG_ACTIVE, index=True)
    created_by: Mapped[int] = mapped_column(index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(64))
    request_fingerprint: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    dissolved_at: Mapped[datetime | None]


class OrgMember(Base):
    """user_id is unique: a user belongs to at most one organization. consent_version is the
    disclosure version the member accepted."""

    __tablename__ = "org_members"
    __table_args__ = (
        CheckConstraint("role IN ('owner', 'admin', 'member')", name="role"),
        Index(
            "uq_org_members_owner", "org_id", unique=True, postgresql_where=text("role = 'owner'")
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    user_id: Mapped[int] = mapped_column(unique=True)
    role: Mapped[str] = mapped_column(String(8))
    remark: Mapped[str | None] = mapped_column(String(64))
    invited_by: Mapped[int | None]
    consent_version: Mapped[str] = mapped_column(String(16))
    joined_at: Mapped[datetime] = mapped_column(server_default=func.now())


class OrgInvitation(Base):
    """One pending invitation per (organization, email); expiry is derived on read and written back
    lazily."""

    __tablename__ = "org_invitations"
    __table_args__ = (
        CheckConstraint("role IN ('admin', 'member')", name="role"),
        CheckConstraint(
            "status IN ('pending', 'accepted', 'declined', 'revoked', 'expired')", name="status"
        ),
        Index(
            "uq_org_invitations_pending",
            "org_id",
            "email",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
        Index(
            "ix_org_invitations_email_pending",
            "email",
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    uuid: Mapped[str] = mapped_column(String(32), unique=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    email: Mapped[str] = mapped_column(String(254))
    role: Mapped[str] = mapped_column(String(8))
    remark: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default=INVITE_PENDING)
    invited_by: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    expires_at: Mapped[datetime]
    responded_at: Mapped[datetime | None]
