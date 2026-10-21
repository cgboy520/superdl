import re
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, Field

from app.core.handles import NormalizedEmail
from app.core.money import MoneyOut

OrgRole = Literal["owner", "admin", "member"]
InviteRole = Literal["admin", "member"]
InvitationStatus = Literal["pending", "accepted", "declined", "revoked", "expired"]
MemberStatus = Literal["active", "frozen", "deleted"]

_NAME = re.compile(r"^[\w][\w .&()+-]*$")
_REMARK_FORBIDDEN = re.compile(r"[\x00-\x1f\x7f]")


def _org_name(value: str) -> str:
    """Letters (any script), digits and ` .&()+-_`, starting with a letter or digit; no URLs or
    addresses, the name is shown to invitees."""
    value = " ".join(value.split())
    if not 2 <= len(value) <= 40 or not _NAME.match(value):
        raise ValueError("2–40 letters, digits, spaces or . & ( ) + - _")
    return value


def _remark(value: str) -> str:
    value = value.strip()
    if _REMARK_FORBIDDEN.search(value):
        raise ValueError("control characters are not allowed")
    return value


OrgName = Annotated[str, Field(max_length=80), AfterValidator(_org_name)]
Remark = Annotated[str, Field(max_length=64), AfterValidator(_remark)]


class OrgCreate(BaseModel):
    name: OrgName


class OrgRename(BaseModel):
    name: OrgName


class OrgTransfer(BaseModel):
    member_id: int


class OrgDissolve(BaseModel):
    """The organization name typed again (L3 confirmation)."""

    confirm_name: str = Field(max_length=80)


class MemberUpdate(BaseModel):
    """Omitted fields stay; an empty remark clears it. Only the owner changes roles."""

    role: InviteRole | None = None
    remark: Remark | None = None


class InvitationCreate(BaseModel):
    email: NormalizedEmail
    role: InviteRole = "member"
    remark: Remark | None = None


class InvitationAccept(BaseModel):
    """The disclosure version the invitee reviewed (`GET /org/disclosure`)."""

    consent_version: str = Field(max_length=16)


class OrgOut(BaseModel):
    uuid: str
    name: str
    status: str
    created_at: datetime


class OrgContactOut(BaseModel):
    role: OrgRole
    label: str


class MyOrgOut(BaseModel):
    """org is null outside an organization; received_invitations counts the pending ones."""

    org: OrgOut | None
    role: OrgRole | None
    member_id: int | None
    member_count: int | None
    admins: list[OrgContactOut]
    received_invitations: int


class DisclosureOut(BaseModel):
    """What organization owners and admins can see about a member; accepting records `version`."""

    version: str
    items: list[str]


class MemberOut(BaseModel):
    member_id: int
    role: OrgRole
    label: str
    email_masked: str
    remark: str | None
    status: MemberStatus
    joined_at: datetime


class InvitationOut(BaseModel):
    """Sent invitation. The email is the one the inviter typed; the list never tells whether an
    account exists for it."""

    uuid: str
    email: str
    role: InviteRole
    remark: str | None
    status: InvitationStatus
    invited_by: str
    created_at: datetime
    expires_at: datetime


class ReceivedInvitationOut(BaseModel):
    """blocked = "member_of_org" when the invitee already belongs to an organization."""

    uuid: str
    org_name: str
    role: InviteRole
    invited_by: str
    created_at: datetime
    expires_at: datetime
    blocked: Literal["member_of_org"] | None


class MemberFinanceOut(BaseModel):
    """month_to_date counts from window_start = max(billing month start, joined_at);
    burn_per_hour covers running on-demand / spot instances; est_hours_left is null without burn
    and excludes the current, not yet settled hour."""

    member_id: int
    role: OrgRole
    label: str
    available_balance: MoneyOut
    month_to_date: MoneyOut
    window_start: datetime
    burn_per_hour: MoneyOut
    running_instances: int
    est_hours_left: float | None


class FinanceTotalsOut(BaseModel):
    available_balance: MoneyOut
    month_to_date: MoneyOut
    burn_per_hour: MoneyOut
    running_instances: int


class OrgFinanceOut(BaseModel):
    currency: str
    billing_timezone: str
    period: str
    period_start: datetime
    generated_at: datetime
    members: list[MemberFinanceOut]
    totals: FinanceTotalsOut


class OrgRefOut(BaseModel):
    """A user's organization as shown on admin tenant rows."""

    uuid: str
    name: str
    role: OrgRole


class AdminOrgOut(BaseModel):
    uuid: str
    name: str
    status: str
    owner_user_id: int | None
    owner_email_masked: str | None
    members: int
    pending_invitations: int
    created_at: datetime
    dissolved_at: datetime | None


class AdminOrgMemberOut(BaseModel):
    user_id: int
    role: OrgRole
    email_masked: str
    status: MemberStatus
    joined_at: datetime


class AdminOrgInvitationOut(BaseModel):
    uuid: str
    email_masked: str
    role: InviteRole
    status: InvitationStatus
    created_at: datetime
    expires_at: datetime


class AdminOrgDetailOut(AdminOrgOut):
    member_list: list[AdminOrgMemberOut]
    invitations: list[AdminOrgInvitationOut]
