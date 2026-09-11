# 在线服务

把用户容器发布成带 API Key 的公网 HTTPS 服务。**服务是独立聚合根,实例是它的不可变版本**(见 [decisions.md](../decisions.md)):`services` 只存身份与网关侧属性,每次部署 = 一台 `workload_type='service'` 的新实例(`instances.service_id` 反指,暴露规格快照在实例行上);计费、配额、回收、reconciler、迁移监听的主体仍是实例(见 [orchestrator.md](./orchestrator.md)),服务级操作委托 `orchestrator.service` 的 row 级函数,orchestrator 不反向依赖本模块、不查 `services` 表。

## 数据模型

- `services`:`public_slug` 唯一、`user_id`、`name`、`protocol`(CHECK ∈ {http})、`require_api_key`、`desired_state`(CHECK ∈ {running, stopped})、`current_instance_id?`、`rollout_instance_id?`(更新中的候选版本)、`revision`、`released_at?`(当前实例 released 时由迁移监听器写入;非空即终态)
- `instances` 的服务快照列:`service_id?`(索引)、`service_revision?`、`service_slug?`、`service_port?`(CHECK 1–65535 且 ∉ {22, 8888})、`health_path?`;CHECK `(workload_type='service') = (service_id IS NOT NULL)`
- `service_api_keys`:`user_id`、`service_id`(密钥归属服务,换版本照常有效)、`name`、`key_hash` 唯一、`key_prefix`、`last_used_at?`、`revoked_at?`

### 状态派生

服务状态不落库,由 `services/state.py::derive_status(service, current, rollout)` 推导:

| released_at | rollout_instance_id | current.status | unready_since | status | ready |
|---|---|---|---|---|---|
| 非空 | — | — | — | `released` | 否 |
| 空 | 非空 | 任意 | — | `deploying` | 否 |
| 空 | 空 | creating / starting | — | `deploying` | 否 |
| 空 | 空 | running | 空 | `running` | **是** |
| 空 | 空 | running | 非空 | `unready` | 否 |
| 空 | 空 | stopping / stopped / frozen / failed / releasing | — | 同名 | 否 |
| 空 | 空 | (无实例) | — | `stopped` | 否 |

`unready` 不是故障(实例仍 running、照常计费);`desired_state` 随出参下发,前端据此区分「用户停的」与「欠费 / 管理员停的」。

## 契约

| 端点 | 角色/鉴权 | 说明 |
|---|---|---|
| `GET /api/v1/services?status=&name=&cursor=&limit=` | user | 降序游标分页;`name` 模糊匹配名称与 slug 前缀;`status` 按派生态在本页内过滤;已删除不列 |
| `POST /api/v1/services` | user | 部署(202):同事务写 `services` + 第 1 版实例(creating)+ 事件 + outbox;实名闸门与创建实例同款;`Idempotency-Key`(键落实例行,重放经实例反查服务;同键异参 409) |
| `GET /api/v1/services/{slug}` | user | 详情:身份 + 派生 `status` / `ready` + `url` + 当前 / 候选实例(`InstanceOut`)+ 当前版本容器配置回显(`container`,env 只回明文项,密文项只回键名);非属主 404 |
| `PATCH /api/v1/services/{slug}` | user | 改名 / `require_api_key`:只改 `services` 行并失效鉴权缓存(≤5s 生效),**不动 K8s** |
| `POST /api/v1/services/{slug}/stop` `/start` | user | 委托当前实例的关机 / 开机(锁序 instance → service),写 `desired_state`;版本更新在途或已删除 409 |
| `DELETE /api/v1/services/{slug}` | user | 释放当前实例 + 吊销全部密钥;运行中 409(`services.deleteNeedsStopped`);释放中 / 已删除幂等直回;slug 不复用 |
| `GET /api/v1/services/{slug}/events` | user | 全部版本实例的 `instance_events` 并集(降序游标分页),每条带 `instance_uuid` 与 `revision` |
| `GET /api/v1/services/{slug}/revisions` | user | 版本历史 = 该服务下全部实例(含已释放),版本号降序 |
| `POST /api/v1/services/{slug}/revisions` | user | 版本更新(202,重建):同事务落新版本实例(creating,`revision+1`)+ 旧版本运行中即关机(reason `rollout`)+ `rollout_instance_id`;`Idempotency-Key`(键落新实例行);包周期服务 / 更新在途 / 旧版本变更中 409;`env_secret_keep` 沿用当前版本密文值 |
| `GET /api/v1/services/{slug}/logs?tail_lines=` | user | 当前版本容器日志,复用实例日志四道闸(owner / 状态 / 限流 20/h/user / K8s 读 5s 超时) |
| `GET /api/v1/services/{slug}/bills` | user | `bills_hourly` 按全部版本实例并集分页 |
| `GET /api/v1/services/{slug}/api-keys` | user | 列表,只回 `key_prefix` |
| `POST /api/v1/services/{slug}/api-keys` | user | 新建;**明文只在本次响应出现一次**;单服务活跃密钥上限见 [limits.md](./limits.md);已删除的服务 409 |
| `DELETE /api/v1/services/{slug}/api-keys/{key_id}` | user | 吊销:写 `revoked_at`,不删行 |
| `GET /api/admin/v1/services` | admin/ops/finance/readonly | 管理端全局列表(`user_id` / `q` / `include_released`),见 [admin.md](./admin.md) |
| `/api/internal/v1/endpoint-auth` | 无(集群内) | 网关 `SecurityPolicy.extAuth` 回调,**不对公网开放**(边缘 404 + edge_guard);接受全部 HTTP 方法;每次拒绝计 `superdl_endpoint_auth_denied_total`(`EndpointAuthDenialSustained` 告警) |

实例层对服务的版本实例只开放只读端点与购买模式类端点;stop / start / restart / DELETE / 重置 token 一律 409 `orchestrator.serviceInstanceLifecycle`,`GET /instances` 默认不列它们。`POST /instances` 不接受服务容器参数。

## 规则与不变量

### 部署与版本

- 部署走 `create_instance_row(service=ServiceBinding(...))`:形态 service、镜像必须钉版本(`:latest` 与不写 tag 拒)、暴露规格快照到实例行、按 `with_ssh` 决定 SSH 入口;实例与服务同名。
- 幂等指纹不含 slug / service_id;并发同键撞库时 `insert_idempotent` 连同未提交的 `services` 行一起回滚。
- `services` 行的唯一非请求写入点是迁移监听器(`register_service_listeners`):RUNNING 迁出即失效鉴权缓存;当前实例 released 即写 `released_at`(覆盖用户删除、欠费回收、保留期 GC)。监听器在 transition 的实例行锁之后才碰 `services` 行(锁序 instance → service),不再锁另一台实例。
- 版本更新只做 recreate,不对包周期服务开放;HTTPRoute 每实例一条。

### 版本更新(recreate)

1. 请求事务:幂等重放最先判;`released` / 在途 / 包周期(请求或当前实例)/ 旧版本不在 `running | stopped | failed` 一律 409;锁旧实例 → 锁服务行 → `create_instance_row(service=ServiceBinding(revision+1), exclude_instance_id=旧)`(配额三维与软准入把旧版本份额让给新版本,余额不让);`env_secret_keep` 的键从旧实例密文解出、按新实例 AAD 重加密;`revision += 1`、`rollout_instance_id = 新`、`desired_state = running`;旧版本 running → `stop_instance_row(reason="rollout")`。
2. 新版本 → running:迁移监听器翻转 `current_instance_id`、清空 `rollout_instance_id`、失效鉴权缓存、入队 **`service.retire{service_id, instance_id=旧}`**(归 `tenant-mgr` 组件)。handler:锁旧实例 → 仍是当前版本 / 已在释放 → no-op;`stopping | stopped | frozen | failed` → `release_instance_row(actor="system", reason="rollout_retire")`;其它状态抛错退避(30 × 20s)。
3. 新版本 → failed:监听器清空 `rollout_instance_id` 并站内信(type `service`,target = slug);旧版本留在 stopped,用户「启动」= 回滚(`revision` 不回退)。
4. 用户可见窗口:旧 Pod 删除到新 Pod Ready 之间网关回 503;slug / URL / API Key 不变。
5. 零重复扣款:旧版本尾账由计费监听器在 running→stopping 出一次;新版本是新 instance_id;retire 重放到释放中的行是 no-op。
6. 数据盘不随版本:旧版本释放前仍占用挂载,新版本 `data_disk_id` 只能挂空闲盘,前端一律不带。

### 域名规则

端点主机名 = `<public_slug>.<SUPERDL_SERVICE_DOMAIN_SUFFIX>`,例如 `svc-a1b2c3d4e5.svc.superdl.example.com`。

- slug 形如 `svc-` + 10 位小写 base32,部署时生成,UNIQUE,碰撞重试(每次插入包在 SAVEPOINT)。**不用 `instances.uuid`**。
- **服务端点与 Jupyter 必须落在两个不同的 listener 上**(只有服务那个挂 `SecurityPolicy.extAuth`)。分开方式二选一:
  - **按 hostname 分(默认)**:`SUPERDL_SERVICE_DOMAIN_SUFFIX` 与 `SUPERDL_JUPYTER_DOMAIN_SUFFIX` 两个不同后缀(`*.svc.<域>` / `*.app.<域>`),两个 listener 同在 443,两张泛域名证书。
  - **按端口分**:只有一张**一级**通配证书(`*.<域>`)时两个后缀都写裸域(Gateway API listener hostname 只允许整标签通配);443 留给服务端点,Jupyter 用 `SUPERDL_JUPYTER_URL_PORT` 让到非 443;把关的只剩 `svc-` 前缀(`endpoint_slug_from_host`)。
- 泛域名解析到网关入口(只需 80/443);证书由 listener 的 `certificateRefs` 引用(默认形态 `deploy/app/k8s/05-cert-manager.yaml` 签发的泛域名证书,按端口分则共用一张一级通配证书)。

### 鉴权链路

```
客户端 ──HTTPS──▶ Envoy(svc-https listener)
                    │  SecurityPolicy.extAuth:每请求一次回源
                    ├──▶ superdl-api /api/internal/v1/endpoint-auth
                    │      Host 头取 slug → services(未删除)→ 当前实例 running
                    │      → 公开端点匿名放行;否则 key_hash 查 service_api_keys:未吊销 ∧ 归属该服务 → 200,否则 401
                    └──200──▶ <uuid>-svc ClusterIP ──▶ 用户容器
```

- slug **从 `Host` 头取,不从 path 取**:清单用 `extAuth.http.pathOverride` 把鉴权请求 path 改写成静态值(同位置的 `path` 是前缀语义,与 `pathOverride` 互斥)。
- **extAuth 策略挂在 `svc-https` listener 上,服务全部端点(对象数 O(1))。** 公开端点(`require_api_key=false`)同样经过回调,由回调匿名放行并回 `x-superdl-key-id: anonymous`;**控制面是全部对外服务的同步依赖,挂了就是全部 503**(ext_authz 不缓存)。
- 回源侧进程内缓存正向结果 5s(负结果不缓存);吊销 / 鉴权开关翻转 / 删除服务主动失效本进程条目,停机经迁移监听器失效,跨副本最坏一个 TTL 收敛;`last_used_at` 每 key 每 60s 至多一写。
- **Key 的归属是服务级,不是账号级。** 校验链完整走「服务未删除 → 当前实例 running → 未吊销 → 归属该服务」。用例在 `tests/test_endpoint_auth.py`。
- **API Key 明文只出现一次。** 库里只有 HMAC-SHA256 摘要(`crypto.hash_api_key`,主密钥只走 env)。遗失只能吊销后新建;吊销写 `revoked_at` 不删行。
- **`/api/internal` 的边缘收口是回调端点唯一的保护。** prod 下带 `X-Forwarded-For` 一律 404(`core/edge_guard.py`,判据与 `/metrics` 同款)。

### 网关策略

以下每条「配错不报错、静默失效」,改动后确认对象 `Accepted=True`。

- **`statusOnError` 显式配 503**(默认 403)。
- **`failOpen` 保持默认 false**(true 时策略配置非法也整个绕过鉴权)。
- **`timeout` 显式收紧**(清单 1s;默认 10s)。
- **`headersToBackend` 是覆盖语义**:列进白名单的头来自鉴权服务,**没列进去的同名头原样透传客户端的值**。租户容器要信任的头都必须列进去。
- **`mergeType` 默认不叠加**:给某条租户路由再挂任何 `SecurityPolicy` 会**整个替换掉** listener 级的 extAuth。要叠加必须显式 `mergeType: StrategicMerge`。
- **拒绝时的响应体与响应头原样透传给客户端**,可复用 `core/errors.py` 统一错误体;**该端点的 4xx 直达公网,绝不能带栈、内网主机名或 `Set-Cookie`**。
- **限流是每端点独立配额**:一条挂 `svc-https` listener 的 `BackendTrafficPolicy`(local),桶按路由分。限额(20/s/端点)手工写在 `deploy/app/k8s/04-gateway.yaml`,改值重新下发。local 计数是每 Envoy 实例本地,多副本全局上限约为配置值 × 副本数。数值见 [limits.md](./limits.md)。

### 实例侧

- 服务的版本实例默认 `with_ssh=false`,**不进 SSH 端口池**。
- 版本实例**持续 not-ready 不判故障**(reconciler 豁免 `pod_unready`),如实呈现为服务的 `unready`;配套 `startupProbe` 15 分钟启动预算。
- 用户 env 整包 AES-GCM 落库(`instances.env_encrypted`,AAD 绑实例 uuid);密文项经 per-instance Secret 以 `secretKeyRef` 注入,**明文不进 Pod spec**。
- 用户 env 键名黑名单:拒 `JUPYTER_` / `SUPERDL_` / `NVIDIA_` 前缀与 `AUTHORIZED_KEYS`;准入层 `superdl-tenant-pod-baseline` 有同口径 CEL 规则双层兜底。
- 容器端口不得为 22 或 8888,DB 侧 CHECK 兜底。
- 保留期 GC、竞价抢占、欠费冻结、管理员强制停止都作用在版本实例上,会让 `desired_state=running` 的服务变成 stopped / released;账号注销的前置检查按实例判。

fake 后端测不出的实机验证项(证书签发、`SecurityPolicy` 的 `status.ancestors[].conditions`、真链路 Key 校验、每端点限流实测、租户间东西向不可达)见 `deploy/cluster/runbooks/cluster-validation.md`「北向入口」节。
