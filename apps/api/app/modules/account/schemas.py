from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.core.regex import PHONE_RE
from app.core.security import PasswordStr

PhoneStr = Field(pattern=PHONE_RE, description="中国大陆手机号")


class SmsCodeRequest(BaseModel):
    phone: str = PhoneStr
    purpose: Literal["register", "login", "reset_password"]
    captcha_token: str | None = Field(default=None, max_length=4096)


class RegisterRequest(BaseModel):
    phone: str = PhoneStr
    sms_code: str = Field(min_length=4, max_length=8)
    password: PasswordStr | None = None
    accept_terms: bool = False


class LoginRequest(BaseModel):
    phone: str = PhoneStr
    sms_code: str | None = Field(default=None, min_length=4, max_length=8)
    password: str | None = Field(default=None, min_length=1, max_length=64)


class PasswordResetRequest(BaseModel):
    """设置/修改/找回密码:凭手机号 + 验证码,不需要旧密码。"""

    phone: str = PhoneStr
    sms_code: str = Field(min_length=4, max_length=8)
    new_password: PasswordStr


class UserOut(BaseModel):
    id: int
    phone: str
    status: str
    low_balance_warn_hours: int
    verification_status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class TokenPair(BaseModel):
    """服务层令牌对(refresh_token 只用于路由层种 Cookie,不进响应体)。"""

    access_token: str
    refresh_token: str
    user: UserOut


class TokenPairOut(BaseModel):
    """认证响应:refresh token 只走 HttpOnly Cookie,不进响应体。"""

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


class RealNameRequest(BaseModel):
    """实名认证(三要素核验:姓名 + 身份证号 + 账号手机号)。"""

    name: str = Field(min_length=2, max_length=32)
    id_number: str = Field(pattern=r"^\d{17}[\dXx]$")


class DeletionRequestCreate(BaseModel):
    """申请注销:须键入与账号一致的完整手机号(二次确认)+ 原因。"""

    phone: str = PhoneStr
    reason: str = Field(min_length=2, max_length=256)


class DeletionRequestOut(BaseModel):
    """用户端注销申请视图。cooldown_ends_at = requested_at + 7 天。"""

    id: int
    status: str
    reason: str
    requested_at: datetime
    cooldown_ends_at: datetime
    processed_at: datetime | None
    note: str | None

    model_config = {"from_attributes": True}


class AdminDeletionRequestOut(DeletionRequestOut):
    """管理端注销申请视图:附租户标识与执行前校验计数。"""

    user_id: int
    phone_masked: str
    processed_by: int | None
    instances_active: int
    disks_active: int
    balance: str


class AdminDeletionReject(BaseModel):
    """驳回注销申请(理由必填,回写 note)。"""

    note: str = Field(min_length=2, max_length=512)


class AdminDeletionApprove(BaseModel):
    """执行注销(操作原因必填,回写 note 并进审计 detail;不可逆操作一律留痕)。"""

    note: str = Field(min_length=2, max_length=512)
