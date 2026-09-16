# acme-dns (DNS01 relay) deployment and rotation runbook

**Scope: full tier** (`environments/full.yaml` `acmeDns.enabled=true`). **The light tier does not enable it** (`environments/light.yaml` `acmeDns.enabled=false`): load an existing wildcard certificate by hand as `superdl/superdl-jupyter-wildcard-tls` and `superdl/superdl-svc-wildcard-tls`; nothing is issued or renewed and the steps here do not apply.

The two wildcard certificates (Jupyter `*.app.<domain>`, service endpoints `*.svc.<domain>`, see `../../app/k8s/05-cert-manager.yaml`) use DNS-01: cert-manager's built-in acmeDNS solver (registers / updates TXT through the HTTP API) → acme-dns (credentials can only update the TXT of their own subdomain) → a one-time CNAME delegation in the primary DNS.

## Architecture and trust model

```
Let's Encrypt validation servers
  └─ look up _acme-challenge.app.superdl.example.com TXT
      └─ CNAME → <uuid>.auth.superdl.example.com (one-time, configured in the primary DNS console)
          └─ NS: auth.superdl.example.com → acme-dns LoadBalancer IP (53, public)
cert-manager (acmeDNS solver)
  └─ account credentials (acmedns.json: username/password/fulldomain/subdomain) → acme-dns HTTP API updates TXT
```

- An acme-dns account can only update the TXT of its own fulldomain; the primary DNS credentials never enter the cluster.
- 53 (UDP/TCP) is open to the public; the register / update API (8080) is ClusterIP only, reachable inside the cluster with username/password.

## One-time deployment steps

1. **Replace the placeholders** (`deploy/cluster/acme-dns.yaml`, scanned by preflight):
   - `CHANGE_ME_ACME_DNS_DIGEST`: pin the digest from `docker buildx imagetools inspect joohoi/acme-dns:v1.0`
   - `auth.superdl.example.com` → the real auth subdomain (a dedicated subdomain, not mixed with business domains)
2. `helmfile -e full apply` (the cert-manager presync hook applies acme-dns.yaml automatically)
3. Read the public LoadBalancer IP, write it into `CHANGE_ME_ACME_DNS_LB_IP` in the `config.cfg` records, then `kubectl apply` + restart the Pod:
   ```bash
   kubectl -n cert-manager get svc acme-dns -o jsonpath='{.status.loadBalancer.ingress[0].ip}'
   ```
4. **Primary DNS console (one-time, by hand)**:
   - `auth.superdl.example.com NS ns1.auth.superdl.example.com`
   - `ns1.auth.superdl.example.com A <LB IP from the previous step>`
5. **Register accounts**: one account per wildcard certificate (once for `app.<domain>` and once for `svc.<domain>`; the image has no curl, use wget):
   ```bash
   kubectl -n cert-manager exec deploy/acme-dns -- \
     wget -qO- --header='Content-Type: application/json' \
       --post-data='{"allowfrom":[]}' http://127.0.0.1:8080/register
   ```
   Save the returned `username/password/fulldomain/subdomain`.
6. **Primary DNS delegation** (one record per domain; the challenge name of the wildcard `*.app.<domain>` is `app.<domain>`):
   - `_acme-challenge.app.superdl.example.com CNAME <fulldomain of the app account>`
   - `_acme-challenge.svc.superdl.example.com CNAME <fulldomain of the svc account>`
7. **Create the account secret** (preflight checks existence and key name): the JSON is keyed by the **validated domain**, each value is the whole object returned by the register API; both domains are two keys in the same `acmedns.json`.
   ```bash
   kubectl -n cert-manager create secret generic acme-dns-account \
     --from-literal=acmedns.json='{"app.superdl.example.com":{"username":"...","password":"...","fulldomain":"<fulldomain>","subdomain":"<subdomain>","allowfrom":[]},"svc.superdl.example.com":{"username":"...","password":"...","fulldomain":"<fulldomain>","subdomain":"<subdomain>","allowfrom":[]}}'
   ```
   A missing key shows up as a certificate stuck at `Ready=False`, the matching listener not Programmed and no fallback certificate. cert-manager calls the in-cluster `acme-dns-api` Service pointed at by the ClusterIssuer `acmeDNS.host`, see `deploy/app/k8s/05-cert-manager.yaml`.
8. Verify:
   ```bash
   kubectl -n superdl get certificate superdl-jupyter-wildcard superdl-svc-wildcard
   kubectl logs -n cert-manager deploy/cert-manager | grep -i acme
   ```

## Credential rotation (quarterly or on leak)

1. Repeat "Register accounts" for a new (username, password) and update the matching domain key in the `acmedns.json` secret
2. `kubectl -n cert-manager rollout restart deploy/cert-manager`
3. Invalidate the old account in acme-dns (delete the row in SQLite, or rebuild the database and register again)
