# 集群部署(人工事项 #2~#5 配套 Runbook)

版本锁定(development-plan §3.3,2026-08-19 核实):
RKE2 **v1.36**(latest 通道,为 userns GA)· Cilium 1.20 · GPU Operator v26.3 ·
HAMi v2.9 · kube-prometheus-stack 88.x · JuiceFS CSI(JuiceFS 1.4.x LTS)· TopoLVM chart 17.x · Kata 4.0

## 顺序

1. **装机基线**(`../ansible/`):驱动、内核参数(IOMMU)、节点标签规划 → 人工事项 #1
2. **RKE2**:`rke2/` 下 server/agent 配置;禁用默认 CNI,装 Cilium
   ```bash
   curl -sfL https://get.rke2.io | INSTALL_RKE2_CHANNEL=latest sh -   # v1.36
   cp rke2/server-config.yaml /etc/rancher/rke2/config.yaml && systemctl enable --now rke2-server
   ```
3. **节点池标签**(分池铁律,Kata 与 HAMi 永不混布):
   ```bash
   kubectl label node <整卡节点> superdl.io/pool=kata
   kubectl label node <共享节点> superdl.io/pool=hami
   kubectl label node <MIG节点>  superdl.io/pool=mig
   ```
4. **组件**:`helmfile apply`(见 `helmfile.yaml`;需先装 helmfile+helm)
5. **Kata 4.0**:`kata/` 下 kata-deploy(仅 kata 池节点)+ RuntimeClass
6. **验证清单**:`runbooks/w1-validation.md`(含修正二的多卡直通实测)
7. **告警**:`monitoring/alert-rules.yaml`(5 条)已随 kps values 装入;
   DCGM 大盘以 grafana.com dashboard **24450** 为底导入改造(12239 已废弃)

## 回退预案

RKE2 v1.36 兼容验证不过 → 退 1.35(stable),并在 kubelet 开
`UserNamespacesSupport=true` feature-gate(beta)保留 userns 加固。
