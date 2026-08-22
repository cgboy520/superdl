# 安全与合规

启动期配置校验、限流、传输与租户隔离、镜像来源、合规页面。

## 数据模型

- `rate_limit_counters`:key(PK,含维度前缀)、window_start、hits、updated_at —— 固定窗口计数

## 规则与不变量

- `Settings._validate_prod` 在 prod 下 fail-fast,任一项不合格即拒绝启动:jwt_secret 仍为开发默认或不足 32 字符、`sms_provider=mock` 或阿里云短信凭据/签名/模板不全、`k8s_backend=fake`、`payment_mock=true`、database_url 仍为本地默认、cors_origins 含 localhost、ssh_host/jupyter_domain_suffix/public_base_url 仍为占位域名、prometheus_url 指向本地、alertmanager_token 未配、metrics_token 未配、config_encryption_key 缺失或非 32 字节 urlsafe-base64、`bootstrap_admin_password` 已设置(一次性 dev 引导变量,初始化后必须删除)、`real_name_required_for_recharge=true` 而 `real_name_provider=mock`(mock 恒过等于实名虚设)、`image_allowed_registries` 为空(空=不限制镜像来源)。
- 启动引导管理员仅 `environment=dev` 生效,口令长度 ≥12(与管理端创建约束对齐);非 prod 启动打 WARNING(防忘记显式设置 `SUPERDL_ENVIRONMENT=prod`),幽灵 `SUPERDL_*` 环境变量(不命中任何字段/别名)启动打 WARNING 但不 fail。
- 限流计数落 PG(`rate_limit_counters`),不用进程内计数(多副本下失效);429 响应带 `Retry-After`(窗口剩余秒数,DB 侧计算),401 统一带 `WWW-Authenticate: Bearer`。
- 统一错误体覆盖框架层异常:路由 404/405 等 StarletteHTTPException 也渲染 `{code, message, message_key, params, detail}`(405 用 `METHOD_NOT_ALLOWED`/`common.methodNotAllowed`);未捕获异常(500)由审计中间件先落 `result=500` 审计行再交由兜底 handler。
- 安全响应头由纯 ASGI 中间件统一注入;`/metrics` 须 Bearer 鉴权(见 [observability.md](./observability.md))。
- 短信渠道走 `app/core/sms.py` 的 Protocol + 工厂(mock / 阿里云 dysmsapi RPC 签名),不在业务代码里直连渠道 SDK;mock 渠道落日志时验证码(code)打码。
- Bearer token 常量时间比较一律先 `.encode()` 成 bytes:compare_digest 收 str 遇非 ASCII 会抛 TypeError。
- 租户 Pod 必须带 Egress 隔离 NetworkPolicy:禁访内网网段(含 CGNAT 100.64.0.0/10 与云元数据地址),并按明确滥用途 TCP 端口黑名单封禁 SMTP(25/465/587)、SMB/NetBIOS(135/139/445)、Telnet(23)、RDP(3389);HTTPS/SSH 出/包管理/对象存储等正常用途不受影响。租户 ns 打 PSA 标签(enforce=baseline、audit/warn=restricted;平台镜像以 root 运行故不能 enforce=restricted),容器有 ephemeral-storage 限额。
- 每租户独立 namespace + ResourceQuota 兜底 + 独立 JuiceFS PVC;JuiceFS 子路径须校验合法性,拒绝越界路径。
- 创建实例只校验镜像引用形态(域名/路径/tag 合法),来源白名单默认关(自定义镜像是产品能力);需要收紧时配 `SUPERDL_IMAGE_ALLOWED_REGISTRIES` 仓库前缀列表,配置后只放行平台镜像目录内的引用与这些前缀。
- 合规:前端 `/legal/terms` 与 `/legal/privacy` 为模板页,注册勾选前后端强校验,备案号运行期下发。
- 高危管理操作一律「原因必填 → 二次确认 → 审计」;审计不落 token、密钥与配置值。
- 密钥与凭据不入 git,只经环境变量或平台配置中心注入;deploy 模板一律 `CHANGE_ME` 占位。
- Secret 模板放 `deploy/app/secrets.example.yaml`,不得放在整目录 apply 路径下,防占位值被 apply。
