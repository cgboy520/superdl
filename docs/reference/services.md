# 对外服务端点

把用户容器发布成带 API Key 的公网 HTTPS 服务。服务型实例是 `instances` 的第二种形态
(`workload_type='service'`,见 [orchestrator.md](./orchestrator.md)),不是独立实体 ——
状态机、计费、配额、回收、reconciler、监控、审计全部复用。本文只写端点这一层:
域名规则、网关鉴权链路、API Key 生命周期、限流,以及为此接受的可用性取舍。

## 数据模型

- `service_endpoints`:`instance_id` 唯一(一实例一端点)、`public_slug` 唯一、`container_port`、
  `protocol`、`health_path?`、`require_api_key`
- `service_api_keys`:`user_id`、`instance_id`、`name`、`key_hash` 唯一、`key_prefix`、
  `last_used_at?`、`revoked_at?`

## 域名规则

端点主机名 = `<public_slug>.<SUPERDL_SERVICE_DOMAIN_SUFFIX>`,例如
`svc-a1b2c3d4e5.svc.superdl.example.com`。

- slug 形如 `svc-` + 10 位小写 base32,建端点时生成,库内 UNIQUE,碰撞重试。
  **刻意不用 `instances.uuid`** —— 内部主键不该出现在公网域名、TLS SNI、访问日志与第三方 Referer 里。
- **不变量:服务端点与 Jupyter 必须落在两个不同的 listener 上。** 只有服务那个挂
  `SecurityPolicy.extAuth`;并成一个 listener 就只能逐路由挂策略,而 EG 里按路由挂是整份替换语义,
  漏挂一条 = 那个端点彻底不鉴权,照样返回 200,没有任何报错。分开的方式有两种,选一种:
  - **按 hostname 分(默认)**:`SUPERDL_SERVICE_DOMAIN_SUFFIX` 与 `SUPERDL_JUPYTER_DOMAIN_SUFFIX`
    写成两个不同后缀(`*.svc.<域>` / `*.app.<域>`),两个 listener 同在 443。需要两张泛域名证书。
  - **按端口分**:手上只有一张**一级**通配证书(`*.<域>`,盖不住 `*.svc.<域>` 这种两级名字)时,
    两个后缀只能都写成裸域,hostname 就分不开了 —— Gateway API 的 listener hostname 只允许整标签
    通配(CRD 正则 `^(\*\.)?…`),写不出 `svc-*.<域>`。此时靠端口分:443 留给服务端点(用户要粘进
    客户端代码的地址),Jupyter 用 `SUPERDL_JUPYTER_URL_PORT` 让到非 443。后缀相同时,把关的只剩
    `svc-` 前缀那一条(`endpoint_slug_from_host`),它因此不是可选的装饰。
- 泛域名解析到网关入口(只需 80/443,不像 Jupyter 后缀那样还要转发 NodePort 段);证书由 listener 的
  `certificateRefs` 引用 —— 默认形态是 `deploy/app/k8s/05-cert-manager.yaml` 里签发的泛域名证书,
  按端口分的形态则是两个 listener 共用同一张一级通配证书。

## 契约

| 端点 | 角色/鉴权 | 说明 |
|---|---|---|
| `GET /api/v1/instances/{uuid}/service` | user | 端点信息(URL / 容器端口 / 健康检查 / 是否需 Key);非服务型实例 404 |
| `GET /api/v1/instances/{uuid}/api-keys` | user | 列表,只回 `key_prefix`,不回明文 |
| `POST /api/v1/instances/{uuid}/api-keys` | user | 新建;**明文只在本次响应里出现一次**,与管理端恢复码同款一次性语义 |
| `DELETE /api/v1/instances/{uuid}/api-keys/{id}` | user | 吊销:写 `revoked_at`,不删行 |
| `/api/internal/v1/endpoint-auth/...` | 无(集群内) | 网关 `SecurityPolicy.extAuth` 的回调,**不对公网开放** |

## 鉴权链路

```
客户端 ──HTTPS──▶ Envoy(svc-https listener)
                    │  SecurityPolicy.extAuth:每请求一次回源
                    ├──▶ superdl-api /api/internal/v1/endpoint-auth/...
                    │      Host 头取 slug → key_hash 查 service_api_keys
                    │      未吊销 ∧ 归属该 slug 的实例 ∧ 实例 running → 200,否则 401
                    └──200──▶ <uuid>-svc ClusterIP ──▶ 用户容器
```

- slug **从 `Host` 头取,不从 path 取**:`extAuth.http.path` 是策略里的静态前缀,没有模板变量,
  编不进 slug。
- `require_api_key=false` 的端点是公开端点,**不挂 extAuth**,因此不依赖控制面。
- 回源不加进程内缓存:`key_hash` 是唯一索引上的单行查,再叠一层 LRU 只会让吊销延迟变长而查不出问题。
  `last_used_at` 在回源时直写,不节流 —— 网关侧本来就无缓存,回源量等于业务 QPS,直写的额外成本是同一行的
  一次 UPDATE,而节流会让「最近使用」失去排查价值。

## 规则与不变量

- **鉴权结果没有任何缓存,这是选 extAuth 的硬代价。** Envoy 的 ext_authz 过滤器本身不具备结果缓存
  (`SecurityPolicy` 里唯一的 `cacheDuration` 属于 `jwt.remoteJWKS`,与鉴权结果无关),
  ingress-nginx 时代的 `auth-cache-duration` **没有等价物**。后果:**控制面是全部需要 Key 的端点的同步依赖,
  挂了就是全部 503**。选它的理由是另外两个方案(`apiKeyAuth` / `jwt`)在 O(1) 对象数下**表达不了
  「这把 Key 只能访问它自己那个端点」**,而这是多租户的本质要求;取舍记录见 [decisions.md](../decisions.md)。
- **`statusOnError` 必须显式配 503。** 默认是 403 —— 会把「控制面挂了」伪装成「凭证不对」,客户端与监控都判错。
- **`failOpen` 保持默认 false。** 它不只在鉴权服务不可达时放行,**策略配置非法时也整个绕过鉴权**。
- **`timeout` 必须显式收紧到 500ms~1s。** 默认 10s,在每个请求的同步路径上太长,客户端会以为在正常处理。
- **`headersToBackend` 是覆盖语义**:列进白名单的头一定来自鉴权服务而非客户端伪造,**没列进去的同名头会原样
  透传客户端的值**。凡是租户容器要信任的头都必须列进去。
- **`mergeType` 默认不叠加**:将来给某条租户路由再挂任何 `SecurityPolicy`(哪怕只为配 CORS),会**整个替换掉**
  listener 级的 extAuth,**鉴权静默消失**。要叠加必须显式 `mergeType: StrategicMerge`。
- **拒绝时的响应体与响应头原样透传给客户端**(Envoy 默认不截断,EG 未暴露该开关)。好处是可以直接复用
  `core/errors.py` 的统一错误体;**风险是该端点的 4xx 直达公网,绝不能带栈、内网主机名或 `Set-Cookie`**。
- **`/api/internal` 的边缘收口是回调端点唯一的保护。** 它本身不能带鉴权(带了就成鸡生蛋),而平台 API 的
  HTTPRoute 是 `path: /` 前缀匹配,不收口它就跟着暴露在公网上。prod 下带 `X-Forwarded-For` 一律 404
  (`core/edge_guard.py`,判据与 `/metrics` 同款 —— 集群内直连 Service 不带该头)。
- **API Key 明文只出现一次。** 库里只有 HMAC-SHA256 摘要(`crypto.hash_api_key`,主密钥只走 env)。
  遗失只能吊销后新建;吊销写 `revoked_at` 不删行,审计要看得见谁在何时吊销了哪把钥匙。
- **Key 的归属是端点级,不是账号级。** 校验链必须完整走「未吊销 → 归属该 slug 的实例 → 实例 running」三步;
  少任一步,A 用户的 Key 就能调 B 用户的端点。用例在 `tests/test_endpoint_auth.py`。
- **限流是每端点独立配额**:一条挂 `svc-https` listener 的 `BackendTrafficPolicy`(local),桶按路由分。
  限额由策略 `service_endpoint_rps`(默认 20)决定,但它**渲染进清单**,改策略值不会自动同步到网关 ——
  必须重新下发 `deploy/app/k8s/04-gateway.yaml`。与其它策略键的行为不同,见 [limits.md](./limits.md)。
- **local 限流是每个 Envoy 实例本地计数**,多副本时全局实际上限约为配置值 × 副本数(与平台 API 域同一口径,
  见 [security.md](./security.md))。
- 服务型实例默认 `with_ssh=false`,**不进 SSH 端口池**(30000–32767 是全平台硬上限);
  需要调试时可在创建时勾选,那时才占一个名额。
- 服务型实例**持续 not-ready 不判故障**(reconciler 豁免 `pod_unready` 一支),就绪与否如实呈现在服务 Tab;
  理由见 [decisions.md](../decisions.md)。配套的 `startupProbe` 给 15 分钟启动预算,否则加载大模型权重的容器
  从第一秒起就 not-ready。
- 用户 env 整包 AES-GCM 落库(`instances.env_encrypted`,AAD 绑实例 uuid);标为密文的项经 per-instance Secret
  以 `secretKeyRef` 注入,**明文不进 Pod spec**(spec 会进 etcd 与审计快照,任何 `pods:get` 身份都能读走)。
- 用户 env 键名黑名单:拒 `JUPYTER_` / `SUPERDL_` 前缀与 `AUTHORIZED_KEYS`,防覆盖平台注入项。
- 容器端口不得为 22 或 8888(sshd 与 JupyterLab),DB 侧有 CHECK 兜底。

## 实机验证清单(fake 后端测不出)

见 `deploy/cluster/runbooks/cluster-validation.md`「北向入口」节:`*.svc` 证书签发、
`SecurityPolicy` 的 `status.ancestors[].conditions` 为 `Accepted=True`、合法/非法/已吊销 Key 的真链路、
每端点限流实测、NetworkPolicy 放宽后租户间仍不可达。
