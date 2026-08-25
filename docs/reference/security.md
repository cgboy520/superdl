# 安全与合规

启动期配置校验、限流、传输与租户隔离、镜像来源、合规页面。

## 数据模型

- `rate_limit_counters`:key(PK,含维度前缀)、window_start、hits、updated_at —— 固定窗口计数

## 规则与不变量

- `Settings._validate_prod` 在 prod 下 fail-fast,任一项不合格即拒绝启动:jwt_secret 仍为开发默认或不足 32 字符、`sms_provider=mock` 或阿里云短信凭据/签名/模板不全、`k8s_backend=fake`、`payment_mock=true`、database_url 仍为本地默认、cors_origins 含 localhost、ssh_host/jupyter_domain_suffix/public_base_url 仍为占位域名、prometheus_url 指向本地、alertmanager_token 未配、metrics_token 未配、config_encryption_key 缺失或非 32 字节 urlsafe-base64、`bootstrap_admin_password` 已设置(一次性 dev 引导变量,初始化后必须删除)、`real_name_required_for_recharge=true` 而 `real_name_provider=mock`、`image_allowed_registries` 为空(空 = 不限制镜像来源)。
- 启动引导管理员仅 `environment=dev` 生效,口令长度 ≥12(与管理端创建约束对齐);非 prod 启动打 WARNING;幽灵 `SUPERDL_*` 环境变量(不命中任何字段或别名)启动打 WARNING 但不 fail。
- 限流计数落 PG(`rate_limit_counters`),不用进程内计数;429 响应带 `Retry-After`(窗口剩余秒数,DB 侧计算),401 统一带 `WWW-Authenticate: Bearer`。
- 统一错误体覆盖框架层异常:路由 404/405 等 StarletteHTTPException 也渲染 `{code, message, message_key, params, detail}`(405 用 `METHOD_NOT_ALLOWED`/`common.methodNotAllowed`);未捕获异常(500)由审计中间件先落 `result=500` 审计行再交由兜底 handler。
- 安全响应头由纯 ASGI 中间件统一注入;`/metrics` 须 Bearer 鉴权(见 [observability.md](./observability.md))。
- 短信渠道走 `app/core/sms.py` 的 Protocol + 工厂(mock / 阿里云 dysmsapi RPC 签名),不在业务代码里直连渠道 SDK;mock 渠道落日志时验证码(code)打码。
- Bearer token 常量时间比较一律先 `.encode()` 成 bytes:compare_digest 收 str 遇非 ASCII 会抛 TypeError。
- 租户 Pod 必须带 Egress 隔离 NetworkPolicy:禁访内网网段(含 CGNAT 100.64.0.0/10 与云元数据地址),并按明确滥用途 TCP 端口黑名单封禁 SMTP(25/465/587)、SMB/NetBIOS(135/139/445)、Telnet(23)、RDP(3389);HTTPS/SSH 出/包管理/对象存储等正常用途不受影响。租户 ns 打 PSA 标签(enforce=baseline、audit/warn=restricted;平台镜像以 root 运行,不能 enforce=restricted),容器有 ephemeral-storage 限额。
- 每租户独立 namespace + ResourceQuota 兜底 + 独立 JuiceFS PVC;JuiceFS 子路径须校验合法性,拒绝越界路径。
- 创建实例只校验镜像引用形态(域名/路径/tag 合法),来源白名单默认关;需要收紧时配 `SUPERDL_IMAGE_ALLOWED_REGISTRIES` 仓库前缀列表,配置后只放行平台镜像目录内的引用与这些前缀。
- 合规:前端 `/legal/terms` 与 `/legal/privacy` 为模板页,注册勾选前后端强校验,备案号运行期下发。
- 高危管理操作一律「原因必填 → 二次确认 → 审计」;审计不落 token、密钥与配置值。
- 密钥与凭据不入 git,只经环境变量或平台配置中心注入;deploy 模板一律 `CHANGE_ME` 占位。
- Secret 模板放 `deploy/app/secrets.example.yaml`,不得放在整目录 apply 路径下。

## 限流覆盖矩阵

两层纵深:边缘层(ingress-nginx 单 IP 兜底)只挡洪水,精细化全在应用层(PG 固定窗口,多副本共享)。

| 路径/动作 | 边缘层(单 IP) | 应用层 |
|---|---|---|
| 全部 API(`04-ingress.yaml`) | 20 rps / 600 rpm / 20 conn | — |
| 登录/注册/找回密码 | 同上 | 手机号+IP 双维(`account/service.py`) |
| 短信验证码发送 | 同上 | 同号 60s + 平台日上限(`core/sms.py`) |
| 图形/滑块验证码 | 同上 | 按场景固定窗口 |
| 管理端登录 + MFA | 白名单 ingress(无边缘限流) | 账号维 5 次/10min(成功也计,防窗口内批量领 token) |
| 支付/退款回调(`webhooks/*`) | 同上 | 按订单/渠道窗口(`webhooks_router.py`) |
| 节点注册/进度上报 | 同上 | 按令牌窗口(`nodes/enroll_router.py`) |
| 工单/通知/法务公开端点 | 同上 | 按用户窗口(工单 113、法务 120 次/分) |

豁免记录:管理面(admin host)不配边缘限流——已有源 IP 白名单作为更强边界。

## 已接受取舍(评审在案,勿再单独立项)

- **token 存 localStorage**:CSP 已按站点拆分强约束(web 放行域白名单化,admin 严格 'self'),XSS 面已由 CSP/框架转义收敛;HttpOnly Cookie 换 CSRF 面的收益不抵改造成本。
- **固定窗口限流 2× 突发**:窗口边界双倍突发可接受,应用层语义简单可测;精度敏感动作(MFA/短信)窗口与配额已按威胁模型单独收紧。
- **用户端无 2FA**:SMS 验证码是信任根(运营商实名体系),用户端不加 TOTP;管理端全角色强制 TOTP + 恢复码 + 防重放(timestep 单调)。
- **仅 +86 手机号**:监管与短信通道约束,不做国际号段。
- **pending 订单 48h 收敛窗**:超时关单由查单 poller 收敛,不再无限挂起。
- **双人制衡「一人控两账号」残余**:组织流程(账号实名到人)兜底,技术层已拦「同账号复核自己」与「发起后新建账号复核」。
- **POLICY_LABELS 运营术语不译**:管理端内部术语(如 dedup_key 原文)不进 i18n,保持排障检索一致。
