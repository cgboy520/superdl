# 日志与审计留存(Loki)

组件:helmfile 的 `loki`(grafana-community/loki,Monolithic 单副本)+ `alloy`(grafana/alloy)。
采集面:全部命名空间的容器日志(discovery.kubernetes)+ 控制面节点 apiserver 审计文件
(`/var/lib/rancher/{rke2,k3s}/server/logs/audit.log`)。

多租户(`auth_enabled: true`):Alloy 按 namespace 打租户——平台组件与
apiserver 审计进 `platform` 租户,`tenant-*` 工作负载进 `tenant` 租户;摄入限流按租户
独立计,租户日志洪峰挤不垮平台/审计流。头是自声明的,真实边界是
`../monitoring-netpol.yaml`(仅 alloy/grafana/prometheus 可到 loki:3100)。

## 留存口径(合规基线)

- **Loki `retention_period: 4320h`(180 天)**,`values/loki.yaml`(compactor `retention_enabled`);
  磁盘按此容量规划,full 档 50Gi JuiceFS,light 档 10Gi TopoLVM。
- DB `audit_log` 表:365 天(`SUPERDL_AUDIT_RETENTION_DAYS`),结构化审计的第一事实源;
  Loki 侧是请求链/异常/apiserver 审计的第二路留存。

## 查询方式

Grafana(full 档)→ Explore → `Loki(平台)` / `Loki(租户)` 数据源(kps.yaml
additionalDataSources,租户头已预置);或 `logcli` 带 `--org-id`:
`kubectl -n monitoring port-forward svc/loki 3100:3100` 后
`logcli --addr=http://localhost:3100 --org-id=platform query ...`
(下面查询均为 platform 租户口径;查租户实例日志换 `--org-id=tenant`)。

```logql
# 1. 按 request_id 串联一次请求的 API + worker(outbox)全链日志
#    (outbox payload 的 _request_id 键在执行时回填日志上下文)
{namespace="superdl"} |~ `"request_id":"<request-id>"`

# 2. API 未捕获异常(统一 500 留痕)
{namespace="superdl", container="api"} |~ "unhandled_exception"

# 3. outbox 死信/重试(编排链路排障)
{namespace="superdl", container="worker"} |~ "outbox_task_(dead|failed)"

# 4. 管理端写操作轨迹(DB audit_log 为主;这里看进程侧留痕与 audit_write_failed)
{namespace="superdl", container="api"} |~ "audit"

# 5. 控制面 apiserver 审计(谁动过集群对象;按动词/用户过滤)
{job="kube-apiserver-audit"} |~ `"verb":"delete"`
{job="kube-apiserver-audit"} |~ `"user":{"username":"system:serviceaccount:superdl`
```

平台日志 prod 为 JSON 行(structlog;`request_id`/`level`/`event` 为键),`| json` 后可按键过滤,
如 `{namespace="superdl"} | json | level="error"`。

## 告警

未捕获异常、outbox 死信等已有 Prometheus 指标告警(kps values 的 `superdl.platform` 规则组:
`ApiHighErrorRate`/`OutboxTaskDead` 等,走 Alertmanager 双通道)。需要按日志内容告警时,
用 Loki ruler 对上面 2/3 号查询建 `count_over_time(...) > 0` 规则,接同一 Alertmanager。

## 采集自检

```bash
kubectl -n monitoring get pods -l app.kubernetes.io/name=alloy   # 每节点一只,含控制面
kubectl -n monitoring logs deploy/loki --tail=5                  # 单副本 loki
# 无 apiserver 审计流时:确认 alloy DaemonSet 在 server 节点有 Pod
# (tolerations 已配 CriticalAddonsOnly/control-plane,见 values/alloy.yaml)
```
