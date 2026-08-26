import os
from collections.abc import Mapping
from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEV_JWT_SECRET = "dev-secret-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SUPERDL_", env_file=".env", extra="ignore")

    # 必填(fail-closed):漏配即拒绝启动。dev/test 的宽松默认(mock 支付、固定短信码、
    # /docs、mock webhook)只允许在显式声明的环境里存在
    environment: Literal["dev", "test", "prod"]

    database_url: str = "postgresql+asyncpg://superdl:superdl@localhost:5432/superdl"
    db_pool_size: int = 10

    # JWT:用户端与管理端物理隔离,audience 不同。
    # 令牌收紧基线:access ≤1h(前端 Web Locks 静默续期,用户无感)、refresh ≤7d;
    # prod 校验在 _validate_prod 兜底上限,防止经 env 放松
    jwt_secret: str = "dev-secret-change-me"
    jwt_issuer: str = "superdl"
    jwt_user_audience: str = "superdl:user"
    jwt_admin_audience: str = "superdl:admin"
    access_token_ttl_seconds: int = 3600
    refresh_token_ttl_seconds: int = 7 * 24 * 3600
    # 管理端两步验证(TOTP)总开关:开 = 全角色强制绑定并二要素登录;关 = 密码校验通过即签发 token
    # (已绑定者也不再挑战,重新开启即恢复)。可被平台配置中心覆盖;prod 关闭不拒启动,只给告警
    admin_mfa_enabled: bool = True

    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:5174"]

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

    # 实名认证(阿里云三要素):real_name_enabled 决定用户能否提交核验(关闭 = 409),
    # real_name_required_for_recharge 决定充值/开通实例是否强制已实名;两者与凭据均可被
    # 平台配置中心在线覆盖。不变量(任意环境):required=true ⇒ enabled=true
    # (_validate_invariants 与 platform_config 写入侧同口径),否则用户永远无法满足强制条件
    real_name_enabled: bool = False
    real_name_required_for_recharge: bool = False
    real_name_access_key_id: str | None = None
    real_name_access_key_secret: str | None = None

    # 人机校验(阿里云验证码 2.0,/auth/sms-code 前置闸)。开关与凭据均可被平台配置中心覆盖:
    # 关闭 = 跳过校验(prod 关闭不拒启动,由配置中心告警提示);scene/prefix 为客户端初始化公开信息
    captcha_enabled: bool = False
    captcha_scene_id: str | None = None
    captcha_prefix: str | None = None
    captcha_access_key_id: str | None = None
    captcha_access_key_secret: str | None = None

    # 平台配置中心:敏感项落库加密主密钥(urlsafe-base64 的 32 字节;只走 env,prod 必配)
    config_encryption_key: str | None = None

    # 合规备案(站点页脚;可被平台配置中心覆盖)
    icp_number: str | None = None
    police_record_number: str | None = None
    # 经营主体信息(《电子商务法》第十五条公示;页脚展示,留空即不展示)
    company_name: str | None = None
    company_address: str | None = None
    company_phone: str | None = None
    business_license_url: str | None = None

    # 客服联系方式(页脚与帮助页展示;可被平台配置中心覆盖)。留空即不展示该入口
    support_email: str | None = None
    support_wechat: str | None = None  # 企微/微信客服号或群二维码说明

    # 镜像仓库(Harbor):全部可被平台配置中心覆盖(env 为默认值层),机器人 Secret 经配置中心加密落库。
    # 拉取凭据由平台生成 K8s Secret(core/registry.PULL_SECRET_NAME)下发到 superdl 与各租户 ns
    registry_host: str = ""
    registry_project: str = "superdl"
    registry_robot_name: str = ""
    registry_robot_secret: str = ""
    registry_ca_pem: str = ""
    registry_proxy_projects: str = ""
    # 创建实例可用的镜像来源白名单:换行/逗号分隔的仓库前缀,空 = 不限制;生效白名单另含 Harbor 地址,
    # 平台镜像目录内的引用恒放行(core/registry.effective_image_allowlist)
    image_allowed_registries: str = ""

    # 每用户配额;K8s ResourceQuota 是集群侧兜底
    max_instances_per_user: int = 10
    max_gpus_per_user: int = 8
    max_disks_per_user: int = 20  # 数据盘数量上限

    # 计费参数(可运营调整)
    freeze_grace_hours: int = 72  # 欠费冻结时长
    # 开户前燃烧率校验:余额须覆盖「在途+新增」实例的这么多小时消耗(护栏,非预占)
    afford_cover_hours: int = 1
    creating_timeout_seconds: int = 300  # creating 超时 → failed 退款
    # running 实例的 Pod 持续 not-ready 多久判定不可用 → 停止计费。
    # 须宽于 K8s unreachable taint 的 tolerationSeconds(默认 300),本判定为兜底。
    running_unready_timeout_seconds: int = 600
    # stopping/releasing 悬挂超时:第一档经 outbox 重发删除,第二档 force 强删。
    stopping_timeout_seconds: int = 600
    releasing_timeout_seconds: int = 600
    # 泄漏回收熔断:未知(DB 无记录)Pod 占比超过该值即中止本轮回收并告警
    leak_reclaim_abort_ratio: float = 0.5
    # 长期停机/失败实例的实例盘保留期,到期转 releasing 回收;stopped 提前 warn_days 通知。
    # 数据盘不受影响。
    failed_retention_days: int = 7
    stopped_retention_days: int = 30
    stopped_retention_warn_days: int = 7
    # Jupyter 一次性入场票据有效期(access 端点签发的 bootstrap URL)
    jupyter_ticket_ttl_seconds: int = 60

    # 镜像预热(可运营调整)
    prewarm_min_coverage_pct: int = 90  # is_prewarmed=true 所需的节点覆盖率下限
    prewarm_recheck_hours: int = 24  # cached 复检窗口

    # 集群接入(节点一键加入;env 为默认值层,生产经管理端「平台配置·集群接入」录入)
    cluster_server_url: str = ""
    # secret:平台配置中心 AES-GCM 加密存 DB 覆盖层
    cluster_join_token: str = ""
    cluster_agent_version: str = "v1.36.2+rke2r1"  # 装机脚本钉死的 K8s agent 版本
    node_driver_version: str = "580"
    node_registries_yaml: str = ""
    node_install_mirror: Literal["cn", "official"] = "cn"  # 装机安装源(国内默认走镜像)

    # K8s 编排(dev 默认 fake)
    k8s_backend: Literal["fake", "real"] = "fake"
    # 共享档 Pod 注 HAMi use-gputype annotation(SKU 原文串);仅混卡节点池需要,默认关
    hami_use_gputype: bool = False
    k8s_namespace_prefix: str = "tenant-"
    # 平台侧 Job(数据盘配额等 JuiceFS 元数据操作)所在 ns:与 superdl-api-secrets 同 ns,
    # Job 以 secretKeyRef 读 juicefs-metaurl,worker 进程零接触明文
    k8s_platform_namespace: str = "superdl"
    # JuiceFS CLI 镜像(quota set/delete):与 deploy/cluster/helmfile 的 CSI chart 钉版对齐
    juicefs_cli_image: str = "juicedata/juicefs-csi-driver:v0.32.3"
    # 每次 K8s 请求的超时(连接, 读);官方客户端无全局超时,须显式设置
    k8s_connect_timeout_seconds: float = 5.0
    k8s_read_timeout_seconds: float = 30.0
    # 租户 Jupyter Ingress 的 IngressClass;未标 default 的 IngressClass 不自动接管,须显式指定
    ingress_class_name: str = "nginx"
    ssh_host: str = "ssh1.superdl.example.com"
    # 管理端域名(admin SPA 经该域 nginx 同源反代 /api/admin/);prod 下 /api/admin/*
    # 仅放行 Host 命中本项的请求(公网 api 域不暴露管理端 API)
    admin_host: str = "admin.superdl.example.com"
    ssh_port_range_start: int = 30000
    ssh_port_range_end: int = 32767
    # 已知被集群其它对象占用的 NodePort(端口池与 NodePort 同段),分配器跳过;
    # 运行期撞到的其它占用由 PortAllocation 标 blocked。
    ssh_port_excluded: set[int] = {30500}  # registry(deploy/cluster/registry/)
    jupyter_domain_suffix: str = "app.superdl.example.com"
    # 实例 Jupyter 主机名 = <前缀><实例 uuid>.<jupyter_domain_suffix>。前缀默认空;当后缀是与其它业务
    # 共用的一级域(如为了复用 *.<域> 通配证书——通配只匹配一级标签,盖不住 <uuid>.app.<域>)时,
    # 用前缀把实例域名从共用域里区分出来(如 superdl-)。DNS 通配仍按整段标签匹配(*.<域>)
    jupyter_host_prefix: str = ""

    # 告警接入
    alertmanager_token: str | None = None
    # 可观测性(平台配置 observability 组的 env 默认值层):Grafana 外链、critical 告警值班手机号
    grafana_url: str = ""
    oncall_phone: str = ""

    # /metrics 抓取鉴权(Prometheus scrape 配置同一 Bearer;prod 必配)
    metrics_token: str | None = None

    # 日志级别(structlog 与 stdlib 桥接同受此控;大写,默认 INFO)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # 数据保洁保留期
    audit_retention_days: int = 365  # 等保要求 ≥6 个月

    # Prometheus 代理
    prometheus_url: str = "http://localhost:9090"
    # 单次 PromQL 请求超时;代理查询可放大(批量端点 cap 20),超时必须显式可调
    prometheus_timeout_seconds: float = 5.0

    # 支付(dev 用 mock 渠道;真实商户凭据经环境变量注入)
    payment_mock: bool = True
    public_base_url: str = "https://api.superdl.example.com"
    recharge_order_ttl_seconds: int = 2 * 3600
    payment_wechat_enabled: bool = False  # 渠道开关
    payment_alipay_enabled: bool = False
    wechat_mchid: str | None = None
    wechat_private_key: str | None = None
    wechat_cert_serial_no: str | None = None
    wechat_apiv3_key: str | None = None
    wechat_appid: str | None = None
    wechat_public_key: str | None = None  # 公钥模式(新商户唯一可用模式)
    wechat_public_key_id: str | None = None  # PUB_KEY_ID_*
    alipay_app_id: str | None = None
    alipay_private_key: str | None = None
    alipay_public_key: str | None = None
    alipay_seller_id: str | None = None  # 收款方 PID(2088 开头;prod 启用支付宝时必填)

    @model_validator(mode="after")
    def _validate_invariants(self) -> "Settings":
        """环境无关的组合约束(dev/test 同样拦;平台配置写入侧 _check_real_name_invariant 同口径)。"""
        if self.real_name_required_for_recharge and not self.real_name_enabled:
            raise ValueError(
                "real_name_required_for_recharge=true 需要 real_name_enabled=true"
                "(实名未开通时用户无法完成实名,充值与开通实例会被永久卡住)"
            )
        return self

    @model_validator(mode="after")
    def _validate_prod(self) -> "Settings":
        """生产配置 fail-fast:开发默认值未改则拒绝启动。

        只管 provider 选择与基础设施项,不查渠道凭据齐全性:短信/验证码/实名凭据可经平台配置
        中心在线录入(DB 覆盖层),运行期渠道工厂(core/sms.py、core/captcha.py)缺凭据即
        fail-closed。alertmanager_token 缺失与 prometheus_url 指向本地只在 lifespan 打 WARNING。
        """
        if self.environment != "prod":
            return self
        problems: list[str] = []
        if self.jwt_secret == _DEV_JWT_SECRET or len(self.jwt_secret) < 32:
            problems.append("jwt_secret 仍为开发默认值或长度不足 32 字符")
        if self.access_token_ttl_seconds > 3600:
            problems.append("access_token_ttl_seconds 超过 1 小时上限(令牌收紧基线)")
        if self.refresh_token_ttl_seconds > 7 * 24 * 3600:
            problems.append("refresh_token_ttl_seconds 超过 7 天上限(令牌收紧基线)")
        if self.sms_provider == "mock":
            problems.append("sms_provider 不得为 mock(验证码将是固定值)")
        if self.k8s_backend == "fake":
            problems.append("k8s_backend 不得为 fake")
        if self.payment_mock:
            problems.append("payment_mock 必须为 false")
        if "superdl:superdl@localhost" in self.database_url:
            problems.append("database_url 仍为本地开发默认")
        if any("localhost" in o or "127.0.0.1" in o for o in self.cors_origins):
            problems.append("cors_origins 含 localhost")
        for name in ("ssh_host", "jupyter_domain_suffix", "public_base_url", "admin_host"):
            if "example.com" in getattr(self, name):
                problems.append(f"{name} 仍为占位域名")
        if self.payment_alipay_enabled and not self.alipay_seller_id:
            # DB 覆盖层也可能已配:env 侧缺失只作 fail-fast 提示的其中一路;
            # 渠道构造期(payment_channels.AlipayChannel)对 effective 配置再拦一次
            problems.append(
                "payment_alipay_enabled=true 时 alipay_seller_id 必填"
                "(收款方 PID,2088 开头;缺失则回调无法核对收款账号)"
            )
        if not self.metrics_token:
            problems.append("metrics_token 未配置(/metrics 将无鉴权暴露)")
        if not self.config_encryption_key:
            problems.append("config_encryption_key 未配置(平台配置敏感项加密主密钥)")
        else:
            import base64

            try:
                if len(base64.urlsafe_b64decode(self.config_encryption_key)) != 32:
                    problems.append("config_encryption_key 解码后须为 32 字节")
            except ValueError:
                problems.append("config_encryption_key 不是合法 urlsafe-base64")
        if problems:
            raise ValueError("生产配置校验失败:" + ";".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    # environment 为必填项(fail-closed),运行期由 pydantic-settings 从 SUPERDL_ENVIRONMENT 注入;
    # 静态检查看不到 env 填充,故忽略 call-arg
    return Settings()  # pyright: ignore[reportCallIssue]


def unknown_superdl_env_keys(env: Mapping[str, str] | None = None) -> list[str]:
    """扫描 SUPERDL_ 前缀环境变量,返回不命中任何 Settings 字段的键。

    幽灵键(拼写错误、改名残留)会被 pydantic 静默忽略,配置者以为生效其实没有;
    启动时打 WARNING 即可,不 fail(兼容滚动发版期间新旧键并存)。
    """
    source = os.environ if env is None else env
    known = {f"SUPERDL_{name.upper()}" for name in Settings.model_fields}
    # pydantic-settings 默认大小写不敏感,统一按大写比对
    return sorted(k for k in source if k.startswith("SUPERDL_") and k.upper() not in known)
