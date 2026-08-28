"""平台配置中心:支付/短信/实名/合规配置,env 默认 + DB 覆盖,管理端在线配置免发版。

SETTING_SPECS 是键白名单,未知键一律拒绝。敏感项经 crypto.py AES-GCM 加密落库,
adminapi 只回配置状态与尾 4 位预览,永不回明文。
"""

import re
from collections.abc import Mapping
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
from app.core.registry import effective_image_allowlist

logger = get_logger(__name__)


class PlatformSetting(Base):
    __tablename__ = "platform_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)  # secret 类为 enc:v1: 密文
    updated_by: Mapped[int | None]  # AdminUser.id(仅追溯,不建外键)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


SettingGroup = Literal[
    "security",
    "payment_wechat",
    "payment_alipay",
    "sms",
    "real_name",
    "captcha",
    "compliance",
    "support",
    "cluster",
    "registry",
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
    # ---- 安全策略(开关 ≠ 替身:关闭即跳过;凭据在各渠道组;prod 关闭不拒启动,只给告警) ----
    "captcha_enabled": SettingSpec(
        "security",
        "bool",
        hint="开启后 /auth/sms-code 必须带阿里云验证码 2.0 的一次性 token(凭据在「人机验证」组);"
        "关闭 = 不做人机校验,发码口子只剩 IP/手机号限流",
    ),
    "admin_mfa_enabled": SettingSpec(
        "security",
        "bool",
        hint="开 = 管理端全角色强制 TOTP 两步验证(首登绑定);关 = 密码即登录,已绑定者也不再校验;"
        "生产环境关闭属高危运营动作",
    ),
    "real_name_enabled": SettingSpec(
        "security",
        "bool",
        hint="开启后用户端「账户设置」可提交三要素核验(凭据在「实名认证」组,缺失即 502);"
        "关闭 = 提交返 409,不影响已实名用户",
    ),
    "real_name_required_for_recharge": SettingSpec(
        "security",
        "bool",
        hint="开启后未实名用户不能充值、不能开通实例;须先开启实名认证(任意环境都拦这个组合)",
    ),
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
    # ---- 实名认证(阿里云实人认证·手机号三要素核验;开关在 security 组) ----
    "real_name_access_key_id": SettingSpec(
        "real_name", "str", pattern=r"[0-9A-Za-z]{16,30}", hint="AccessKey ID(建议独立 RAM 子账号)"
    ),
    "real_name_access_key_secret": SettingSpec("real_name", "secret", max_len=128),
    # ---- 人机校验(阿里云验证码 2.0,/auth/sms-code 前置闸;防分布式脚本刷码) ----
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
        hint="HA 集群填控制面 VIP:RKE2 形如 https://<vip>:9345;k3s 单 server 填 https://<server-ip>:6443",
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
    # ---- 镜像仓库(Harbor):平台镜像与租户实例镜像的权威源;拉取凭据由平台托管为 K8s Secret ----
    "registry_host": SettingSpec(
        "registry",
        "str",
        pattern=r"[a-z0-9.-]+(?::\d{1,5})?",
        max_len=253,
        hint="Harbor 访问地址,不带 scheme,如 harbor.example.com(内网自签证书时同时填 CA)",
    ),
    "registry_project": SettingSpec(
        "registry",
        "str",
        pattern=r"[a-z0-9]+(?:[._-][a-z0-9]+)*",
        max_len=255,
        hint="平台镜像所在的 Harbor 项目(默认 superdl):租户实例镜像与预热 Job 均从这里拉",
    ),
    "registry_robot_name": SettingSpec(
        "registry",
        "str",
        pattern=r"robot\$[A-Za-z0-9._+-]+",
        max_len=255,
        hint="机器人账户(项目级 robot$<项目>+<名> 或系统级 robot$<名>),至少授予 Pull Repository + "
        "List Repository;项目为 public 可留空",
    ),
    "registry_robot_secret": SettingSpec(
        "registry",
        "secret",
        max_len=256,
        hint="机器人账户 Secret;轮换时先在此保存新值、待新建 Pod 拉取成功后再在 Harbor 撤销旧值",
    ),
    "registry_ca_pem": SettingSpec(
        "registry",
        "text",
        must_contain="-----BEGIN CERTIFICATE-----",
        max_len=16384,
        hint="自签/私有 CA 时粘贴 PEM:node-join 落到节点并写 containerd tls.ca_file,"
        "平台探测 Harbor API 也据此校验;公信证书留空",
    ),
    "registry_proxy_projects": SettingSpec(
        "registry",
        "text",
        pattern=r"(?:[a-z0-9.-]+=[a-z0-9]+(?:[._-][a-z0-9]+)*\n?)*",
        max_len=2048,
        hint="Harbor 代理缓存:每行 <上游>=<代理项目>,如 docker.io=dockerhub、ghcr.io=ghcr"
        "(项目须先在 Harbor 建好并设 public);节点 containerd 对该上游做 mirror,拉不到回落上游",
    ),
    "image_allowed_registries": SettingSpec(
        "registry",
        "text",
        pattern=r"(?:[a-z0-9][a-z0-9.\-:/_]*\n?)*",
        max_len=4096,
        hint="创建实例的镜像来源白名单,每行一个仓库前缀(如 docker.io/);留空 = 不限制;"
        "Harbor 地址自动放行,平台镜像目录内的引用恒放行",
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


@dataclass(frozen=True)
class ConfigWarning:
    """配置风险(服务端计算):管理端配置页顶部红牌与 prod lifespan 启动日志共用同一份规则。"""

    key: str  # 关联配置键(前端据此定位分组导航)
    level: Literal["error", "warning"]
    message: str  # 运营文案,与 hint 同为 i18n-exempt


def _all(cfg: Mapping[str, str], *keys: str) -> bool:
    return all(cfg.get(k) for k in keys)


def compute_config_warnings(cfg: Mapping[str, str], environment: str) -> list[ConfigWarning]:
    """安全开关与凭据的组合风险。开关允许在 prod 关闭(运营决定),但必须看得见。"""
    prod = environment == "prod"
    out: list[ConfigWarning] = []
    if prod and cfg.get("captcha_enabled") != "true":
        out.append(
            ConfigWarning(
                "captcha_enabled",
                "error",
                "生产环境人机验证已关闭:/auth/sms-code 对脚本敞开,仅剩 IP/手机号限流",
            )
        )
    if cfg.get("captcha_enabled") == "true" and not _all(
        cfg, "captcha_scene_id", "captcha_access_key_id", "captcha_access_key_secret"
    ):
        out.append(
            ConfigWarning(
                "captcha_enabled",
                "error",
                "人机验证已开启但阿里云验证码凭据/场景不全,发码将一律 502",
            )
        )
    if prod and cfg.get("admin_mfa_enabled") != "true":
        out.append(
            ConfigWarning(
                "admin_mfa_enabled",
                "warning",
                "生产环境管理端两步验证已关闭:口令泄漏即可登录管理端",
            )
        )
    if cfg.get("real_name_enabled") == "true" and not _all(
        cfg, "real_name_access_key_id", "real_name_access_key_secret"
    ):
        out.append(
            ConfigWarning(
                "real_name_enabled",
                "error",
                "实名认证已开启但阿里云实人认证凭据不全,用户提交将一律 502",
            )
        )
    if (
        cfg.get("registry_host")
        and cfg.get("registry_robot_name")
        and not cfg.get("registry_robot_secret")
    ):
        out.append(
            ConfigWarning(
                "registry_robot_name",
                "error",
                "镜像仓库已填机器人账户但未填 Secret:私有项目的镜像拉取将失败",
            )
        )
    if prod and not effective_image_allowlist(cfg):
        out.append(
            ConfigWarning(
                "image_allowed_registries",
                "warning",
                "生产环境镜像来源白名单为空且未配 Harbor 地址:租户可把任意仓库的镜像拉进集群",
            )
        )
    return out


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


def _decrypt_row(key: str, value: str, *, aad: str) -> str | None:
    """单行解密;密文损坏(主密钥换错/手工改库)返回 None 让调用方回落 env,
    不得拖垮整份配置(prod lifespan 也走这里)。"""
    try:
        return crypto.decrypt_str(value, aad=aad)
    except Exception:
        logger.error("platform_setting_decrypt_failed", key=key, fallback="env")
        return None


async def get_effective_platform_config(session: AsyncSession) -> dict[str, str]:
    """生效配置全量映射(secret 已解密,仅进程内使用,严禁整体入日志/响应)。

    每次直接全量读,不做进程内缓存:表只有几十行,一趟 SELECT + 少量 AES-GCM 解密是微秒级。
    """
    eff = _env_layer()
    for row in (await session.execute(select(PlatformSetting))).scalars():
        spec = SETTING_SPECS.get(row.key)
        if spec is None:
            continue  # 不在白名单内的键忽略
        if spec.kind == "secret":
            if (plain := _decrypt_row(row.key, row.value, aad=row.key)) is not None:
                eff[row.key] = plain
        else:
            eff[row.key] = row.value
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
                index_elements=["key"],
                # updated_at 显式 bump:onupdate 只在 ORM 路径生效,upsert 语句要自己写
                set_={"value": value, "updated_by": updated_by, "updated_at": func.now()},
            )
        )
    await _check_real_name_invariant(session, updates)


async def _check_real_name_invariant(session: AsyncSession, updates: dict[str, str]) -> None:
    """与 Settings._validate_invariants 同口径的写入侧守卫(任意环境):

    real_name_required_for_recharge=true 必须伴随 real_name_enabled=true——实名未开通时
    用户永远完不成实名,充值与开通实例会被永久卡住。
    """
    if not (updates.keys() & {"real_name_enabled", "real_name_required_for_recharge"}):
        return
    rows = await list_platform_overrides(session)

    def effective(key: str) -> str:
        return rows[key].value if key in rows else _env_default(key)

    if effective("real_name_required_for_recharge") == "true" and (
        effective("real_name_enabled") != "true"
    ):
        raise ValueError(
            "real_name_required_for_recharge=true 需要先开启 real_name_enabled"
            "(实名未开通时用户无法完成实名,充值与开通实例会被永久卡住)"
        )


async def list_platform_overrides(session: AsyncSession) -> dict[str, PlatformSetting]:
    return {r.key: r for r in (await session.execute(select(PlatformSetting))).scalars()}


def secret_preview(plaintext: str) -> str | None:
    """脱敏预览:尾 4 位(PEM 等结构化文本无意义,返回 None 只显示"已配置")。"""
    plaintext = plaintext.strip()
    if len(plaintext) < 8 or "-----" in plaintext:
        return None
    return f"****{plaintext[-4:]}"
