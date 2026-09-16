import base64
import ipaddress
import os
import re
from functools import lru_cache
from typing import Literal
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.currencies import SUPPORTED_CURRENCIES

_DEV_JWT_SECRET = "dev-secret-change-me"

_URL_PATTERN = (
    r"https?://"
    r"[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*"
    r"(:\d{1,5})?"
    r"(/[A-Za-z0-9._~/-]*)?"
)
_URL_RE = re.compile(rf"^{_URL_PATTERN}$")
_HOSTNAME_RE = re.compile(
    r"^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*(:\d{1,5})?$"
)


def check_real_name_invariant(*, enabled: bool, required_for_recharge: bool) -> None:
    """环境无关的组合约束:required_for_recharge=true ⇒ enabled=true。
    Settings 启动校验与平台配置中心写入侧共用同一实现。"""
    if required_for_recharge and not enabled:
        raise ValueError(
            "real_name_required_for_recharge=true 需要 real_name_enabled=true"
            "(实名未开通时用户无法完成实名,充值与开通实例会被永久卡住)"
        )


def decode_master_key(raw: str, *, label: str) -> bytes:
    """主密钥解码(urlsafe-base64,解码后 32 字节);label 进错误消息。"""
    try:
        key = base64.urlsafe_b64decode(raw)
    except ValueError as exc:
        raise ValueError(f"{label} 不是合法 urlsafe-base64") from exc
    if len(key) != 32:
        raise ValueError(f"{label} 解码后须为 32 字节")
    return key


_WORKER_SECRET_DOMAINS: dict[str, frozenset[str]] = {
    "all": frozenset({"crypto", "cloud", "payment", "registry"}),
    "core": frozenset({"crypto", "cloud", "payment"}),
    "tenant-mgr": frozenset({"crypto", "registry"}),
    "node-mgr": frozenset({"crypto"}),
    "prewarm": frozenset({"crypto", "registry"}),
    "disk-ops": frozenset(),
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SUPERDL_", env_file=".env", extra="ignore")

    environment: Literal["dev", "test", "prod"]
    compliance_profile: Literal["none", "cn"] | None = None
    platform_currency: str = "USD"
    billing_timezone: str = "UTC"
    billing_identity_rekey: bool = False

    database_url: str = "postgresql+asyncpg://superdl:superdl@localhost:5432/superdl"
    db_pool_size: int = 10
    db_max_overflow: int = 10
    db_pool_timeout_seconds: int = 10

    jwt_secret: str = _DEV_JWT_SECRET
    jwt_issuer: str = "superdl"
    jwt_user_audience: str = "superdl:user"
    jwt_admin_audience: str = "superdl:admin"
    access_token_ttl_seconds: int = 3600
    refresh_token_ttl_seconds: int = 7 * 24 * 3600
    bcrypt_rounds: int = Field(default=12, ge=4, le=31)
    admin_mfa_enabled: bool = True

    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:5174"]

    sms_provider: Literal["mock", "aliyun", "twilio"] = "mock"
    sms_code_ttl_seconds: int = 300
    sms_send_interval_seconds: int = 60
    sms_access_key_id: str | None = None
    sms_access_key_secret: str | None = None
    sms_sign_name: str | None = None
    sms_template_verify: str | None = None
    sms_template_notice: str | None = None
    sms_twilio_account_sid: str | None = None
    sms_twilio_auth_token: str | None = None
    sms_twilio_from: str | None = None

    email_provider: Literal["mock", "smtp"] = "mock"
    smtp_host: str | None = None
    smtp_port: str = "587"
    smtp_security: Literal["starttls", "tls", "none"] = "starttls"
    smtp_username: str | None = None
    smtp_password: str | None = None
    email_from: str | None = None
    email_reply_to: str | None = None

    disk_price_gb_month: str = "0.0350"
    disk_min_gb: int = 10
    disk_max_gb: int = 4096
    disk_grace_days: int = 7
    disk_frozen_days: int = 30

    real_name_enabled: bool = False
    real_name_required_for_recharge: bool = False
    real_name_max_accounts_per_identity: int = 3
    real_name_access_key_id: str | None = None
    real_name_access_key_secret: str | None = None

    captcha_enabled: bool = False
    captcha_scene_id: str | None = None
    captcha_prefix: str | None = None
    captcha_access_key_id: str | None = None
    captcha_access_key_secret: str | None = None

    config_encryption_key: str | None = None
    config_encryption_key_previous: str | None = None

    icp_number: str | None = None
    police_record_number: str | None = None
    company_name: str | None = None
    company_address: str | None = None
    company_phone: str | None = None
    business_license_url: str | None = None

    support_email: str | None = None
    support_wechat: str | None = None

    registry_host: str = ""
    registry_project: str = "superdl"
    registry_robot_name: str = ""
    registry_robot_secret: str = ""
    registry_ca_pem: str = ""
    registry_proxy_projects: str = ""
    image_allowed_registries: str = ""

    max_instances_per_user: int = 10
    max_gpus_per_user: int = 8
    max_vcpus_per_user: int = 64
    max_disks_per_user: int = 20
    max_disk_gb_per_user: int = 8192
    gpu_node_cpu_instance_vcpu_cap: int = 16
    tenant_egress_bandwidth_mbps: int = 200
    tenant_ingress_bandwidth_mbps: int = 0

    period_discount_day: int = 95
    period_discount_week: int = 90
    period_discount_month: int = 80
    period_discount_year: int = 70
    period_expire_warn_days: int = 3
    spot_discount_pct: int = 40
    spot_grace_seconds: int = 60

    freeze_grace_hours: int = 72
    afford_cover_hours: int = 1
    creating_timeout_seconds: int = 300
    running_unready_timeout_seconds: int = 600
    stopping_timeout_seconds: int = 600
    releasing_timeout_seconds: int = 600
    leak_reclaim_abort_ratio: float = 0.5
    failed_retention_days: int = 7
    stopped_retention_days: int = 30
    stopped_retention_warn_days: int = 7
    jupyter_ticket_ttl_seconds: int = 60

    prewarm_min_coverage_pct: int = 90
    prewarm_recheck_hours: int = 24

    cluster_server_url: str = ""
    cluster_join_token: str = ""
    cluster_agent_version: str = "v1.36.2+rke2r1"
    node_driver_version: str = "580"
    node_registries_yaml: str = ""
    node_install_mirror: Literal["cn", "official"] = "cn"

    k8s_backend: Literal["fake", "real"] = "fake"
    tenant_pod_cidr: str = "10.42.0.0/16"
    shared_tier_allowed_pools: str = "mig,hami"
    hami_use_gputype: bool = False
    k8s_namespace_prefix: str = "tenant-"
    k8s_platform_namespace: str = "superdl"
    k8s_connect_timeout_seconds: float = 5.0
    k8s_read_timeout_seconds: float = 30.0
    admin_host: str = "admin.superdl.example.com"
    admin_edge_token: str = ""
    process_role: Literal["api", "worker"] = "api"
    ssh_port_range_start: int = 30000
    ssh_port_range_end: int = 32767
    ssh_port_excluded: set[int] = {30500}
    jupyter_domain_suffix: str = "app.superdl.example.com"
    jupyter_host_prefix: str = ""
    jupyter_url_port: int = 443
    service_domain_suffix: str = "svc.superdl.example.com"

    alertmanager_token: str | None = None
    grafana_url: str = ""
    oncall_phone: str = ""

    metrics_token: str | None = None

    worker_outbox_concurrency: int = 4
    worker_heartbeat: str | None = None
    worker_metrics_port: int = 9000
    worker_component: str = "all"

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    audit_retention_days: int = 365

    prometheus_url: str = "http://localhost:9090"
    prometheus_timeout_seconds: float = 5.0

    payment_mock: bool = True
    public_base_url: str = "https://api.superdl.example.com"
    recharge_order_ttl_seconds: int = 2 * 3600
    payment_wechat_enabled: bool = False
    payment_alipay_enabled: bool = False
    wechat_mchid: str | None = None
    wechat_private_key: str | None = None
    wechat_cert_serial_no: str | None = None
    wechat_apiv3_key: str | None = None
    wechat_appid: str | None = None
    wechat_public_key: str | None = None
    wechat_public_key_id: str | None = None
    alipay_app_id: str | None = None
    alipay_private_key: str | None = None
    alipay_public_key: str | None = None
    alipay_seller_id: str | None = None

    @model_validator(mode="after")
    def _validate_invariants(self) -> "Settings":
        """校验实名组合、CORS、共享池、地址、网段和密钥格式;主密钥与 previous 不得相同。"""
        check_real_name_invariant(
            enabled=self.real_name_enabled,
            required_for_recharge=self.real_name_required_for_recharge,
        )
        self._validate_deployment_identity()
        if "*" in self.cors_origins:
            raise ValueError("cors_origins 不允许通配符 *(allow_credentials=true 下等于全网放行)")
        bad_pools = set(self.parsed_shared_tier_pools()) - {"mig", "hami"}
        if bad_pools:
            raise ValueError(
                f"shared_tier_allowed_pools 含未知池:{sorted(bad_pools)}(只认 mig/hami)"
            )
        if not _URL_RE.match(self.public_base_url):
            raise ValueError(
                "public_base_url 形态非法:须为 http(s)://<主机>[:端口][/路径],"
                "不得含空白或 shell 元字符(它会逐字进 node-join.sh 的 root 执行上下文)"
            )
        for name in ("jupyter_domain_suffix", "service_domain_suffix", "admin_host"):
            value = getattr(self, name)
            if value and not _HOSTNAME_RE.match(value):
                raise ValueError(f"{name} 形态非法:须为裸主机名(可带端口),不得含协议头或元字符")
        if self.tenant_pod_cidr:
            try:
                ipaddress.ip_network(self.tenant_pod_cidr, strict=False)
            except ValueError as exc:
                raise ValueError(f"tenant_pod_cidr 不是合法网段:{self.tenant_pod_cidr}") from exc
        for name in ("config_encryption_key", "config_encryption_key_previous"):
            raw = getattr(self, name)
            if raw is not None:
                decode_master_key(raw, label=name)
        if (
            self.config_encryption_key
            and self.config_encryption_key_previous
            and self.config_encryption_key == self.config_encryption_key_previous
        ):
            raise ValueError(
                "config_encryption_key_previous 与当前主密钥相同:轮换窗口应挂「旧」密钥,"
                "相同等于没轮换(钥匙串里去重后仍是一把)"
            )
        return self

    def _validate_deployment_identity(self) -> None:
        """Currency must be in the supported table; the billing timezone must be an IANA zone."""
        if self.platform_currency not in SUPPORTED_CURRENCIES:
            raise ValueError(
                f"platform_currency {self.platform_currency!r} is not supported "
                f"(one of {', '.join(sorted(SUPPORTED_CURRENCIES))})"
            )
        try:
            ZoneInfo(self.billing_timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(
                f"billing_timezone {self.billing_timezone!r} is not an IANA zone name "
                "(e.g. UTC, Asia/Shanghai, America/New_York)"
            ) from exc

    def parsed_shared_tier_pools(self) -> tuple[str, ...]:
        """共享档允许池(逗号分隔,去空白去空项)。"""
        return tuple(p.strip() for p in self.shared_tier_allowed_pools.split(",") if p.strip())

    def _secret_domains(self) -> frozenset[str]:
        """本进程挂载的 Secret 域。"""
        if self.process_role == "api":
            return frozenset({"auth", "crypto", "edge", "cloud", "payment", "registry"})
        return _WORKER_SECRET_DOMAINS.get(self.worker_component, _WORKER_SECRET_DOMAINS["all"])

    @model_validator(mode="after")
    def _validate_prod(self) -> "Settings":
        """prod 配置 fail-fast:只管 provider 与基础设施项;渠道凭据由渠道工厂运行期 fail-closed。
        分四组:进程凭据 / 替身 provider(只校验本组件挂载的 Secret 域)/ 数据库 / 对外地址。"""
        if self.environment != "prod":
            return self
        problems = [
            *self._prod_deployment_problems(),
            *self._prod_secret_problems(),
            *self._prod_provider_problems(),
            *self._prod_database_problems(),
            *self._prod_endpoint_problems(),
        ]
        if problems:
            raise ValueError("生产配置校验失败:" + ";".join(problems))
        return self

    def _prod_deployment_problems(self) -> list[str]:
        if self.compliance_profile is None:
            return [
                "compliance_profile must be set explicitly in prod "
                "(SUPERDL_COMPLIANCE_PROFILE=none|cn; it selects which regional gates apply)"
            ]
        return []

    def _prod_secret_problems(self) -> list[str]:
        out: list[str] = []
        domains = self._secret_domains()
        if self.process_role == "api" and (
            self.jwt_secret == _DEV_JWT_SECRET
            or "change_me" in self.jwt_secret.lower()
            or len(self.jwt_secret) < 32
            or len(set(self.jwt_secret)) < 16
        ):
            out.append(
                "jwt_secret 仍为开发默认值/占位符/低熵串"
                "(需 ≥32 字符且唯一字符 ≥16;生成:openssl rand -hex 32)"
            )
        if self.access_token_ttl_seconds > 3600:
            out.append("access_token_ttl_seconds 超过 1 小时上限")
        if self.refresh_token_ttl_seconds > 7 * 24 * 3600:
            out.append("refresh_token_ttl_seconds 超过 7 天上限")
        if self.bcrypt_rounds < 12:
            out.append("bcrypt_rounds 低于 12(口令哈希强度不足)")
        if not self.metrics_token:
            out.append("metrics_token 未配置(/metrics 将无鉴权暴露)")
        if self.process_role == "api" and not self.admin_edge_token:
            out.append("admin_edge_token 未配置(管理端边缘共享密钥:/api/admin 双闸的其中一闸)")
        if "crypto" in domains and not self.config_encryption_key:
            out.append("config_encryption_key 未配置(平台配置敏感项加密主密钥)")
        return out

    def _prod_provider_problems(self) -> list[str]:
        out: list[str] = []
        domains = self._secret_domains()
        if "cloud" in domains and self.sms_provider == "mock":
            out.append("sms_provider 不得为 mock(验证码将是固定值)")
        if "cloud" in domains and self.email_provider == "mock":
            out.append(
                "email_provider must not be mock in prod "
                "(verification codes would be a fixed value)"
            )
        if self.k8s_backend == "fake":
            out.append("k8s_backend 不得为 fake")
        if "payment" in domains and self.payment_mock:
            out.append("payment_mock 必须为 false")
        if self.payment_alipay_enabled and not self.alipay_seller_id:
            out.append(
                "payment_alipay_enabled=true 时 alipay_seller_id 必填"
                "(收款方 PID,2088 开头;缺失则回调无法核对收款账号)"
            )
        return out

    def _prod_database_problems(self) -> list[str]:
        out: list[str] = []
        if "superdl:superdl@localhost" in self.database_url:
            out.append("database_url 仍为本地开发默认")
        parsed = urlparse(self.database_url)
        if (parsed.hostname or "") not in ("localhost", "127.0.0.1", "::1"):
            sslmode = parse_qs(parsed.query).get("sslmode", [""])[0]
            if sslmode not in ("require", "verify-ca", "verify-full"):
                out.append(
                    "database_url 指向非本机 PG 但无 TLS:"
                    "加 ?sslmode=require(或 verify-ca/verify-full)"
                )
        return out

    def _prod_endpoint_problems(self) -> list[str]:
        out: list[str] = []
        if any("localhost" in o or "127.0.0.1" in o for o in self.cors_origins):
            out.append("cors_origins 含 localhost")
        out.extend(
            f"{name} 仍为占位域名"
            for name in (
                "jupyter_domain_suffix",
                "service_domain_suffix",
                "public_base_url",
                "admin_host",
            )
            if "example.com" in getattr(self, name)
        )
        if not self.public_base_url.startswith("https://"):
            out.append("public_base_url 必须是 https://(装机脚本与注册令牌走这条链路)")
        return out


@lru_cache
def get_settings() -> Settings:
    return Settings()  # pyright: ignore[reportCallIssue]


def unknown_superdl_env_keys() -> list[str]:
    """返回 SUPERDL_ 前缀里不命中任何 Settings 字段的环境变量键(启动时只 WARNING)。"""
    known = {f"SUPERDL_{name.upper()}" for name in Settings.model_fields}
    return sorted(k for k in os.environ if k.startswith("SUPERDL_") and k.upper() not in known)
