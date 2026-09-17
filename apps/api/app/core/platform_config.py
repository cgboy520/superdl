"""Platform configuration centre: env defaults + DB overrides, one `platform_settings` table, one
`SETTING_SPECS` allow-list and one strongly typed `RuntimeConfig` read surface (channel credentials
/
security switches / compliance info / cluster access / policy parameters). Secrets are stored
AES-GCM encrypted via crypto.py; adminapi returns only the state and a last-4 preview."""

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
    """Validation rule of a setting; patterns must avoid catastrophic backtracking, numeric kinds
    must provide lo and hi."""

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
        hint="When on, /auth/verification-code must carry a one-time CAPTCHA token (credentials in"
        " the"
        " CAPTCHA group); off = only IP / handle rate limits guard code sending; switching off"
        " online in"
        " prod is blocked, off in the env layer fails fast at boot",
        prod_hint="CAPTCHA is off in production: /auth/verification-code is open to scripts, only"
        " IP /"
        " handle rate limits remain",
    ),
    "admin_mfa_enabled": SettingSpec(
        "security",
        "bool",
        prod_forbidden=("false",),
        hint="On = TOTP two-factor mandatory for every admin role (enrolled at first login); off ="
        " password alone signs in, enrolled admins are no longer checked; switching off in"
        " production is"
        " a high-risk operation and blocked online (needs a deployment-layer change)",
        prod_hint="Admin two-factor is off in production: a leaked password is enough to sign in",
    ),
    "real_name_enabled": SettingSpec(
        "security",
        "bool",
        prod_forbidden=("false",),
        prod_forbidden_profiles=("cn",),
        prod_gate=True,
        hint="When on, users can submit identity verification under Account settings (credentials"
        " in the"
        " identity-verification group, 502 when missing); off = submissions return 409, verified"
        " users"
        " are unaffected; switching off online in prod is blocked, off in the env layer fails fast",
        prod_hint="Identity verification is off in production: the cn compliance profile requires"
        " it"
        " before compute can be used",
    ),
    "real_name_required_for_recharge": SettingSpec(
        "security",
        "bool",
        prod_forbidden=("false",),
        prod_forbidden_profiles=("cn",),
        prod_gate=True,
        hint="When on, unverified users cannot top up or create instances; requires identity"
        " verification to be on (the combination is rejected in every environment); switching off"
        " online in prod is blocked and fails fast at boot (cn compliance requirement)",
        prod_hint="Top-ups do not require identity verification in production: cn compliance"
        " requirement",
    ),
    "payment_wechat_enabled": SettingSpec("payment_wechat", "bool"),
    "wechat_mchid": SettingSpec(
        "payment_wechat", "str", pattern=r"\d{8,12}", hint="Merchant ID is 8-12 digits"
    ),
    "wechat_appid": SettingSpec(
        "payment_wechat", "str", pattern=r"wx[0-9a-zA-Z]{10,30}", hint="AppID starts with wx"
    ),
    "wechat_cert_serial_no": SettingSpec(
        "payment_wechat",
        "str",
        pattern=r"[0-9A-Fa-f]{8,64}",
        hint="Merchant API certificate serial (hex)",
    ),
    "wechat_private_key": SettingSpec(
        "payment_wechat",
        "secret",
        must_contain="-----BEGIN",
        hint="Paste the full PEM (contents of apiclient_key.pem, including -----BEGIN PRIVATE"
        " KEY-----)",
    ),
    "wechat_apiv3_key": SettingSpec(
        "payment_wechat",
        "secret",
        pattern=r"[0-9A-Za-z]{32}",
        hint="The APIv3 key is 32 characters",
    ),
    "wechat_public_key_id": SettingSpec(
        "payment_wechat",
        "str",
        pattern=r"PUB_KEY_ID_[0-9A-Za-z]+",
        hint="WeChat Pay public key ID starts with PUB_KEY_ID_ (merchant platform · API security ·"
        " WeChat Pay public key)",
    ),
    "wechat_public_key": SettingSpec(
        "payment_wechat",
        "text",
        must_contain="-----BEGIN PUBLIC KEY-----",
        hint="Paste the full WeChat Pay public key PEM (contents of pub_key.pem)",
    ),
    "payment_alipay_enabled": SettingSpec("payment_alipay", "bool"),
    "alipay_app_id": SettingSpec(
        "payment_alipay", "str", pattern=r"\d{13,16}", hint="The app APPID is 13-16 digits"
    ),
    "alipay_private_key": SettingSpec(
        "payment_alipay",
        "secret",
        forbid_contains="-----",
        hint="Paste the bare base64 app private key body (no -----BEGIN----- lines, official SDK"
        " format)",
    ),
    "alipay_public_key": SettingSpec(
        "payment_alipay",
        "text",
        forbid_contains="-----",
        hint="Paste the bare base64 Alipay public key body (open platform · signing method · Alipay"
        " public key)",
    ),
    "alipay_seller_id": SettingSpec(
        "payment_alipay",
        "str",
        pattern=r"|2088\d{12}",
        hint="Payee PID (16 digits starting with 2088), found in the open platform account centre;"
        " required in prod when Alipay is enabled",
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
        prod_hint="The SMS channel is mock in production: verification codes are a fixed value",
    ),
    "sms_access_key_id": SettingSpec(
        "sms",
        "str",
        pattern=r"[0-9A-Za-z]{16,30}",
        hint="AccessKey ID (a least-privilege RAM sub-account)",
    ),
    "sms_access_key_secret": SettingSpec("sms", "secret", max_len=128),
    "sms_sign_name": SettingSpec("sms", "str", max_len=24, hint="Registered SMS signature name"),
    "sms_template_verify": SettingSpec(
        "sms",
        "str",
        pattern=r"SMS_[0-9A-Za-z]+",
        hint="Verification template code, e.g. SMS_123456789 (variable code)",
    ),
    "sms_template_notice": SettingSpec(
        "sms",
        "str",
        pattern=r"SMS_[0-9A-Za-z]+",
        hint="Notice template code, e.g. SMS_123456789 (variable title)",
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
        "real_name",
        "str",
        pattern=r"[0-9A-Za-z]{16,30}",
        hint="AccessKey ID (a dedicated RAM sub-account)",
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
        "captcha",
        "str",
        max_len=64,
        hint="Scene ID (console · scene management; written into the server-side verification"
        " against tampering)",
    ),
    "captcha_prefix": SettingSpec(
        "captcha",
        "str",
        max_len=64,
        hint="Identity prefix (console · overview; used by the frontend SDK, public information)",
    ),
    "captcha_access_key_id": SettingSpec(
        "captcha",
        "str",
        pattern=r"[0-9A-Za-z]{16,30}",
        hint="AccessKey ID (a dedicated RAM sub-account with only AliyunYundunAFSFullAccess)",
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
        "compliance",
        "str",
        max_len=64,
        hint="ICP filing number, e.g. 京ICP备2026012345号-1",  # cjk-ok
    ),
    "police_record_number": SettingSpec(
        "compliance",
        "str",
        max_len=64,
        hint="Public security filing number, e.g. 京公网安备11010502000000号",  # cjk-ok
    ),
    "company_name": SettingSpec(
        "compliance", "str", max_len=128, hint="Full company name as on the business licence"
    ),
    "company_address": SettingSpec(
        "compliance",
        "str",
        max_len=256,
        hint="Registered company address (as on the business licence)",
    ),
    "company_phone": SettingSpec(
        "compliance",
        "str",
        max_len=32,
        hint="Public contact phone, e.g. 010-12345678 or 400-800-1234",
    ),
    "business_license_url": SettingSpec(
        "compliance",
        "str",
        pattern=r"|https?://\S+",
        max_len=256,
        hint="Link to the electronic business licence; empty = not shown",
    ),
    "support_email": SettingSpec(
        "support",
        "str",
        pattern=r"[^@\s]+@[^@\s]+\.[^@\s]+",
        max_len=128,
        hint="Support email, e.g. support@example.com",
    ),
    "support_wechat": SettingSpec(
        "support",
        "str",
        max_len=64,
        hint="WeCom / WeChat support account (shown as text for users to search)",
    ),
    "cluster_server_url": SettingSpec(
        "cluster",
        "str",
        pattern=r"https://[0-9A-Za-z.\-\[\]:]+:\d{1,5}",
        hint="HA clusters: the control-plane VIP, RKE2 like https://<vip>:9345; single-server k3s:"
        " https://<server-ip>:6443",
    ),
    "cluster_join_token": SettingSpec(
        "cluster",
        "secret",
        # negative lookahead rejects the server node-token shape (K10<64hex>::server:<pw>);
        # node-join.sh
        # applies the same rule
        pattern=r"(?!K10[0-9A-Fa-f]{64}::server:)[A-Za-z0-9:._~+/=\-]{16,512}",
        max_len=512,
        hint="Dedicated agent token (the agent-token value of the server config);"
        " never the server node-token (K10...::server:...), agent token only",
    ),
    "cluster_agent_version": SettingSpec(
        "cluster",
        "str",
        pattern=r"v\d+\.\d+\.\d+(\+(rke2r|k3s)\d+)?",
        hint="Version pinned by the install script, e.g. v1.36.2+rke2r1 / v1.36.3+k3s1",
    ),
    "node_driver_version": SettingSpec(
        "cluster",
        "str",
        pattern=r"\d{3}",
        hint="NVIDIA driver major version, e.g. 580 (check the GPU Operator compatibility matrix)",
    ),
    "node_registries_yaml": SettingSpec(
        "cluster",
        "text",
        max_len=8192,
        hint="Advanced override: empty = generated by the platform from the server address (Spegel"
        " P2P +"
        " internal registry mirror)",
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
        hint="Harbor address without scheme, e.g. harbor.example.com (also fill the CA for an"
        " internal"
        " self-signed certificate)",
    ),
    "registry_project": SettingSpec(
        "registry",
        "str",
        pattern=r"[a-z0-9]+(?:[._-][a-z0-9]+)*",
        max_len=255,
        hint="Harbor project holding the platform images (default superdl): tenant instance images"
        " and"
        " prewarm Jobs pull from here",
    ),
    "registry_robot_name": SettingSpec(
        "registry",
        "str",
        pattern=r"robot\$[A-Za-z0-9._+-]+",
        max_len=255,
        hint="Robot account (project-level robot$<project>+<name> or system-level robot$<name>)"
        " with at"
        " least Pull Repository + List Repository; may be empty for a public project",
    ),
    "registry_robot_secret": SettingSpec(
        "registry",
        "secret",
        max_len=256,
        hint="Robot account secret; on rotation save the new value here first and revoke the old"
        " one in"
        " Harbor once new Pods pull successfully",
    ),
    "registry_ca_pem": SettingSpec(
        "registry",
        "text",
        must_contain="-----BEGIN CERTIFICATE-----",
        max_len=16384,
        hint="Paste the PEM of a self-signed / private CA: node-join installs it on the node and"
        " writes"
        " containerd tls.ca_file, the platform verifies the Harbor API with it too; leave empty for"
        " a"
        " public certificate",
    ),
    "registry_proxy_projects": SettingSpec(
        "registry",
        "text",
        line_pattern=r"[a-z0-9.-]+=[a-z0-9]+([._-][a-z0-9]+)*",
        max_len=2048,
        hint="Harbor proxy cache: one <upstream>=<proxy project> per line, e.g."
        " docker.io=dockerhub,"
        " ghcr.io=ghcr (create the project in Harbor as public first); node containerd mirrors the"
        " upstream through it and falls back to the upstream",
    ),
    "image_allowed_registries": SettingSpec(
        "registry",
        "text",
        line_pattern=r"[a-z0-9][a-z0-9.\-:/_]*",
        max_len=4096,
        hint="Image source allow-list for instance creation, one registry prefix per line (e.g."
        " docker.io/); empty = unrestricted; the Harbor address is always allowed, references from"
        " the"
        " platform image catalog always pass",
    ),
    "grafana_url": SettingSpec(
        "observability",
        "str",
        pattern=r"https?://\S+",
        hint='Optional Grafana URL; when set the admin nodes page shows an "Open in Grafana" link',
    ),
    "oncall_phone": SettingSpec(
        "observability",
        "str",
        pattern=r"|\+[1-9]\d{6,14}",
        hint="On-call phone: critical alerts are sent as SMS directly; empty = disabled",
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
    "freeze_grace_hours": _num("int", "1", "720", "Hours from arrears freeze to reclamation"),
    "afford_cover_hours": _num("int", "1", "24", "Hours the balance must cover before creation"),
    "prewarm_min_coverage_pct": _num("int", "1", "100"),
    "prewarm_recheck_hours": _num("int", "1", "168"),
    "max_instances_per_user": _num("int", "1", "1000"),
    "max_gpus_per_user": _num("int", "1", "1024"),
    "max_vcpus_per_user": _num("int", "1", "4096"),
    "max_disks_per_user": _num("int", "1", "1000"),
    "max_disk_gb_per_user": _num("int", "10", "1048576", "Total data-disk capacity per user"),
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
    """Strongly typed view of the effective configuration (env defaults + DB overrides); fields map
    one to one to SETTING_SPECS, types follow kind.
    Secrets are decrypted, so the whole object must never enter logs / responses."""

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
        """Effective image source allow-list (configured lines ∪ Harbor address prefix); empty =
        unrestricted."""
        return effective_image_allowlist(
            allowed_registries=self.image_allowed_registries, registry_host=self.registry_host
        )


RUNTIME_CONFIG_FIELDS: tuple[str, ...] = tuple(f.name for f in fields(RuntimeConfig))
if set(RUNTIME_CONFIG_FIELDS) != set(SETTING_SPECS):
    raise RuntimeError("RuntimeConfig fields must map one to one to SETTING_SPECS")


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
    """Merge env defaults and convert types; unknown keys and empty numeric overrides are ignored,
    no value validation."""
    merged = _env_layer()
    for key, value in values.items():
        spec = SETTING_SPECS.get(key)
        if spec is None or (value == "" and spec.kind in ("int", "decimal")):
            continue
        merged[key] = value
    return RuntimeConfig(**{k: _coerce(k, v) for k, v in merged.items()})


@dataclass(frozen=True)
class ConfigWarning:
    """Configuration risk (computed server-side): the admin config page badges and the prod lifespan
    boot log share one rule set."""

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
    """Keys whose effective value hits prod_forbidden (same source as the spec declarations; not
    environment-aware, the caller decides);
    only rules that apply to the current compliance profile."""
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
        message = (
            "CAPTCHA is on but the Aliyun CAPTCHA credentials / scene are incomplete, code sending"
            " will"
            " always be 502"
        )
    return None if complete else ConfigWarning("captcha_enabled", "error", message)


def compute_config_warnings(cfg: RuntimeConfig, environment: str) -> list[ConfigWarning]:
    """Combined risks of security switches and credentials. Badges for prod-forbidden values derive
    from SettingSpec.prod_forbidden."""
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
                "Identity verification is on but the Aliyun credentials are incomplete, user"
                " submissions"
                " will always be 502",
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
                "The registry has a robot account but no secret: pulls from private projects will"
                " fail",
            )
        )
    if prod and not cfg.image_allowlist():
        out.append(
            ConfigWarning(
                "image_allowed_registries",
                "error",
                "The production image source allow-list is empty and no Harbor address is set:"
                " tenants"
                " can pull images from any registry into the cluster",
            )
        )
    return out


def assert_prod_image_allowlist(cfg: RuntimeConfig, environment: str) -> None:
    """In prod the effective image allow-list (configured lines ∪ Harbor address) must not be empty,
    otherwise refuse to boot."""
    if environment == "prod" and not cfg.image_allowlist():
        raise RuntimeError(
            "production image source allow-list is empty and no Harbor address is set, refusing to"
            " boot: set registry_host or image_allowed_registries (env or platform configuration)"
        )


def assert_prod_compliance_gates(cfg: RuntimeConfig, environment: str) -> None:
    """In prod the effective values of prod_gate keys must not hit prod_forbidden (CAPTCHA / KYC /
    KYC-before-top-up),
    otherwise refuse to boot."""
    if environment != "prod":
        return
    gated = [key for key, spec in _prod_violations(cfg) if spec.prod_gate]
    if gated:
        raise RuntimeError(
            "production compliance gates are not all on, refusing to boot: "
            + ",".join(gated)
            + " (cn compliance requirement; turn them on via env or the platform configuration"
            " first)"
        )


def validate_setting_value(key: str, value: str) -> str:
    """Validate and normalise (strip; numeric kinds normalised by kind). Unknown keys / malformed
    values raise ValueError (the caller converts to AppError)."""
    spec = SETTING_SPECS.get(key)
    if spec is None:
        raise ValueError(f"unknown setting key: {key}")
    value = value.strip()
    if len(value) > spec.max_len:
        raise ValueError(f"{key} too long (at most {spec.max_len} characters)")
    if spec.kind == "bool" and value not in ("true", "false"):
        raise ValueError(f"{key} accepts only true/false")
    if spec.kind == "choice" and value not in spec.choices:
        raise ValueError(f"{key} accepts only: {'/'.join(spec.choices)}")
    if spec.kind in ("int", "decimal"):
        value = _validate_number(key, value, spec)
    if _prod_forbids(spec, value):
        raise ValueError(f"{key} value {value} is forbidden in production{_hint_suffix(spec)}")
    _validate_shape(key, value, spec)
    return value


def _hint_suffix(spec: SettingSpec) -> str:
    return f"({spec.hint})" if spec.hint else ""


def _validate_shape(key: str, value: str, spec: SettingSpec) -> None:
    """Shape allow-list: whole-string fullmatch / per-line fullmatch / required substring /
    forbidden
    substring."""
    suffix = _hint_suffix(spec)
    if spec.pattern and not re.fullmatch(spec.pattern, value):
        raise ValueError(f"{key} is malformed{suffix}")
    if spec.line_pattern is not None:
        for line in value.replace(",", "\n").splitlines():
            line = line.strip()
            if line and not re.fullmatch(spec.line_pattern, line):
                raise ValueError(f"{key} has an invalid line: {line[:64]!r}{suffix}")
    if spec.must_contain and spec.must_contain not in value:
        raise ValueError(f"{key} is malformed{suffix}")
    if spec.forbid_contains and spec.forbid_contains in value:
        raise ValueError(f"{key} is malformed{suffix}")


def _validate_number(key: str, value: str, spec: SettingSpec) -> str:
    assert spec.lo is not None and spec.hi is not None
    try:
        num = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{key} is not a valid number: {value}") from exc
    if spec.kind == "int" and num != num.to_integral_value():
        raise ValueError(f"{key} must be an integer: {value}")
    if not spec.lo <= num <= spec.hi:
        raise ValueError(f"{key} must be between {spec.lo} and {spec.hi}")
    if key == "spot_grace_seconds":
        budget = get_settings().creating_timeout_seconds - PREEMPT_TIME_RESERVE_SECONDS
        if num > budget:
            raise ValueError(
                f"spot_grace_seconds must not exceed {budget} seconds"
                f" (creating timeout {get_settings().creating_timeout_seconds}s minus the"
                f" {PREEMPT_TIME_RESERVE_SECONDS}s scheduling margin)"
            )
    return str(int(num)) if spec.kind == "int" else str(num)


def _to_string(value: object) -> str:
    """Field value → configuration-centre string form (bool as true/false, None as empty)."""
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
    """Validate format, range and prod-forbidden values of non-empty env settings; returns error
    descriptions."""
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
    """Decrypt one row, fail-closed: corrupt ciphertext / mismatched master key raises, no fallback
    to env."""
    try:
        return crypto.decrypt_str(value, aad=aad)
    except Exception as exc:
        logger.error("platform_setting_decrypt_failed", key=key)
        raise ValueError(
            f"platform setting {key}: decryption failed (master key mismatch or corrupt ciphertext)"
        ) from exc


async def effective_strings(session: AsyncSession) -> dict[str, str]:
    """String map of the effective configuration (secrets decrypted; shared by the admin config page
    and the RuntimeConfig build), not cached."""
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
    """Effective configuration (strongly typed); read in full every time, writes take effect at
    once."""
    return runtime_config_from_strings(await effective_strings(session))


async def set_platform_settings(
    session: AsyncSession,
    updates: dict[str, str],
    *,
    updated_by: int | None,
    allowed_groups: frozenset[str] | None = None,
) -> None:
    """Write overrides (no commit; the caller commits together with the audit). Empty string =
    clear the override, back to the env default.
    allowed_groups limits the groups this entry point may write: /policies only the policy group,
    /platform-config never the policy group.
    """
    for key, raw in updates.items():
        spec = SETTING_SPECS.get(key)
        if spec is None or (allowed_groups is not None and spec.group not in allowed_groups):
            raise ValueError(f"unknown setting key: {key}")
        PLATFORM_CONFIG_WRITE_TOTAL.labels(domain=spec.group).inc()
        if raw.strip() == "":
            fallback = _env_default(key)
            if _prod_forbids(spec, fallback):
                raise ValueError(
                    f"{key} cannot clear the override: it would fall back to the deployment value"
                    f" {fallback!r}, which is forbidden in production (write a compliant value"
                    " explicitly, or change the deployment env and clear afterwards)"
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
    """Masked preview: last 4 characters (meaningless for structured text such as PEM, then None and
    only "configured" is shown)."""
    plaintext = plaintext.strip()
    if len(plaintext) < 8 or "-----" in plaintext:
        return None
    return f"****{plaintext[-4:]}"
