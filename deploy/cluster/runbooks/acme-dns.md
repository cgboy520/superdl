# acme-dns(RFC2136 DNS01 中转)部署与轮换 Runbook

泛域名证书(`*.app.superdl.example.com`)的 DNS01 挑战路径。替代原个人仓
`cert-manager-webhook-alidns`:cert-manager 内置 RFC2136 solver → acme-dns
(凭据仅可更新 `_acme-challenge` 子域 TXT)→ 主域 DNS 一次性 CNAME 委托。

## 架构与信任模型

```
Let's Encrypt 验证服务器
  └─ 查 _acme-challenge.app.superdl.example.com TXT
      └─ CNAME → <uuid>.auth.superdl.example.com(一次性,主域 DNS 控制台配置)
          └─ NS: auth.superdl.example.com → acme-dns LoadBalancer IP(53,公网)
cert-manager(RFC2136 solver)
  └─ TSIG(acmedns.json:username/password/fulldomain)→ acme-dns 动态更新 TXT
```

- 凭据爆炸半径:acme-dns 账户只能更新自己 fulldomain 的 TXT;主域 RAM 凭据不再进集群。
- 攻击面:53(UDP/TCP) 公网开放(DNS 服务本体,Let's Encrypt 查询必需);
  注册/更新 API(8080) 仅 ClusterIP 集群内可达(cert-manager 经它更新 TXT),
  接口需 username/password 凭据。

## 一次性部署步骤

1. **替换占位符**(`deploy/cluster/acme-dns.yaml`):
   - `CHANGE_ME_ACME_DNS_DIGEST`:`docker buildx imagetools inspect joohoi/acme-dns:v1.0` 取 digest 钉死
   - `auth.superdl.example.com` → 实际 auth 子域(建议独立子域,勿与业务域混用)
2. `helmfile -e <env> apply`(presync hook 自动 apply acme-dns.yaml)
3. 取 LoadBalancer 公网 IP:
   `kubectl -n cert-manager get svc acme-dns -o jsonpath='{.status.loadBalancer.ingress[0].ip}'`
   回填 `config.cfg` records 的 `CHANGE_ME_ACME_DNS_LB_IP` 并 `kubectl apply` + 重启 Pod
4. **主域 DNS 控制台(一次性手工)**:
   - `auth.superdl.example.com NS ns1.auth.superdl.example.com`
   - `ns1.auth.superdl.example.com A <上一步 LB IP>`
5. **注册账户**(每个需要签发的域一次):
   ```bash
   kubectl -n cert-manager exec deploy/acme-dns -- \
     curl -sX POST http://127.0.0.1:8080/register \
       -H 'Content-Type: application/json' \
       -d '{"allowfrom":[]}'  # 空 = 任意来源可更新(8080 仅 ClusterIP,调用面已在集群内)
   ```
   保存返回的 `username/password/fulldomain`。
6. **主域 DNS 委托**:`_acme-challenge.app.superdl.example.com CNAME <fulldomain>`
   (`*.app` 泛域名挑战查询的就是 `_acme-challenge.app.<域>`)
7. **建 TSIG secret**(preflight 强制校验):
   ```bash
   kubectl -n cert-manager create secret generic acme-dns-account \
     --from-literal=acmedns.json='{"<fulldomain>":{"username":"...","password":"...","fulldomain":"<fulldomain>","server":"http://acme-dns-api.cert-manager.svc.cluster.local:8080"}}'
   ```
   server 字段是 cert-manager 更新 TXT 时调用的注册 API 地址(acme-dns 模式经 HTTP API,
   不是 RFC2136 协议),必须指向集群内 `acme-dns-api` Service。
8. 验证:`kubectl describe certificate superdl-jupyter-wildcard -n superdl`
   (Ready=True);`kubectl logs -n cert-manager deploy/cert-manager | grep -i acme`

## 凭据轮换(每季度或泄漏时)

1. 重复「注册账户」取新 (username, password),`acmedns.json` 更新 secret
2. `kubectl -n cert-manager rollout restart deploy/cert-manager`
3. 旧账户在 acme-dns 中失效(SQLite 删除对应行或整库重建后重注册)

## 退役回滚

若需回到 alidns webhook:恢复 helmfile 的 repo/release 段与
`deploy/app/k8s/05-cert-manager.yaml` 的 webhook solver,重建 alidns-credentials secret。
