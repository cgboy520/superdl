# 安全与合规

启动期配置校验、限流、传输与租户隔离、镜像来源、合规页面。

## 数据模型

- `rate_limit_counters`:key(PK,含维度前缀)、window_start、hits、updated_at —— 固定窗口计数
- `audit_log`:actor_type、actor_id?、action(≤128,形如 `admin.POST /api/admin/v1/...`)、target?(≤256)、ip、result(HTTP 状态码)、detail jsonb?、request_id?(≤64,与响应头 `X-Request-ID` 同值)、user_agent?(≤256)、created_at

## 规则与不变量

### 启动与配置

- `Settings._validate_prod` 在 prod 下 fail-fast,任一项不合格即拒绝启动(API 与 worker 同一份校验,这份清单就是生产必配项清单;部署模板见 `deploy/app/k8s/00-namespace-config.yaml` 非密与 `deploy/app/secrets.example.yaml` 密):jwt_secret 仍为开发默认、含 `CHANGE_ME` 占位残留、不足 32 字符或唯一字符不足 16 个(低熵串);access token TTL >1h 或 refresh TTL >7d;`sms_provider=mock`;`k8s_backend=fake`;`payment_mock=true`;database_url 仍为本地默认、或指向非本机 PG 而无 `sslmode=require+`;cors_origins 含 localhost;jupyter_domain_suffix / service_domain_suffix / public_base_url / admin_host 仍为 example.com 占位;`public_base_url` 不是 `https://` 开头(它是装机脚本的下载源、bootstrap Bearer 令牌与 join token 的传输端点,明文 http 等于路径上任何人都能改写一份以 root 执行的脚本、顺手读走 join token);`payment_alipay_enabled=true` 而 `alipay_seller_id` 缺失;metrics_token 未配;`admin_edge_token` 未配(管理端边缘共享密钥,见下「请求边界」);config_encryption_key 缺失(格式校验在 `_validate_invariants`,任意环境:32 字节 urlsafe-base64,`config_encryption_key_previous` 同口径且不得与当前密钥相同)。`process_role`(env `SUPERDL_PROCESS_ROLE`,api / worker,`deploy/app/k8s/03-worker.yaml` 置 worker)为 worker 时跳过 jwt_secret 与 admin_edge_token 两项:worker 按 secrets 分域不挂 `superdl-auth` / `superdl-edge`;分组件 worker 再按 `config._WORKER_SECRET_DOMAINS`(与 `03-worker.yaml` 各 envFrom 同源)只校验拿得到的项:sms_provider / payment_mock 只查挂了 `superdl-cloud` / `superdl-payment` 的 core,config_encryption_key 不查只挂 db/metrics 的 disk-ops;其余校验不变。
- `_validate_invariants` 里另有三条**与环境无关**的形态闸(dev 配歪了同样会把元字符带进装机脚本与连接串):`public_base_url` 必须匹配锚定的 `scheme://主机[:端口][/路径]`,不得含空白或 shell 元字符 —— 它会被逐字替换进 `node-join.sh` 的 `API_BASE="__API_BASE__"`(双引号赋值,以 root 执行),且经 ConfigMap(非 Secret)下发,改它不需要密钥权限;`jupyter_domain_suffix` / `service_domain_suffix` / `admin_host` 必须是裸主机名(可带端口,无协议头),三者会进用户可见的 ssh 连接串、HTTPRoute hostname 与 Host 比较;`tenant_pod_cidr` 非空时必须是合法网段。
- 启动校验只管 provider 选择,不查渠道凭据齐全性:短信 / 验证码 / 实名凭据经平台配置中心在线录入,运行期渠道工厂(`app/core/sms.py`、`app/core/captcha.py`)缺凭据即 fail-closed,管理端 `test-sms` 可验。`alertmanager_token` 未配与 `prometheus_url` 仍指向本地只在 lifespan 打 WARNING。`k8s_backend=real` 不要求 `environment=prod`。
- 人机验证与实名认证是运行期开关(平台配置·安全策略 `captcha_enabled` / `real_name_enabled`,默认关),不是 provider 选择:关闭即跳过对应校验(发码不带 `captcha_token`、实名提交返 409)。**prod 下关闭是双重封死**:在线写库层 `prod_forbidden` 禁关(单管理员一次请求即降防的口子),lifespan 启动 fail-fast(`assert_prod_compliance_gates`:captcha_enabled / real_name_enabled / real_name_required_for_recharge 任一未开即拒绝启动);`compute_config_warnings` 仍在管理端配置页出红牌。没有 mock 渠道,测试经 `set_captcha_channel` / `set_realname_provider` 注入。组合约束:`real_name_required_for_recharge=true ⇒ real_name_enabled=true`,任意环境生效(`Settings._validate_invariants` 与写入侧 `_check_real_name_invariant` 同口径)。
- 首个管理员由脚本创建(`ensure_bootstrap_admin`:`admin_users` 为空时才建,口令 ≥12 字符、≤72 字节),没有启动期引导变量:dev/test 随 `apps/api/scripts/seed_dev.py` 一起建,prod 用 `apps/api/scripts/bootstrap_admin.py`(走完整 Settings 校验,口令取 `SUPERDL_SEED_ADMIN_PASSWORD`,未设则随机生成写 0600 文件——绝不打印 stdout,日志采集会长期留存)。幽灵 `SUPERDL_*` 环境变量(不命中任何字段)启动打 WARNING 但不 fail。
- 密钥与凭据不入 git,只经环境变量或平台配置中心注入;deploy 模板一律 `CHANGE_ME` 占位。Secret 模板放 `deploy/app/secrets.example.yaml`,不得放在整目录 apply 路径下。

### 请求边界

- 限流计数落 PG(`rate_limit_counters`),不用进程内计数;429 带 `Retry-After`(窗口剩余秒数,DB 侧计算),401 统一带 `WWW-Authenticate: Bearer`。
- 统一错误体覆盖框架层异常:路由 404/405 等 StarletteHTTPException 也渲染 `{code, message, message_key, params, detail}`(405 用 `METHOD_NOT_ALLOWED` / `common.methodNotAllowed`);未捕获异常由内层 `Uniform500Middleware` 渲染成 500 响应,审计中间件按响应状态码落 `result=500` 审计行。
- 安全响应头由纯 ASGI 中间件统一注入;`/metrics` 须 Bearer 鉴权(见 [observability.md](./observability.md))。
- 边缘收口中间件(`app/core/edge_guard.py`)prod 恒开、无开关(`environment` 只有 dev / test / prod 三值,类生产环境也以 prod 运行并配独立 secrets;dev / test 无网关,不启用):`/api/admin/*` 双闸 —— Host 命中 `admin_host` **且** `X-Admin-Edge-Token` 命中 `admin_edge_token`(admin 域 nginx 同源反代注入,见 `deploy/app/nginx.admin.conf`;只验 Host 时集群内能直连 API Service 的调用方伪造 Host 即穿闸),任一不符 404;`/metrics` 与 `/api/internal*` 带 `X-Forwarded-For`(经网关进入)一律 404,集群内直刮 / 直连不带该头。`/api/internal` 下只有服务端点鉴权回调,它本身不能带鉴权,这条收口是它唯一的保护 —— 平台 API 的 HTTPRoute 是 `path: /` 前缀匹配,不收口它就跟着暴露在公网上,成为 API Key 的在线爆破预言机(见 [services.md](./services.md))。
- Bearer token 常量时间比较统一走 `app/core/http.py` 的 `bearer_matches`(先 `.encode()` 成 bytes:compare_digest 收 str 遇非 ASCII 会抛 TypeError),/metrics(API 与 worker)与 Alertmanager webhook 三处共用。
- 短信渠道走 `app/core/sms.py` 的 Protocol + 工厂(mock / 阿里云 dysmsapi RPC 签名),不在业务代码里直连渠道 SDK;落日志时手机号与验证码由全局日志处理器(`app/core/logging.py`)按键名打码,渠道不各自打码。
- 高危管理操作一律「原因必填 → 二次确认 → 审计」;审计不落 token、密钥与配置值。
- 审计行带 `request_id`(与响应头 `X-Request-ID`、结构化日志同一值)与 `user_agent`:没有前者,一条审计与那次请求的日志之间无键可连。中间件路径的 `request_id` 从响应头抄回(审计中间件注册在 Observability 之外,它的 finally 跑在 contextvar 解绑之后),资金域同步审计在处理函数内调用,直接读 contextvar。
- `action` / `target` / `user_agent` 入库前一律按列宽截断:三者长度都由请求侧决定,超宽会让 INSERT 抛 `StringDataRightTruncation` —— 既丢掉这一行审计,又推进 fail-closed 闸的连续失败计数与 `superdl_audit_write_failed_total`,等于把「审计写不进去」这个信号交给任何一个能构造超长 URL 的人。同理 `Idempotency-Key` 请求头在契约层就按承载列宽(64)拦下,否则超长键会在 INSERT 时抛 `DataError` 变 500。

### 租户隔离

#### 隔离级别分级

平台提供三级 GPU 隔离;用户可见的「档位」与底层隔离强度并非一一对应,档位的唯一事实源是 `pool_label`:

| 级别 | 池 | 机制 | 安全属性 | 适用场景 |
|---|---|---|---|---|
| **VM 级隔离** | kata | Kata Containers (QEMU VM) + VFIO 整卡直通 | 硬件级安全边界;租户无法逃逸 VM | 生产服务、敏感数据 |
| **硬件切分隔离** | mig | NVIDIA MIG (Multi-Instance GPU) | GPU 硬件强制隔离;显存/算力切分由硬件执行,租户无法绕过 | 标准共享、可信多租户 |
| **软件限额** | hami | HAMi `libvgpu.so` (LD_PRELOAD CUDA 拦截) | **非安全边界**;容器内 root 可通过 unset LD_PRELOAD、静态链接 CUDA、直接调用 CUDA Driver API 绕过配额;HAMi 官方文档明确列出多种绕过方式(见 HAMi troubleshooting「GPU Memory Limit Not Enforced」) | 成本优化、容错性高的批处理任务 |

**关键安全约束:**
- HAMi 池的隔离是**性能隔离/资源限额**,不是**安全隔离**。租户容器以 root 运行(ssh 入口所需),root 可以绕过 HAMi 的软限额。
- 跨租户显存残留:HAMi 将多个租户放在同一未分区 GPU 上,存在已知的跨租户显存残留面(MIG 硬件切分不存在此问题)。
- 平台默认 `shared_tier_allowed_pools = "mig,hami"`;运营方可通过摘掉 hami 将共享档限制为仅 MIG 硬切分。
- 前端对经济档(hami 池)有知情同意 modal,明确告知「软件隔离共享」与「性能可能波动」,且文案与代码同提交。

- 租户容器加固基线(`core/k8s/real.py::tenant_security_context`,无条件下发):`allowPrivilegeEscalation=false`、`seccompProfile=RuntimeDefault`、`capabilities.drop=[ALL]` 之后只 add 回 `SYS_CHROOT` / `SETUID` / `SETGID` —— OpenSSH 的预认证特权分离强制需要这三个,缺任一个则 `ssh root@` 入口在密钥交换阶段即断。不下发 `runAsNonRoot`(平台镜像以 root 运行)。
- 实例容器内的两处凭据落点由 entrypoint 钉死(`deploy/instance-images/entrypoint.sh`,契约见 [../../deploy/instance-images/README.md](../../deploy/instance-images/README.md)):
  - **Jupyter token 显式上 argv**(`--IdentityProvider.token`)。只靠环境变量 `JUPYTER_TOKEN` 时它仅是 traitlets 的**默认值**(优先级最低),配置文件里一行 `c.IdentityProvider.token = ""` 就能把鉴权整个关掉(`auth_enabled` 变 False,匿名请求一律发到生成用户),命令行才压得住配置文件。配套把 `JUPYTER_CONFIG_DIR` 挪出实例盘(改到容器可写层 `/run/jupyter-config`):`/root` 是 PVC,落在那里的 `jupyter_server_config.py` 会跨 Pod 重建长期存活 —— 任何一个恶意 pip 包都能顺手写下,用户自己并不知情。两者互为两道。
  - **`AUTHORIZED_KEYS` 无条件覆写 `~/.ssh/authorized_keys`**,空值必须把文件清空。加 `[[ -n ... ]]` 守卫会让「删掉最后一把公钥」变成空操作,而 `/root` 是持久实例盘、旧文件原样留着 —— 公钥泄漏的用户永远吊销不掉攻击者的访问。
- 租户 Pod 必须带 Egress 隔离 NetworkPolicy:禁访内网网段(含 CGNAT 100.64.0.0/10 与云元数据地址),并按滥用用途封禁 TCP 端口 SMTP(25/465/587)、SMB/NetBIOS(135/139/445)、Telnet(23)、RDP(3389),外加数据库 / 缓存 / 检索端口 MySQL(3306)、PostgreSQL(5432)、Redis(6379)、Elasticsearch(9200)、Memcached(11211)、MongoDB(27017)。**数据存储端口必须封在端口维**:私网黑名单只挡内网,带公网端点的托管库(RDS / 云 Redis 等)走公网 IP 照样可达,而这类服务默认口令 / 无鉴权是常态,互扫与横向移动成本几乎为零。租户确需外连自有数据库时走 TLS 反代或工单白名单逐案开放,与 UDP 同口径。
- 入方向默认拒东西向,只放行 `envoy-gateway-system`(Envoy **数据面 Pod** 所在 ns,`core/k8s/real.py` 的 `GATEWAY_DATAPLANE_NAMESPACE`)到租户 Pod(**不限端口**)与 SSH 22。**SSH 22 那条规则的来源是 `0.0.0.0/0` 除去 Pod 网段**(`tenant_pod_cidr`,默认 `10.42.0.0/16`,k3s/rke2 出厂值;改 CNI 网段必须同步改它):整段私网排不得 —— 跨节点 NodePort 经 SNAT 后来源是节点内网 IP,排掉就没人能 SSH;Pod 网段则必须排 —— Pod→Pod 是同一 overlay 内直连、不经 SNAT,来源仍是对端 Pod IP,不排等于把 22 端口对全集群租户敞开(扫一遍 Pod 网段就能挨个连别人的实例),而排掉它不影响任何一条合法路径。留空 = 不下发 except,仅供排障临时回退。**排掉之后要把各节点 Pod 子网的网关地址逐个放回**(`core/k8s/real.py::pod_cidr_gateways`,每个 podCIDR 的 `.0/32` 与 `.1/32`):flannel 上跨节点 NodePort 的 SNAT 来源是入口节点的 flannel.1(子网 `.0`),落在被排掉的 Pod 网段内,不放回则只有直连实例所在节点的 IP 才能 SSH。放回清单在建 ns 时算,节点集合变化时由 tenant-mgr 的 reconciler 对在册租户 ns 重下发。**不是 `Gateway` 对象所在的 `superdl` ns**:未开 Gateway Namespace Mode 时数据面与 EG 控制面同 ns,按 Gateway 所在 ns 写会双不通。平台自身前端与 API 的入向 NetworkPolicy(`deploy/app/k8s/09-networkpolicy.yaml`)同源。
- 每租户独立 namespace + ResourceQuota 兜底 + 独立 JuiceFS PVC;租户 ns 打 PSA 标签(enforce=baseline、audit/warn=restricted;平台镜像以 root 运行,不能 enforce=restricted),容器有 ephemeral-storage 限额。JuiceFS 子路径须校验合法性,拒绝越界路径。
- **租户手里没有任何 K8s 凭据,`httproutes` 的写权限只给 `superdl-tenant-mgr` 这一个 SA**(`deploy/app/k8s/01-rbac.yaml`):HTTPRoute 只要 hostname 与 listener 有交集就能挂上,能在租户 ns 里任意建路由就能声明平台域名劫走流量。与之配套的是 listener 侧的消歧规则 —— 平台三个 listener 写**精确 hostname**、租户 listener 写通配,SNI 与 Host 都按「精确优先于通配」匹配,所以租户域与平台域共用一级域(如租户 `*.<域>` + 平台 `api.<域>`)是支持的形态;把平台域也换成通配就真分不开了。
- 创建实例只校验镜像引用形态(域名/路径/tag/digest 合法,`core/registry.is_valid_image_ref`),来源白名单默认关;需要收紧时在平台配置·镜像仓库填 `image_allowed_registries`(每行一个仓库前缀),生效白名单 = 配置行 ∪ Harbor 地址前缀(`core/registry.effective_image_allowlist`),配置后只放行平台镜像目录内的引用与这些前缀;prod 下白名单为空且未配 Harbor 地址只给配置告警(`compute_config_warnings`),不拒启动。**每条前缀一律补成以 `/` 结尾**:匹配方是裸 `image_ref.startswith(prefix)`,不补斜杠的 `docker.io` 会顺带放行 `docker.io.attacker.example/evil:1` —— 注册一个以白名单项开头的域名就绕过了整道闸门。不改成「解析出仓库主机后相等比较」:运营录入的前缀常带项目路径(`harbor.internal/superdl/`),按主机相等会把「只许本项目」放宽成整台仓库。
- **准入层是七条 ValidatingAdmissionPolicy,全部 `Deny`**(`deploy/cluster/admission/tenant-restrictions.yaml`,apiserver 内置、无 webhook 依赖):① 平台 SA 的写操作范围(`superdl` / `tenant-*` ns + nodes;集群级请求没有 `request.namespace` 键,表达式一律 `has()` 守卫,否则求值报错即拒绝)、② 租户 ns Pod 安全基线、③ 平台 SA 对 Node 的字段级写白名单(cordon + `superdl.io/*` 标签)、④ 全局 Pod 兜底、⑤ `superdl` ns 内 Pod 的 Secret 引用白名单、⑥ 平台 SA 建 Job 时 Pod 模板的 Secret 引用白名单、⑦ 平台 SA 只能删 GPU 工作节点(控制面 / etcd / infra 节点锁死)。**缺 Binding 是静默 fail-open** —— `failurePolicy: Fail` 只在策略被求值时生效,没装策略的集群等于全放行,而 tenant-mgr 的 `pods:create` 与 `roles:escalate,bind` 是全命名空间的,少了①它约等于 cluster-admin;故由 `deploy/cluster/apply.sh` 在 helmfile 之前无条件 apply 并当场回读,`deploy/cluster/preflight.sh` 与 `scripts/release.sh` 各再断言一次。
- **「不给 `secrets` 动词」不等于「读不到 Secret」**:命名空间内的 `pods:create`(tenant-mgr)或 `batch/jobs:create`(prewarm / disk-ops)等价于该命名空间的 `secrets:get` —— kubelet 代创建者解析 `secretKeyRef` / `envFrom` / secret 卷 / `imagePullSecrets`,这条路径**不做任何 secrets 授权检查**,PSA `restricted` 也不约束 Secret 挂载(一个 sleep + env 的 Pod 完全合规)。所以 `deploy/app/k8s/01-rbac.yaml` 少给的 secrets 动词保不住 `superdl-auth`(JWT 签发密钥)与 `superdl-crypto`(配置中心主密钥,解得开全部渠道凭据与集群 join token);真正的防线是策略⑤⑥的「能引用哪个 Secret」白名单。⑤ 另覆盖 Job 派生 Pod 的创建者 `system:serviceaccount:kube-system:job-controller` —— 策略①按 `request.userInfo.username` 匹配,对那条路径完全不适用。
- 服务端点的 API Key 摘要、鉴权链路与网关策略约束见 [services.md](./services.md)。
- 合规:前端 `/legal/terms` 与 `/legal/privacy` 为模板页,注册勾选前后端强校验,备案号运行期下发。

### 限流分层

两层纵深:边缘层(Envoy Gateway,`deploy/app/k8s/04-gateway.yaml` 的 `BackendTrafficPolicy superdl-api-ratelimit`)对公网 API 域按**每源 IP** 兜底,只挡爆破与洪水;精细化限流全在应用层(`app/core/ratelimit.py`,PG 固定窗口计数,多副本共享)。管理面(admin host)不配边缘限流 —— 源 IP 白名单是更强的边界。全部限额数值见 [limits.md](./limits.md)。

边缘层这几条都属于「配错了不报错、只是静默失效」,改动后必须 `kubectl describe backendtrafficpolicy/securitypolicy/clienttrafficpolicy -n superdl` 看 `Accepted=True` 再收工:

- **每源 IP 靠 `sourceCIDR.type: Distinct`**:它让 `0.0.0.0/0` 里每个源 IP 各占一个桶;写成默认的 `Exact` 会变成全网共用一个桶,正常业务量就能把所有人一起限死。(官方文档写着「local 限流不支持 distinct 匹配」,对 v1.9.0 不成立,以源码为准;升版时按源码复核。)
- **没有每源 IP 并发连接数限制**:Envoy Gateway 无此原语,`ClientTrafficPolicy.connection.connectionLimit` 是**每个 Envoy 实例的连接总量**,只作防内存耗尽的兜底,按每 IP 口径调小会瞬间打死全站。每 IP 维度只由 RPS/RPM 承担。
- **管理端源 IP 白名单**(`SecurityPolicy superdl-admin-allowlist`,挂 `superdl-admin` 路由,默认启用):`authorization.defaultAction: Deny` + 一条 `action: Allow` 的 `principal.clientCIDRs`。漏写 `defaultAction: Deny` 则默认是 Allow,规则从白名单退化成一条毫无作用的显式放行。占位符是 `192.0.2.0/24`(RFC 5737 文档网段)而不是 `CHANGE_ME_*`:`clientCIDRs` 在 CRD 里带 CIDR 正则,非法字符串会被 apiserver **单独拒收该对象**,而 apply 是逐对象的 —— 结果会是只有白名单没建起来、其余全部生效,管理端就此无声敞开;换成合法但没有任何真实主机的网段,忘了替换时是 fail-closed(管理端谁也进不去,当场发现)。`deploy/cluster/preflight.sh` 按这个网段扫描,未替换不予放行。
- **每 IP 限流与管理端白名单都建立在 `envoyService.externalTrafficPolicy: Local` 之上**(`EnvoyProxy superdl-proxy`,虽是默认值仍显式写死):改成 `Cluster` 会多一跳 kube-proxy SNAT,Envoy 看到的源 IP 变成节点 IP —— 白名单把全部流量算成同一个源、每 IP 限流退化成全网一个桶,两条策略当场失效且不报错。
- **租户 Jupyter 的 `app-https` listener 也挂了一条 local 限流**(`BackendTrafficPolicy superdl-app-ratelimit`,每端点 100/s):该 listener 既不过 `extAuth` 也不在 API 那条限流的射程内,不挂就是每个租户的 Jupyter 端点在边缘层裸奔,一条 HTTPRoute 被打满吃掉的是同一对 Envoy 副本上**全部**租户与平台三域名的 CPU 与连接预算。与 `superdl-svc-ratelimit` 同构(一实例一条 HTTPRoute ⇒ 一个端点一个桶):**不许**加 `sourceCIDR.type: Distinct` —— EG 把 `alwaysConsumeDefaultTokenBucket` 钉死为 false,命中任一 descriptor 后默认桶一次都不消耗,「每端点 N/s」当场退化成「每端点每源 IP N/s」,换 100 个 IP 就打穿。同一 targetRef 也不能再挂第二条同类策略(EG 不合并,最老者生效、另一条静默 Conflicted)。
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
