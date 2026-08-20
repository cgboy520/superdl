from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEV_JWT_SECRET = "dev-secret-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SUPERDL_", env_file=".env", extra="ignore")

    environment: Literal["dev", "test", "prod"] = "dev"

    database_url: str = "postgresql+asyncpg://superdl:superdl@localhost:5432/superdl"
    db_pool_size: int = 10

    # JWT:用户端与管理端物理隔离,audience 不同
    jwt_secret: str = "dev-secret-change-me"
    jwt_issuer: str = "superdl"
    jwt_user_audience: str = "superdl:user"
    jwt_admin_audience: str = "superdl:admin"
    access_token_ttl_seconds: int = 2 * 3600
    refresh_token_ttl_seconds: int = 30 * 24 * 3600

    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:5174"]

    # 启动引导管理员:默认关闭。仅当显式设置 SUPERDL_BOOTSTRAP_ADMIN_PASSWORD
    # 且 environment=dev 时,在无任何管理员的库里创建 admin 账号。生产用运维脚本创建。
    bootstrap_admin_password: str | None = None

    # 短信:dev/test 用 mock(验证码固定 + 落日志);aliyun 凭据与模板码经环境变量注入
    sms_provider: Literal["mock", "aliyun"] = "mock"
    sms_code_ttl_seconds: int = 300
    sms_send_interval_seconds: int = 60
    sms_access_key_id: str | None = None
    sms_access_key_secret: str | None = None
    sms_sign_name: str | None = None  # 报备的短信签名
    sms_template_verify: str | None = None  # 验证码模板码(变量 code)
    sms_template_notice: str | None = None  # 通知模板码(变量 title)

    # 数据盘
    disk_price_gb_month: str = "0.0350"  # 元/GB·月(Decimal 字符串,新盘快照)
    disk_min_gb: int = 10
    disk_max_gb: int = 4096
    disk_grace_days: int = 7
    disk_frozen_days: int = 30

    # 实名认证:资质就绪后打开(充值前强制;《网络安全法》要求)。
    # provider/凭据与开关均可被平台配置中心(platform_settings)在线覆盖
    real_name_required_for_recharge: bool = False
    real_name_provider: Literal["mock", "aliyun"] = "mock"
    real_name_access_key_id: str | None = None
    real_name_access_key_secret: str | None = None

    # 平台配置中心:敏感项落库加密主密钥(urlsafe-base64 的 32 字节;只走 env,prod 必配)
    config_encryption_key: str | None = None

    # 合规备案(站点页脚;可被平台配置中心覆盖)
    icp_number: str | None = None
    police_record_number: str | None = None

    # 每用户配额(防单账号无限开机;K8s ResourceQuota 是集群侧兜底)
    max_instances_per_user: int = 10
    max_gpus_per_user: int = 8

    # 计费参数(可运营调整)
    freeze_grace_hours: int = 72  # 欠费冻结时长
    low_balance_warn_hours: int = 24  # 预估可用时长低于此值预警
    creating_timeout_seconds: int = 300  # creating 超时 → failed 退款

    # 镜像预热(WP22,可运营调整)
    prewarm_min_coverage_pct: int = 90  # is_prewarmed=true 所需的节点覆盖率下限
    prewarm_recheck_hours: int = 24  # cached 复检窗口(防 kubelet 镜像 GC 后状态失真)

    # 集群接入(WP23 节点一键加入;env 为默认层,生产建议经管理端「平台配置·集群接入」录入)
    rke2_server_url: str = ""
    rke2_join_token: str = ""  # secret:平台配置中心 AES-GCM 加密存 DB 覆盖层
    rke2_version: str = "v1.36.2+rke2r1"  # 装机脚本 INSTALL_RKE2_VERSION 钉死,实机核定后更新
    node_driver_version: str = "580"
    node_registries_yaml: str = ""

    # K8s 编排(dev 默认 fake)
    k8s_backend: Literal["fake", "real"] = "fake"
    k8s_namespace_prefix: str = "tenant-"
    ssh_host: str = "ssh1.superdl.example.com"
    ssh_port_range_start: int = 30000
    ssh_port_range_end: int = 32767
    jupyter_domain_suffix: str = "app.superdl.example.com"

    # 告警接入
    alertmanager_token: str | None = None

    # /metrics 抓取鉴权(Prometheus scrape 配置同一 Bearer;prod 必配)
    metrics_token: str | None = None

    # 错误上报(可选:配置 DSN 且安装 sentry-sdk 即启用)
    sentry_dsn: str | None = None

    # 数据保洁保留期
    audit_retention_days: int = 365  # 等保要求日志留存 ≥6 个月,默认留 1 年

    # Prometheus 代理
    prometheus_url: str = "http://localhost:9090"

    # 支付(dev 用 mock 渠道;真实商户凭据经环境变量注入,人工事项 #6)
    payment_mock: bool = True
    public_base_url: str = "https://api.superdl.example.com"
    recharge_order_ttl_seconds: int = 2 * 3600
    payment_wechat_enabled: bool = False  # 渠道开关(凭据配置完成后在管理端开启)
    payment_alipay_enabled: bool = False
    wechat_mchid: str | None = None
    wechat_private_key: str | None = None
    wechat_cert_serial_no: str | None = None
    wechat_apiv3_key: str | None = None
    wechat_appid: str | None = None
    wechat_public_key: str | None = None  # 微信支付公钥模式(2024-10 后新商户唯一模式)
    wechat_public_key_id: str | None = None  # PUB_KEY_ID_*
    alipay_app_id: str | None = None
    alipay_private_key: str | None = None
    alipay_public_key: str | None = None

    @model_validator(mode="after")
    def _validate_prod(self) -> "Settings":
        """生产配置 fail-fast:任何开发默认值漏改都在启动时拒绝,而非静默事故。"""
        if self.environment != "prod":
            return self
        problems: list[str] = []
        if self.jwt_secret == _DEV_JWT_SECRET or len(self.jwt_secret) < 32:
            problems.append("jwt_secret 仍为开发默认值或长度不足 32 字符")
        if self.sms_provider == "mock":
            problems.append("sms_provider 不得为 mock(验证码将是固定值)")
        elif not (
            self.sms_access_key_id
            and self.sms_access_key_secret
            and self.sms_sign_name
            and self.sms_template_verify
            and self.sms_template_notice
        ):
            problems.append("阿里云短信凭据/签名/模板码不完整(SUPERDL_SMS_*)")
        if self.k8s_backend == "fake":
            problems.append("k8s_backend 不得为 fake")
        if self.payment_mock:
            problems.append("payment_mock 必须为 false")
        if "superdl:superdl@localhost" in self.database_url:
            problems.append("database_url 仍为本地开发默认")
        if any("localhost" in o or "127.0.0.1" in o for o in self.cors_origins):
            problems.append("cors_origins 含 localhost")
        for name in ("ssh_host", "jupyter_domain_suffix", "public_base_url"):
            if "example.com" in getattr(self, name):
                problems.append(f"{name} 仍为占位域名")
        if not self.alertmanager_token:
            problems.append("alertmanager_token 未配置")
        if not self.metrics_token:
            problems.append("metrics_token 未配置(/metrics 将无鉴权暴露)")
        if not self.config_encryption_key:
            problems.append("config_encryption_key 未配置(平台配置敏感项加密主密钥)")
        else:
            import base64

            try:
                if len(base64.urlsafe_b64decode(self.config_encryption_key)) != 32:
                    problems.append("config_encryption_key 解码后须为 32 字节")
            except Exception:
                problems.append("config_encryption_key 不是合法 urlsafe-base64")
        if problems:
            raise ValueError("生产配置校验失败:" + ";".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
