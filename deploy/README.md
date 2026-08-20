# deploy

| 目录 | 内容 | 状态 |
|---|---|---|
| `app/` | 平台自身部署:本地 compose(PG18)+ 生产 K8s 清单(`k8s/`:API/worker/前端/Ingress-TLS/RBAC/迁移 Job/PG 备份 CronJob)+ 前端镜像(`frontend.Dockerfile`+nginx) | compose 可用;K8s 清单齐备待实机 |
| `ansible/` | 装机基线(存量机器批量):NVIDIA 驱动 / 内核参数(IOMMU、userns)/ NVMe VG / registries.yaml 分发。新节点首选管理端「添加节点」一键加入,本目录用于存量机器批量处理 | 人工事项 #1 配套 |
| `cluster/` | RKE2 v1.36 + Cilium 1.20 + GPU Operator v26.3 + HAMi v2.9 + kube-prometheus-stack 88.x + JuiceFS CSI 1.4 + TopoLVM(helmfile) | 人工事项 #2~#5 配套 |

集群侧脚本按人工事项清单(development-plan §7.3)节奏产出;
平台代码不依赖真实集群 —— K8s 走 `app/core/k8s` 抽象层,dev/test 用 FakeOrchestrator。


## 生产发布流程(deploy/app/k8s)

1. `helmfile apply`(cluster/:含 cert-manager 与 ingress-nginx)→ 建 `superdl-api-secrets` 等 Secret(值不入库)
2. 打 tag 触发 `.github/workflows/release.yml`:构建 api/web/admin 三镜像 + Trivy 扫描 + 推 ghcr
3. `kubectl create -f k8s/10-migrate-job.yaml`(镜像与 name 替换为本次 tag)→ `kubectl wait --for=condition=complete`
4. 更新三个 Deployment 镜像 tag 滚动发布;`/readyz` 就绪即接流量
5. 回滚:Deployment 回退上一 tag(迁移只增不删,向后兼容窗口内可直接回滚)
6. 备份:`06-pg-backup.yaml` 每日逻辑备份;恢复演练见 `cluster/runbooks/pg-backup-restore.md`
