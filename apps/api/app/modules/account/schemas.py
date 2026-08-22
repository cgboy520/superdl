from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

PhoneStr = Field(pattern=r"^1[3-9]\d{9}$", description="中国大陆手机号")


class SmsCodeRequest(BaseModel):
    phone: str = PhoneStr
    purpose: Literal["register", "login", "reset_password"]


class RegisterRequest(BaseModel):
    phone: str = PhoneStr
    sms_code: str = Field(min_length=4, max_length=8)
    password: str | None = Field(default=None, min_length=8, max_length=64)
    accept_terms: bool = False  # 必须显式同意用户协议与隐私政策(服务端强校验)


class LoginRequest(BaseModel):
    phone: str = PhoneStr
    sms_code: str | None = Field(default=None, min_length=4, max_length=8)
    password: str | None = Field(default=None, min_length=1, max_length=64)


class RefreshRequest(BaseModel):
    refresh_token: str


class PasswordResetRequest(BaseModel):
    """设置/修改/找回密码:凭手机号 + 验证码,不需要旧密码。"""

    phone: str = PhoneStr
    sms_code: str = Field(min_length=4, max_length=8)
    new_password: str = Field(min_length=8, max_length=64)


class UserOut(BaseModel):
    id: int
    phone: str
    status: str
    low_balance_warn_hours: int
    verification_status: str  # unverified / verified
    created_at: datetime

    model_config = {"from_attributes": True}


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
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
