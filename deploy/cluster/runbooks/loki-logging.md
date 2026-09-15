# 日志与审计留存(Loki)

组件:helmfile 的 `loki`(grafana-community/loki,Monolithic 单副本)+ `alloy`(grafana/alloy)。
采集面:全部命名空间的容器日志(discovery.kubernetes)+ 控制面节点 apiserver 审计文件(`/var/lib/rancher/{rke2,k3s}/server/logs/audit.log`)。

多租户(`auth_enabled: true`):Alloy 按 namespace 打租户,平台组件与 apiserver 审计进 `platform`,`tenant-*` 工作负载进 `tenant`;摄入限流按租户独立计。入库前两段 `stage.replace`:日志行里 `token=` 的值,以及 apiserver 审计行里 Pod 模板的 `"name":"JUPYTER_TOKEN"` / `"name":"AUTHORIZED_KEYS"` 的 `value`,都抹成 `<redacted>`。租户头自声明,网络边界是 `../monitoring-netpol.yaml`(仅 alloy/grafana/prometheus 可到 loki:3100)。

RBAC:Alloy 的 ClusterRole 只有 pods / pods/log / namespaces / services / endpoints / endpointslices / nodes 的读(`values/alloy.yaml` 的 `rbac.rules` / `rbac.clusterRules`),无 secrets / configmaps;Loki 关掉 ruler sidecar(`sidecar.rules.enabled: false`),SA 与 Pod 均不挂 token。两者都由 CI `monitoring-rbac` job 的 helm 渲染断言与 `../preflight.sh` 守着。

## 留存口径(合规基线)

- **Loki `retention_period: 4320h`(180 天)**,`values/loki.yaml`(compactor `retention_enabled`);磁盘 full 档 50Gi CephFS,light 档 10Gi TopoLVM。
- DB `audit_log` 表:365 天(`SUPERDL_AUDIT_RETENTION_DAYS`),结构化审计第一事实源;Loki 是请求链/异常/apiserver 审计的第二路留存。

## 查询方式

Grafana(full 档)→ Explore → `Loki(平台)` / `Loki(租户)` 数据源(kps.yaml additionalDataSources);或 `logcli` 带 `--org-id`:`kubectl -n monitoring port-forward svc/loki 3100:3100` 后 `logcli --addr=http://localhost:3100 --org-id=platform query ...`(下面查询为 platform 租户口径;租户实例日志换 `--org-id=tenant`)。

下面六条查询依次对应:请求 ID 全链路、API 未捕获异常、outbox 死信/重试、审计事件、apiserver 删除操作、平台 ServiceAccount 的 apiserver 操作。

```logql
{namespace="superdl"} |~ `"request_id":"<request-id>"`

{namespace="superdl", container="api"} |~ "unhandled_exception"

{namespace="superdl", container="worker"} |~ "outbox_task_(dead|failed)"

{namespace="superdl", container="api"} |~ "audit"

{job="kube-apiserver-audit"} |~ `"verb":"delete"`
{job="kube-apiserver-audit"} |~ `"user":{"username":"system:serviceaccount:superdl`
```

平台日志 prod 为 JSON 行(structlog;`request_id`/`level`/`event` 为键),`| json` 后按键过滤,如 `{namespace="superdl"} | json | level="error"`。

## 告警

未捕获异常、outbox 死信等已有 Prometheus 指标告警(kps values 的 `superdl.platform` 规则组:`ApiHighErrorRate`/`OutboxTaskDead` 等)。按日志内容告警时,用 Loki ruler 对上面的 API 异常与 outbox 死信/重试查询建 `count_over_time(...) > 0` 规则,接同一 Alertmanager。

## 采集自检

Alloy 应每节点一只 Pod,包括控制面节点;缺少 apiserver 审计流时先确认 server 上的 Alloy Pod 已就绪,再核对 `values/alloy.yaml` 的 tolerations 与审计文件挂载。

```bash
kubectl -n monitoring get pods -l app.kubernetes.io/name=alloy
kubectl -n monitoring logs deploy/loki --tail=5
```
