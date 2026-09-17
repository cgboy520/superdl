import base64
import ipaddress
import os
import re
from decimal import Decimal, InvalidOperation
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
    """Environment-independent combined constraint: required_for_recharge=true ⇒ enabled=true.
    Shared by the Settings boot validation and the platform-config write side."""
    if required_for_recharge and not enabled:
        raise ValueError(
            "real_name_required_for_recharge=true requires real_name_enabled=true"
            " (users could never verify, so top-ups and instance creation would stay blocked)"
        )


def decode_master_key(raw: str, *, label: str) -> bytes:
    """Decode the master key (urlsafe-base64, 32 bytes decoded); label goes into the error
    message."""
    try:
        key = base64.urlsafe_b64decode(raw)
    except ValueError as exc:
        raise ValueError(f"{label} is not valid urlsafe-base64") from exc
    if len(key) != 32:
        raise ValueError(f"{label} must decode to 32 bytes")
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
    recharge_min: str = "1.00"
    recharge_max: str = "50000.00"
    recharge_presets: str = "50,100,500"
    disk_min_gb: int = 10
    disk_max_gb: int = 4096
    disk_grace_days: int = 7
    disk_frozen_days: int = 30

    real_name_enabled: bool = False
    real_name_required_for_recharge: bool = False
    real_name_max_accounts_per_identity: int = 3
    real_name_access_key_id: str | None = None
    real_name_access_key_secret: str | None = None
    kyc_provider: Literal["aliyun_mobile3"] = "aliyun_mobile3"

    captcha_enabled: bool = False
    captcha_scene_id: str | None = None
    captcha_prefix: str | None = None
    captcha_access_key_id: str | None = None
    captcha_access_key_secret: str | None = None
    captcha_provider: Literal["aliyun", "turnstile"] = "turnstile"
    captcha_turnstile_site_key: str | None = None
    captcha_turnstile_secret_key: str | None = None

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
    node_install_mirror: Literal["official", "cn"] = "official"

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
    web_base_url: str = "http://localhost:5173"
    recharge_order_ttl_seconds: int = 2 * 3600
    payment_wechat_enabled: bool = False
    payment_alipay_enabled: bool = False
    payment_stripe_enabled: bool = False
    stripe_secret_key: str | None = None
    stripe_webhook_secret: str | None = None
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

    def _validate_recharge_policy(self) -> None:
        """recharge_min ≤ recharge_max and every preset inside the range, on the env layer too;
        malformed numbers are reported by `env_layer_problems`."""
        try:
            minimum, maximum = Decimal(self.recharge_min), Decimal(self.recharge_max)
            presets = [Decimal(p) for p in self.recharge_presets.split(",") if p]
        except InvalidOperation:
            return
        if minimum > maximum:
            raise ValueError(f"recharge_min ({minimum}) must not exceed recharge_max ({maximum})")
        for preset in presets:
            if not minimum <= preset <= maximum:
                raise ValueError(f"recharge preset {preset} is outside {minimum}~{maximum}")

    @model_validator(mode="after")
    def _validate_invariants(self) -> "Settings":
        """Validate the KYC combination, CORS, shared pools, addresses, CIDR and key formats; the
        master key and the previous key must differ."""
        check_real_name_invariant(
            enabled=self.real_name_enabled,
            required_for_recharge=self.real_name_required_for_recharge,
        )
        self._validate_recharge_policy()
        self._validate_deployment_identity()
        if "*" in self.cors_origins:
            raise ValueError(
                "cors_origins must not contain the wildcard *"
                " (with allow_credentials=true it means any origin)"
            )
        bad_pools = set(self.parsed_shared_tier_pools()) - {"mig", "hami"}
        if bad_pools:
            raise ValueError(
                f"shared_tier_allowed_pools has unknown pools: {sorted(bad_pools)} (only mig/hami)"
            )
        if not _URL_RE.match(self.public_base_url):
            raise ValueError(
                "public_base_url is malformed: must be http(s)://<host>[:port][/path] without"
                " whitespace or shell metacharacters (pasted verbatim into node-join.sh, run as"
                " root)"
            )
        if not _URL_RE.match(self.web_base_url):
            raise ValueError("web_base_url must be http(s)://<host>[:port][/path]")
        for name in ("jupyter_domain_suffix", "service_domain_suffix", "admin_host"):
            value = getattr(self, name)
            if value and not _HOSTNAME_RE.match(value):
                raise ValueError(
                    f"{name} is malformed: must be a bare hostname (port allowed),"
                    " no scheme or metacharacters"
                )
        if self.tenant_pod_cidr:
            try:
                ipaddress.ip_network(self.tenant_pod_cidr, strict=False)
            except ValueError as exc:
                raise ValueError(
                    f"tenant_pod_cidr is not a valid network: {self.tenant_pod_cidr}"
                ) from exc
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
                "config_encryption_key_previous equals the current master key: the rotation window"
                " must carry the OLD key; identical keys mean no rotation happened"
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
        """Pools allowed for the shared tier (comma-separated, blanks and empty items dropped)."""
        return tuple(p.strip() for p in self.shared_tier_allowed_pools.split(",") if p.strip())

    def _secret_domains(self) -> frozenset[str]:
        """Secret domains mounted into this process."""
        if self.process_role == "api":
            return frozenset({"auth", "crypto", "edge", "cloud", "payment", "registry"})
        return _WORKER_SECRET_DOMAINS.get(self.worker_component, _WORKER_SECRET_DOMAINS["all"])

    @model_validator(mode="after")
    def _validate_prod(self) -> "Settings":
        """prod configuration fail-fast: only providers and infrastructure items; channel
        credentials fail closed at runtime in the channel factories.
        Four groups: process secrets / stand-in providers (only the Secret domains mounted into this
        component) / database / public addresses."""
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
            raise ValueError("production configuration check failed: " + "; ".join(problems))
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
                "jwt_secret is still the development default / a placeholder / low-entropy"
                " (needs >= 32 characters with >= 16 distinct ones; generate: openssl rand -hex 32)"
            )
        if self.access_token_ttl_seconds > 3600:
            out.append("access_token_ttl_seconds exceeds the 1-hour cap")
        if self.refresh_token_ttl_seconds > 7 * 24 * 3600:
            out.append("refresh_token_ttl_seconds exceeds the 7-day cap")
        if self.bcrypt_rounds < 12:
            out.append("bcrypt_rounds below 12 (password hash too weak)")
        if not self.metrics_token:
            out.append("metrics_token unset (/metrics would be exposed without auth)")
        if self.process_role == "api" and not self.admin_edge_token:
            out.append(
                "admin_edge_token unset (admin edge shared secret: one of the two /api/admin gates)"
            )
        if "crypto" in domains and not self.config_encryption_key:
            out.append("config_encryption_key unset (master key for sensitive platform settings)")
        return out

    def _prod_provider_problems(self) -> list[str]:
        out: list[str] = []
        domains = self._secret_domains()
        if "cloud" in domains and self.sms_provider == "mock":
            out.append("sms_provider must not be mock (verification codes would be a fixed value)")
        if "cloud" in domains and self.email_provider == "mock":
            out.append(
                "email_provider must not be mock in prod "
                "(verification codes would be a fixed value)"
            )
        if self.k8s_backend == "fake":
            out.append("k8s_backend must not be fake")
        if "payment" in domains and self.payment_mock:
            out.append("payment_mock must be false")
        if "payment" in domains and not self.web_base_url.startswith("https://"):
            out.append("web_base_url must be an https URL in prod (payment return URLs)")
        if self.payment_alipay_enabled and not self.alipay_seller_id:
            out.append(
                "alipay_seller_id is required when payment_alipay_enabled=true"
                " (payee PID starting with 2088; without it callbacks cannot verify the payee)"
            )
        return out

    def _prod_database_problems(self) -> list[str]:
        out: list[str] = []
        if "superdl:superdl@localhost" in self.database_url:
            out.append("database_url is still the local development default")
        parsed = urlparse(self.database_url)
        if (parsed.hostname or "") not in ("localhost", "127.0.0.1", "::1"):
            sslmode = parse_qs(parsed.query).get("sslmode", [""])[0]
            if sslmode not in ("require", "verify-ca", "verify-full"):
                out.append(
                    "database_url points at a non-local PG without TLS:"
                    " add ?sslmode=require (or verify-ca/verify-full)"
                )
        return out

    def _prod_endpoint_problems(self) -> list[str]:
        out: list[str] = []
        if any("localhost" in o or "127.0.0.1" in o for o in self.cors_origins):
            out.append("cors_origins contains localhost")
        out.extend(
            f"{name} is still a placeholder domain"
            for name in (
                "jupyter_domain_suffix",
                "service_domain_suffix",
                "public_base_url",
                "admin_host",
            )
            if "example.com" in getattr(self, name)
        )
        if not self.public_base_url.startswith("https://"):
            out.append(
                "public_base_url must start with https://"
                " (the install script and enrollment tokens use it)"
            )
        return out


@lru_cache
def get_settings() -> Settings:
    return Settings()  # pyright: ignore[reportCallIssue]


def unknown_superdl_env_keys() -> list[str]:
    """SUPERDL_-prefixed environment variables matching no Settings field (WARNING only at boot)."""
    known = {f"SUPERDL_{name.upper()}" for name in Settings.model_fields}
    return sorted(k for k in os.environ if k.startswith("SUPERDL_") and k.upper() not in known)
