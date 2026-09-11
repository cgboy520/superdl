import base64
import ipaddress
import os
import re
from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEV_JWT_SECRET = "dev-secret-change-me"

# 对外地址形态白名单(锚定,拒绝 shell 元字符与空白);public_base_url 逐字替换进 node-join.sh
_URL_PATTERN = (
    r"https?://"
    r"[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*"
    r"(:\d{1,5})?"
    r"(/[A-Za-z0-9._~/-]*)?"
)
_URL_RE = re.compile(rf"^{_URL_PATTERN}$")
# 裸主机名(可带端口):域名后缀与 admin_host 同口径
_HOSTNAME_RE = re.compile(
    r"^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*(:\d{1,5})?$"
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


# worker 组件消费的 Secret 域,与 deploy/app/k8s/03-worker.yaml envFrom 同源(改一起改);
# superdl-db / superdl-metrics 人人都有,不列。all = 单进程跑全部组件
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

    # 必填;mock 支付 / 固定短信码 / docs / mock webhook 只在 dev/test 存在
    environment: Literal["dev", "test", "prod"]

    database_url: str = "postgresql+asyncpg://superdl:superdl@localhost:5432/superdl"
    db_pool_size: int = 10

    # JWT:用户端与管理端 audience 分离;prod 上限 access ≤1h、refresh ≤7d(_validate_prod)
    jwt_secret: str = _DEV_JWT_SECRET
    jwt_issuer: str = "superdl"
    jwt_user_audience: str = "superdl:user"
    jwt_admin_audience: str = "superdl:admin"
    access_token_ttl_seconds: int = 3600
    refresh_token_ttl_seconds: int = 7 * 24 * 3600
    # 口令 bcrypt cost(prod 下限 12,_validate_prod);测试降到 4 换速度,哈希自带 cost 可互认
    bcrypt_rounds: int = Field(default=12, ge=4, le=31)
    # 管理端 TOTP 总开关:开 = 全角色强制绑定并二要素登录;关 = 口令即签发。可被配置中心覆盖;
    # prod 关闭只告警
    admin_mfa_enabled: bool = True

    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:5174"]

    # 短信:mock(固定码 + 落日志)/ aliyun
    sms_provider: Literal["mock", "aliyun"] = "mock"
    sms_code_ttl_seconds: int = 300
    sms_send_interval_seconds: int = 60
    sms_access_key_id: str | None = None
    sms_access_key_secret: str | None = None
    sms_sign_name: str | None = None  # 报备的短信签名
    sms_template_verify: str | None = None  # 验证码模板码(变量 code)
    sms_template_notice: str | None = None  # 通知模板码(变量 title)

    disk_price_gb_month: str = "0.0350"  # 元/GB·月(Decimal 字符串,新盘快照)
    disk_min_gb: int = 10
    disk_max_gb: int = 4096
    disk_grace_days: int = 7
    disk_frozen_days: int = 30

    # 实名(阿里云三要素):enabled 关闭 = 提交核验 409;required 决定充值/开机是否强制已实名。
    # 均可被配置中心覆盖;不变量 required=true ⇒ enabled=true(platform_config 写入侧同口径)
    real_name_enabled: bool = False
    real_name_required_for_recharge: bool = False
    real_name_access_key_id: str | None = None
    real_name_access_key_secret: str | None = None

    # 人机校验(阿里云验证码 2.0,/auth/sms-code 前置闸);可被配置中心覆盖;
    # prod 必须开启(platform_config.assert_prod_compliance_gates);scene/prefix 为客户端公开信息
    captcha_enabled: bool = False
    captcha_scene_id: str | None = None
    captcha_prefix: str | None = None
    captcha_access_key_id: str | None = None
    captcha_access_key_secret: str | None = None

    # 配置中心敏感项加密主密钥(urlsafe-base64 32 字节;只走 env,prod 必配);previous 只参与
    # 解密/摘要回读,写入用当前密钥。轮换见 deploy/cluster/runbooks/key-rotation.md
    config_encryption_key: str | None = None
    config_encryption_key_previous: str | None = None

    # 合规备案(页脚;可被配置中心覆盖)
    icp_number: str | None = None
    police_record_number: str | None = None
    # 经营主体信息(页脚;留空不展示)
    company_name: str | None = None
    company_address: str | None = None
    company_phone: str | None = None
    business_license_url: str | None = None

    # 客服联系方式(页脚与帮助页;可被配置中心覆盖;留空不展示)
    support_email: str | None = None
    support_wechat: str | None = None  # 企微/微信客服号或群二维码说明

    # 镜像仓库(Harbor):可被配置中心覆盖;拉取凭据生成 K8s Secret(core/registry.PULL_SECRET_NAME)
    registry_host: str = ""
    registry_project: str = "superdl"
    registry_robot_name: str = ""
    registry_robot_secret: str = ""
    registry_ca_pem: str = ""
    registry_proxy_projects: str = ""
    # 镜像来源白名单:换行/逗号分隔的仓库前缀,空 = 不限制(core/registry.effective_image_allowlist)
    image_allowed_registries: str = ""

    # 每用户配额(K8s ResourceQuota 为集群侧兜底)
    max_instances_per_user: int = 10
    max_gpus_per_user: int = 8
    max_vcpus_per_user: int = 64  # 只计 CPU 实例
    max_disks_per_user: int = 20
    # 单个 GPU 节点让给 CPU 实例的 vCPU 上限;0 = 不许 CPU 实例落 GPU 节点
    gpu_node_cpu_instance_vcpu_cap: int = 16

    # 包周期折扣(百分数,80 = 8 折)
    period_discount_day: int = 95
    period_discount_week: int = 90
    period_discount_month: int = 80
    period_discount_year: int = 70
    period_expire_warn_days: int = 3
    # 竞价价 = 按量价 × pct/100;抢占前宽限窗(秒)
    spot_discount_pct: int = 40
    spot_grace_seconds: int = 60

    # 计费参数
    freeze_grace_hours: int = 72  # 欠费冻结时长
    afford_cover_hours: int = 1  # 开机前余额须覆盖「在途+新增」实例的小时数(护栏,非预占)
    creating_timeout_seconds: int = 300  # creating 超时 → failed 退款
    # running 实例 Pod 持续 not-ready 判不可用的时长;须宽于 K8s unreachable tolerationSeconds(300)
    running_unready_timeout_seconds: int = 600
    # stopping/releasing 悬挂超时:一档 outbox 重发删除,二档 force 强删
    stopping_timeout_seconds: int = 600
    releasing_timeout_seconds: int = 600
    leak_reclaim_abort_ratio: float = 0.5  # 未知 Pod 占比超过即中止本轮回收并告警
    # 停机/失败实例的实例盘保留期(数据盘不受影响),到期转 releasing;stopped 提前 warn_days 通知
    failed_retention_days: int = 7
    stopped_retention_days: int = 30
    stopped_retention_warn_days: int = 7
    jupyter_ticket_ttl_seconds: int = 60  # Jupyter 一次性入场票据有效期

    # 镜像预热
    prewarm_min_coverage_pct: int = 90  # is_prewarmed=true 的节点覆盖率下限
    prewarm_recheck_hours: int = 24  # cached 复检窗口

    # 集群接入(节点一键加入;可经管理端「平台配置·集群接入」覆盖)
    cluster_server_url: str = ""
    cluster_join_token: str = ""  # secret
    cluster_agent_version: str = "v1.36.2+rke2r1"  # 装机脚本钉死的 agent 版本
    node_driver_version: str = "580"
    node_registries_yaml: str = ""
    node_install_mirror: Literal["cn", "official"] = "cn"  # 装机安装源

    # K8s 编排
    k8s_backend: Literal["fake", "real"] = "fake"
    # 集群 Pod 网段:租户 NetworkPolicy 的 SSH 入方向据此排除 Pod→Pod;与 deploy/app/k8s 的
    # FORWARDED_ALLOW_IPS 同源,改 CNI 网段一起改。留空 = 不加 except(仅排障)
    tenant_pod_cidr: str = "10.42.0.0/16"
    # 共享档允许落的池(逗号分隔:mig=硬隔离,hami=软限额;空 = 共享档停售);建 SKU 与改池同拦
    shared_tier_allowed_pools: str = "mig,hami"
    hami_use_gputype: bool = False  # 共享档 Pod 注 HAMi use-gputype annotation;仅混卡池需要
    k8s_namespace_prefix: str = "tenant-"
    # 平台侧 Job(JuiceFS 配额等)所在 ns,与 superdl-db 同 ns,Job 以 secretKeyRef 读 juicefs-metaurl
    k8s_platform_namespace: str = "superdl"
    # JuiceFS CLI 镜像,与 deploy/cluster/helmfile 的 CSI chart 版本对齐
    juicefs_cli_image: str = "juicedata/juicefs-csi-driver:v0.32.3"
    # 每次 K8s 请求超时(连接, 读)
    k8s_connect_timeout_seconds: float = 5.0
    k8s_read_timeout_seconds: float = 30.0
    # 管理端域名;prod 下 /api/admin/* 仅放行 Host 命中本项的请求
    admin_host: str = "admin.superdl.example.com"
    # 管理端边缘共享密钥(admin 域反代注 X-Admin-Edge-Token,edge_guard 第二闸);prod 必填
    admin_edge_token: str = ""
    # 进程角色;worker 不挂 superdl-auth / superdl-edge,prod 校验按角色跳过 jwt_secret 与
    # admin_edge_token(deploy/app/k8s/03-worker.yaml 置 worker)
    process_role: Literal["api", "worker"] = "api"
    # SSH 无独立域名:连接串用实例 Jupyter 域名 + NodePort(orchestrator/service.jupyter_host);
    # 泛域名解析到的地址须能转发本端口段
    ssh_port_range_start: int = 30000
    ssh_port_range_end: int = 32767
    # 集群其它对象占用的 NodePort,分配器跳过;运行期撞到的由 PortAllocation 标 blocked
    ssh_port_excluded: set[int] = {30500}  # registry(deploy/cluster/registry/)
    jupyter_domain_suffix: str = "app.superdl.example.com"
    # Jupyter 主机名 = <前缀><uuid>.<jupyter_domain_suffix>;后缀与其它业务共用一级域时用前缀区分
    jupyter_host_prefix: str = ""
    # Jupyter 入场 URL 与 JUPYTER_ALLOW_ORIGIN 的端口;443 = 不带端口。两个 listener 共用一张
    # 一级通配证书、靠端口分流时改它;HTTPRoute hostname 与 SSH 连接串不带端口
    jupyter_url_port: int = 443
    # 服务端点后缀:主机名 = <slug>.<service_domain_suffix>;独立 listener,只有它挂 extAuth
    # (core/k8s/base.GATEWAY_SVC_LISTENER)
    service_domain_suffix: str = "svc.superdl.example.com"

    alertmanager_token: str | None = None
    # 可观测性(可被配置中心覆盖):Grafana 外链、critical 告警值班手机号
    grafana_url: str = ""
    oncall_phone: str = ""

    # /metrics 抓取 Bearer(prod 必配)
    metrics_token: str | None = None

    # worker 进程
    worker_outbox_concurrency: int = 4  # outbox 并发领取协程数
    worker_heartbeat: str | None = None  # liveness 心跳文件;None = /tmp/superdl-worker-heartbeat
    worker_metrics_port: int = 9000  # worker 进程内 /metrics 端口(PodMonitor 直抓)
    worker_component: str = "all"  # 组件身份(workers/components.py;非法值拒启)

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    audit_retention_days: int = 365  # 等保要求 ≥6 个月

    # Prometheus 代理
    prometheus_url: str = "http://localhost:9090"
    prometheus_timeout_seconds: float = 5.0  # 单次 PromQL 超时

    # 支付
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
    wechat_public_key: str | None = None  # 公钥模式
    wechat_public_key_id: str | None = None  # PUB_KEY_ID_*
    alipay_app_id: str | None = None
    alipay_private_key: str | None = None
    alipay_public_key: str | None = None
    alipay_seller_id: str | None = None  # 收款方 PID(2088 开头;prod 启用时必填)

    @model_validator(mode="after")
    def _validate_invariants(self) -> "Settings":
        """环境无关的组合约束(平台配置写入侧 _check_real_name_invariant 同口径)。"""
        if self.real_name_required_for_recharge and not self.real_name_enabled:
            raise ValueError(
                "real_name_required_for_recharge=true 需要 real_name_enabled=true"
                "(实名未开通时用户无法完成实名,充值与开通实例会被永久卡住)"
            )
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
                self._check_master_key_format(name, raw)
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

    @staticmethod
    def _check_master_key_format(name: str, raw: str) -> None:
        """主密钥格式:urlsafe-base64 且解码后 32 字节。"""
        decode_master_key(raw, label=name)

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
        """prod 配置 fail-fast:只管 provider 与基础设施项;渠道凭据由渠道工厂运行期 fail-closed。"""
        if self.environment != "prod":
            return self
        problems: list[str] = []
        # 占位符与低熵串都拒:长度 ≥32 且唯一字符 ≥16
        if self.process_role == "api" and (
            self.jwt_secret == _DEV_JWT_SECRET
            or "change_me" in self.jwt_secret.lower()
            or len(self.jwt_secret) < 32
            or len(set(self.jwt_secret)) < 16
        ):
            problems.append(
                "jwt_secret 仍为开发默认值/占位符/低熵串"
                "(需 ≥32 字符且唯一字符 ≥16;生成:openssl rand -hex 32)"
            )
        if self.access_token_ttl_seconds > 3600:
            problems.append("access_token_ttl_seconds 超过 1 小时上限")
        if self.refresh_token_ttl_seconds > 7 * 24 * 3600:
            problems.append("refresh_token_ttl_seconds 超过 7 天上限")
        if self.bcrypt_rounds < 12:
            problems.append("bcrypt_rounds 低于 12(口令哈希强度不足)")
        # 只校验本组件挂载的 Secret 域
        domains = self._secret_domains()
        if "cloud" in domains and self.sms_provider == "mock":
            problems.append("sms_provider 不得为 mock(验证码将是固定值)")
        if self.k8s_backend == "fake":
            problems.append("k8s_backend 不得为 fake")
        if "payment" in domains and self.payment_mock:
            problems.append("payment_mock 必须为 false")
        if "superdl:superdl@localhost" in self.database_url:
            problems.append("database_url 仍为本地开发默认")
        # 非本机 PG 必须 TLS(db._split_db_tls 翻译成 asyncpg ssl 参数)
        from urllib.parse import parse_qs, urlparse

        db_host = urlparse(self.database_url).hostname or ""
        if db_host not in ("localhost", "127.0.0.1", "::1"):
            sslmode = parse_qs(urlparse(self.database_url).query).get("sslmode", [""])[0]
            if sslmode not in ("require", "verify-ca", "verify-full"):
                problems.append(
                    "database_url 指向非本机 PG 但无 TLS:"
                    "加 ?sslmode=require(或 verify-ca/verify-full)"
                )
        if any("localhost" in o or "127.0.0.1" in o for o in self.cors_origins):
            problems.append("cors_origins 含 localhost")
        for name in (
            "jupyter_domain_suffix",
            "service_domain_suffix",
            "public_base_url",
            "admin_host",
        ):
            if "example.com" in getattr(self, name):
                problems.append(f"{name} 仍为占位域名")
        # public_base_url 承载装机脚本与注册令牌,必须 https
        if not self.public_base_url.startswith("https://"):
            problems.append("public_base_url 必须是 https://(装机脚本与注册令牌走这条链路)")
        if self.payment_alipay_enabled and not self.alipay_seller_id:
            # 渠道构造期(payment_channels.AlipayChannel)对 effective 配置再拦一次
            problems.append(
                "payment_alipay_enabled=true 时 alipay_seller_id 必填"
                "(收款方 PID,2088 开头;缺失则回调无法核对收款账号)"
            )
        if not self.metrics_token:
            problems.append("metrics_token 未配置(/metrics 将无鉴权暴露)")
        if self.process_role == "api" and not self.admin_edge_token:
            problems.append("admin_edge_token 未配置(管理端边缘共享密钥:/api/admin 双闸的其中一闸)")
        if "crypto" in domains and not self.config_encryption_key:
            problems.append("config_encryption_key 未配置(平台配置敏感项加密主密钥)")
        if problems:
            raise ValueError("生产配置校验失败:" + ";".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()  # pyright: ignore[reportCallIssue]


def unknown_superdl_env_keys() -> list[str]:
    """返回 SUPERDL_ 前缀里不命中任何 Settings 字段的环境变量键(启动时只 WARNING)。"""
    known = {f"SUPERDL_{name.upper()}" for name in Settings.model_fields}
    return sorted(k for k in os.environ if k.startswith("SUPERDL_") and k.upper() not in known)
