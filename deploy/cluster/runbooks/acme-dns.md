# acme-dns(DNS01 中转)部署与轮换 Runbook

**适用范围:full 档**(`environments/full.yaml` `acmeDns.enabled=true`)。**light 档不启用**(`environments/light.yaml` `acmeDns.enabled=false`):把现成通配证书手工灌成 `superdl/superdl-jupyter-wildcard-tls` 与 `superdl/superdl-svc-wildcard-tls`,不签发、不续签,本文步骤不适用。

两张泛域名证书(Jupyter `*.app.<域>`、服务端点 `*.svc.<域>`,见 `../../app/k8s/05-cert-manager.yaml`)走 DNS-01:cert-manager 内置 acmeDNS solver(HTTP 注册/更新 API 写 TXT)→ acme-dns(凭据仅可更新自己子域的 TXT)→ 主域 DNS 一次性 CNAME 委托。

## 架构与信任模型

```
Let's Encrypt 验证服务器
  └─ 查 _acme-challenge.app.superdl.example.com TXT
      └─ CNAME → <uuid>.auth.superdl.example.com(一次性,主域 DNS 控制台配置)
          └─ NS: auth.superdl.example.com → acme-dns LoadBalancer IP(53,公网)
cert-manager(acmeDNS solver)
  └─ 账户凭据(acmedns.json:username/password/fulldomain/subdomain)→ acme-dns HTTP API 更新 TXT
```

- acme-dns 账户只能更新自己 fulldomain 的 TXT,主域 DNS 凭据不进集群。
- 53(UDP/TCP)公网开放;注册/更新 API(8080)仅 ClusterIP 集群内可达,需 username/password。

## 一次性部署步骤

1. **替换占位符**(`deploy/cluster/acme-dns.yaml`,preflight 强制扫描):
   - `CHANGE_ME_ACME_DNS_DIGEST`:`docker buildx imagetools inspect joohoi/acme-dns:v1.0` 取 digest 钉死
   - `auth.superdl.example.com` → 实际 auth 子域(独立子域,勿与业务域混用)
2. `helmfile -e full apply`(cert-manager 的 presync hook 自动 apply acme-dns.yaml)
3. 取 LoadBalancer 公网 IP,回填 `config.cfg` records 的 `CHANGE_ME_ACME_DNS_LB_IP` 后 `kubectl apply` + 重启 Pod:
   ```bash
   kubectl -n cert-manager get svc acme-dns -o jsonpath='{.status.loadBalancer.ingress[0].ip}'
   ```
4. **主域 DNS 控制台(一次性手工)**:
   - `auth.superdl.example.com NS ns1.auth.superdl.example.com`
   - `ns1.auth.superdl.example.com A <上一步 LB IP>`
5. **注册账户**:每张泛域名证书各一个账户(`app.<域>` 与 `svc.<域>` 各做一次;镜像无 curl,用 wget):
   ```bash
   # {"allowfrom":[]} = 任意来源可更新(8080 仅 ClusterIP)
   kubectl -n cert-manager exec deploy/acme-dns -- \
     wget -qO- --header='Content-Type: application/json' \
       --post-data='{"allowfrom":[]}' http://127.0.0.1:8080/register
   ```
   保存返回的 `username/password/fulldomain/subdomain`。
6. **主域 DNS 委托**(每个域一条,泛域名 `*.app.<域>` 的挑战名是 `app.<域>`):
   - `_acme-challenge.app.superdl.example.com CNAME <app 账户的 fulldomain>`
   - `_acme-challenge.svc.superdl.example.com CNAME <svc 账户的 fulldomain>`
7. **建账户 secret**(preflight 校验存在与键名):JSON 以**被验证的域**为键,值是注册 API 返回的整个对象;两个域两个键写在同一份 `acmedns.json`。
   ```bash
   kubectl -n cert-manager create secret generic acme-dns-account \
     --from-literal=acmedns.json='{"app.superdl.example.com":{"username":"...","password":"...","fulldomain":"<fulldomain>","subdomain":"<subdomain>","allowfrom":[]},"svc.superdl.example.com":{"username":"...","password":"...","fulldomain":"<fulldomain>","subdomain":"<subdomain>","allowfrom":[]}}'
   ```
   漏键的现场是证书长期 `Ready=False`、对应 listener 不 Programmed、无兜底证书。cert-manager 调的是 ClusterIssuer `acmeDNS.host` 指向的集群内 `acme-dns-api` Service,见 `deploy/app/k8s/05-cert-manager.yaml`。
8. 验证:
   ```bash
   kubectl -n superdl get certificate superdl-jupyter-wildcard superdl-svc-wildcard   # 均 Ready=True
   kubectl logs -n cert-manager deploy/cert-manager | grep -i acme
   ```

## 凭据轮换(每季度或泄漏时)

1. 重复「注册账户」取新 (username, password),更新 `acmedns.json` secret 中对应域的键
2. `kubectl -n cert-manager rollout restart deploy/cert-manager`
3. 旧账户在 acme-dns 中失效(SQLite 删除对应行或整库重建后重注册)
