# WP25 可观测性实机验证清单

进不了 CI 的人工事项。全部通过后在本文件勾选并记录实测值。

## HAMi 指标核定(共享档实例曲线的数据源)

- [ ] `kubectl -n kube-system get svc hami-scheduler -o yaml`:确认 monitor 端口名与端口(默认 31993/monitor);
      与 `values/kps.yaml` 的 additionalScrapeConfigs 比对,如有出入改 values。
- [ ] vGPUmonitor 指标名与标签:在 Prometheus 里查
      `Device_utilization_desc_of_container` / `vGPU_device_memory_usage_in_bytes`,
      确认容器维标签为 `podnamespace`/`podname`;如有出入只改
      `apps/api/app/modules/metering/prom.py` 顶部常量与 HAMI_QUERIES。
- [ ] 开一台共享档实例跑负载,用户端详情页 GPU 利用率曲线出数且与 nvidia-smi 观测一致。

## dcgm-exporter 标签形态(节点热力格数据源)

- [ ] `DCGM_FI_DEV_GPU_UTIL` 的节点标签是否为 `Hostname`(gpu-operator 版默认);
      如为 `kubernetes_node` 等,改 prom.py 的 DCGM_NODE_LABEL。
- [ ] 管理端节点页热力格出真实 util/显存/温度;拔负载后 60s 内回落。

## kps light(k3s 单机)

- [ ] `helmfile -e light apply` 后 monitoring 命名空间全部 Pod Running;
      记录实测占用(目标:Prometheus RSS < 1Gi)。
- [ ] Prometheus 停机(scale 0)时:用户端列表「监控暂不可用」、详情 503 文案、
      管理端热力格回落两态——全站无报错(断源降级语义)。

## 告警链路

- [ ] 停 HAMi scheduler → 5 分钟内 HamiSchedulerDown 告警进管理端告警流。
