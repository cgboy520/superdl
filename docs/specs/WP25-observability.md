# WP25 · 可观测性收口(管理端自绘 + 轻量档监控)

方向决策经人工确认:
1. **弃 Grafana iframe,管理端自绘**(修订 development-plan:187 / ui-ux-spec:178):iframe 需 allow_embedding + CSP frame-ancestors + cookie_samesite=none + 反代注入身份,链路脆、与 NOC 深色主题割裂、无业务下钻;现占位卡无条件硬编码且 URL 无配置通道(WP21:40 遗留)。自绘复用 metering 白名单查询机制,增量小;`grafana_url` 仅做「在 Grafana 打开」**外链**(observability 新配置组,可空)。
2. **不引 VictoriaMetrics**:kps 88.0 已全接线(14 条告警规则/Alertmanager 双通道/ServiceMonitor/Secret),换栈纯折腾;k3s 轻量诉求用**同一 chart 的 light values** 解决(见 WP27 helmfile environments)。
3. **按 tier 选指标源**:HAMi 共享卡上 DCGM{pod=} 归属不可靠;shared 档实例指标改 **HAMi vGPUmonitor per-container 指标**(已核实默认开启,scheduler :31993),查空回落 DCGM;dedicated/mig 保持 DCGM。指标名/端口为实机验证项,标签常量集中 prom.py 文件头。

## 目标

- **管理端节点页自绘**:每卡视图从「已租/空闲」两态升级为真实热力格(util% / 显存 / 温度染色,XID>0 红点——告警数据已入库);节点详情区 ECharts 曲线(per-GPU util/显存/温度,1h/6h/24h);Grafana 占位卡删除 → `grafana_url` 有值时显示外链按钮,无值一行提示;断源降级回「已租/空闲」形态不报错。
- **metering 扩展**:prom.py 增 `NODE_QUERIES`(DCGM Hostname 维度 per-GPU 多序列)与 `HAMI_QUERIES`(vGPUmonitor per-container);新增 `query_range_multi`(多序列返回);`instance_metrics` 与 `aggregate_previous_hour` 按实例 spec.tier 选源;**修 `usage_hourly.cpu_avg_pct` 死列**(查询模板既有,聚合漏写)。
- **修洞**:`prometheus_url` 补 prod fail-fast(含 localhost 即拒启,对齐 database_url 先例);运营总览告警流条目关联节点/实例内部链接。
- **抓取补全**:kps values 增 HAMi additionalScrapeConfigs(scheduler + vGPUmonitor)+ `absent(up{job="hami-scheduler"})` warning 告警。

## 契约

| 端点 | 角色 | 说明 |
|---|---|---|
| `GET /api/admin/v1/nodes/{node_name}/metrics?range=1h\|6h\|24h` | ops/readonly | `{available, gpus:[{index, util:[[ts,v]], mem_used_mb:[...], temp:[...]}], xid_count_24h}`;断源 `available=false`(200,对齐 sparkline 批量端点降级语义);节点存在性校验先用实时节点列表,WP26 台账落地后一行切换 |
| platform-config 新组 `observability` | admin | 键 `grafana_url`(str,`https?://` pattern,可空);SettingGroup Literal 扩 → openapi/api-client 再生成 |

用户端契约不变(`GET /instances/{id}/metrics` 形状不变,仅 shared 档数据源切换)。

## 数据变更

无新表。`usage_hourly.cpu_avg_pct` 从死列变为有值(行为变更,无迁移);platform_settings 新组键为数据行。

## 部署变更

- `deploy/cluster/values/kps.yaml`:additionalScrapeConfigs 两个 job(hami-scheduler / hami-device-plugin vGPUmonitor,端口实机核定)+ 1 条失联告警。
- light 档 kps 精简 values(grafana off / retention 3d / 资源收紧)随 WP27 helmfile environments 落地;本 WP 先交付 values 文件本体。

## 分批(8 批)

| 批 | 内容 |
|---|---|
| 1 | prom.py NODE_QUERIES/HAMI_QUERIES + query_range_multi + 标签常量收口 |
| 2 | cpu_avg_pct 修复 + 聚合按 tier 选源 + MockTransport 用例 |
| 3 | 节点指标端点 + 降级语义 + 契约再生成 |
| 4 | observability 配置组(grafana_url)+ platform.tsx Tab + 契约 |
| 5 | 管理端每卡热力格自绘 + 节点曲线 + 占位卡替换为外链 |
| 6 | kps HAMi scrape + 失联告警 + kps-light values 文件 |
| 7 | 用户端共享档指标源切换(instance_metrics 传 tier,HAMi 优先回落 DCGM) |
| 8 | 实机验证 runbook + 集群 README 监控节收尾 |

## 验收用例

1. 节点指标端点:MockTransport 注入 per-GPU 多序列 → 响应 gpus 数组正确分卡;Prometheus 断源 → `available=false` 且 200(仿 test_metering 断源用例)。
2. shared 档实例:HAMi 模板命中时用其数据;HAMi 查空回落 DCGM;dedicated 恒 DCGM(用例断言查询串)。
3. 聚合:cpu_avg_pct 有值;重复执行幂等不变(既有 ON CONFLICT 语义)。
4. prod 启动:`prometheus_url` 含 localhost → fail-fast 报错文案指明键名。
5. 管理端:grafana_url 未配 → 无 iframe 无占位说明,仅一行「可在平台配置接入 Grafana 外链」;配了 → 外链按钮。
6. 实机(人工):k3s light 档装出监控后热力格出真实数据;HAMi 指标名/端口核对回填 prom.py 常量。

## 实机验证项(人工事项)

HAMi vGPUmonitor 指标名/端口(9394/31993)与标签集(podnamespace/podname 形态);dcgm-exporter 的 Hostname 标签形态(gpu-operator 版 vs 独立 chart 版);kps light 在单机的真实资源占用。
