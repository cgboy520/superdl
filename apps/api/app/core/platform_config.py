"""平台配置中心:env 默认 + DB 覆盖,一张 `platform_settings` 表、一份 `SETTING_SPECS` 白名单、
一个强类型的 `RuntimeConfig` 读取面(渠道凭据 / 安全开关 / 合规信息 / 集群接入 / 运营策略参数
全在其中)。敏感项经 crypto.py AES-GCM 加密落库,adminapi 只回状态与尾 4 位预览。"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, get_args

from sqlalchemy import String, Text, delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core import crypto
from app.core.compliance import ComplianceProfile, profile_for
from app.core.config import check_real_name_invariant, get_settings
from app.core.db import Base
from app.core.logging import get_logger
from app.core.metrics import PLATFORM_CONFIG_WRITE_TOTAL
from app.core.registry import effective_image_allowlist

logger = get_logger(__name__)


class PlatformSetting(Base):
    __tablename__ = "platform_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_by: Mapped[int | None]
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


PlatformConfigGroup = Literal[
    "security",
    "payment_wechat",
    "payment_alipay",
    "payment_stripe",
    "sms",
    "email",
    "real_name",
    "captcha",
    "compliance",
    "support",
    "cluster",
    "registry",
    "observability",
]
SettingGroup = Literal[PlatformConfigGroup, "policy"]
PlatformConfigKind = Literal["str", "text", "bool", "choice", "secret"]
SettingKind = Literal[PlatformConfigKind, "int", "decimal"]
PLATFORM_CONFIG_GROUPS: tuple[str, ...] = get_args(PlatformConfigGroup)
POLICY_GROUP: SettingGroup = "policy"


@dataclass(frozen=True)
class SettingSpec:
    """配置校验规则;正则须避免灾难性回溯,数值项须提供 lo 和 hi。"""

    group: SettingGroup
    kind: SettingKind
    choices: tuple[str, ...] = ()
    pattern: str | None = None
    line_pattern: str | None = None
    must_contain: str | None = None
    forbid_contains: str | None = None
    max_len: int = 8192
    lo: Decimal | None = None
    hi: Decimal | None = None
    prod_forbidden: tuple[str, ...] = field(default=())
    prod_forbidden_profiles: tuple[str, ...] = ()
    prod_gate: bool = False
    hint: str = ""
    prod_hint: str = ""


def _num(kind: Literal["int", "decimal"], lo: str, hi: str, hint: str = "") -> SettingSpec:
    return SettingSpec(POLICY_GROUP, kind, lo=Decimal(lo), hi=Decimal(hi), hint=hint)


SETTING_SPECS: dict[str, SettingSpec] = {
    "captcha_enabled": SettingSpec(
        "security",
        "bool",
        prod_forbidden=("false",),
        prod_forbidden_profiles=("cn",),
        prod_gate=True,
        hint="开启后 /auth/sms-code 必须带阿里云验证码 2.0 的一次性 token(凭据在「人机验证」组);"
        "关闭 = 发码口子只剩 IP/手机号限流;prod 在线关闭已禁,env 层关闭启动 fail-fast",
        prod_hint="生产环境人机验证已关闭:/auth/sms-code 对脚本敞开,仅剩 IP/手机号限流",
    ),
    "admin_mfa_enabled": SettingSpec(
        "security",
        "bool",
        prod_forbidden=("false",),
        hint="开 = 管理端全角色强制 TOTP 两步验证(首登绑定);关 = 密码即登录,已绑定者也不再校验;"
        "生产环境关闭属高危运营动作,prod 在线关闭已禁(需部署层变更)",
        prod_hint="生产环境管理端两步验证已关闭:口令泄漏即可登录管理端",
    ),
    "real_name_enabled": SettingSpec(
        "security",
        "bool",
        prod_forbidden=("false",),
        prod_forbidden_profiles=("cn",),
        prod_gate=True,
        hint="开启后用户端「账户设置」可提交三要素核验(凭据在「实名认证」组,缺失即 502);"
        "关闭 = 提交返 409,不影响已实名用户;prod 在线关闭已禁,env 层关闭启动 fail-fast",
        prod_hint="生产环境实名认证已关闭:境内合规要求实名后方可使用算力",
    ),
    "real_name_required_for_recharge": SettingSpec(
        "security",
        "bool",
        prod_forbidden=("false",),
        prod_forbidden_profiles=("cn",),
        prod_gate=True,
        hint="开启后未实名用户不能充值、不能开通实例;须先开启实名认证(任意环境都拦这个组合);"
        "prod 在线关闭已禁且启动 fail-fast(境内合规要求)",
        prod_hint="生产环境未强制实名后充值:境内合规要求",
    ),
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
    "alipay_seller_id": SettingSpec(
        "payment_alipay",
        "str",
        pattern=r"|2088\d{12}",
        hint="收款账号 PID(2088 开头 16 位),开放平台·账户中心可查;prod 启用支付宝时必填",
    ),
    "payment_stripe_enabled": SettingSpec("payment_stripe", "bool"),
    "stripe_secret_key": SettingSpec(
        "payment_stripe",
        "secret",
        pattern=r"(sk|rk)_(test|live)_[0-9A-Za-z]{10,}",
        hint="Secret or restricted API key from the Stripe dashboard (sk_live_… in prod; "
        "sk_test_… only for sandbox deployments)",
    ),
    "stripe_webhook_secret": SettingSpec(
        "payment_stripe",
        "secret",
        pattern=r"whsec_[0-9A-Za-z]{10,}",
        hint="Signing secret of the webhook endpoint {public_base_url}/api/v1/webhooks/stripe "
        "(events: checkout.session.*, charge.refunded, charge.dispute.created)",
    ),
    "sms_provider": SettingSpec(
        "sms",
        "choice",
        choices=("mock", "aliyun", "twilio"),
        prod_forbidden=("mock",),
        hint="mock only logs (development); aliyun needs the sms_* credentials, "
        "twilio the sms_twilio_* credentials",
        prod_hint="生产环境短信渠道为 mock:验证码是固定值",
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
    "sms_twilio_account_sid": SettingSpec(
        "sms", "str", pattern=r"AC[0-9a-fA-F]{32}", hint="Twilio Account SID (starts with AC)"
    ),
    "sms_twilio_auth_token": SettingSpec("sms", "secret", max_len=64),
    "sms_twilio_from": SettingSpec(
        "sms",
        "str",
        pattern=r"\+[1-9]\d{6,14}|MG[0-9a-fA-F]{32}",
        hint="Sender: an E.164 Twilio number (+14155550123) or a Messaging Service SID (MG…)",
    ),
    "email_provider": SettingSpec(
        "email",
        "choice",
        choices=("mock", "smtp"),
        prod_forbidden=("mock",),
        hint="mock only logs the message (development); smtp delivers through the server below",
        prod_hint="Email provider is mock in production: verification codes are a fixed value",
    ),
    "smtp_host": SettingSpec(
        "email",
        "str",
        pattern=r"[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*",
        max_len=253,
        hint="Mail server hostname, e.g. smtp.example.com",
    ),
    "smtp_port": SettingSpec(
        "email", "str", pattern=r"\d{2,5}", hint="587 for STARTTLS, 465 for implicit TLS"
    ),
    "smtp_security": SettingSpec(
        "email",
        "choice",
        choices=("starttls", "tls", "none"),
        hint="starttls upgrades after connecting (587); tls connects over TLS (465); "
        "none sends in clear (private networks only)",
    ),
    "smtp_username": SettingSpec("email", "str", max_len=128, hint="Leave empty for no auth"),
    "smtp_password": SettingSpec("email", "secret", max_len=256),
    "email_from": SettingSpec(
        "email",
        "str",
        pattern=r"[^<>@\s]+@[^<>@\s]+\.[^<>@\s]+|[^<>]+ <[^<>@\s]+@[^<>@\s]+\.[^<>@\s]+>",
        max_len=254,
        hint='Sender, e.g. "SuperDL <no-reply@example.com>"',
    ),
    "email_reply_to": SettingSpec(
        "email",
        "str",
        pattern=r"|[^<>@\s]+@[^<>@\s]+\.[^<>@\s]+",
        max_len=254,
        hint="Optional Reply-To address",
    ),
    "real_name_access_key_id": SettingSpec(
        "real_name", "str", pattern=r"[0-9A-Za-z]{16,30}", hint="AccessKey ID(建议独立 RAM 子账号)"
    ),
    "real_name_access_key_secret": SettingSpec("real_name", "secret", max_len=128),
    "kyc_provider": SettingSpec(
        "real_name",
        "choice",
        choices=("aliyun_mobile3",),
        hint="Identity-verification provider; aliyun_mobile3 = Aliyun three-factor mobile check "
        "(needs the real_name_* AccessKey and a +86 phone on the account)",
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
    "captcha_provider": SettingSpec(
        "captcha",
        "choice",
        choices=("aliyun", "turnstile"),
        hint="turnstile needs the site key and secret key; aliyun needs scene ID, prefix and "
        "AccessKey",
    ),
    "captcha_turnstile_site_key": SettingSpec(
        "captcha",
        "str",
        pattern=r"[0-9A-Za-z_-]{10,128}",
        hint="Turnstile site key (public; the web app renders the widget with it)",
    ),
    "captcha_turnstile_secret_key": SettingSpec("captcha", "secret", max_len=128),
    "icp_number": SettingSpec(
        "compliance", "str", max_len=64, hint="ICP 备案号,形如 京ICP备2026012345号-1"
    ),
    "police_record_number": SettingSpec(
        "compliance", "str", max_len=64, hint="公安备案号,形如 京公网安备11010502000000号"
    ),
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
    "cluster_server_url": SettingSpec(
        "cluster",
        "str",
        pattern=r"https://[0-9A-Za-z.\-\[\]:]+:\d{1,5}",
        hint="HA 集群填控制面 VIP:RKE2 形如 https://<vip>:9345;k3s 单 server 填 https://<server-ip>:6443",
    ),
    "cluster_join_token": SettingSpec(
        "cluster",
        "secret",
        # 负前瞻拒绝 server node-token 形态(K10<64hex>::server:<pw>),node-join.sh 同规则再拒
        pattern=r"(?!K10[0-9A-Fa-f]{64}::server:)[A-Za-z0-9:._~+/=\-]{16,512}",
        max_len=512,
        hint="专用 agent token(server config 的 agent-token 值);"
        "不得填 server node-token(K10…::server:…),只许 agent token",
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
        choices=("official", "cn"),
        hint="Installer source for node-join: official = get.k3s.io / get.rke2.io (default); "
        "cn = mainland-China mirror rancher-mirror.rancher.cn (opt in when the official hosts "
        "are unreachable)",
    ),
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
        line_pattern=r"[a-z0-9.-]+=[a-z0-9]+([._-][a-z0-9]+)*",
        max_len=2048,
        hint="Harbor 代理缓存:每行 <上游>=<代理项目>,如 docker.io=dockerhub、ghcr.io=ghcr"
        "(项目须先在 Harbor 建好并设 public);节点 containerd 对该上游做 mirror,拉不到回落上游",
    ),
    "image_allowed_registries": SettingSpec(
        "registry",
        "text",
        line_pattern=r"[a-z0-9][a-z0-9.\-:/_]*",
        max_len=4096,
        hint="创建实例的镜像来源白名单,每行一个仓库前缀(如 docker.io/);留空 = 不限制;"
        "Harbor 地址自动放行,平台镜像目录内的引用恒放行",
    ),
    "grafana_url": SettingSpec(
        "observability",
        "str",
        pattern=r"https?://\S+",
        hint="可选:Grafana 地址,配置后管理端节点页显示「在 Grafana 打开」外链",
    ),
    "oncall_phone": SettingSpec(
        "observability",
        "str",
        pattern=r"|\+[1-9]\d{6,14}",
        hint="值班手机号:critical 告警短信直发;留空则不启用",
    ),
    "disk_price_gb_month": _num(
        "decimal",
        "0.0010",
        "1.0000",
        "Per GB·month in the platform currency; snapshot price for new disks",
    ),
    "recharge_min": _num("decimal", "0.01", "1000000", "Smallest top-up a user may submit"),
    "recharge_max": _num("decimal", "1", "10000000", "Largest single top-up"),
    "recharge_presets": SettingSpec(
        POLICY_GROUP,
        "str",
        pattern=r"\d{1,9}(\.\d{1,2})?(,\d{1,9}(\.\d{1,2})?){0,7}",
        max_len=128,
        hint="Comma-separated preset amounts offered in the top-up dialog (1–8 values)",
    ),
    "disk_min_gb": _num("int", "1", "1024"),
    "disk_max_gb": _num("int", "10", "65536"),
    "disk_grace_days": _num("int", "1", "365"),
    "disk_frozen_days": _num("int", "1", "365"),
    "freeze_grace_hours": _num("int", "1", "720", "欠费冻结时长"),
    "afford_cover_hours": _num("int", "1", "24", "开机前余额须覆盖的小时数"),
    "prewarm_min_coverage_pct": _num("int", "1", "100"),
    "prewarm_recheck_hours": _num("int", "1", "168"),
    "max_instances_per_user": _num("int", "1", "1000"),
    "max_gpus_per_user": _num("int", "1", "1024"),
    "max_vcpus_per_user": _num("int", "1", "4096"),
    "max_disks_per_user": _num("int", "1", "1000"),
    "max_disk_gb_per_user": _num("int", "10", "1048576", "每用户数据盘总容量上限"),
    "gpu_node_cpu_instance_vcpu_cap": _num("int", "0", "1024"),
    "period_discount_day": _num("int", "50", "100"),
    "period_discount_week": _num("int", "50", "100"),
    "period_discount_month": _num("int", "50", "100"),
    "period_discount_year": _num("int", "50", "100"),
    "period_expire_warn_days": _num("int", "1", "30"),
    "spot_discount_pct": _num("int", "10", "90"),
    "spot_grace_seconds": _num("int", "30", "600"),
}

POLICY_KEYS: tuple[str, ...] = tuple(k for k, s in SETTING_SPECS.items() if s.group == POLICY_GROUP)

PREEMPT_TIME_RESERVE_SECONDS = 120


@dataclass(frozen=True)
class RuntimeConfig:
    """生效配置(env 默认 + DB 覆盖)的强类型视图;字段与 SETTING_SPECS 一一对应,类型由 kind 决定。
    secret 已解密,整体禁止入日志 / 响应。"""

    captcha_enabled: bool
    admin_mfa_enabled: bool
    real_name_enabled: bool
    real_name_required_for_recharge: bool
    payment_wechat_enabled: bool
    wechat_mchid: str
    wechat_appid: str
    wechat_cert_serial_no: str
    wechat_private_key: str
    wechat_apiv3_key: str
    wechat_public_key_id: str
    wechat_public_key: str
    payment_alipay_enabled: bool
    alipay_app_id: str
    alipay_private_key: str
    alipay_public_key: str
    alipay_seller_id: str
    payment_stripe_enabled: bool
    stripe_secret_key: str
    stripe_webhook_secret: str
    sms_provider: str
    sms_access_key_id: str
    sms_access_key_secret: str
    sms_sign_name: str
    sms_template_verify: str
    sms_template_notice: str
    sms_twilio_account_sid: str
    sms_twilio_auth_token: str
    sms_twilio_from: str
    email_provider: str
    smtp_host: str
    smtp_port: str
    smtp_security: str
    smtp_username: str
    smtp_password: str
    email_from: str
    email_reply_to: str
    real_name_access_key_id: str
    real_name_access_key_secret: str
    kyc_provider: str
    captcha_scene_id: str
    captcha_prefix: str
    captcha_access_key_id: str
    captcha_access_key_secret: str
    captcha_provider: str
    captcha_turnstile_site_key: str
    captcha_turnstile_secret_key: str
    icp_number: str
    police_record_number: str
    company_name: str
    company_address: str
    company_phone: str
    business_license_url: str
    support_email: str
    support_wechat: str
    cluster_server_url: str
    cluster_join_token: str
    cluster_agent_version: str
    node_driver_version: str
    node_registries_yaml: str
    node_install_mirror: str
    registry_host: str
    registry_project: str
    registry_robot_name: str
    registry_robot_secret: str
    registry_ca_pem: str
    registry_proxy_projects: str
    image_allowed_registries: str
    grafana_url: str
    oncall_phone: str
    disk_price_gb_month: Decimal
    recharge_min: Decimal
    recharge_max: Decimal
    recharge_presets: str
    disk_min_gb: int
    disk_max_gb: int
    disk_grace_days: int
    disk_frozen_days: int
    freeze_grace_hours: int
    afford_cover_hours: int
    prewarm_min_coverage_pct: int
    prewarm_recheck_hours: int
    max_instances_per_user: int
    max_gpus_per_user: int
    max_vcpus_per_user: int
    max_disks_per_user: int
    max_disk_gb_per_user: int
    gpu_node_cpu_instance_vcpu_cap: int
    period_discount_day: int
    period_discount_week: int
    period_discount_month: int
    period_discount_year: int
    period_expire_warn_days: int
    spot_discount_pct: int
    spot_grace_seconds: int

    def image_allowlist(self) -> list[str]:
        """生效镜像来源白名单(配置行 ∪ Harbor 地址前缀);空 = 不限制。"""
        return effective_image_allowlist(
            allowed_registries=self.image_allowed_registries, registry_host=self.registry_host
        )


RUNTIME_CONFIG_FIELDS: tuple[str, ...] = tuple(f.name for f in fields(RuntimeConfig))
if set(RUNTIME_CONFIG_FIELDS) != set(SETTING_SPECS):
    raise RuntimeError("RuntimeConfig 字段须与 SETTING_SPECS 一一对应")


def _coerce(key: str, raw: str) -> Any:
    kind = SETTING_SPECS[key].kind
    if kind == "bool":
        return raw == "true"
    if kind == "int":
        return int(raw)
    if kind == "decimal":
        return Decimal(raw)
    return raw


def runtime_config_from_strings(values: Mapping[str, str]) -> RuntimeConfig:
    """合并 env 默认并转换类型;忽略未知键和空的数值覆盖,不执行取值校验。"""
    merged = _env_layer()
    for key, value in values.items():
        spec = SETTING_SPECS.get(key)
        if spec is None or (value == "" and spec.kind in ("int", "decimal")):
            continue
        merged[key] = value
    return RuntimeConfig(**{k: _coerce(k, v) for k, v in merged.items()})


@dataclass(frozen=True)
class ConfigWarning:
    """配置风险(服务端计算):管理端配置页顶部红牌与 prod lifespan 启动日志共用同一份规则。"""

    key: str
    level: Literal["error", "warning"]
    message: str


def _active_profile() -> ComplianceProfile:
    return profile_for(getattr(get_settings(), "compliance_profile", None))


def _spec_enforced(spec: SettingSpec) -> bool:
    """A prod_forbidden rule applies to every profile unless the spec narrows it."""
    return (
        not spec.prod_forbidden_profiles or _active_profile().name in spec.prod_forbidden_profiles
    )


def _prod_forbids(spec: SettingSpec, value: str) -> bool:
    """True when writing/effective `value` is refused in prod under the active profile."""
    return (
        bool(spec.prod_forbidden)
        and get_settings().environment == "prod"
        and value in spec.prod_forbidden
        and _spec_enforced(spec)
    )


def _prod_violations(cfg: RuntimeConfig) -> list[tuple[str, SettingSpec]]:
    """生效值命中 prod_forbidden 的键(与 spec 声明同源;不区分环境,由调用方决定处置);
    只含对当前合规档位生效的规则。"""
    out: list[tuple[str, SettingSpec]] = []
    for key, spec in SETTING_SPECS.items():
        if (
            spec.prod_forbidden
            and _to_string(getattr(cfg, key)) in spec.prod_forbidden
            and _spec_enforced(spec)
        ):
            out.append((key, spec))
    return out


def _captcha_credentials_warning(cfg: RuntimeConfig) -> ConfigWarning | None:
    """CAPTCHA on but the selected provider's credentials incomplete → every code request is 502."""
    if not cfg.captcha_enabled:
        return None
    if cfg.captcha_provider == "turnstile":
        complete = bool(cfg.captcha_turnstile_site_key and cfg.captcha_turnstile_secret_key)
        message = (
            "CAPTCHA is on but the Turnstile site key / secret key are missing: "
            "every code request will fail (502)"
        )
    else:
        complete = bool(
            cfg.captcha_scene_id and cfg.captcha_access_key_id and cfg.captcha_access_key_secret
        )
        message = "人机验证已开启但阿里云验证码凭据/场景不全,发码将一律 502"
    return None if complete else ConfigWarning("captcha_enabled", "error", message)


def compute_config_warnings(cfg: RuntimeConfig, environment: str) -> list[ConfigWarning]:
    """安全开关与凭据的组合风险。prod 禁止取值的红牌从 SettingSpec.prod_forbidden 派生。"""
    prod = environment == "prod"
    out: list[ConfigWarning] = []
    if prod:
        out.extend(
            ConfigWarning(key, "error" if spec.prod_gate else "warning", spec.prod_hint)
            for key, spec in _prod_violations(cfg)
        )
        if not cfg.captcha_enabled and not _spec_enforced(SETTING_SPECS["captcha_enabled"]):
            out.append(
                ConfigWarning(
                    "captcha_enabled",
                    "warning",
                    "CAPTCHA is off: the verification-code endpoint is protected only by "
                    "IP and account rate limits",
                )
            )
    captcha_warning = _captcha_credentials_warning(cfg)
    if captcha_warning is not None:
        out.append(captcha_warning)
    if cfg.real_name_enabled and not (
        cfg.real_name_access_key_id and cfg.real_name_access_key_secret
    ):
        out.append(
            ConfigWarning(
                "real_name_enabled",
                "error",
                "实名认证已开启但阿里云实人认证凭据不全,用户提交将一律 502",
            )
        )
    if cfg.sms_provider == "twilio" and not (
        cfg.sms_twilio_account_sid and cfg.sms_twilio_auth_token and cfg.sms_twilio_from
    ):
        out.append(
            ConfigWarning(
                "sms_provider",
                "error",
                "SMS provider is Twilio but its credentials are incomplete: every SMS will fail",
            )
        )
    if cfg.email_provider == "smtp" and not (cfg.smtp_host and cfg.email_from):
        out.append(
            ConfigWarning(
                "email_provider",
                "error",
                "Email provider is SMTP but smtp_host / email_from are missing: "
                "every email will fail",
            )
        )
    if cfg.registry_host and cfg.registry_robot_name and not cfg.registry_robot_secret:
        out.append(
            ConfigWarning(
                "registry_robot_name",
                "error",
                "镜像仓库已填机器人账户但未填 Secret:私有项目的镜像拉取将失败",
            )
        )
    if prod and not cfg.image_allowlist():
        out.append(
            ConfigWarning(
                "image_allowed_registries",
                "error",
                "生产环境镜像来源白名单为空且未配 Harbor 地址:租户可把任意仓库的镜像拉进集群",
            )
        )
    return out


def assert_prod_image_allowlist(cfg: RuntimeConfig, environment: str) -> None:
    """prod 下生效镜像白名单(配置行 ∪ Harbor 地址)不得为空,否则拒绝启动。"""
    if environment == "prod" and not cfg.image_allowlist():
        raise RuntimeError(
            "生产环境镜像来源白名单为空且未配 Harbor 地址,拒绝启动:"
            "填 registry_host 或 image_allowed_registries(env 或平台配置中心)"
        )


def assert_prod_compliance_gates(cfg: RuntimeConfig, environment: str) -> None:
    """prod 下 prod_gate 键的生效值不得命中 prod_forbidden(人机验证 / 实名 / 充值强制实名),
    否则拒绝启动。"""
    if environment != "prod":
        return
    gated = [key for key, spec in _prod_violations(cfg) if spec.prod_gate]
    if gated:
        raise RuntimeError(
            "生产环境合规开关未全开,拒绝启动:"
            + ",".join(gated)
            + "(境内合规要求;经 env 或平台配置中心开启后再启动)"
        )


def validate_setting_value(key: str, value: str) -> str:
    """校验并归一化(strip;数值项按 kind 归一)。未知键/格式不符抛 ValueError(调用方转 AppError)。"""
    spec = SETTING_SPECS.get(key)
    if spec is None:
        raise ValueError(f"未知配置键:{key}")
    value = value.strip()
    if len(value) > spec.max_len:
        raise ValueError(f"{key} 超长(最多 {spec.max_len} 字符)")
    if spec.kind == "bool" and value not in ("true", "false"):
        raise ValueError(f"{key} 只接受 true/false")
    if spec.kind == "choice" and value not in spec.choices:
        raise ValueError(f"{key} 只接受:{'/'.join(spec.choices)}")
    if spec.kind in ("int", "decimal"):
        value = _validate_number(key, value, spec)
    if _prod_forbids(spec, value):
        raise ValueError(f"{key} 生产环境禁止取值 {value}{_hint_suffix(spec)}")
    _validate_shape(key, value, spec)
    return value


def _hint_suffix(spec: SettingSpec) -> str:
    return f"({spec.hint})" if spec.hint else ""


def _validate_shape(key: str, value: str, spec: SettingSpec) -> None:
    """形态白名单:整串 fullmatch / 逐行 fullmatch / 必含子串 / 禁含子串。"""
    suffix = _hint_suffix(spec)
    if spec.pattern and not re.fullmatch(spec.pattern, value):
        raise ValueError(f"{key} 格式不符{suffix}")
    if spec.line_pattern is not None:
        for line in value.replace(",", "\n").splitlines():
            line = line.strip()
            if line and not re.fullmatch(spec.line_pattern, line):
                raise ValueError(f"{key} 含非法行:{line[:64]!r}{suffix}")
    if spec.must_contain and spec.must_contain not in value:
        raise ValueError(f"{key} 格式不符{suffix}")
    if spec.forbid_contains and spec.forbid_contains in value:
        raise ValueError(f"{key} 格式不符{suffix}")


def _validate_number(key: str, value: str, spec: SettingSpec) -> str:
    assert spec.lo is not None and spec.hi is not None
    try:
        num = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{key} 不是合法数字:{value}") from exc
    if spec.kind == "int" and num != num.to_integral_value():
        raise ValueError(f"{key} 须为整数:{value}")
    if not spec.lo <= num <= spec.hi:
        raise ValueError(f"{key} 取值须在 {spec.lo}~{spec.hi} 之间")
    if key == "spot_grace_seconds":
        budget = get_settings().creating_timeout_seconds - PREEMPT_TIME_RESERVE_SECONDS
        if num > budget:
            raise ValueError(
                f"spot_grace_seconds 不得超过 {budget} 秒"
                f"(creating 超时 {get_settings().creating_timeout_seconds}s 减去"
                f" {PREEMPT_TIME_RESERVE_SECONDS}s 调度余量)"
            )
    return str(int(num)) if spec.kind == "int" else str(num)


def _to_string(value: object) -> str:
    """字段值 → 配置中心的字符串形态(bool 为 true/false,None 为空串)。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _env_default(key: str) -> str:
    return _to_string(getattr(get_settings(), key))


def _env_layer() -> dict[str, str]:
    return {key: _env_default(key) for key in SETTING_SPECS}


def env_layer_problems() -> list[str]:
    """校验非空 env 配置的格式、范围及生产环境禁用值,返回错误描述。"""
    problems: list[str] = []
    for key, value in _env_layer().items():
        if value == "":
            continue
        try:
            validate_setting_value(key, value)
        except ValueError as exc:
            problems.append(str(exc))
    return problems


def _decrypt_row(key: str, value: str, *, aad: str) -> str:
    """单行解密,fail-closed:密文损坏/主密钥不配套即抛,不回落 env。"""
    try:
        return crypto.decrypt_str(value, aad=aad)
    except Exception as exc:
        logger.error("platform_setting_decrypt_failed", key=key)
        raise ValueError(f"平台配置项 {key} 解密失败(主密钥不配套或密文损坏)") from exc


async def effective_strings(session: AsyncSession) -> dict[str, str]:
    """生效配置的字符串映射(secret 已解密;管理端配置页展示与 RuntimeConfig 构造共用),不缓存。"""
    eff = _env_layer()
    for row in (await session.execute(select(PlatformSetting))).scalars():
        spec = SETTING_SPECS.get(row.key)
        if spec is None:
            continue
        eff[row.key] = (
            _decrypt_row(row.key, row.value, aad=row.key) if spec.kind == "secret" else row.value
        )
    return eff


async def get_runtime_config(session: AsyncSession) -> RuntimeConfig:
    """生效配置(强类型);每次全量读,写入即生效。"""
    return runtime_config_from_strings(await effective_strings(session))


async def set_platform_settings(
    session: AsyncSession,
    updates: dict[str, str],
    *,
    updated_by: int | None,
    allowed_groups: frozenset[str] | None = None,
) -> None:
    """写覆盖(不 commit,由调用方与审计同事务提交)。空串 = 清除覆盖,回退 env 默认。
    allowed_groups 限定本入口可写的配置组:/policies 只许 policy 组,/platform-config 不许 policy 组。
    """
    for key, raw in updates.items():
        spec = SETTING_SPECS.get(key)
        if spec is None or (allowed_groups is not None and spec.group not in allowed_groups):
            raise ValueError(f"未知配置键:{key}")
        PLATFORM_CONFIG_WRITE_TOTAL.labels(domain=spec.group).inc()
        if raw.strip() == "":
            fallback = _env_default(key)
            if _prod_forbids(spec, fallback):
                raise ValueError(
                    f"{key} 不允许清除覆盖:清除后回落到部署层取值 {fallback!r},"
                    "生产环境禁止该取值(请显式写入合规值,或修改部署层 env 后清除)"
                )
            await session.execute(delete(PlatformSetting).where(PlatformSetting.key == key))
            continue
        value = validate_setting_value(key, raw)
        if spec.kind == "secret":
            value = crypto.encrypt_str(value, aad=key)
        await session.execute(
            pg_insert(PlatformSetting)
            .values(key=key, value=value, updated_by=updated_by)
            .on_conflict_do_update(
                index_elements=["key"],
                set_={"value": value, "updated_by": updated_by, "updated_at": func.now()},
            )
        )
    if updates.keys() & _CROSS_KEY_INVARIANT_KEYS:
        rows = await list_platform_overrides(session)

        def effective(key: str) -> str:
            return rows[key].value if key in rows else _env_default(key)

        if updates.keys() & {"real_name_enabled", "real_name_required_for_recharge"}:
            check_real_name_invariant(
                enabled=effective("real_name_enabled") == "true",
                required_for_recharge=effective("real_name_required_for_recharge") == "true",
            )
        if updates.keys() & {"recharge_min", "recharge_max", "recharge_presets"}:
            check_recharge_policy_invariant(
                minimum=Decimal(effective("recharge_min")),
                maximum=Decimal(effective("recharge_max")),
                presets=effective("recharge_presets"),
            )


_CROSS_KEY_INVARIANT_KEYS = frozenset(
    {
        "real_name_enabled",
        "real_name_required_for_recharge",
        "recharge_min",
        "recharge_max",
        "recharge_presets",
    }
)


def recharge_presets_of(cfg: "RuntimeConfig") -> list[Decimal]:
    """Preset top-up amounts in configured order (`recharge_presets` is a comma list)."""
    return [Decimal(part) for part in cfg.recharge_presets.split(",") if part]


def check_recharge_policy_invariant(*, minimum: Decimal, maximum: Decimal, presets: str) -> None:
    """recharge_min ≤ recharge_max and every preset inside [min, max]; a preset outside the
    bounds would render a chip the API always rejects."""
    if minimum > maximum:
        raise ValueError(f"recharge_min ({minimum}) must not exceed recharge_max ({maximum})")
    for part in presets.split(","):
        if part and not minimum <= Decimal(part) <= maximum:
            raise ValueError(f"recharge preset {part} is outside {minimum}~{maximum}")


async def list_platform_overrides(session: AsyncSession) -> dict[str, PlatformSetting]:
    return {r.key: r for r in (await session.execute(select(PlatformSetting))).scalars()}


def secret_preview(plaintext: str) -> str | None:
    """脱敏预览:尾 4 位(PEM 等结构化文本无意义,返回 None 只显示"已配置")。"""
    plaintext = plaintext.strip()
    if len(plaintext) < 8 or "-----" in plaintext:
        return None
    return f"****{plaintext[-4:]}"
