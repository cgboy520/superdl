# 安全与合规

启动期配置校验、限流、传输与租户隔离、镜像来源、合规页面。

## 数据模型

- `rate_limit_counters`:key(PK,含维度前缀)、window_start、hits、updated_at —— 固定窗口计数
- `audit_log`:actor_type、actor_id?、action(≤128,形如 `admin.POST /api/admin/v1/...`)、target?(≤256)、ip、result(HTTP 状态码)、detail jsonb?、request_id?(≤64,与响应头 `X-Request-ID` 同值)、user_agent?(≤256)、created_at

## 规则与不变量

### 启动与配置

- `Settings._validate_prod` 在 prod 下 fail-fast,任一项不合格即拒绝启动(API 与 worker 同一份校验,即生产必配项清单;部署模板 `deploy/app/k8s/00-namespace-config.yaml` 非密与 `deploy/app/secrets.example.yaml` 密):jwt_secret 为开发默认、含 `CHANGE_ME`、不足 32 字符或唯一字符不足 16 个;access token TTL >1h 或 refresh TTL >7d;`bcrypt_rounds` <12(口令哈希 cost,默认 12,允许 4–31;测试基建置 4 换速度);`sms_provider=mock`;`email_provider=mock`;`k8s_backend=fake`;`payment_mock=true`;database_url 为本地默认、或指向非本机 PG 而无 `sslmode=require+`;cors_origins 含 localhost;jupyter_domain_suffix / service_domain_suffix / public_base_url / admin_host 为 example.com 占位;`public_base_url` 不是 `https://` 开头;`payment_alipay_enabled=true` 而 `alipay_seller_id` 缺失;metrics_token 未配;`admin_edge_token` 未配;config_encryption_key 缺失(格式校验在 `_validate_invariants`,任意环境:32 字节 urlsafe-base64,`config_encryption_key_previous` 同口径且不得与当前相同);`compliance_profile` unset (prod must say `none` or `cn`). `platform_currency` (supported table) and `billing_timezone` (IANA zone) are shape-checked in every environment.`process_role`(env `SUPERDL_PROCESS_ROLE`,api / worker,`deploy/app/k8s/03-worker.yaml` 置 worker)为 worker 时跳过 jwt_secret 与 admin_edge_token;分组件 worker 再按 `config._WORKER_SECRET_DOMAINS`(与 `03-worker.yaml` 各 envFrom 同源)只校验拿得到的项:sms_provider / payment_mock 只查 core,config_encryption_key 不查 disk-ops。
- `_validate_invariants` 另有三条**与环境无关**的形态闸:`public_base_url` 必须匹配锚定的 `scheme://主机[:端口][/路径]`,不含空白或 shell 元字符;`jupyter_domain_suffix` / `service_domain_suffix` / `admin_host` 必须是裸主机名(可带端口);`tenant_pod_cidr` 非空时必须是合法网段。
- 启动校验只管 provider 选择,不查渠道凭据齐全性:短信 / 验证码 / 实名凭据经平台配置中心在线录入,运行期渠道工厂(`app/core/sms.py`、`app/core/email.py`、`app/core/captcha.py`)缺凭据即 fail-closed,管理端 `test-sms` / `test-email` 可验。`alertmanager_token` 未配与 `prometheus_url` 指向本地只在 lifespan 打 WARNING。`k8s_backend=real` 不要求 `environment=prod`。
- 人机验证与实名认证是运行期开关(`captcha_enabled` / `real_name_enabled`,默认关):关闭即跳过对应校验。**prod 下关闭双重封死**:在线写库层 `prod_forbidden` 禁关,lifespan 启动 fail-fast(`assert_prod_compliance_gates`:`prod_gate=True` 的 captcha_enabled / real_name_enabled / real_name_required_for_recharge 任一未开即拒启);`compute_config_warnings` 在配置页出红牌。三者共用 `SettingSpec` 上的同一份声明。These three gates apply under `compliance_profile=cn` only (`SettingSpec.prod_forbidden_profiles`); under `none` they are ordinary switches and a disabled CAPTCHA in prod is a configuration warning. 无 mock 渠道,测试经 `set_captcha_channel` / `set_realname_provider` 注入。组合约束 `real_name_required_for_recharge=true ⇒ real_name_enabled=true` 任意环境生效。
- 首个管理员由脚本创建(`ensure_bootstrap_admin`:`admin_users` 为空时才建,口令 ≥12 字符、≤72 字节):dev/test 随 `apps/api/scripts/seed_dev.py` 建,prod 用 `apps/api/scripts/bootstrap_admin.py`(口令取 `SUPERDL_SEED_ADMIN_PASSWORD`,未设则随机生成写 0600 文件,不打印 stdout)。幽灵 `SUPERDL_*` 环境变量启动打 WARNING。
- 密钥与凭据不入 git,只经环境变量或平台配置中心注入;deploy 模板一律 `CHANGE_ME`。Secret 模板 `deploy/app/secrets.example.yaml` 不放在整目录 apply 路径下。

### 请求边界

- 限流计数落 PG(`rate_limit_counters`);429 带 `Retry-After`(DB 侧计算),401 带 `WWW-Authenticate: Bearer`。
- 统一错误体覆盖框架层异常:路由 404/405 也渲染 `{code, message, message_key, params, detail}`(405 用 `METHOD_NOT_ALLOWED` / `common.methodNotAllowed`);未捕获异常由内层 `Uniform500Middleware` 渲染成 500,审计中间件落 `result=500`。
- 安全响应头由纯 ASGI 中间件注入;`/metrics` 须 Bearer(见 [observability.md](./observability.md))。
- 边缘收口中间件(`app/core/edge_guard.py`)prod 恒开、无开关(`environment` 只有 dev / test / prod;dev / test 不启用):`/api/admin/*` 双闸 —— Host 命中 `admin_host` **且** `X-Admin-Edge-Token` 命中 `admin_edge_token`(admin 域 nginx 同源反代注入,见 `deploy/app/nginx.admin.conf`),任一不符 404;`/metrics` 与 `/api/internal*` 带 `X-Forwarded-For` 一律 404。`/api/internal` 下只有服务端点鉴权回调,这条收口是它唯一的保护(见 [services.md](./services.md))。
- 公网真实入口是 console 域(CDN → 前置反代 → Envoy `console-https` listener),`api-https` listener 公网不可达:`/api/v1` 由 HTTPRoute `superdl-console-api` 直达 `superdl-api`(nginx 同源反代只在 compose / dev 生效),边缘 404 清单在 `superdl-console-edge-deny` 与 API 域同一份,限流与请求体上限同 API 域。链路上每一跳前置地址都必须同时进 ConfigMap `FORWARDED_ALLOW_IPS` 与 `ClientTrafficPolicy.clientIPDetection.xForwardedFor.numTrustedHops`(少一跳,按 IP 的限流桶与 `audit_log.ip` 全部塌成那一跳的地址),拓扑见 [../architecture.md](../architecture.md)「公网真实链路」。
- Bearer token 常量时间比较统一走 `app/core/http.py` 的 `bearer_matches`(先 `.encode()` 成 bytes),/metrics(API 与 worker)与 Alertmanager webhook 共用。
- 登录限流「先计数再判定」:密码路径在 bcrypt 前对 IP / IP+账号 / 账号短窗三桶各记一次并判定(`core/loginguard.login_attempt`),并发突发按桶容量截断而不是按在途并发放大;成功后清零 / 退还,账号日窗只计失败。bcrypt 线程池(4 线程)在途上限 64,超出直接 429(`core/security._BCRYPT_MAX_INFLIGHT`),不无限排队。发码闸门顺序与按号 / 按 IP / 平台分桶见 [account.md](./account.md)。
- `client_ip` 取 uvicorn 按 `FORWARDED_ALLOW_IPS` 改写后的 `scope["client"]`:每一跳前置代理 / CDN 回源地址都必须列入该网段(`deploy/app/k8s/00-namespace-config.yaml`),漏一跳即全部公网请求坍缩成同一个 IP,按 IP 的限流桶与 `audit_log.ip` 一并失真。
- 短信渠道走 `app/core/sms.py` 的 Protocol + 工厂(mock / 阿里云 dysmsapi);落日志时手机号与验证码由全局日志处理器(`app/core/logging.py`)按键名打码。
- 高危管理操作「原因必填 → 二次确认 → 审计」;审计不落 token、密钥与配置值。
- 入站 `X-Request-ID` 只在匹配 `[A-Za-z0-9._-]{1,64}` 时沿用,否则服务端生成 16 位十六进制 id 回带(`app/core/observability.py`)。
- `/api/*`、`/metrics`、`/docs`、`/redoc`、`/openapi.json` 响应带 `Cache-Control: no-store`(`app/core/security_headers.py`),CDN 与浏览器不得缓存 API 响应;`/healthz` `/readyz` 不加。
- 审计行带 `request_id`(与响应头 `X-Request-ID`、结构化日志同值)与 `user_agent`。中间件路径的 `request_id` 从响应头抄回,资金域同步审计直接读 contextvar。
- `action` / `target` / `user_agent` 入库前按列宽截断;`Idempotency-Key` 请求头在契约层按承载列宽(64)拦下。

### 租户隔离

#### 隔离级别分级

三级 GPU 隔离;档位的唯一事实源是 `pool_label`:

| 级别             | 池   | 机制                                      | 安全属性                                 | 适用场景                   |
| ---------------- | ---- | ----------------------------------------- | ---------------------------------------- | -------------------------- |
| **VM 级隔离**    | kata | Kata Containers (QEMU VM) + VFIO 整卡直通 | 硬件级安全边界                           | 生产服务、敏感数据         |
| **硬件切分隔离** | mig  | NVIDIA MIG                                | GPU 硬件强制隔离;显存/算力切分由硬件执行 | 标准共享、可信多租户       |
| **软件限额**     | hami | HAMi `libvgpu.so` (LD_PRELOAD CUDA 拦截)  | **非安全边界**;容器内 root 可绕过配额    | 成本优化、容错性高的批处理 |

**关键安全约束:**

- HAMi 池是**性能隔离/资源限额**,不是**安全隔离**。租户容器以 root 运行。
- 跨租户显存残留:HAMi 将多租户放在同一未分区 GPU 上(MIG 无此问题)。
- 默认 `shared_tier_allowed_pools = "mig,hami"`;摘掉 hami 即共享档仅 MIG。
- 前端对经济档(hami 池)有知情同意 modal,文案与代码同提交。

- 租户容器加固基线(`core/k8s/real.py::tenant_security_context`,无条件下发):`allowPrivilegeEscalation=false`、`seccompProfile=RuntimeDefault`、`capabilities.drop=[ALL]` 后只 add 回 `SYS_CHROOT` / `SETUID` / `SETGID`。不下发 `runAsNonRoot`。
- 实例容器内的两处凭据落点由 entrypoint 钉死(`deploy/instance-images/entrypoint.sh`,契约见 [../../deploy/instance-images/README.md](../../deploy/instance-images/README.md)):
  - **Jupyter token 显式上 argv**(`--IdentityProvider.token`);配套 `JUPYTER_CONFIG_DIR` 挪到容器可写层 `/run/jupyter-config`(不在 PVC `/root` 下)。
  - **`AUTHORIZED_KEYS` 无条件覆写 `~/.ssh/authorized_keys`**,空值把文件清空;不加 `[[ -n ... ]]` 守卫。
- 租户 Pod 必须带 Egress 隔离 NetworkPolicy:禁访内网网段(含 CGNAT 100.64.0.0/10 与云元数据地址),封 TCP 端口 SMTP(25/465/587)、SMB/NetBIOS(135/139/445)、Telnet(23)、RDP(3389)、MySQL(3306)、PostgreSQL(5432)、Redis(6379)、Elasticsearch(9200)、Memcached(11211)、MongoDB(27017)。**数据存储端口封在端口维**(公网端点的托管库也挡)。租户确需外连自有数据库走 TLS 反代或工单白名单逐案开放,与 UDP 同口径。
- 入方向默认拒东西向,只放行 `envoy-gateway-system`(Envoy **数据面 Pod** 所在 ns,`core/k8s/real.py` 的 `GATEWAY_DATAPLANE_NAMESPACE`)到租户 Pod(**不限端口**)与 SSH 22。**SSH 22 来源是 `0.0.0.0/0` 除去 Pod 网段**(`tenant_pod_cidr`,默认 `10.42.0.0/16`;改 CNI 网段必须同步改它);整段私网不排;Pod 网段必须排。留空 = 不下发 except,仅排障回退。**排掉的网段里含跨节点 NodePort 的 SNAT 来源**(入口节点的 `cilium_host`,从 Pod 子网池动态分配),由 `deploy/cluster/cilium-policies.yaml` 的 `superdl-tenant-ssh-from-nodes` 按身份放行 22。**不是 `Gateway` 对象所在的 `superdl` ns**。平台自身前端与 API 的入向 NetworkPolicy(`deploy/app/k8s/09-networkpolicy.yaml`)同源。
- 每租户独立 namespace + ResourceQuota,数据盘一盘一只 CephFS PVC(RWX);租户 ns 打 PSA 标签(enforce=baseline、audit/warn=restricted),容器有 ephemeral-storage 限额(请求 10Gi / 上限 64Gi)与带宽上限注解(`kubernetes.io/egress-bandwidth`,默认 200 Mbit/s,`SUPERDL_TENANT_EGRESS_BANDWIDTH_MBPS`;ingress 默认不限;两档均由 Cilium `bandwidthManager` 执行)。数据盘 PVC 名由盘 uuid 算出(`data_disk_pvc_name`),租户之间天然隔离,无共享卷与子路径。
- **租户手里没有任何 K8s 凭据,`httproutes` 写权限只给 `superdl-tenant-mgr` 这一个 SA**(`deploy/app/k8s/01-rbac.yaml`)。listener 侧消歧:平台三个 listener 写**精确 hostname**、租户 listener 写通配,SNI 与 Host 按「精确优先于通配」匹配;租户域与平台域共用一级域是支持的形态。
- 租户 ns 的 secrets 权限是预置 ClusterRole `superdl-tenant-secrets`(无 ClusterRoleBinding)经 tenant-mgr 在每个 tenant ns 建的 RoleBinding 生效(`core/k8s/real.py::_ensure_tenant_rbac_sync`):tenant-mgr 对 `clusterroles` 只有 `bind` 且 `resourceNames` 锁定该名,对 `roles` 无 create / patch / escalate / bind(只留 `delete` 清旧版 Role);存量 binding 的 roleRef 不可改,指向旧 Role 的删掉重建。`superdl-api` 另持 `pods/log: get`(ClusterRole `superdl-api-logs`,实例日志直读),其余平台 SA 无;任何平台 SA 都无 `pods/exec|attach|portforward|ephemeralcontainers`(RBAC 不给,准入 ① 再拦一道)。
- **任何非 `superdl` 命名空间的 SA 都不得持有 secrets 读权**(Alloy / node-exporter 是 DaemonSet,跑在租户 GPU 节点上,节点 root 即可取其 token):监控栈 RBAC 由 values(`alloy` 的 `rbac.rules` 收窄到 pods / pods/log / namespaces / services / endpoints / nodes,`loki` 关 ruler sidecar 且 SA 与 Pod 均不挂 token,kps `global.rbac.create=false` + kube-state-metrics 去掉 secrets 采集器)+ `deploy/cluster/monitoring-rbac.yaml`(prometheus-operator 的 configmaps / secrets 只在 `monitoring` ns 的 Role,集群级只留 CRD / statefulsets / pods 等;Prometheus SA 无 secrets 动词)收窄;引用凭据的 ServiceMonitor / PodMonitor 一律放 `monitoring` ns,Bearer 取 `monitoring/superdl-metrics-token`(`deploy/app/k8s/08-monitoring.yaml`)。CI helm 渲染断言(`monitoring-rbac` job)+ kind `auth can-i` 断言 + `preflight.sh` 各一道;Grafana(仅 full 档)的 dashboard / datasource sidecar 是唯一豁免。
- 创建实例只校验镜像引用形态(`core/registry.is_valid_image_ref`),来源白名单默认关;收紧时在平台配置·镜像仓库填 `image_allowed_registries`(每行一个仓库前缀),生效白名单 = 配置行 ∪ Harbor 地址前缀(`core/registry.effective_image_allowlist`),配置后只放行平台镜像目录内的引用与这些前缀;prod 下生效白名单为空(既无配置行也未配 Harbor)拒绝启动(`assert_prod_image_allowlist`)。实例镜像 `ENV NVIDIA_VISIBLE_DEVICES=void` 覆盖 nvidia/cuda 基座的 `all`,可见卡只来自分配链注入。**每条前缀一律补成以 `/` 结尾**(匹配是裸 `startswith`)。
- **准入层是七条 ValidatingAdmissionPolicy,全部 `Deny`**(`deploy/cluster/admission/tenant-restrictions.yaml`):① 平台 SA 的写操作范围(`superdl` / `tenant-*` ns + nodes,含子资源 `*/*`;禁 `pods/exec|attach|portforward|ephemeralcontainers`;集群级请求无 `request.namespace` 键,表达式一律 `has()` 守卫)、② 租户 ns Pod 安全基线(`containers + initContainers + ephemeralContainers` 同一份 securityContext 基线;不得指定非 `default` 的 serviceAccountName)、③ 平台 SA 对 Node 的字段级写白名单(cordon + `superdl.io/*`、池标签 `node-restriction.kubernetes.io/superdl-pool` 与两个 GPU operand 键;`superdl-infra` 落点键不在其列)、④ 全局 Pod 兜底(只拦显式 `hostUsers: true`)、⑤ `superdl` ns 内 Pod 的 Secret 引用白名单(平台 SA 直建的 Pod 另须 `automountServiceAccountToken: false` 且不指定 SA)、⑥ 平台 SA 建 Job 时 Pod 模板的 Secret 引用白名单(模板同 ⑤ 两条 SA 约束)、⑦ 平台 SA 只能删 GPU 工作节点。**缺 Binding 是静默 fail-open**(`failurePolicy: Fail` 只在策略被求值时生效);由 `deploy/cluster/apply.sh` 在 helmfile 之前无条件 apply 并回读,`deploy/cluster/preflight.sh` 与 `scripts/release.sh` 各再断言一次。
- **「不给 `secrets` 动词」不等于「读不到 Secret」**:命名空间内 `pods:create` 或 `batch/jobs:create` 等价于该 ns 的 `secrets:get`(kubelet 代创建者解析 `secretKeyRef` / `envFrom` / secret 卷 / `imagePullSecrets`,不做 secrets 授权检查,PSA `restricted` 也不约束)。真正的防线是策略⑤⑥的「能引用哪个 Secret」白名单;⑤ 另覆盖 Job 派生 Pod 的创建者 `system:serviceaccount:kube-system:job-controller`。
- 服务端点的 API Key 摘要、鉴权链路与网关策略约束见 [services.md](./services.md)。
- 合规:前端 `/legal/terms` 与 `/legal/privacy` 为模板页,注册勾选前后端强校验,备案号运行期下发。
- 公网 API 域与 console 域上 `/api/admin`、`/api/internal`、`/metrics`、`/docs`、`/redoc`、`/openapi.json` 在边缘直接 404(`04-gateway.yaml` 的 `HTTPRoute superdl-api-edge-deny` / `superdl-console-edge-deny` + `HTTPRouteFilter superdl-edge-not-found`),应用层 `edge_guard` 是第二道。
- 平台库:api / worker 以无 DDL 的应用角色连接(`deploy/pg/roles.sql`;`balance_ledger`、`audit_log`、`instance_events` 只追加,`alembic_version` 只读;审计保洁只经 SECURITY DEFINER 函数 `audit_log_prune(days ≥ 30)`),owner 连接串只给迁移 Job(`superdl-db-migrate`,须为非 SUPERUSER 的库 owner,见 `deploy/pg/README.md`);非本机 PG 连接串 `sslmode=verify-full&sslrootcert=...`(自签 CA 经 ConfigMap `superdl-db-ca` 挂载,`db._split_db_tls` 翻成校验主机名的 SSLContext)。
- 部署层(env)进入平台配置的值同样过 `SETTING_SPECS` 格式白名单(`platform_config.env_layer_problems`,prod 不合格拒启);`cluster_join_token` 字符集锁死 `[A-Za-z0-9:._~+/=-]{16,512}`,node-join.sh 写 agent config.yaml 前再校验一次并用双引号标量。

### 限流分层

两层:边缘层(Envoy Gateway,`deploy/app/k8s/04-gateway.yaml`)按**每源 IP** 兜底 —— API 域 `BackendTrafficPolicy superdl-api-ratelimit`,console 域的 `/api/v1` 路由(公网真实入口)挂同一份数值的 `superdl-console-api-ratelimit`,管理端路由挂自己的 `superdl-admin-ratelimit`(白名单不是限流);精细化限流在应用层(`app/core/ratelimit.py`,PG 固定窗口,多副本共享)。数值见 [limits.md](./limits.md)。

边缘层这几条「配错不报错、静默失效」,改动后须 `kubectl describe backendtrafficpolicy/securitypolicy/clienttrafficpolicy -n superdl` 看 `Accepted=True`:

- **每源 IP 靠 `sourceCIDR.type: Distinct`**(默认 `Exact` 是全网一个桶)。EG v1.9.0 的 local 限流支持 distinct,升版按源码复核。
- **没有每源 IP 并发连接数限制**:`ClientTrafficPolicy.connection.connectionLimit` 是每个 Envoy 实例的连接总量,只作防内存耗尽兜底。
- **管理端源 IP 白名单**(`SecurityPolicy superdl-admin-allowlist`,挂 `superdl-admin` 路由):`authorization.defaultAction: Deny` + 一条 `action: Allow` 的 `principal.clientCIDRs`。占位符是 `192.0.2.0/24`(RFC 5737 文档网段,未替换即 fail-closed);`deploy/cluster/preflight.sh` 按这个网段扫描,未替换不放行。白名单只许具体出口 /32 或办公网段;禁止 `100.64.0.0/10`、`10.0.0.0/8`、`172.16.0.0/12`、`192.168.0.0/16` 这类整段(preflight 同样拦)。
- **每 IP 限流与管理端白名单都建立在 `envoyService.externalTrafficPolicy: Local` 之上**(`EnvoyProxy superdl-proxy`,显式写死)与 `ClientTrafficPolicy.clientIPDetection.xForwardedFor.numTrustedHops` 之上(仓库值 `0` = 以 TCP 对端为客户端 IP;公网前置 CDN / 反代时改成实际前置层数,并把前置地址同步进 `FORWARDED_ALLOW_IPS`)。
- **租户 Jupyter 的 `app-https` listener 挂一条 local 限流**(`BackendTrafficPolicy superdl-app-ratelimit`,每端点 100/s),与 `superdl-svc-ratelimit` 同构(一实例一条 HTTPRoute ⇒ 一个端点一个桶):**不许**加 `sourceCIDR.type: Distinct`(EG 的 `alwaysConsumeDefaultTokenBucket` 钉死 false,命中 descriptor 后默认桶不消耗)。同一 targetRef 不能再挂第二条同类策略(EG 不合并,最老者生效)。
- local 限流是每个 Envoy 实例本地计数,多副本全局上限约为配置值 × 副本数。

### 宿主机与备份

- 全部宿主机 sshd 只公钥(口令 / 键盘交互关、root 只公钥)+ nftables `input` 默认拒(SSH 按 `ssh_allow_cidrs`、集群端口只对 `cluster_cidrs`、NodePort 与 server 的 80/443 对外),由 `deploy/ansible/harden.yml` 下发,关口令前断言目标机已有公钥。
- 自建单实例 PG 三条备份链(每日 dump、每周 basebackup、每 5 分钟 WAL)全部 gpg 后才离开本机,各有 node-exporter textfile 指标(`superdl_pg_backup_last_success_timestamp_seconds` / `superdl_pg_basebackup_last_success_timestamp_seconds` / `superdl_pg_wal_sync_last_success_timestamp_seconds`),`pg_hba.conf` 生产与仓库漂移写 `superdl_pg_hba_drift`(`deploy/pg/README.md`)。
- k3s 集群状态(`server/token`、`cred`、`tls`、etcd 快照或 SQLite 副本)每 6 小时 gpg 加密异机(`deploy/cluster/k3s/state-backup.sh`,指标 `superdl_k3s_state_backup_last_success_timestamp_seconds`);server 损毁而无此备份 = 全部 Secret 含配置主密钥不可恢复。
- 备份镜像机不得是承载租户负载的节点。

## 已接受取舍

- **token 存 localStorage**;CSP 按站点收敛(web 放行域白名单化,admin 严格 'self')。
- **固定窗口限流 2× 突发**;精度敏感动作(MFA / 短信)窗口与配额单独收紧。
- **用户端无 2FA**:SMS 验证码是信任根;管理端全角色强制 TOTP + 恢复码 + 防重放,开关 `admin_mfa_enabled` 默认开,prod 关闭需 reason 进审计。
- **仅 +86 手机号**。
- **pending 订单 48h 收敛窗**:超时关单由查单 poller 收敛。
- **双人制衡「一人控两账号」残余**:组织流程兜底,技术层已拦「同账号复核自己」与「发起后新建账号复核」。
- **租户入向放行网关数据面不限端口**:服务型实例容器端口由用户声明;HTTPRoute 全部由控制面生成。
- **服务型实例持续 not-ready 不判故障**:`reconciler._running_pod_lost_reason` 对 `workload_type='service'` 跳过 `pod_unready`;`pod_lost` 与 `node_lost` 不豁免。
- **POLICY_LABELS 运营术语不译**。
