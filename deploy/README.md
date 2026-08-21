# deploy

| 目录 | 内容 |
|---|---|
| `app/` | 平台自身部署:本地 compose(PG18)+ 生产 K8s 清单(`k8s/`:API/worker/前端/Ingress-TLS/RBAC/迁移 Job/PG 备份 CronJob)+ 前端镜像(`frontend.Dockerfile`+nginx) |
| `ansible/` | 装机基线(存量机器批量):NVIDIA 驱动 / 内核参数(IOMMU、userns)/ NVMe VG / registries.yaml 分发。新节点首选管理端「添加节点」一键加入,本目录用于存量机器批量处理 |
| `cluster/` | 集群组件 helmfile(RKE2/k3s + Cilium + GPU Operator + HAMi + kube-prometheus-stack + JuiceFS CSI + TopoLVM),full/light 双档与版本锁定见 `cluster/README.md` |

实机侧事项见 development-plan §7.3 人工事项清单;平台代码不依赖真实集群 —— K8s 走 `app/core/k8s` 抽象层,dev/test 用 FakeOrchestrator。

## 生产发布流程(deploy/app/k8s)

1. `helmfile -e <full|light> apply`(cluster/:双档见 `cluster/README.md`,先 `./preflight.sh`)→ 建 `superdl-api-secrets` 等 Secret(值不入库)
2. 打 tag 触发 `.github/workflows/release.yml`:构建 api/web/admin 三镜像 + Trivy 扫描 + 推 ghcr
3. `kubectl create -f k8s/10-migrate-job.yaml`(把 `CHANGE_TAG` 占位符替换为本次 tag,name 与镜像各一处)→ `kubectl wait --for=condition=complete`
4. 更新三个 Deployment 镜像 tag 滚动发布(清单里同样是 `CHANGE_TAG` 占位符,未替换直接 apply 会拉不到镜像而不是静默跑旧版);`/readyz` 就绪即接流量
5. 回滚:Deployment 回退上一 tag(迁移只增不删,向后兼容窗口内可直接回滚)
6. 备份:`06-pg-backup.yaml` 每日逻辑备份;恢复演练见 `cluster/runbooks/pg-backup-restore.md`
