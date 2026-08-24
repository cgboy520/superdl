from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, Field

PhoneStr = Field(pattern=r"^1[3-9]\d{9}$", description="中国大陆手机号")


def _within_bcrypt_limit(v: str) -> str:
    # bcrypt 只认前 72 字节,超长会在哈希层抛错;
    # max_length 按字符计,中文等多字节口令必须再按字节数拦一道
    if len(v.encode()) > 72:
        raise ValueError("密码过长:UTF-8 编码后不得超过 72 字节")
    return v


# 常见弱口令黑名单(Top 20,不区分大小写)。含 <12 位条目属纵深防御:
# 它们先被 min_length 拦,黑名单兜住「满足长度但人尽皆知」的口令
WEAK_PASSWORDS = frozenset(
    {
        "123456789",
        "1234567890",
        "111111111111",
        "123123123123",
        "123456789012",
        "012345678901",
        "password",
        "password1",
        "password123",
        "password1234",
        "passw0rd1234",
        "p@ssw0rd123",
        "qwerty123456",
        "qwertyuiop12",
        "1q2w3e4r5t6y",
        "admin123456",
        "admin1234567",
        "root12345678",
        "abc123456789",
        "aa1234567890",
    }
)


def _password_strength(v: str) -> str:
    v = _within_bcrypt_limit(v)
    if v.lower() in WEAK_PASSWORDS:
        raise ValueError("密码过于常见(弱口令黑名单),请更换更强口令")
    return v


PasswordStr = Annotated[str, AfterValidator(_password_strength)]


class SmsCodeRequest(BaseModel):
    phone: str = PhoneStr
    purpose: Literal["register", "login", "reset_password"]
    # 人机校验(验证码 2.0 的 CaptchaVerifyParam;mock 渠道为固定放行串 "mock-pass")
    captcha_token: str | None = Field(default=None, max_length=4096)


class RegisterRequest(BaseModel):
    phone: str = PhoneStr
    sms_code: str = Field(min_length=4, max_length=8)
    password: PasswordStr | None = Field(default=None, min_length=12, max_length=64)
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
    new_password: PasswordStr = Field(min_length=12, max_length=64)


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


# ---------- 账号注销 ----------


class DeletionRequestCreate(BaseModel):
    """申请注销:须键入与账号一致的完整手机号(二次确认)+ 原因。"""

    phone: str = PhoneStr
    reason: str = Field(min_length=2, max_length=256)


class DeletionRequestOut(BaseModel):
    """用户端注销申请视图。cooldown_ends_at = requested_at + 7 天(冷静期截止)。"""

    id: int
    status: str
    reason: str
    requested_at: datetime
    cooldown_ends_at: datetime
    processed_at: datetime | None
    note: str | None

    model_config = {"from_attributes": True}


class AdminDeletionRequestOut(DeletionRequestOut):
    """管理端注销申请视图:附租户标识与执行前校验计数(确认弹窗直接渲染)。"""

    user_id: int
    phone_masked: str
    processed_by: int | None
    instances_active: int  # 未释放实例数(status 不在 released/failed 终态)
    disks_active: int  # 未删除数据盘数(status != deleted)
    balance: str  # 当前余额(Decimal 字符串)


class AdminDeletionReject(BaseModel):
    """驳回注销申请(理由必填,回写 note 展示给用户)。"""

    note: str = Field(min_length=2, max_length=512)
