# deploy

| 目录 | 内容 |
|---|---|
| `app/` | 平台自身部署:本地 compose(PG18)+ 生产 K8s 清单(`k8s/`:API/worker/前端/Ingress-TLS/RBAC/迁移 Job/PG 备份 CronJob)+ 前端镜像(`frontend.Dockerfile`+nginx) |
| `ansible/` | 装机基线(存量机器批量):NVIDIA 驱动 / 内核参数(IOMMU、userns)/ NVMe VG / registries.yaml 分发。新节点首选管理端「添加节点」一键加入,本目录用于存量机器批量处理 |
| `cluster/` | 集群组件 helmfile(RKE2/k3s + Cilium + GPU Operator + HAMi + kube-prometheus-stack + JuiceFS CSI + TopoLVM),full/light 双档与版本锁定见 `cluster/README.md`;`cluster/admission/` 为准入策略(可选独立清单) |

平台代码不依赖真实集群:K8s 走 `app/core/k8s` 抽象层,dev/test 用 FakeOrchestrator。

## 生产发布流程(deploy/app/k8s)

1. `helmfile -e <full|light> apply`(cluster/:双档见 `cluster/README.md`,先 `./preflight.sh`)→ 建 `superdl-api-secrets` 等 Secret(值不入库)
2. 打 tag 触发 `.github/workflows/release.yml`:构建 api/web/admin 三镜像 + Trivy 扫描 + 推 ghcr
3. `kubectl create -f k8s/10-migrate-job.yaml`(把 `CHANGE_TAG` 占位符替换为本次 tag,name 与镜像各一处)→ `kubectl wait --for=condition=complete`
4. 更新三个 Deployment 镜像 tag 滚动发布(清单里同样是 `CHANGE_TAG` 占位符,未替换直接 apply 会拉不到镜像而不是静默跑旧版);`/readyz` 就绪即接流量
5. 回滚:Deployment 回退上一 tag(迁移只增不删,向后兼容窗口内可直接回滚)
6. 备份:`06-pg-backup.yaml` 每日逻辑备份;恢复演练见 `cluster/runbooks/pg-backup-restore.md`

## 生产数据库要求(必读)

**本仓库 `app/k8s/` 全部 10 个清单 deliberately 不含任何 PostgreSQL 对象**——平台库
(钱包/账本)不允许以「清单里顺手起一个容器」的方式进生产。生产数据库二选一:

1. **托管 PG**(云 RDS/裸金属自建主备):PG ≥ 18(与 dev/test 对齐),开自动备份 +
   PITR(WAL 归档);`SUPERDL_DATABASE_URL` 经 `secrets.example.yaml` 注入。
2. **CloudNativePG 集群**(进 K8s 时唯一受支持形态):3 实例 + `backup` 到对象存储
   (持续 WAL 归档,RPO 分钟级)+ 定时候份校验;禁止单实例 cnpg 上生产。

两条硬性要求(与备份分层一致,见 `cluster/runbooks/pg-backup-restore.md`):

- **WAL 归档必须开**:每日 `pg_dump` 逻辑备份只有 RPO=24h,资金库不能只靠它;
- **定期 restore 校验**:每季度按 runbook 做一次恢复演练(含 ledger 链抽检),
  未演练过的备份视为不存在。

## staging overlay 建议

当前只有 full/light 双档,没有 staging 环境定义。建议(未实施,按需自建):
复制 `cluster/environments/full.yaml` 为 `staging.yaml`,values 叠加层把副本数
(ingress-nginx/api 等)降到 1、域名换 `*.staging.example.com`、关 SMTP 第二通道;
`app/k8s/` 侧用 kustomize overlay 或独立 `secrets` + ConfigMap 注入
`SUPERDL_ENVIRONMENT=staging`。staging 的 PG 同样适用上节的备份要求(演练靶场)。

## 管理端访问边界

`admin.superdl.example.com` 默认公网可达(仅 TLS + 管理端 JWT)。生产建议至少叠加一层:
VPN / 身份感知代理(oauth2-proxy 等)/ 源 IP 白名单——`app/k8s/04-ingress.yaml` 的
`superdl-admin` Ingress 已留好**可选**白名单注解(默认注释,不锁死),填办公网出口 CIDR
即可启用;Grafana 等其他管理面同理(只走内网或 port-forward,勿经 Ingress 暴露)。
