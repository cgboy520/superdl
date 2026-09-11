# deploy

| 目录 | 内容 |
|---|---|
| `app/` | 平台自身部署:本地 compose(PG18)+ 生产 K8s 清单(`k8s/`:API/worker/前端/网关与 TLS/RBAC/迁移 Job/PG 备份 CronJob)+ 前端镜像(`frontend.Dockerfile`+nginx) |
| `ansible/` | 初始控制面装机([servers] 组 rke2/k3s server:审计策略、server config 渲染、安装器 sha256 校验后安装)。GPU 节点走管理端「添加节点」一键命令(node-join.sh),不走 ansible |
| `cluster/` | 集群组件 helmfile(RKE2/k3s + Cilium + GPU Operator + HAMi + kube-prometheus-stack + JuiceFS CSI + TopoLVM + Envoy Gateway + Loki/Alloy),full/light 双档与版本锁定见 `cluster/README.md`;Gateway API CRD 由 `cluster/gateway-api-crds.sh` 单点管(helmfile presync 调用);`cluster/admission/` 为七条 VAP 准入策略(非 helm release,`cluster/apply.sh` 在 helmfile 之前 apply 并回读,`cluster/preflight.sh` 与 `scripts/release.sh` 各再断言一次全部为 Deny) |

平台代码不依赖真实集群:K8s 走 `app/core/k8s` 抽象层,dev/test 用 FakeOrchestrator。

## 生产发布流程(deploy/app/k8s)

唯一入口 `SUPERDL_IMAGE_PREFIX=harbor.<域>/superdl scripts/release.sh <tag>`,禁止绕过脚本手改清单 tag:
准入策略断言 → 迁移 Job → kustomize 渲染后替换占位 `CHANGE_IMAGE_PREFIX`、平台镜像钉**不可变 digest** 再 apply → rollout status → 经网关从集群外 GET `/readyz`;任一步失败即非零退出。

1. `helmfile -e <full|light> apply`(cluster/,先 `./preflight.sh`;双档见 `cluster/README.md`)→ 按 `app/secrets.example.yaml` 建分域 Secret(`superdl-db`(应用角色)/`superdl-db-migrate`(库 owner,仅迁移 Job)/`superdl-auth`/`superdl-crypto`/`superdl-metrics`/`superdl-edge`/`superdl-cloud`/`superdl-payment`/`superdl-registry`/`superdl-pg-backup`)与 `superdl-registry-pull`(Harbor 拉取机器人;项目 public 可省);库用自签/私有 CA 时另建 ConfigMap `superdl-db-ca`(key `ca.crt`,各 Deployment 以 optional 卷挂到 `/etc/superdl/db-ca`)。字段清单见 `app/k8s/00-namespace-config.yaml`(非密)与 `app/secrets.example.yaml`(密),prod 必配项以 `docs/reference/security.md` 的 `_validate_prod` 清单为准
2. 打 tag:`gh release create vX.Y.Z --generate-notes`(不维护 CHANGELOG)。tag 触发 `.github/workflows/release.yml`:CI 闸门复跑 → 构建 api/web/admin 三镜像 + Trivy 扫描 + 推 Harbor(仓库 secrets `HARBOR_HOST` / `HARBOR_ROBOT_NAME` / `HARBOR_ROBOT_SECRET`,variables `HARBOR_PROJECT` 缺省 superdl)。api 镜像三环境同一产物;mock 支付回调路由仅在非 prod 注册
3. `SUPERDL_IMAGE_PREFIX=harbor.<域>/superdl scripts/release.sh vX.Y.Z`(前置工具:`kubectl` + `crane` / `skopeo` / `docker buildx` 三选一,需对 Harbor 有读权限且已 `docker login`;三个都没有即拒绝发布):
   - 第 0 步断言七条准入策略的 Policy 与 Binding 都在且 `validationActions` 含 Deny;
   - 第 1 步检查 `superdl-registry-pull`(仅告警不阻断);
   - 第 2 步建迁移 Job(`k8s/10-migrate-job.yaml`,单独 create)并 `wait complete`,先于滚动;`/readyz` 比对 DB `alembic_version` 与代码 head,不一致 503 `schema_mismatch`,从未迁移 503 `never_migrated`;
   - 第 3 步 `kubectl kustomize` 渲染后 apply:tag 解析成 digest,三条平台镜像整串换成 `<前缀>/superdl-<name>@sha256:...`;渲染后自检 `CHANGE_*` 占位零残留 + 清单里**全部**镜像(含 postgres / aws-cli 等第三方)一律带 `@sha256:`,任一不满足即拒绝下发(`CHANGE_TAG` 保留给迁移 Job 的 Job 名);
   - 第 4 步等全部 Deployment(api + 5 个 worker 组件 + web/admin)滚动完成(readinessProbe 即 `/readyz`);
   - 第 5 步经网关从集群外 `curl -fsS https://<api-domain>/readyz`:域名取环境变量 `SUPERDL_API_BASE_URL`,缺省读 ConfigMap `superdl-api-config` 的 `SUPERDL_PUBLIC_BASE_URL`,取不到或仍是占位则跳过并提示。失败先查迁移 Job 与网关链路,修复后重新发布,不回滚。
4. 首个管理员(库迁移后、仅首发一次):`cd apps/api && uv run python scripts/bootstrap_admin.py`(`seed_dev.py` 只允许 dev/test),口令只打印一次,首次登录强制绑定 TOTP
5. 备份:`06-pg-backup.yaml` 每日逻辑备份;恢复演练见 `cluster/runbooks/pg-backup-restore.md`

### 发布与迁移约定

- **停机发布**:迁移与代码同 tag,顺序恒为「先 `alembic upgrade head`,后替换代码」;`/readyz` 只认 DB == 代码 head,迁移完成到滚动完成之间旧 Pod 503 摘流。
- **不支持发布回滚**:fix-forward;基线迁移 downgrade 一律 raise。
- 迁移无需向前兼容,破坏性 DDL 允许(提交说明写明数据影响);迁移 Job 带 `PGOPTIONS`(`lock_timeout=3s` / `statement_timeout=60s`),超预算的大表改动放维护窗口手工执行。

上线硬性核查项(每次首发/变更发布通道后必过):

- [ ] `curl -s https://<api-domain>/api/v1/webhooks/mock -X POST` 返回 404
- [ ] `curl -s https://<api-domain>/api/admin/v1/auth/login -X POST` 返回 404
- [ ] `curl -s https://<api-domain>/metrics` 返回 404 或 401
- [ ] Alertmanager critical 告警端到端实测一次(管理端告警流与值班邮箱到人;启用了钉钉 sidecar 或 `oncall_phone` 的一并验证)
- [ ] `superdl-db` 含 `juicefs-metaurl` 键(值同 kube-system/superdl-juicefs-secret 的 metaurl);缺失则数据盘配额 Job 死信(`superdl_juicefs_quota_failed_total`)

## 生产数据库要求(必读)

`app/k8s/` 的清单不含任何 PostgreSQL 对象。生产数据库二选一:

1. **托管 PG**(云 RDS/裸金属自建主备):PG ≥ 18,开自动备份 + PITR(WAL 归档);`SUPERDL_DATABASE_URL` 经 `secrets.example.yaml` 注入。
2. **CloudNativePG 集群**(进 K8s 时唯一受支持形态):3 实例 + `backup` 到对象存储(持续 WAL 归档)+ 定时备份校验;禁止单实例 cnpg 上生产。

硬性要求(备份分层见 `cluster/runbooks/pg-backup-restore.md`):

- **WAL 归档必须开**;每日 `pg_dump` 只有 RPO=24h。
- **每季度按 runbook 做一次恢复演练**(含 ledger 链抽检)。

连接数对齐(改副本数或 `SUPERDL_DB_POOL_SIZE` 时复核):

```
max_connections ≥ 进程数 × (db_pool_size + max_overflow) + 迁移/运维预留
              = (api 2 + worker 2+2+1+1+1) × (10 + 10) + 20 = 200
```

- SQLAlchemy 异步引擎默认 `max_overflow=10`:每进程峰值是 pool_size **+10**;
- 五个 worker Deployment 各自建池,副本数按 `app/k8s/03-worker.yaml` 计入;
- PG 默认 `max_connections=100` 不够,生产按上式取值并留余量;
- api/worker 进程内带 `statement_timeout=30s / lock_timeout=5s / idle_in_transaction_session_timeout=60s`。

## 前端可用性与 HPA 结论

web/admin 前端:各 2 副本 + PDB `minAvailable: 1` + liveness/readiness 同探 `/`(见 `app/k8s/07-frontends.yaml`)。
**不配置 HPA**:双端 `requests == limits`(Guaranteed QoS)。引入 SSR/BFF 再重估。

## 独立环境副本(预发/演示)

仓库只定义 full/light 双档,不含预发 overlay。自建:复制 `cluster/environments/full.yaml` 改名,叠加层把副本数降到 1、换域名、关 SMTP 第二通道;`app/k8s/` 侧用 kustomize overlay 或独立 secrets + ConfigMap。
`SUPERDL_ENVIRONMENT` 只接受 `dev` / `test` / `prod`,预发**仍以 `prod` 运行**,只是 secrets/ConfigMap/域名独立;其 PG 同样适用上节备份要求。

## 管理端访问边界

- 管理端 API 在公网 api 域下不可达(prod 下 Host 非 admin 域一律 404,恒开、无开关,见 `docs/reference/security.md`);`admin.superdl.example.com` 本身仅 TLS + 管理端 JWT + TOTP(全角色强制)。
- 生产必须再叠加网络边界:`app/k8s/04-gateway.yaml` 的 `SecurityPolicy superdl-admin-allowlist`(挂在 `superdl-admin` HTTPRoute 上)**默认启用**源 IP 白名单:`authorization.defaultAction: Deny` 加一条 `action: Allow` 的 `principal.clientCIDRs`,填办公网/跳板机出口 CIDR(多个多写几条)。VPN 或身份感知代理(oauth2-proxy 等)可替代。
- 占位符是 `192.0.2.0/24`(`clientCIDRs` 有 CRD 正则校验);`preflight.sh` 按这个网段扫描,未替换不予放行。
- `defaultAction: Deny` 不可漏写。
- 源 IP 真实性依赖 `EnvoyProxy` 的 `envoyService.externalTrafficPolicy: Local`,不可改 `Cluster`。
- 应急通道:白名单误伤时用 `kubectl port-forward`,勿放开 0.0.0.0/0。
- Grafana 等其他管理面只走内网或 port-forward,勿经网关暴露。
