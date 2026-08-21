# WP25 · 可观测性收口(管理端自绘 + 轻量档监控)

决策:
1. 不做 Grafana iframe,管理端自绘:iframe 需 allow_embedding/CSP/cookie/反代注入身份等脆链路,且与 NOC 深色主题割裂、无业务下钻;自绘复用 metering 白名单查询机制。`grafana_url` 仅作「在 Grafana 打开」外链(observability 配置组,可空)。
2. 不引 VictoriaMetrics:kube-prometheus-stack 已全接线(告警规则/Alertmanager 双通道/ServiceMonitor/Secret),轻量诉求用同一 chart 的 light values(helmfile environments 见 WP27)。
3. 按 tier 选指标源:HAMi 共享卡上 DCGM `{pod=}` 归属不可靠;shared 档实例指标用 HAMi vGPUmonitor per-container 指标(scheduler 默认开启),查空回落 DCGM;dedicated/mig 用 DCGM。指标名/端口为实机验证项,标签常量集中 `prom.py` 文件头。

## 目标

- 管理端节点页:每卡真实热力格(util% / 显存 / 温度染色,XID>0 红点)+ 节点详情 ECharts 曲线(per-GPU util/显存/温度,1h/6h/24h);`grafana_url` 有值显示外链按钮,无值一行提示;断源降级为「已租/空闲」形态不报错。
- metering:`prom.py` 含 `NODE_QUERIES`(DCGM Hostname 维度 per-GPU 多序列)与 `HAMI_QUERIES`(vGPUmonitor per-container);`query_range_multi` 多序列返回;`instance_metrics` 与 `aggregate_previous_hour` 按实例 spec.tier 选源;`usage_hourly.cpu_avg_pct` 由聚合写入。
- `prometheus_url` prod fail-fast(含 localhost 拒启);运营总览告警流条目关联节点/实例内部链接。
- 抓取:kps values 含 HAMi additionalScrapeConfigs(scheduler + vGPUmonitor)+ `absent(up{job="hami-scheduler"})` warning 告警。

## 契约

| 端点 | 角色 | 说明 |
|---|---|---|
| `GET /api/admin/v1/nodes/{node_name}/metrics?range=1h\|6h\|24h` | ops/readonly | `{available, gpus:[{index, util:[[ts,v]], mem_used_mb:[...], temp:[...]}], xid_count_24h}`;断源 `available=false`(200,对齐 sparkline 批量端点降级语义) |
| platform-config `observability` 组 | admin | 键 `grafana_url`(str,`https?://` pattern,可空) |

用户端契约:`GET /instances/{id}/metrics` 形状不变,仅 shared 档数据源不同。

## 数据变更

无新表;`usage_hourly.cpu_avg_pct` 由聚合写入(无迁移);platform_settings 新组键为数据行。

## 部署变更

- `deploy/cluster/values/kps.yaml`:additionalScrapeConfigs 两个 job(hami-scheduler / vGPUmonitor,端口实机核定)+ 1 条失联告警。
- light 档 kps 精简 values(grafana off / retention 3d / 资源收紧),由 helmfile environments 选用(见 WP27)。

## 验收用例

1. 节点指标端点:MockTransport 注入 per-GPU 多序列 → 响应 gpus 数组正确分卡;Prometheus 断源 → `available=false` 且 200。
2. shared 档实例:HAMi 模板命中时用其数据;HAMi 查空回落 DCGM;dedicated 恒 DCGM(用例断言查询串)。
3. 聚合:cpu_avg_pct 有值;重复执行幂等不变(ON CONFLICT 语义)。
4. prod 启动:`prometheus_url` 含 localhost → fail-fast 报错文案指明键名。
5. 管理端:grafana_url 未配 → 仅一行「可在平台配置接入 Grafana 外链」;配了 → 外链按钮。

## 实机验证项(人工事项)

HAMi vGPUmonitor 指标名/端口(9394/31993)与标签集(podnamespace/podname 形态);dcgm-exporter 的 Hostname 标签形态(gpu-operator 版 vs 独立 chart 版);kps light 在单机的真实资源占用;light 档装出监控后热力格出真实数据。
