import re
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, Field

from app.core.handles import TENANT_CODE_RE, USERNAME_NAME_RE, NormalizedEmail
from app.core.money import MoneyOut
from app.core.security import PasswordStr

OrgRole = Literal["owner", "admin", "member"]
InviteRole = Literal["admin", "member"]
InvitationStatus = Literal["pending", "accepted", "declined", "revoked", "expired"]
MemberStatus = Literal["active", "frozen", "deleted"]
OrgStatus = Literal["active", "dissolved"]

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


_TENANT_CODE = re.compile(TENANT_CODE_RE)
_USER_NAME = re.compile(USERNAME_NAME_RE)


def _tenant_code(value: str) -> str:
    value = value.strip().lower()
    if not _TENANT_CODE.match(value):
        raise ValueError("2-20 lowercase letters, digits or dashes, starting with a letter")
    return value


def _user_name(value: str) -> str:
    value = value.strip().lower()
    if not _USER_NAME.match(value):
        raise ValueError("1-32 lowercase letters, digits, dash or underscore")
    return value


OrgName = Annotated[str, Field(max_length=80), AfterValidator(_org_name)]
Remark = Annotated[str, Field(max_length=64), AfterValidator(_remark)]
TenantCode = Annotated[
    str,
    Field(
        max_length=20,
        description="Short code prefixed to the usernames of accounts the tenant creates"
        " (<code>.<name>)",
    ),
    AfterValidator(_tenant_code),
]
UserName = Annotated[str, Field(max_length=32), AfterValidator(_user_name)]


class OrgCreate(BaseModel):
    name: OrgName
    code: TenantCode


class OrgRename(BaseModel):
    """Omitted fields stay; the code changes only while the tenant has created no accounts."""

    name: OrgName | None = None
    code: TenantCode | None = None


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
    code: str
    status: OrgStatus
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
    """managed = an account this tenant created (username shown in full, password reset,
    sign-in switch, deletion and balance take-back by the tenant's admins)."""

    member_id: int
    role: OrgRole
    label: str
    email_masked: str
    remark: str | None
    status: MemberStatus
    joined_at: datetime
    managed: bool = False
    username: str | None = None
    login_disabled: bool = False


class InvitationOut(BaseModel):
    """Sent invitation. A pending one shows the email the inviter typed (the list never tells
    whether an account exists for it); once answered, revoked or expired it is masked like any
    member's."""

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
    code: str
    status: OrgStatus
    owner_user_id: int | None
    owner_email_masked: str | None
    members: int
    pending_invitations: int
    created_at: datetime
    dissolved_at: datetime | None


class AdminOrgMemberOut(BaseModel):
    """managed = created by the tenant (signs in with username, no email)."""

    user_id: int
    role: OrgRole
    email_masked: str
    username: str | None = None
    managed: bool = False
    login_disabled: bool = False
    status: MemberStatus
    joined_at: datetime


class AdminOrgInvitationOut(BaseModel):
    uuid: str
    email_masked: str
    role: InviteRole
    status: InvitationStatus
    created_at: datetime
    expires_at: datetime


_MOUNT_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,39}$")
_CEPH_PATH = re.compile(r"^/(?:[A-Za-z0-9._@+=-]+/?)*$")
_CEPH_USER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_CEPH_KEY = re.compile(r"^[A-Za-z0-9+/]{20,120}={0,2}$")
MountStatus = Literal["checking", "ready", "failed"]


def _mount_name(value: str) -> str:
    if not _MOUNT_NAME.match(value):
        raise ValueError("1-40 lowercase letters, digits, dot, dash or underscore")
    return value


def _ceph_path(value: str) -> str:
    value = value.strip()
    parts = [p for p in value.split("/") if p]
    if (
        not value.startswith("/")
        or not _CEPH_PATH.match(value)
        or any(p in (".", "..") for p in parts)
    ):
        raise ValueError("an absolute CephFS path without . or .. segments")
    return "/" + "/".join(parts)


def _ceph_user(value: str) -> str:
    value = value.strip().removeprefix("client.")
    if not _CEPH_USER.match(value):
        raise ValueError("a cephx id such as cloud.team-a (the client. prefix is optional)")
    return value


def _ceph_key(value: str) -> str:
    value = value.strip()
    if not _CEPH_KEY.match(value):
        raise ValueError("the base64 secret of the cephx key")
    return value


MountName = Annotated[str, Field(max_length=40), AfterValidator(_mount_name)]
CephPath = Annotated[str, Field(max_length=255), AfterValidator(_ceph_path)]
CephUser = Annotated[str, Field(max_length=71), AfterValidator(_ceph_user)]
CephKey = Annotated[str, Field(max_length=128), AfterValidator(_ceph_key)]


class OrgMountCreate(BaseModel):
    """A directory on the platform's Ceph cluster plus the organization's own key for it; mounted
    at /mnt/org/<name> in every member's instances."""

    name: MountName
    ceph_path: CephPath
    ceph_user: CephUser
    ceph_key: CephKey
    read_only: bool = True
    enabled: bool = True


class OrgMountUpdate(BaseModel):
    """Omitted fields stay; a new path, user, key or access is checked again and reaches
    instances from their next start."""

    ceph_path: CephPath | None = None
    ceph_user: CephUser | None = None
    ceph_key: CephKey | None = None
    read_only: bool | None = None
    enabled: bool | None = None


class OrgMountOut(BaseModel):
    """ceph_path and ceph_user are shown to owners and admins only; the key never comes back."""

    uuid: str
    name: str
    mount_path: str
    read_only: bool
    enabled: bool
    status: MountStatus
    last_error: str | None = None
    checked_at: datetime | None = None
    ceph_path: str | None = None
    ceph_user: str | None = None
    created_at: datetime
    updated_at: datetime


class AdminOrgDetailOut(AdminOrgOut):
    """mounts carry the Ceph path and user (never the key)."""

    member_list: list[AdminOrgMemberOut]
    invitations: list[AdminOrgInvitationOut]
    mounts: list[OrgMountOut]


class ManagedUsersCreate(BaseModel):
    """Create 1-100 accounts named <tenant code>.<name>; one shared password, or a random one per
    account when omitted. Passwords come back once, in the response."""

    names: list[UserName] = Field(min_length=1, max_length=100)
    password: PasswordStr | None = None
    remark: Remark | None = None


class ManagedUserCreatedOut(BaseModel):
    member_id: int
    user_id: int
    username: str
    password: str


class ManagedUserUpdate(BaseModel):
    login_disabled: bool


class ManagedPasswordReset(BaseModel):
    """Omit the password for a random one; the account must pick its own at next sign-in."""

    password: PasswordStr | None = None


class ManagedPasswordOut(BaseModel):
    username: str
    password: str


TransferKind = Literal["allocate", "reclaim"]


class BalanceTransferCreate(BaseModel):
    """allocate: from the caller's available balance to the member; reclaim: back from an
    account the tenant created, up to its available balance. Moved money can only be spent."""

    kind: TransferKind
    amount: str = Field(pattern=r"^\d{1,8}(\.\d{1,2})?$", description="Amount, decimal string")


class BalanceTransferOut(BaseModel):
    uuid: str
    kind: TransferKind
    member_id: int | None
    member_label: str
    actor_label: str
    amount: MoneyOut
    created_at: datetime
