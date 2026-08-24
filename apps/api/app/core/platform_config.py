"""平台配置中心:支付/短信/实名/合规配置,env 默认 + DB 覆盖,管理端在线配置免发版。

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
from app.core.logging import get_logger

logger = get_logger(__name__)


class PlatformSetting(Base):
    __tablename__ = "platform_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)  # secret 类为 enc:v1: 密文
    updated_by: Mapped[int | None]  # AdminUser.id(仅追溯,不建外键)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


SettingGroup = Literal[
    "payment_wechat",
    "payment_alipay",
    "sms",
    "real_name",
    "captcha",
    "compliance",
    "support",
    "cluster",
    "observability",
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
    # 收款方 PID(2088 开头 16 位)。异步通知除验签外还要核对 app_id 与 seller_id;
    # prod 启用支付宝渠道时必填(渠道构造期 fail-fast),缺失 seller_id 的回调一律拒收
    "alipay_seller_id": SettingSpec(
        "payment_alipay",
        "str",
        pattern=r"|2088\d{12}",
        hint="收款账号 PID(2088 开头 16 位),开放平台·账户中心可查;prod 启用支付宝时必填",
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
    "real_name_provider": SettingSpec(
        "real_name",
        "choice",
        choices=("mock", "aliyun"),
        prod_forbidden=("mock",),
        hint="生产环境不得切回 mock(mock 对非 0000 结尾恒过,实名形同虚设)",
    ),
    "real_name_required_for_recharge": SettingSpec("real_name", "bool"),
    "real_name_access_key_id": SettingSpec(
        "real_name", "str", pattern=r"[0-9A-Za-z]{16,30}", hint="AccessKey ID(建议独立 RAM 子账号)"
    ),
    "real_name_access_key_secret": SettingSpec("real_name", "secret", max_len=128),
    # ---- 人机校验(阿里云验证码 2.0,/auth/sms-code 前置闸;防分布式脚本刷码) ----
    "captcha_provider": SettingSpec(
        "captcha",
        "choice",
        choices=("mock", "aliyun"),
        prod_forbidden=("mock",),
        hint="生产环境不得切回 mock(无校验,短信口子对脚本敞开)",
    ),
    "captcha_scene_id": SettingSpec(
        "captcha", "str", max_len=64, hint="场景 ID(控制台·场景管理;服务端验签强制写入防篡改)"
    ),
    "captcha_prefix": SettingSpec(
        "captcha", "str", max_len=64, hint="身份标(控制台·概览;前端 SDK 初始化用,公开信息)"
    ),
    "captcha_access_key_id": SettingSpec(
        "captcha",
        "str",
        pattern=r"[0-9A-Za-z]{16,30}",
        hint="AccessKey ID(建议独立 RAM 子账号,仅授 AliyunYundunAFSFullAccess)",
    ),
    "captcha_access_key_secret": SettingSpec("captcha", "secret", max_len=128),
    # ---- 合规备案(站点页脚展示) ----
    "icp_number": SettingSpec(
        "compliance", "str", max_len=64, hint="ICP 备案号,形如 京ICP备2026012345号-1"
    ),
    "police_record_number": SettingSpec(
        "compliance", "str", max_len=64, hint="公安备案号,形如 京公网安备11010502000000号"
    ),
    # 经营主体信息(《电子商务法》第十五条:首页显著位置持续公示;页脚展示,留空即不展示)
    "company_name": SettingSpec(
        "compliance", "str", max_len=128, hint="营业执照上的公司全称,如 某某科技(北京)有限公司"
    ),
    "company_address": SettingSpec(
        "compliance", "str", max_len=256, hint="公司注册地址(营业执照住所)"
    ),
    "company_phone": SettingSpec(
        "compliance", "str", max_len=32, hint="对外联系电话,形如 010-12345678 或 400-800-1234"
    ),
    "business_license_url": SettingSpec(
        "compliance",
        "str",
        pattern=r"|https?://\S+",
        max_len=256,
        hint="营业执照电子版链接(亮照);留空则不展示",
    ),
    # ---- 客服联系方式(页脚与帮助页;留空即不展示对应入口) ----
    "support_email": SettingSpec(
        "support",
        "str",
        pattern=r"[^@\s]+@[^@\s]+\.[^@\s]+",
        max_len=128,
        hint="客服邮箱,如 support@example.com",
    ),
    "support_wechat": SettingSpec(
        "support", "str", max_len=64, hint="企业微信/微信客服号(展示为文本,用户自行搜索添加)"
    ),
    # ---- 集群接入(仅 admin 可读写;ops 生成注册命令时由服务端代读) ----
    "cluster_server_url": SettingSpec(
        "cluster",
        "str",
        pattern=r"https://[0-9A-Za-z.\-\[\]:]+:\d{1,5}",
        hint="HA 集群填控制面 VIP(P0-1):RKE2 形如 https://<vip>:9345;k3s 单 server 填 https://<server-ip>:6443",
    ),
    "cluster_join_token": SettingSpec(
        "cluster",
        "secret",
        max_len=512,
        hint="专用 agent token(server 的 .../server/agent-token;禁止填 node-token)",
    ),
    "cluster_agent_version": SettingSpec(
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
        hint="高级覆盖:留空=平台按 server 地址自动生成(Spegel P2P + 内网 registry mirror)",
    ),
    "node_install_mirror": SettingSpec(
        "cluster",
        "choice",
        choices=("cn", "official"),
        hint="装机安装源:cn=国内镜像(rancher-mirror.rancher.cn),official=官方源",
    ),
    # ---- 可观测性(管理端自绘为主;Grafana 仅作可选深挖外链,不做 iframe) ----
    "grafana_url": SettingSpec(
        "observability",
        "str",
        pattern=r"https?://\S+",
        hint="可选:Grafana 地址,配置后管理端节点页显示「在 Grafana 打开」外链",
    ),
    # 值班手机号:critical 平台告警短信直发(不依赖平台自身通知流;复用阿里云短信通道)
    "oncall_phone": SettingSpec(
        "observability",
        "str",
        pattern=r"|1[3-9]\d{9}",
        hint="值班手机号:critical 告警短信直发;留空则不启用",
    ),
}


# 旧键读回落:secret 的 AES-GCM AAD=行 key,直接 UPDATE 键名会毁掉密文,
# 只能按旧行原键解密;写新键后同事务删旧行(见 set_platform_settings)。
LEGACY_KEY_ALIASES: dict[str, str] = {
    "cluster_join_token": "rke2_join_token",
    "cluster_server_url": "rke2_server_url",  # 明文行,别名兜未跑迁移的库
    "cluster_agent_version": "rke2_version",
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


def _env_layer() -> dict[str, str]:
    return {key: _env_default(key) for key in SETTING_SPECS}


# 生效配置进程内缓存(读路径调用密集,不每次全表读 + 全量 AES-GCM 解密)。
# 失效签名 =(行数, max(updated_at), env 默认值层指纹):
# 改值 bump updated_at,增删动行数,env 变更动指纹。
_config_cache: tuple[tuple[object, ...], dict[str, str]] | None = None


async def _config_signature(session: AsyncSession) -> tuple[object, ...]:
    count, max_updated = (
        await session.execute(select(func.count(), func.max(PlatformSetting.updated_at)))
    ).one()
    return (count, max_updated, tuple(_env_layer().values()))


def _decrypt_row(key: str, value: str, *, aad: str) -> str | None:
    """单行解密;密文损坏(主密钥换错/手工改库)返回 None 让调用方回落 env,
    不得拖垮整份配置(prod lifespan 也走这里)。"""
    try:
        return crypto.decrypt_str(value, aad=aad)
    except Exception:
        logger.error("platform_setting_decrypt_failed", key=key, fallback="env")
        return None


async def get_effective_platform_config(session: AsyncSession) -> dict[str, str]:
    """生效配置全量映射(secret 已解密,仅进程内使用,严禁整体入日志/响应)。"""
    global _config_cache
    signature = await _config_signature(session)
    if _config_cache is not None and _config_cache[0] == signature:
        return dict(_config_cache[1])
    eff = _env_layer()
    rows = {r.key: r for r in (await session.execute(select(PlatformSetting))).scalars()}
    for key, row in rows.items():
        spec = SETTING_SPECS.get(key)
        if spec is None:
            continue  # 不在白名单内的键忽略(含改名后的遗留行,由别名回落处理)
        if spec.kind == "secret":
            if (plain := _decrypt_row(key, row.value, aad=key)) is not None:
                eff[key] = plain
        else:
            eff[key] = row.value
    for new_key, old_key in LEGACY_KEY_ALIASES.items():
        if new_key not in rows and old_key in rows:
            spec = SETTING_SPECS[new_key]
            if spec.kind == "secret":
                plain = _decrypt_row(new_key, rows[old_key].value, aad=old_key)
                if plain is not None:
                    eff[new_key] = plain
            else:
                eff[new_key] = rows[old_key].value
    # 以本次全量读自身的快照重算签名,保证缓存内容与签名自洽
    # (快捷签名查询与全量读之间可能隔着其他事务的提交)
    built_signature = (
        len(rows),
        max((r.updated_at for r in rows.values() if r.updated_at is not None), default=None),
        signature[2],
    )
    _config_cache = (built_signature, eff)
    return dict(eff)


async def set_platform_settings(
    session: AsyncSession, updates: dict[str, str], *, updated_by: int | None
) -> None:
    """写覆盖(不 commit,由调用方与审计同事务提交)。空串 = 清除覆盖,回退 env 默认。"""
    for key, raw in updates.items():
        if key not in SETTING_SPECS:
            raise ValueError(f"未知配置键:{key}")
        if raw.strip() == "":
            await session.execute(delete(PlatformSetting).where(PlatformSetting.key == key))
            if key in LEGACY_KEY_ALIASES:  # 清除时连旧行一起删,防遗留行借别名复活
                await session.execute(
                    delete(PlatformSetting).where(PlatformSetting.key == LEGACY_KEY_ALIASES[key])
                )
            continue
        value = validate_setting_value(key, raw)
        if SETTING_SPECS[key].kind == "secret":
            value = crypto.encrypt_str(value, aad=key)
        await session.execute(
            pg_insert(PlatformSetting)
            .values(key=key, value=value, updated_by=updated_by)
            .on_conflict_do_update(
                index_elements=["key"],
                # updated_at 显式 bump:既让管理端看到真实更新时间,也驱动读缓存失效
                set_={"value": value, "updated_by": updated_by, "updated_at": func.now()},
            )
        )
        if key in LEGACY_KEY_ALIASES:  # 写新删旧:此后不再走别名回落
            await session.execute(
                delete(PlatformSetting).where(PlatformSetting.key == LEGACY_KEY_ALIASES[key])
            )
    await _check_prod_real_name_combination(session, updates)


async def _check_prod_real_name_combination(session: AsyncSession, updates: dict[str, str]) -> None:
    """与 Settings._validate_prod 同口径的写入侧 fail-closed:

    prod 下「充值强制实名 + mock 渠道」组合经 DB 覆盖层也要拦住
    (mock 恒过等于实名形同虚设)。实名未启用时 mock 无害,不拦。
    """
    if get_settings().environment != "prod" or not (
        updates.keys() & {"real_name_provider", "real_name_required_for_recharge"}
    ):
        return
    rows = await list_platform_overrides(session)
    provider = (
        rows["real_name_provider"].value
        if "real_name_provider" in rows
        else _env_default("real_name_provider")
    )
    required = (
        rows["real_name_required_for_recharge"].value
        if "real_name_required_for_recharge" in rows
        else _env_default("real_name_required_for_recharge")
    )
    if provider == "mock" and required == "true":
        raise ValueError(
            "real_name_provider=mock 与 real_name_required_for_recharge=true 不能同时生效"
            "(mock 渠道核验恒过,等于实名形同虚设):请先接入阿里云实名"
            "(real_name_access_key_*),或先关闭充值强制实名"
        )


async def list_platform_overrides(session: AsyncSession) -> dict[str, PlatformSetting]:
    return {r.key: r for r in (await session.execute(select(PlatformSetting))).scalars()}


def secret_preview(plaintext: str) -> str | None:
    """脱敏预览:尾 4 位(PEM 等结构化文本无意义,返回 None 只显示"已配置")。"""
    plaintext = plaintext.strip()
    if len(plaintext) < 8 or "-----" in plaintext:
        return None
    return f"****{plaintext[-4:]}"
