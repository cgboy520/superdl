# 安全与合规

启动期配置校验、限流、传输与租户隔离、镜像来源、合规页面。

## 数据模型

- `rate_limit_counters`:key(PK,含维度前缀)、window_start、hits、updated_at —— 固定窗口计数

## 规则与不变量

- `Settings._validate_prod` 在 prod 下 fail-fast,任一项不合格即拒绝启动(API 与 worker 同一份校验,这份清单就是生产必配项清单;部署模板见 `deploy/app/k8s/00-namespace-config.yaml` 非密与 `deploy/app/secrets.example.yaml` 密):jwt_secret 仍为开发默认或不足 32 字符;access token TTL >1h 或 refresh TTL >7d;`sms_provider=mock`;`k8s_backend=fake`;`payment_mock=true`;database_url 仍为本地默认;cors_origins 含 localhost;jupyter_domain_suffix / public_base_url / admin_host 仍为 example.com 占位;`payment_alipay_enabled=true` 而 `alipay_seller_id` 缺失;metrics_token 未配;config_encryption_key 缺失或非 32 字节 urlsafe-base64。
- 人机验证与实名认证是运行期开关(平台配置·安全策略 `captcha_enabled` / `real_name_enabled`,默认关),不是 provider 选择:关闭即跳过对应校验(发码不带 `captcha_token`、实名提交返 409);prod 关闭不拒启动(放弃这道纵深是运营决定,写入需 reason 进审计),但必须看得见:`compute_config_warnings` 在管理端配置页顶部出红牌、在 prod lifespan 打 error/warning。没有 mock 渠道,测试经 `set_captcha_channel` / `set_realname_provider` 注入。唯一组合约束:`real_name_required_for_recharge=true ⇒ real_name_enabled=true`,任意环境生效(`Settings._validate_invariants` 与写入侧 `_check_real_name_invariant` 同口径)。
- 启动校验只管 provider 选择,不查渠道凭据齐全性:短信 / 验证码 / 实名凭据可经平台配置中心在线录入,运行期渠道工厂(`app/core/sms.py`、`app/core/captcha.py`)缺凭据即 fail-closed,管理端 `test-sms` 可验。`alertmanager_token` 未配与 `prometheus_url` 仍指向本地只在 lifespan 打 WARNING(webhook 端点未配 token 本就拒收,指标子系统优雅降级)。`k8s_backend=real` 不要求 `environment=prod`:真实集群的暴露面由部署拓扑决定,实机验证需要 dev + real(决策见 `docs/decisions.md`「安全」)。
- 首个管理员由脚本创建(`ensure_bootstrap_admin`:`admin_users` 为空时才建,口令 ≥12 字符、≤72 字节),没有启动期引导变量:dev/test 随 `apps/api/scripts/seed_dev.py` 种子一起建;prod 用 `apps/api/scripts/bootstrap_admin.py`(走完整 Settings 校验,只建管理员,口令取 `SUPERDL_SEED_ADMIN_PASSWORD` 或随机生成只打印一次);幽灵 `SUPERDL_*` 环境变量(不命中任何字段)启动打 WARNING 但不 fail。
- 限流计数落 PG(`rate_limit_counters`),不用进程内计数;429 响应带 `Retry-After`(窗口剩余秒数,DB 侧计算),401 统一带 `WWW-Authenticate: Bearer`。
- 统一错误体覆盖框架层异常:路由 404/405 等 StarletteHTTPException 也渲染 `{code, message, message_key, params, detail}`(405 用 `METHOD_NOT_ALLOWED`/`common.methodNotAllowed`);未捕获异常由内层 `Uniform500Middleware` 渲染成 500 响应,审计中间件按响应状态码落 `result=500` 审计行。
- 安全响应头由纯 ASGI 中间件统一注入;`/metrics` 须 Bearer 鉴权(见 [observability.md](./observability.md))。
- 边缘收口中间件(`app/core/edge_guard.py`):`/api/admin/*` 仅放行 Host 命中 `admin_host` 的请求,其余 404;`/metrics` 带 `X-Forwarded-For`(经 ingress 进入)一律 404,集群内直刮不带该头,与 Bearer 双闸并存。prod 恒开、无开关:`environment` 只有 dev / test / prod 三值,类生产环境(staging)也以 `prod` 运行(独立 secrets),收口随之生效;dev / test 无 ingress,不启用。
- 短信渠道走 `app/core/sms.py` 的 Protocol + 工厂(mock / 阿里云 dysmsapi RPC 签名),不在业务代码里直连渠道 SDK;落日志时手机号与验证码由全局日志处理器(`app/core/logging.py`)按键名打码,渠道不各自打码。
- Bearer token 常量时间比较统一走 `app/core/http.py` 的 `bearer_matches`(先 `.encode()` 成 bytes:compare_digest 收 str 遇非 ASCII 会抛 TypeError),/metrics(API 与 worker)与 Alertmanager webhook 三处共用。
- 租户容器加固基线(`core/k8s/real.py::tenant_security_context`,无条件下发):`allowPrivilegeEscalation=false`、
  `seccompProfile=RuntimeDefault`、`capabilities.drop=[ALL]` 之后只 add 回 `SYS_CHROOT` / `SETUID` / `SETGID` ——
  OpenSSH 的预认证特权分离强制需要这三个,缺任一个则平台承诺的 `ssh root@` 入口在密钥交换阶段即断(见 `docs/decisions.md`);
  容器本就以 root 跑在自己的 user namespace 里,这三个不产生新的宿主侧权限。不下发 `runAsNonRoot`(平台镜像以 root 运行)。
- 租户 Pod 必须带 Egress 隔离 NetworkPolicy:禁访内网网段(含 CGNAT 100.64.0.0/10 与云元数据地址),并按明确滥用途 TCP 端口黑名单封禁 SMTP(25/465/587)、SMB/NetBIOS(135/139/445)、Telnet(23)、RDP(3389);HTTPS/SSH 出/包管理/对象存储等正常用途不受影响。租户 ns 打 PSA 标签(enforce=baseline、audit/warn=restricted;平台镜像以 root 运行,不能 enforce=restricted),容器有 ephemeral-storage 限额。
- 每租户独立 namespace + ResourceQuota 兜底 + 独立 JuiceFS PVC;JuiceFS 子路径须校验合法性,拒绝越界路径。
- 创建实例只校验镜像引用形态(域名/路径/tag/digest 合法,`core/registry.is_valid_image_ref`),来源白名单默认关;需要收紧时在平台配置·镜像仓库填 `image_allowed_registries`(每行一个仓库前缀),生效白名单 = 配置行 ∪ Harbor 地址前缀(`core/registry.effective_image_allowlist`),配置后只放行平台镜像目录内的引用与这些前缀;prod 下白名单为空且未配 Harbor 地址只给配置告警(`compute_config_warnings`),不拒启动。
- 合规:前端 `/legal/terms` 与 `/legal/privacy` 为模板页,注册勾选前后端强校验,备案号运行期下发。
- 高危管理操作一律「原因必填 → 二次确认 → 审计」;审计不落 token、密钥与配置值。
- 密钥与凭据不入 git,只经环境变量或平台配置中心注入;deploy 模板一律 `CHANGE_ME` 占位。
- Secret 模板放 `deploy/app/secrets.example.yaml`,不得放在整目录 apply 路径下。

## 限流分层

两层纵深:边缘层(ingress-nginx,`deploy/app/k8s/04-ingress.yaml`)对公网 API 域按单 IP 兜底 20 rps / 600 rpm / 20 并发连接,只挡洪水;精细化限流全在应用层(`app/core/ratelimit.py`,PG 固定窗口计数,多副本共享,429 带 `Retry-After`)。管理面(admin host)不配边缘限流——源 IP 白名单是更强的边界。各端点的应用层限额以模块文档为准,汇总表见 [limits.md](./limits.md)。

## 已接受取舍(评审在案,勿再单独立项)

- **token 存 localStorage**:CSP 已按站点拆分强约束(web 放行域白名单化,admin 严格 'self'),XSS 面已由 CSP/框架转义收敛;HttpOnly Cookie 换 CSRF 面的收益不抵改造成本。
- **固定窗口限流 2× 突发**:窗口边界双倍突发可接受,应用层语义简单可测;精度敏感动作(MFA/短信)窗口与配额已按威胁模型单独收紧。
- **用户端无 2FA**:SMS 验证码是信任根(运营商实名体系),用户端不加 TOTP;管理端全角色强制 TOTP + 恢复码 + 防重放(timestep 单调)。强制与否是安全策略开关 `admin_mfa_enabled`(默认开;关 = 全员免二要素,prod 关闭属高危运营动作,需 reason 进审计)。
- **仅 +86 手机号**:监管与短信通道约束,不做国际号段。
- **pending 订单 48h 收敛窗**:超时关单由查单 poller 收敛,不再无限挂起。
- **双人制衡「一人控两账号」残余**:组织流程(账号实名到人)兜底,技术层已拦「同账号复核自己」与「发起后新建账号复核」。
- **POLICY_LABELS 运营术语不译**:管理端内部术语(如 dedup_key 原文)不进 i18n,保持排障检索一致。
