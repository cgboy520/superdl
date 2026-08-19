# deploy

| 目录 | 内容 | 状态 |
|---|---|---|
| `app/` | 平台自身部署:本地 docker compose(PG18)+ K8s 清单(后续) | compose 可用 |
| `ansible/` | 装机基线:NVIDIA 驱动 / containerd / 内核参数(IOMMU、userns)/ 镜像预热 | 人工事项 #1 配套,随 W1 产出 |
| `cluster/` | RKE2 v1.36 + Cilium 1.20 + GPU Operator v26.3 + HAMi v2.9 + kube-prometheus-stack 88.x + JuiceFS CSI 1.4 + TopoLVM(helmfile) | 人工事项 #2~#5 配套,随 W1~W2 产出 |

集群侧脚本按人工事项清单(development-plan §7.3)节奏产出;
平台代码(WP0~WP12)不依赖真实集群 —— K8s 走 `app/core/k8s` 抽象层,dev/test 用 FakeOrchestrator。
