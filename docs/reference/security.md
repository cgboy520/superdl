# 安全与合规

启动期配置校验、限流、传输与租户隔离、镜像来源、合规页面。

## 数据模型

- `rate_limit_counters`:key(PK,含维度前缀)、window_start、hits、updated_at —— 固定窗口计数

## 规则与不变量

### 启动与配置

- `Settings._validate_prod` 在 prod 下 fail-fast,任一项不合格即拒绝启动(API 与 worker 同一份校验,这份清单就是生产必配项清单;部署模板见 `deploy/app/k8s/00-namespace-config.yaml` 非密与 `deploy/app/secrets.example.yaml` 密):jwt_secret 仍为开发默认或不足 32 字符;access token TTL >1h 或 refresh TTL >7d;`sms_provider=mock`;`k8s_backend=fake`;`payment_mock=true`;database_url 仍为本地默认;cors_origins 含 localhost;jupyter_domain_suffix / service_domain_suffix / public_base_url / admin_host 仍为 example.com 占位;`payment_alipay_enabled=true` 而 `alipay_seller_id` 缺失;metrics_token 未配;config_encryption_key 缺失或非 32 字节 urlsafe-base64。
- 启动校验只管 provider 选择,不查渠道凭据齐全性:短信 / 验证码 / 实名凭据经平台配置中心在线录入,运行期渠道工厂(`app/core/sms.py`、`app/core/captcha.py`)缺凭据即 fail-closed,管理端 `test-sms` 可验。`alertmanager_token` 未配与 `prometheus_url` 仍指向本地只在 lifespan 打 WARNING。`k8s_backend=real` 不要求 `environment=prod`。
- 人机验证与实名认证是运行期开关(平台配置·安全策略 `captcha_enabled` / `real_name_enabled`,默认关),不是 provider 选择:关闭即跳过对应校验(发码不带 `captcha_token`、实名提交返 409)。prod 关闭不拒启动(写入需 reason 进审计),但必须看得见:`compute_config_warnings` 在管理端配置页顶部出红牌、prod lifespan 打 error/warning。没有 mock 渠道,测试经 `set_captcha_channel` / `set_realname_provider` 注入。唯一组合约束:`real_name_required_for_recharge=true ⇒ real_name_enabled=true`,任意环境生效(`Settings._validate_invariants` 与写入侧 `_check_real_name_invariant` 同口径)。
- 首个管理员由脚本创建(`ensure_bootstrap_admin`:`admin_users` 为空时才建,口令 ≥12 字符、≤72 字节),没有启动期引导变量:dev/test 随 `apps/api/scripts/seed_dev.py` 一起建,prod 用 `apps/api/scripts/bootstrap_admin.py`(走完整 Settings 校验,口令取 `SUPERDL_SEED_ADMIN_PASSWORD` 或随机生成只打印一次)。幽灵 `SUPERDL_*` 环境变量(不命中任何字段)启动打 WARNING 但不 fail。
- 密钥与凭据不入 git,只经环境变量或平台配置中心注入;deploy 模板一律 `CHANGE_ME` 占位。Secret 模板放 `deploy/app/secrets.example.yaml`,不得放在整目录 apply 路径下。

### 请求边界

- 限流计数落 PG(`rate_limit_counters`),不用进程内计数;429 带 `Retry-After`(窗口剩余秒数,DB 侧计算),401 统一带 `WWW-Authenticate: Bearer`。
- 统一错误体覆盖框架层异常:路由 404/405 等 StarletteHTTPException 也渲染 `{code, message, message_key, params, detail}`(405 用 `METHOD_NOT_ALLOWED` / `common.methodNotAllowed`);未捕获异常由内层 `Uniform500Middleware` 渲染成 500 响应,审计中间件按响应状态码落 `result=500` 审计行。
- 安全响应头由纯 ASGI 中间件统一注入;`/metrics` 须 Bearer 鉴权(见 [observability.md](./observability.md))。
- 边缘收口中间件(`app/core/edge_guard.py`)prod 恒开、无开关(`environment` 只有 dev / test / prod 三值,类生产环境也以 prod 运行并配独立 secrets;dev / test 无网关,不启用):`/api/admin/*` 仅放行 Host 命中 `admin_host` 的请求,其余 404;`/metrics` 与 `/api/internal*` 带 `X-Forwarded-For`(经网关进入)一律 404,集群内直刮 / 直连不带该头。`/api/internal` 下只有服务端点鉴权回调,它本身不能带鉴权,这条收口是它唯一的保护 —— 平台 API 的 HTTPRoute 是 `path: /` 前缀匹配,不收口它就跟着暴露在公网上,成为 API Key 的在线爆破预言机(见 [services.md](./services.md))。
- Bearer token 常量时间比较统一走 `app/core/http.py` 的 `bearer_matches`(先 `.encode()` 成 bytes:compare_digest 收 str 遇非 ASCII 会抛 TypeError),/metrics(API 与 worker)与 Alertmanager webhook 三处共用。
- 短信渠道走 `app/core/sms.py` 的 Protocol + 工厂(mock / 阿里云 dysmsapi RPC 签名),不在业务代码里直连渠道 SDK;落日志时手机号与验证码由全局日志处理器(`app/core/logging.py`)按键名打码,渠道不各自打码。
- 高危管理操作一律「原因必填 → 二次确认 → 审计」;审计不落 token、密钥与配置值。

### 租户隔离

- 租户容器加固基线(`core/k8s/real.py::tenant_security_context`,无条件下发):`allowPrivilegeEscalation=false`、`seccompProfile=RuntimeDefault`、`capabilities.drop=[ALL]` 之后只 add 回 `SYS_CHROOT` / `SETUID` / `SETGID` —— OpenSSH 的预认证特权分离强制需要这三个,缺任一个则 `ssh root@` 入口在密钥交换阶段即断。不下发 `runAsNonRoot`(平台镜像以 root 运行)。
- 租户 Pod 必须带 Egress 隔离 NetworkPolicy:禁访内网网段(含 CGNAT 100.64.0.0/10 与云元数据地址),并按滥用用途封禁 TCP 端口 SMTP(25/465/587)、SMB/NetBIOS(135/139/445)、Telnet(23)、RDP(3389)。
- 入方向默认拒东西向,只放行 `envoy-gateway-system`(Envoy **数据面 Pod** 所在 ns,`core/k8s/real.py` 的 `GATEWAY_DATAPLANE_NAMESPACE`)到租户 Pod(**不限端口**)与 SSH 22。**不是 `Gateway` 对象所在的 `superdl` ns**:未开 Gateway Namespace Mode 时数据面与 EG 控制面同 ns,按 Gateway 所在 ns 写会双不通。平台自身前端与 API 的入向 NetworkPolicy(`deploy/app/k8s/09-networkpolicy.yaml`)同源。
- 每租户独立 namespace + ResourceQuota 兜底 + 独立 JuiceFS PVC;租户 ns 打 PSA 标签(enforce=baseline、audit/warn=restricted;平台镜像以 root 运行,不能 enforce=restricted),容器有 ephemeral-storage 限额。JuiceFS 子路径须校验合法性,拒绝越界路径。
- **租户手里没有任何 K8s 凭据,`httproutes` 的写权限只给 `superdl-tenant-mgr` 这一个 SA**(`deploy/app/k8s/01-rbac.yaml`):HTTPRoute 只要 hostname 与 listener 有交集就能挂上,能在租户 ns 里任意建路由就能声明平台域名劫走流量。与之配套的是 listener 侧的消歧规则 —— 平台三个 listener 写**精确 hostname**、租户 listener 写通配,SNI 与 Host 都按「精确优先于通配」匹配,所以租户域与平台域共用一级域(如租户 `*.<域>` + 平台 `api.<域>`)是支持的形态;把平台域也换成通配就真分不开了。
- 创建实例只校验镜像引用形态(域名/路径/tag/digest 合法,`core/registry.is_valid_image_ref`),来源白名单默认关;需要收紧时在平台配置·镜像仓库填 `image_allowed_registries`(每行一个仓库前缀),生效白名单 = 配置行 ∪ Harbor 地址前缀(`core/registry.effective_image_allowlist`),配置后只放行平台镜像目录内的引用与这些前缀;prod 下白名单为空且未配 Harbor 地址只给配置告警(`compute_config_warnings`),不拒启动。
- 服务端点的 API Key 摘要、鉴权链路与网关策略约束见 [services.md](./services.md)。
- 合规:前端 `/legal/terms` 与 `/legal/privacy` 为模板页,注册勾选前后端强校验,备案号运行期下发。

### 限流分层

两层纵深:边缘层(Envoy Gateway,`deploy/app/k8s/04-gateway.yaml` 的 `BackendTrafficPolicy superdl-api-ratelimit`)对公网 API 域按**每源 IP** 兜底,只挡爆破与洪水;精细化限流全在应用层(`app/core/ratelimit.py`,PG 固定窗口计数,多副本共享)。管理面(admin host)不配边缘限流 —— 源 IP 白名单是更强的边界。全部限额数值见 [limits.md](./limits.md)。

边缘层这几条都属于「配错了不报错、只是静默失效」,改动后必须 `kubectl describe backendtrafficpolicy/securitypolicy/clienttrafficpolicy -n superdl` 看 `Accepted=True` 再收工:

- **每源 IP 靠 `sourceCIDR.type: Distinct`**:它让 `0.0.0.0/0` 里每个源 IP 各占一个桶;写成默认的 `Exact` 会变成全网共用一个桶,正常业务量就能把所有人一起限死。(官方文档写着「local 限流不支持 distinct 匹配」,对 v1.9.0 不成立,以源码为准;升版时按源码复核。)
- **没有每源 IP 并发连接数限制**:Envoy Gateway 无此原语,`ClientTrafficPolicy.connection.connectionLimit` 是**每个 Envoy 实例的连接总量**,只作防内存耗尽的兜底,按每 IP 口径调小会瞬间打死全站。每 IP 维度只由 RPS/RPM 承担。
- **管理端源 IP 白名单**(`SecurityPolicy superdl-admin-allowlist`,挂 `superdl-admin` 路由,默认启用):`authorization.defaultAction: Deny` + 一条 `action: Allow` 的 `principal.clientCIDRs`。漏写 `defaultAction: Deny` 则默认是 Allow,规则从白名单退化成一条毫无作用的显式放行。占位符是 `192.0.2.0/24`(RFC 5737 文档网段)而不是 `CHANGE_ME_*`:`clientCIDRs` 在 CRD 里带 CIDR 正则,非法字符串会被 apiserver **单独拒收该对象**,而 apply 是逐对象的 —— 结果会是只有白名单没建起来、其余全部生效,管理端就此无声敞开;换成合法但没有任何真实主机的网段,忘了替换时是 fail-closed(管理端谁也进不去,当场发现)。`deploy/cluster/preflight.sh` 按这个网段扫描,未替换不予放行。
- **每 IP 限流与管理端白名单都建立在 `envoyService.externalTrafficPolicy: Local` 之上**(`EnvoyProxy superdl-proxy`,虽是默认值仍显式写死):改成 `Cluster` 会多一跳 kube-proxy SNAT,Envoy 看到的源 IP 变成节点 IP —— 白名单把全部流量算成同一个源、每 IP 限流退化成全网一个桶,两条策略当场失效且不报错。
- local 限流是每个 Envoy 实例本地计数,多副本时全局实际上限约为配置值 × 副本数;严格全局需另部署 rate limit service + Redis,当前规模不做。

## 已接受取舍

- **token 存 localStorage**:XSS 面由 CSP 按站点拆分收敛(web 放行域白名单化,admin 严格 'self');不换 HttpOnly Cookie。
- **固定窗口限流 2× 突发**:窗口边界双倍突发可接受;精度敏感动作(MFA / 短信)的窗口与配额单独收紧。
- **用户端无 2FA**:SMS 验证码是信任根;管理端全角色强制 TOTP + 恢复码 + 防重放(timestep 单调),开关 `admin_mfa_enabled` 默认开,prod 关闭需 reason 进审计。
- **仅 +86 手机号**:监管与短信通道约束,不做国际号段。
- **pending 订单 48h 收敛窗**:超时关单由查单 poller 收敛。
- **双人制衡「一人控两账号」残余**:组织流程(账号实名到人)兜底,技术层已拦「同账号复核自己」与「发起后新建账号复核」。
- **租户入向放行网关数据面不限端口**:服务型实例的容器端口由用户声明;Envoy 只会打到自己 HTTPRoute 里声明的 backend Service 端口,而 HTTPRoute 全部由控制面生成,租户之间的东西向仍默认拒。
- **服务型实例持续 not-ready 不判故障**:`reconciler._running_pod_lost_reason` 对 `workload_type='service'` 跳过 `pod_unready` 一支;`pod_lost` 与 `node_lost` 两支对两种形态一视同仁。
- **POLICY_LABELS 运营术语不译**:管理端内部术语(如 dedup_key 原文)不进 i18n,保持排障检索一致。
