"""平台配置中心:支付/短信/实名/合规配置,env 默认 + DB 覆盖,管理端在线配置免发版。

与 policies.py 同构且同址(core):billing / account / notify / catalog 都要读。
SETTING_SPECS 是键白名单,未知键一律拒绝。敏感项经 crypto.py AES-GCM 加密落库,
adminapi 只回配置状态与尾 4 位预览,永不回明文。
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from sqlalchemy import String, Text, delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core import crypto
from app.core.config import get_settings
from app.core.db import Base


class PlatformSetting(Base):
    __tablename__ = "platform_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)  # secret 类为 enc:v1: 密文
    updated_by: Mapped[int | None]  # AdminUser.id(仅追溯,不建外键)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


SettingGroup = Literal[
    "payment_wechat", "payment_alipay", "sms", "real_name", "compliance", "cluster"
]
SettingKind = Literal["str", "text", "bool", "choice", "secret"]


@dataclass(frozen=True)
class SettingSpec:
    group: SettingGroup
    kind: SettingKind
    choices: tuple[str, ...] = ()
    pattern: str | None = None  # fullmatch 校验(str/secret 适用)
    must_contain: str | None = None  # 子串校验(PEM 头等)
    forbid_contains: str | None = None  # 反向校验(如支付宝密钥禁 PEM 头)
    max_len: int = 8192
    prod_forbidden: tuple[str, ...] = field(default=())  # prod 环境禁止写入的取值
    hint: str = ""  # 校验失败时的人话提示


# key 与 Settings 同名字段一一对应(env 即默认值层;K8s Secret 注入仍有效)
SETTING_SPECS: dict[str, SettingSpec] = {
    # ---- 微信支付(APIv3;公钥模式与平台证书模式二选一,新商户仅公钥模式) ----
    "payment_wechat_enabled": SettingSpec("payment_wechat", "bool"),
    "wechat_mchid": SettingSpec(
        "payment_wechat", "str", pattern=r"\d{8,12}", hint="商户号为 8~12 位数字"
    ),
    "wechat_appid": SettingSpec(
        "payment_wechat", "str", pattern=r"wx[0-9a-zA-Z]{10,30}", hint="AppID 以 wx 开头"
    ),
    "wechat_cert_serial_no": SettingSpec(
        "payment_wechat", "str", pattern=r"[0-9A-Fa-f]{8,64}", hint="商户 API 证书序列号(十六进制)"
    ),
    "wechat_private_key": SettingSpec(
        "payment_wechat",
        "secret",
        must_contain="-----BEGIN",
        hint="需粘贴完整 PEM(apiclient_key.pem 内容,含 -----BEGIN PRIVATE KEY-----)",
    ),
    "wechat_apiv3_key": SettingSpec(
        "payment_wechat", "secret", pattern=r"[0-9A-Za-z]{32}", hint="APIv3 密钥为 32 位字符"
    ),
    "wechat_public_key_id": SettingSpec(
        "payment_wechat",
        "str",
        pattern=r"PUB_KEY_ID_[0-9A-Za-z]+",
        hint="微信支付公钥 ID 以 PUB_KEY_ID_ 开头(商户平台·API安全·微信支付公钥)",
    ),
    "wechat_public_key": SettingSpec(
        "payment_wechat",
        "text",
        must_contain="-----BEGIN PUBLIC KEY-----",
        hint="需粘贴完整微信支付公钥 PEM(pub_key.pem 内容)",
    ),
    # ---- 支付宝(开放平台当面付;普通公钥模式,RSA2) ----
    "payment_alipay_enabled": SettingSpec("payment_alipay", "bool"),
    "alipay_app_id": SettingSpec(
        "payment_alipay", "str", pattern=r"\d{13,16}", hint="应用 APPID 为 13~16 位数字"
    ),
    "alipay_private_key": SettingSpec(
        "payment_alipay",
        "secret",
        forbid_contains="-----",
        hint="粘贴纯 base64 应用私钥体(不含 -----BEGIN----- 头尾,官方 SDK 格式)",
    ),
    "alipay_public_key": SettingSpec(
        "payment_alipay",
        "text",
        forbid_contains="-----",
        hint="粘贴纯 base64 支付宝公钥体(开放平台·接口加签方式·支付宝公钥)",
    ),
    # ---- 阿里云短信(dysmsapi) ----
    "sms_provider": SettingSpec(
        "sms",
        "choice",
        choices=("mock", "aliyun"),
        prod_forbidden=("mock",),
        hint="生产环境不得切回 mock",
    ),
    "sms_access_key_id": SettingSpec(
        "sms", "str", pattern=r"[0-9A-Za-z]{16,30}", hint="AccessKey ID(建议 RAM 子账号最小授权)"
    ),
    "sms_access_key_secret": SettingSpec("sms", "secret", max_len=128),
    "sms_sign_name": SettingSpec("sms", "str", max_len=24, hint="已报备的短信签名名称"),
    "sms_template_verify": SettingSpec(
        "sms", "str", pattern=r"SMS_[0-9A-Za-z]+", hint="验证码模板码,形如 SMS_123456789(变量 code)"
    ),
    "sms_template_notice": SettingSpec(
        "sms", "str", pattern=r"SMS_[0-9A-Za-z]+", hint="通知模板码,形如 SMS_123456789(变量 title)"
    ),
    # ---- 实名认证(阿里云实人认证·手机号三要素核验) ----
    "real_name_provider": SettingSpec("real_name", "choice", choices=("mock", "aliyun")),
    "real_name_required_for_recharge": SettingSpec("real_name", "bool"),
    "real_name_access_key_id": SettingSpec(
        "real_name", "str", pattern=r"[0-9A-Za-z]{16,30}", hint="AccessKey ID(建议独立 RAM 子账号)"
    ),
    "real_name_access_key_secret": SettingSpec("real_name", "secret", max_len=128),
    # ---- 合规备案(站点页脚展示) ----
    "icp_number": SettingSpec(
        "compliance", "str", max_len=64, hint="ICP 备案号,形如 京ICP备2026012345号-1"
    ),
    "police_record_number": SettingSpec(
        "compliance", "str", max_len=64, hint="公安备案号,形如 京公网安备11010502000000号"
    ),
    # ---- 集群接入(仅 admin 可读写;ops 生成注册命令时由服务端代读) ----
    "k8s_distro": SettingSpec(
        "cluster",
        "choice",
        choices=("rke2", "k3s"),
        prod_forbidden=("k3s",),
        hint="生产一律 RKE2;k3s 仅供轻量/本地验证环境",
    ),
    "rke2_server_url": SettingSpec(
        "cluster",
        "str",
        pattern=r"https://[0-9A-Za-z.\-\[\]:]+:\d{1,5}",
        hint="RKE2 supervisor 形如 https://<server-ip>:9345;k3s 为 https://<server-ip>:6443",
    ),
    "rke2_join_token": SettingSpec(
        "cluster",
        "secret",
        max_len=512,
        hint="server 节点 /var/lib/rancher/<rke2|k3s>/server/node-token 文件内容",
    ),
    "rke2_version": SettingSpec(
        "cluster",
        "str",
        pattern=r"v\d+\.\d+\.\d+(\+(rke2r|k3s)\d+)?",
        hint="装机脚本钉死的版本,形如 v1.36.2+rke2r1 / v1.36.3+k3s1",
    ),
    "node_driver_version": SettingSpec(
        "cluster",
        "str",
        pattern=r"\d{3}",
        hint="NVIDIA 驱动主版本,如 580(与 GPU Operator 兼容矩阵核对)",
    ),
    "node_registries_yaml": SettingSpec(
        "cluster",
        "text",
        max_len=8192,
        hint="节点 /etc/rancher/rke2/registries.yaml 内容(镜像缓存 mirror;留空则脚本跳过)",
    ),
}


def validate_setting_value(key: str, value: str) -> str:
    """校验并归一化(strip)。未知键/格式不符抛 ValueError(调用方转 AppError)。"""
    spec = SETTING_SPECS.get(key)
    if spec is None:
        raise ValueError(f"未知配置键:{key}")
    value = value.strip()
    suffix = f"({spec.hint})" if spec.hint else ""
    if len(value) > spec.max_len:
        raise ValueError(f"{key} 超长(最多 {spec.max_len} 字符)")
    if spec.kind == "bool" and value not in ("true", "false"):
        raise ValueError(f"{key} 只接受 true/false")
    if spec.kind == "choice" and value not in spec.choices:
        raise ValueError(f"{key} 只接受:{'/'.join(spec.choices)}")
    if (
        spec.prod_forbidden
        and get_settings().environment == "prod"
        and value in spec.prod_forbidden
    ):
        raise ValueError(f"{key} 生产环境禁止取值 {value}{suffix}")
    if spec.pattern and not re.fullmatch(spec.pattern, value):
        raise ValueError(f"{key} 格式不符{suffix}")
    if spec.must_contain and spec.must_contain not in value:
        raise ValueError(f"{key} 格式不符{suffix}")
    if spec.forbid_contains and spec.forbid_contains in value:
        raise ValueError(f"{key} 格式不符{suffix}")
    return value


def _env_default(key: str) -> str:
    v = getattr(get_settings(), key, None)
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


async def get_effective_platform_config(session: AsyncSession) -> dict[str, str]:
    """生效配置全量映射(secret 已解密,仅进程内使用,严禁整体入日志/响应)。"""
    eff = {key: _env_default(key) for key in SETTING_SPECS}
    for row in (await session.execute(select(PlatformSetting))).scalars():
        spec = SETTING_SPECS.get(row.key)
        if spec is None:
            continue  # 不在白名单内的键忽略
        eff[row.key] = (
            crypto.decrypt_str(row.value, aad=row.key) if spec.kind == "secret" else row.value
        )
    return eff


async def set_platform_settings(
    session: AsyncSession, updates: dict[str, str], *, updated_by: int | None
) -> None:
    """写覆盖(不 commit,由调用方与审计同事务提交)。空串 = 清除覆盖,回退 env 默认。"""
    for key, raw in updates.items():
        if key not in SETTING_SPECS:
            raise ValueError(f"未知配置键:{key}")
        if raw.strip() == "":
            await session.execute(delete(PlatformSetting).where(PlatformSetting.key == key))
            continue
        value = validate_setting_value(key, raw)
        if SETTING_SPECS[key].kind == "secret":
            value = crypto.encrypt_str(value, aad=key)
        await session.execute(
            pg_insert(PlatformSetting)
            .values(key=key, value=value, updated_by=updated_by)
            .on_conflict_do_update(
                index_elements=["key"], set_={"value": value, "updated_by": updated_by}
            )
        )


async def list_platform_overrides(session: AsyncSession) -> dict[str, PlatformSetting]:
    return {r.key: r for r in (await session.execute(select(PlatformSetting))).scalars()}


def secret_preview(plaintext: str) -> str | None:
    """脱敏预览:尾 4 位(PEM 等结构化文本无意义,返回 None 只显示"已配置")。"""
    plaintext = plaintext.strip()
    if len(plaintext) < 8 or "-----" in plaintext:
        return None
    return f"****{plaintext[-4:]}"
