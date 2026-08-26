# deploy

| 目录 | 内容 |
|---|---|
| `app/` | 平台自身部署:本地 compose(PG18)+ 生产 K8s 清单(`k8s/`:API/worker/前端/Ingress-TLS/RBAC/迁移 Job/PG 备份 CronJob)+ 前端镜像(`frontend.Dockerfile`+nginx) |
| `ansible/` | 初始控制面装机([servers] 组 rke2/k3s server 安装:审计策略、server config 渲染、安装器 sha256 校验后安装)。GPU 节点一律走管理端「添加节点」一键命令(node-join.sh),不走 ansible |
| `cluster/` | 集群组件 helmfile(RKE2/k3s + Cilium + GPU Operator + HAMi + kube-prometheus-stack + JuiceFS CSI + TopoLVM + Loki/Alloy 日志栈),full/light 双档与版本锁定见 `cluster/README.md`;`cluster/admission/` 为准入策略(非 helm release,发布流程内单独 `kubectl apply`,preflight 强制校验 Deny 生效) |

平台代码不依赖真实集群:K8s 走 `app/core/k8s` 抽象层,dev/test 用 FakeOrchestrator。

## 生产发布流程(deploy/app/k8s)

发布走 `SUPERDL_IMAGE_PREFIX=harbor.<域>/superdl scripts/release.sh <tag>` 一个入口:迁移 Job → set image+apply
(kustomize 渲染后替换清单占位 `CHANGE_IMAGE_PREFIX`(Harbor 项目前缀)与 `CHANGE_TAG`)→ rollout status →
经 Ingress 从集群外 GET `/readyz`,任一步失败即非零退出(第 4 步取不到域名时跳过并提示)。禁止绕过脚本手改各清单 tag。

1. `helmfile -e <full|light> apply`(cluster/:双档见 `cluster/README.md`,先 `./preflight.sh`)→ 建 `superdl-api-secrets` 与 `superdl-registry-pull`(Harbor 拉取机器人;项目 public 可省)等 Secret(值不入库;字段清单 `app/k8s/00-namespace-config.yaml` 非密 + `app/secrets.example.yaml` 密,prod 必配项以 `docs/reference/security.md` 的 `_validate_prod` 清单为准)
2. 打 tag:`gh release create vX.Y.Z --generate-notes`(一步建 tag 与 GitHub Release,release notes 由提交信息自动生成,不维护 CHANGELOG 文件)。tag 触发 `.github/workflows/release.yml`:CI 闸门(api/frontend/security 复跑)→ 构建 api/web/admin 三镜像 + Trivy 扫描 + 推 Harbor(仓库 secrets `HARBOR_HOST` / `HARBOR_ROBOT_NAME`(push 机器人)/ `HARBOR_ROBOT_SECRET`,variables `HARBOR_PROJECT` 缺省 superdl;api 镜像三环境同一产物;mock 支付回调路由仅在非 prod 注册)
3. `SUPERDL_IMAGE_PREFIX=harbor.<域>/superdl scripts/release.sh vX.Y.Z`:
   - 第 1 步建迁移 Job(`k8s/10-migrate-job.yaml`,Job 不可 apply 复用故单独 create)并 `wait complete`——**必须先于滚动**;`/readyz` 会比对 DB `alembic_version` 与代码 head,迁移未跑(503 `schema_mismatch`)或库从未迁移(503 `never_migrated`)时新 Pod 不接流量,漏跑/乱序都会在这一关现形;
   - 第 2 步 `kubectl kustomize` 渲染后把 `CHANGE_IMAGE_PREFIX` / `CHANGE_TAG` 换成 Harbor 项目前缀与本次 tag 再 apply;
   - 第 3 步等全部 Deployment(api + 5 个 worker 组件 + web/admin)滚动完成(readinessProbe 即 `/readyz`,Pod 内不再重复探测);
   - 第 4 步经 Ingress 从集群外 `curl -fsS https://<api-domain>/readyz`,多验 DNS/TLS/Ingress 一层:域名取环境变量 `SUPERDL_API_BASE_URL`,缺省读 ConfigMap `superdl-api-config` 的 `SUPERDL_PUBLIC_BASE_URL`,取不到或仍是占位则跳过并提示。任一步失败即退、按下方回滚指引处理。
4. 首个管理员(库迁移后、仅首发一次):`cd apps/api && uv run python scripts/bootstrap_admin.py`(prod 可跑;`seed_dev.py` 只允许 dev/test),口令只打印一次,首次登录强制绑定 TOTP
5. 备份:`06-pg-backup.yaml` 每日逻辑备份;恢复演练见 `cluster/runbooks/pg-backup-restore.md`

### 回滚指引

- **应用回滚**(向后兼容窗口内,迁移只增不删,无需回滚库):
  `kubectl -n superdl rollout undo deploy/superdl-api deploy/superdl-worker deploy/superdl-worker-tenant-mgr deploy/superdl-worker-node-mgr deploy/superdl-worker-prewarm deploy/superdl-worker-disk-ops deploy/superdl-web deploy/superdl-admin`
  (worker 组件集群共 5 个 Deployment,回滚必须成组;或 `scripts/release.sh <上一 tag>` 重放一遍——迁移 Job 对已追平的库是 no-op)。
- **不得回滚的情形**:本次发布含 contract 迁移(删列/改名/改类型,见下节——正常
  流程下这类迁移要分两个发布窗口,窗口之间禁止回滚越过边界)。回滚前
  `git log <上一 tag>..<当前 tag> -- apps/api/alembic/versions/` 确认只有 expand 类迁移。
- 回滚后核对:`kubectl -n superdl rollout status` × 8(api + 5 个 worker 组件 + web/admin) + 外部 `/readyz` 一条(同 release.sh 第 4 步)。

### 迁移向前兼容窗口(expand-only)规范

滚动窗口内必然存在「老代码 + 新 schema」与「新代码 + 旧 schema」并存,因此:

- **expand-only**:新增表/新增可空列/新增索引(CONCURRENTLY)/加约束(NOT VALID 先行)
  随时可发;迁移与代码同 tag 发布,顺序「先迁移后滚动」由 release.sh 保证。
- **contract(删列/改列名/改类型/删表)分两窗口**:窗口 A 先发「代码不再读写旧列 +
  expand 部分」;全量滚动完成、确认无回滚需求后,窗口 B 再发删除性迁移。两窗口之间
  禁止回滚越过 A 的边界。
- 闸门:CI「迁移危险 DDL 检查」(`scripts/check-migration-ddl.py`)拦 drop/rename/
  非空列无默认/非 CONCURRENTLY 索引/ALTER TYPE;确属 contract 窗口 B 的迁移,在文件内
  标注 `# ddl-risk: reviewed` 并在提交说明里写明窗口安排。
- **大表迁移替代流程(三步法)**,以「大表加非空列」为例:
  1. **加列带默认**:`add_column(..., nullable=True)`(PG ≥ 11 下 `server_default` 加列
     也是 O(1) 元数据操作,但写入语义以可空 + 代码双写最稳);
  2. **回填**:分批小步回填(按主键区间 UPDATE ... WHERE id BETWEEN,每批 commit,
     迁移 Job 有 `statement_timeout=60s` 与 `lock_timeout=3s`,单批必须远小于此);
  3. **校验后收口**:核对回填完整 → 下一窗口 `alter_column(nullable=False)` 或补
     CHECK NOT VALID → VALIDATE CONSTRAINT。
  既有表上建索引一律 `postgresql_concurrently=True`,且必须放在
  `with op.get_context().autocommit_block():` 里(`alembic/env.py` 整轮单事务,
  `CREATE INDEX CONCURRENTLY` 不能在事务块内执行,裸写会在升级时直接报错);这类迁移
  独立成文件,接受失去跨迁移原子性。本迁移内新建表上的索引不受此限(空表建索引零成本,
  门禁脚本已豁免;不在 autocommit_block 内的 CONCURRENTLY 也由门禁拦下)。锁表型 DDL(ALTER TYPE、表重写)一律拆窗口,不得在在线迁移里做。

上线硬性核查项(每次首发/变更发布通道后必过):

- [ ] `curl -s https://<api-domain>/api/v1/webhooks/mock -X POST` 返回 404(mock 回调路由仅非 prod 注册)
- [ ] `curl -s https://<api-domain>/api/admin/v1/auth/login -X POST` 返回 404(管理端 API 不经公网 api 域暴露)
- [ ] `curl -s https://<api-domain>/metrics` 返回 404 或 401(不带集群内 Bearer 不得取到指标)
- [ ] Alertmanager critical 告警端到端实测一次(钉钉 + 值班手机短信都到人)
- [ ] `superdl-api-secrets` 含 `juicefs-metaurl` 键(值同 kube-system/superdl-juicefs-secret 的 metaurl):缺失则数据盘配额 Job 永远死信(管理端死信页 + `superdl_juicefs_quota_failed_total` 可见),容量上限不被强制

## 生产数据库要求(必读)

**`app/k8s/` 的清单不含任何 PostgreSQL 对象**:平台库(钱包/账本)不以清单内起容器的
方式进生产。生产数据库二选一:

1. **托管 PG**(云 RDS/裸金属自建主备):PG ≥ 18(与 dev/test 对齐),开自动备份 +
   PITR(WAL 归档);`SUPERDL_DATABASE_URL` 经 `secrets.example.yaml` 注入。
2. **CloudNativePG 集群**(进 K8s 时唯一受支持形态):3 实例 + `backup` 到对象存储
   (持续 WAL 归档,RPO 分钟级)+ 定时备份校验;禁止单实例 cnpg 上生产。

两条硬性要求(备份分层见 `cluster/runbooks/pg-backup-restore.md`):

- **WAL 归档必须开**:每日 `pg_dump` 逻辑备份只有 RPO=24h,资金库不能只靠它;
- **定期 restore 校验**:每季度按 runbook 做一次恢复演练(含 ledger 链抽检),
  未演练过的备份视为不存在。

连接数对齐(改副本数或 `SUPERDL_DB_POOL_SIZE` 时必须复核):

```
max_connections ≥ (api 副本 + worker 副本) × (db_pool_size + max_overflow) + 迁移/运维预留
              = (2 + 2) × (10 + 10) + 20 = 100   ← 默认值恰好在 PG 默认 100 的红线上
```

- SQLAlchemy 异步引擎默认 `max_overflow=10`:每进程峰值是 pool_size **+10**,不是 pool_size;
- 建议生产 `max_connections=200` 留余量;超配症状为 `FATAL: remaining connection slots`
  伴随批量 500 与 readiness 抖动;
- api/worker 进程内已带 `statement_timeout=30s / lock_timeout=5s /
  idle_in_transaction_session_timeout=60s`,卡死语句不会无限占连接。

## staging overlay(未实施,按需自建)

仓库只定义 full/light 双档。自建 staging:复制 `cluster/environments/full.yaml` 为
`staging.yaml`,叠加层把副本数降到 1、域名换 `*.staging.example.com`、关 SMTP 第二通道;
`app/k8s/` 侧用 kustomize overlay 或独立 secrets + ConfigMap,**仍以
`SUPERDL_ENVIRONMENT=prod` 运行**(`environment` 只接受 dev/test/prod,没有 staging 值),
只是 secrets/ConfigMap/域名独立于生产;管理端边缘收口在 prod 恒开,没有也不需要单独的开关。
staging 的 PG 同样适用上节备份要求。

## 管理端访问边界

管理端 API 在公网 api 域下不可达(API 侧边缘收口:prod 下 Host 非 admin 域一律 404,恒开、无开关,见 `docs/reference/security.md`);
`admin.superdl.example.com` 本身仅 TLS + 管理端 JWT + TOTP(全角色强制)。
生产必须再叠加一层网络边界——`app/k8s/04-ingress.yaml` 的 `superdl-admin` Ingress
**默认启用**源 IP 白名单注解(`CHANGE_ME_OFFICE_CIDR/32` 占位,preflight 强制校验已替换),
填办公网/跳板机出口 CIDR;VPN 或身份感知代理(oauth2-proxy 等)可替代之。
应急通道:白名单误伤时用 `kubectl port-forward`(见 runbook),勿直接放开 0.0.0.0/0。
Grafana 等其他管理面只走内网或 port-forward,勿经 Ingress 暴露。
