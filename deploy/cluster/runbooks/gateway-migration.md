# 北向入口切换 Runbook:ingress-nginx → Gateway API + Envoy Gateway

一次性迁移 SOP。**只有已经在跑 ingress-nginx 的存量集群需要走这一页**;全新集群按
[`../README.md`](../README.md) 正常装机即可,Envoy Gateway 已是默认入口。

背景:ingress-nginx 于 2026-03 退休,最后版本 controller-v1.15.1,此后发现的任何 CVE 都不会修。
平台的四条北向链路(api / console / admin / 租户 Jupyter 泛域名)全跑在它上面。

## 这次迁移最容易翻车的四件事

1. **存量实例的入口对象不会自动补建。** 已经在跑的实例带的是旧 `Ingress`,新代码只建 `HTTPRoute`,
   而 reconciler 只清孤儿、从不为活跃实例补建端点。摘掉 ingress-nginx 的那一刻,这些实例的
   Jupyter 全部 502,直到用户自己关机再开机。**必须在切流前手工补建**(见步骤 5)。
2. **Gateway API CRD 的 channel 首装即定。** 随 CRD 一起装的 `safe-upgrades`
   ValidatingAdmissionPolicy 用 CEL 拒绝「standard 之上装 experimental」。装错只能删净 CRD 重来,
   而删 CRD 会连带删掉集群内**全部** Gateway/HTTPRoute。`./gateway-api-crds.sh` 装前有闸门,别绕过。
3. **`kubectl apply -k` 不会删掉旧对象。** `04-ingress.yaml` 已经不在 kustomization 里了,
   kustomize 不做 prune —— 三个平台 Ingress 会一直留着。删干净是本 runbook 的显式步骤,不是自动的。
4. **`limit-connections` 没有等价物。** 这是一处能力回退,不是无损平移,详见文末「语义变化」。

## 前置

- 集群 K8s 版本在 v1.33–v1.36(Envoy Gateway v1.9.0 的支持范围;低于 v1.33 先升集群)
- `deploy/cluster/preflight.sh <full|light>` 除 Gateway API CRD 一项外全绿
- 手上有当前入口的 LB/DNS 控制权(第 6 步要切地址)
- 选一个低峰窗口:第 6 步切流期间存量连接会断一次(HTTP 重连无感,Jupyter 的 WebSocket 会重连一次)

## 步骤

### 1. 装 Gateway API + Envoy Gateway 的 CRD

```bash
cd deploy/cluster
./gateway-api-crds.sh --dry-run    # 先干跑,看 channel 闸门是否放行
./gateway-api-crds.sh
```

**判据**:末行输出 `channel=experimental bundle-version=v1.6.1`。不符即停,按脚本给的错误处理,
不要「再跑一次试试」。

> 集群里此前若因别的原因装过 standard channel 的 Gateway API CRD(如 Cilium/Traefik 带进来的),
> 脚本会在这里停手。此时先确认那些 CRD 上没有任何存活的 Gateway/HTTPRoute,再删净重来。

### 2. 装 Envoy Gateway 控制面(ingress-nginx 暂不动,两者并存)

```bash
./apply.sh <full|light> -l name=envoy-gateway
kubectl -n envoy-gateway-system rollout status deploy/envoy-gateway
```

并存是刻意的:此时 DNS 还指向 ingress-nginx,现网流量不受影响,可以从容验证新入口。

准入策略的豁免名单也要跟着换(Envoy 数据面 Deployment 由 EG 控制器动态生成,仓库里改不到它,
命中租户基线就是整个网关起不来,而拒绝信息只出现在 EG 控制器日志里):

```bash
kubectl apply -f admission/tenant-restrictions.yaml
```

### 3. 先落证书,再落 Gateway

顺序不能反:listener 引用的 Secret 不存在时该 listener 不会 Programmed。

```bash
kubectl apply -f ../app/k8s/05-cert-manager.yaml
kubectl -n superdl get certificate -w        # 等 superdl-api / -frontends / -admin 三张 Ready
```

> 新的三张 Certificate 与 ingress-shim 自动生成的那三张**同 secretName、不同对象名**,会短暂共存。
> dnsNames 完全一致,cert-manager 校验通过即不重新签发,不消耗 Let's Encrypt 额度。
> 第 7 步删掉旧 Ingress 后,shim 生成的那三张会随 ownerReference 一并回收,Secret 留下。

### 4. 落 Gateway 与路由策略

```bash
kubectl apply -f ../app/k8s/04-gateway.yaml
kubectl apply -f ../app/k8s/09-networkpolicy.yaml   # 放行来源 ns 改成 envoy-gateway-system
kubectl apply -f ../app/k8s/01-rbac.yaml            # tenant-mgr 要 httproutes 写权限
```

**判据**(缺一不可):

```bash
# ① Gateway 整体 Programmed,且 5 个 listener 全部 ResolvedRefs
kubectl -n superdl get gateway superdl -o yaml | grep -A4 'conditions:'
# ② 三条策略必须 Accepted=True —— sectionName 写错不报错、apply 照样成功,
#    只是策略静默失效(白名单没了、限流没了),唯一线索就在这里
kubectl -n superdl describe securitypolicy superdl-admin-allowlist
kubectl -n superdl describe backendtrafficpolicy superdl-api-ratelimit
kubectl -n superdl describe clienttrafficpolicy superdl-gateway
# ③ 记下新入口地址(下一步与第 6 步都要用)
kubectl -n envoy-gateway-system get svc -l gateway.envoyproxy.io/owning-gateway-name=superdl
```

用 `--resolve` 绕开 DNS 直接验新入口(此时 DNS 还指着老的):

```bash
NEW_IP=<上一步取到的 EXTERNAL-IP>
curl -fsS --resolve "api.superdl.example.com:443:$NEW_IP" https://api.superdl.example.com/readyz
curl -fsS --resolve "console.superdl.example.com:443:$NEW_IP" https://console.superdl.example.com/ -o /dev/null
```

### 5. 给存量实例补建 HTTPRoute(切流前必做)

新代码只在实例**创建/开机**时建 HTTPRoute,不会为已经在跑的实例补建。下面这段按现存的
旧 Ingress 逐条翻译,租户 Pod 全程不动,零中断:

```bash
kubectl get ingress -A -l superdl.io/managed=true \
  -o jsonpath='{range .items[*]}{.metadata.namespace}{" "}{.metadata.name}{" "}{.spec.rules[0].host}{"\n"}{end}' \
| while read -r ns name host; do
    [ -n "$ns" ] || continue
    kubectl apply -f - <<EOF
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: ${name}
  namespace: ${ns}
  labels:
    superdl.io/instance: ${name}
    superdl.io/managed: "true"
spec:
  parentRefs:
    - group: gateway.networking.k8s.io
      kind: Gateway
      name: superdl
      namespace: superdl
      sectionName: app-https
  hostnames: ["${host}"]
  rules:
    - matches: [{path: {type: PathPrefix, value: /}}]
      backendRefs: [{name: ${name}-jupyter, port: 8888}]
EOF
  done
```

**判据**:HTTPRoute 条数与旧 Ingress 条数相等,且每条 `Accepted=True`。

```bash
kubectl get httproute -A -l superdl.io/managed=true --no-headers | wc -l
kubectl get ingress   -A -l superdl.io/managed=true --no-headers | wc -l
# 有任何一条没被 listener 接纳,这里会打印出来(常见原因:租户 ns 缺 superdl.io/managed 标签)
kubectl get httproute -A -l superdl.io/managed=true \
  -o jsonpath='{range .items[*]}{.metadata.namespace}/{.metadata.name} {.status.parents[0].conditions[?(@.type=="Accepted")].status}{"\n"}{end}' \
  | grep -v ' True$' || echo "全部 Accepted"
```

挑一个存活实例,用 `--resolve` 打新入口验 Jupyter 能开:

```bash
curl -fsS --resolve "<uuid>.app.superdl.example.com:443:$NEW_IP" \
  https://<uuid>.app.superdl.example.com/ -o /dev/null && echo OK
```

### 6. 切流(DNS / LB)

把四个域名(`api` / `console` / `admin` / `*.app` 泛域名)指向第 4 步取到的新入口地址。
裸金属经 Cilium L2/BGP 通告时,通常是把原先给 ingress-nginx 的 VIP 挪给新 Service
(`envoyService.loadBalancerIP`),这种情况下 DNS 不用动。

**判据**:四条链路都从公网走通,且 `admin` 域从白名单外的地址访问返回 403。

```bash
curl -fsS https://api.superdl.example.com/readyz
curl -fsS https://console.superdl.example.com/ -o /dev/null
curl -sS -o /dev/null -w '%{http_code}\n' https://admin.superdl.example.com/   # 白名单外应为 403
curl -sS -o /dev/null -w '%{http_code}\n' http://api.superdl.example.com/      # 应为 301
```

> `admin` 返回的不是 403 而是 200:检查 `SecurityPolicy` 的 `clientCIDRs` 是否还是占位符
> `192.0.2.0/24`(那样应该谁都进不去),以及 `envoyService.externalTrafficPolicy` 是否为 `Local`
> —— 改成 `Cluster` 会让 Envoy 看到的源 IP 变成节点 IP,白名单当场失效且不报错。

### 7. 摘掉 ingress-nginx

**观察至少一个工作日**再做这一步 —— 它之后就没有快速回滚路径了。

```bash
# 平台三个 Ingress(kustomize 不会 prune,必须显式删)
kubectl -n superdl delete ingress superdl-api superdl-frontends superdl-admin
# 租户遗留的旧 Ingress(先 --dry-run=client 看清要删哪些,再去掉该参数执行)
kubectl delete ingress -A -l superdl.io/managed=true --dry-run=client
kubectl delete ingress -A -l superdl.io/managed=true
# 控制器本体
helm -n ingress-nginx uninstall ingress-nginx
kubectl delete namespace ingress-nginx
```

**验收**:

```bash
kubectl get ingress -A                                   # 应为空
kubectl -n superdl get certificate                       # 应恰好 4 张,全 Ready
kubectl get validatingadmissionpolicybinding superdl-platform-sa-scope   # 仍在
```

管理端 **集群** 页的 `实例入口(网关)` 一项应为绿 —— 它的判据是 Gateway 对象的
`Programmed` 条件,不是「控制器 Pod 活着」,所以它绿了才代表流量真的进得来。
节点巡检每轮回写 `cluster_status.gateway_ready`,最长等一个巡检周期。

## 回滚

第 7 步之前,回滚就是把 DNS/VIP 指回 ingress-nginx —— 它一直在跑,旧 Ingress 一直在,秒级生效。
新建的 Gateway/HTTPRoute 留着不碍事(没有流量指向它们)。

第 7 步之后要回滚,只能重装 ingress-nginx 并重建全部 Ingress,代价远大于向前修。所以第 7 步
一定要等观察期过完再做。

## 语义变化(不是无损平移)

| 原 ingress-nginx | 现在 | 差异 |
|---|---|---|
| `limit-rps: 20` / `limit-rpm: 600`(每源 IP) | `BackendTrafficPolicy` local + `sourceCIDR.type: Distinct` | 等价。`Distinct` 是命门,写成默认的 `Exact` 会退化成全网共用一个桶 |
| `limit-connections: 20`(每源 IP 并发) | `ClientTrafficPolicy.connection.connectionLimit: 10000` | **能力回退**:EG 没有每源 IP 连接数原语,现在只是每 Envoy 实例的总量兜底。每 IP 维度的压制全靠 RPS/RPM |
| `whitelist-source-range` | `SecurityPolicy.authorization`(`defaultAction: Deny` + `clientCIDRs`) | 等价。占位符从 `CHANGE_ME_OFFICE_CIDR` 改成 `192.0.2.0/24`:CRD 对 clientCIDRs 有正则校验,非法串会让**这一个对象**被拒收而其余照常生效,管理端就此无声敞开 |
| `ssl-redirect: "true"`(全局) | `superdl-https-redirect` HTTPRoute 挂 `http` listener | 等价 |
| `default-ssl-certificate`(泛域名兜底) | `app-https` listener 的 `certificateRefs` | 更显式;代价是没有兜底证书,某条 listener 的 Secret 缺失时该域直接 TLS 握手失败 |
| (nginx 无此概念) | `ClientTrafficPolicy.timeout.http.streamIdleTimeout: 1h` | **必配项**:EG 默认 5 分钟,会按时掐断 Jupyter 的 WebSocket 与 SSE。症状是「用着用着内核就断了」,极易误诊成鉴权过期或网络抖动 |

## 迁移之后要盯的两件事

- **Envoy 数据面内存**。我们是「一实例一条 HTTPRoute」,活跃实例多了就是几百上千条路由,
  全量 xDS 下发进每个 Envoy。`04-gateway.yaml` 里的 memory limit 是照原 ingress-nginx 抄的起步值,
  **必须按自己的路由数实测后再定**;这里 OOMKill 掉的是全站入口,不是单个租户。
- **策略对象的 status**。listener 改名、Secret 换名这类改动不会让 apply 失败,只会让策略静默失效。
  任何改动 `04-gateway.yaml` 的收工动作都是 `kubectl -n superdl describe {security,backendtraffic,clienttraffic}policy`
  确认 `Accepted=True`。

## 升级 Envoy Gateway 版本

chart 侧 `crds.enabled=false`,helm **不会**替你升 CRD;而 gateway-helm 自带的 CRD 子 chart 走的是
helm 的 `crds/` 目录,那个目录在 `helm upgrade` 时永不更新。所以顺序固定:

```bash
cd deploy/cluster
# 1. 三处版本号一起改:gateway-api-crds.sh 顶部的 EG_VERSION / GATEWAY_API_VERSION、
#    helmfile.yaml.gotmpl 里 envoy-gateway release 的 version、
#    scripts/check-gateway-manifests.py 的 CHART_VERSION(CI 的清单校验闸门,
#    不改就是拿旧 schema 校验新清单,校验通过也不说明什么)
./gateway-api-crds.sh          # 2. 先升 CRD
./apply.sh <full|light> -l name=envoy-gateway   # 3. 再升控制面
```

反过来(先升控制面)会让新控制面读到旧 CRD 里不存在的字段,官方升级说明明确写着这会中断流量。
