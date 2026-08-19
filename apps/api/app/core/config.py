from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # 短信:dev/test 用 mock(验证码固定 + 落日志)
    sms_provider: Literal["mock", "aliyun"] = "mock"
    sms_code_ttl_seconds: int = 300
    sms_send_interval_seconds: int = 60

    # 数据盘
    disk_price_gb_month: str = "0.0350"  # 元/GB·月(Decimal 字符串,新盘快照)
    disk_min_gb: int = 10
    disk_max_gb: int = 4096
    disk_grace_days: int = 7
    disk_frozen_days: int = 30

    # 计费参数(可运营调整)
    freeze_grace_hours: int = 72  # 欠费冻结时长
    low_balance_warn_hours: int = 24  # 预估可用时长低于此值预警
    creating_timeout_seconds: int = 300  # creating 超时 → failed 退款

    # K8s 编排(dev 默认 fake)
    k8s_backend: Literal["fake", "real"] = "fake"
    k8s_namespace_prefix: str = "tenant-"
    ssh_host: str = "ssh1.superdl.example.com"
    ssh_port_range_start: int = 30000
    ssh_port_range_end: int = 32767
    jupyter_domain_suffix: str = "app.superdl.example.com"

    # 告警接入
    alertmanager_token: str | None = None

    # Prometheus 代理
    prometheus_url: str = "http://localhost:9090"

    # 支付(dev 用 mock 渠道;真实商户凭据经环境变量注入,人工事项 #6)
    payment_mock: bool = True
    public_base_url: str = "https://api.superdl.example.com"
    recharge_order_ttl_seconds: int = 2 * 3600
    wechat_mchid: str | None = None
    wechat_private_key: str | None = None
    wechat_cert_serial_no: str | None = None
    wechat_apiv3_key: str | None = None
    wechat_appid: str | None = None
    alipay_app_id: str | None = None
    alipay_private_key: str | None = None
    alipay_public_key: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
