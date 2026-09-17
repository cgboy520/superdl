from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.core.handles import NormalizedEmail, NormalizedHandle, NormalizedPhone
from app.core.security import PasswordStr

CodeStr = Field(min_length=4, max_length=8)


class VerificationCodeRequest(BaseModel):
    """Send a code to an email address or an E.164 phone number; the channel follows the handle."""

    handle: NormalizedHandle
    purpose: Literal["register", "login", "reset_password"]
    captcha_token: str | None = Field(default=None, max_length=4096)


class RegisterRequest(BaseModel):
    """Email + email code are always required; phone + SMS code only when the compliance profile
    requires a phone number."""

    email: NormalizedEmail
    email_code: str = CodeStr
    password: PasswordStr | None = None
    accept_terms: bool = False
    phone: NormalizedPhone | None = None
    phone_code: str | None = Field(default=None, min_length=4, max_length=8)


class LoginRequest(BaseModel):
    handle: NormalizedHandle
    code: str | None = Field(default=None, min_length=4, max_length=8)
    password: str | None = Field(default=None, min_length=1, max_length=128)


class PasswordResetRequest(BaseModel):
    """Set / change / recover the password with a verification code; no old password needed."""

    handle: NormalizedHandle
    code: str = CodeStr
    new_password: PasswordStr


class HandleCodeRequest(BaseModel):
    """Start binding or replacing a contact handle on the signed-in account."""

    handle: NormalizedHandle


class HandleConfirmRequest(BaseModel):
    handle: NormalizedHandle
    code: str = CodeStr


class UserOut(BaseModel):
    id: int
    email: str | None
    email_verified_at: datetime | None
    phone: str | None
    status: str
    low_balance_warn_hours: int
    kyc_status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class TokenPair(BaseModel):
    """Service-layer token pair (refresh_token is only used by the router to set the cookie, never
    in
    the response body)."""

    access_token: str
    refresh_token: str
    user: UserOut


class TokenPairOut(BaseModel):
    """Auth response: the refresh token travels only in the HttpOnly cookie, never in the body."""

    access_token: str
    user: UserOut


class SshKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    public_key: str = Field(min_length=1, max_length=8192)


class SshKeyOut(BaseModel):
    id: int
    name: str
    public_key: str
    fingerprint: str
    created_at: datetime

    model_config = {"from_attributes": True}


class WarnThresholdUpdate(BaseModel):
    low_balance_warn_hours: int = Field(ge=1, le=168)


class KycSubmitRequest(BaseModel):
    """Identity verification; the compliance profile decides which identity number is expected."""

    full_name: str = Field(min_length=2, max_length=128)
    identity_number: str | None = Field(default=None, min_length=1, max_length=64)


class DeletionRequestCreate(BaseModel):
    """Request deletion: retype one of the account's handles (email or phone) plus a reason."""

    handle: NormalizedHandle
    reason: str = Field(min_length=2, max_length=256)


class DeletionRequestOut(BaseModel):
    """User view of a deletion request. cooldown_ends_at = requested_at + 7 days."""

    id: int
    status: str
    reason: str
    requested_at: datetime
    cooldown_ends_at: datetime
    processed_at: datetime | None
    note: str | None

    model_config = {"from_attributes": True}


class AdminDeletionRequestOut(DeletionRequestOut):
    """Admin view of a deletion request: with the tenant identity and pre-execution check counts."""

    user_id: int
    email_masked: str | None
    phone_masked: str | None
    processed_by: int | None
    instances_active: int
    disks_active: int
    balance: str


class AdminDeletionReject(BaseModel):
    """Reject a deletion request (reason required, written to note)."""

    note: str = Field(min_length=2, max_length=512)


class AdminDeletionApprove(BaseModel):
    """Execute the deletion (operator reason required, written to note and the audit detail;
    irreversible actions always leave a trace)."""

    note: str = Field(min_length=2, max_length=512)
