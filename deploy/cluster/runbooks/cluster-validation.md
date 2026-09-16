# Real-hardware cluster validation checklist

Checks CI cannot cover; each item is "what to do + pass criterion".

## A. Node baseline (every node)

- [ ] `nvidia-smi` works, the driver version matches the GPU Operator compatibility matrix
- [ ] `kubectl get node -o wide`: all Ready, K8s **v1.36.x**
- [ ] `kubectl get node -L node-restriction.kubernetes.io/superdl-pool`: pool labels complete, **kata and hami disjoint**; the `-L superdl.io/pool` column all empty (the old key has been removed by the platform, migration in [node-pool-switch.md](./node-pool-switch.md) "Pool label key migration")
- [ ] `kubectl explain pod.spec.hostUsers` exists; run a `hostUsers: false` test Pod, `readlink /proc/self/ns/user` inside the container differs from the host
- [ ] Kernel ≥6.3: `uname -r`
- [ ] Installer source reachable: `curl -fsSI https://get.rke2.io` / `https://get.k3s.io` (or the mirror matching the chosen `node_install_mirror`) returns 200; hardware-related limits in [hardware-notes.md](./hardware-notes.md)
- [ ] The platform component placement label is only on control-plane nodes: `kubectl get nodes -l node-restriction.kubernetes.io/superdl-infra=true` at least one, and **none of them carries `node-restriction.kubernetes.io/superdl-pool`**; `preflight.sh` runs the same positive and negative checks. The 2 replicas of api / workers / frontends / Envoy spread by hostname with `DoNotSchedule`: **redundancy needs ≥2 infra nodes** (one node puts both replicas on the same machine); with two, `kubectl -n superdl get pods -o wide` shows every Deployment's two replicas on different nodes; after pulling one infra node the replacement replica stays Pending until the node returns, which is the expected signal
- [ ] **The global Pod fallback policy is `Deny`** (`superdl-global-pod-guard`, `admission/tenant-restrictions.yaml`): `kubectl debug node/<node>` lands in the `default` ns by default and is refused; add `--namespace kube-system` (an exempt ns) when debugging

## B. Kata whole-card passthrough

1. IOMMU group check (every multi-card node):
   ```bash
   for g in /sys/kernel/iommu_groups/*/devices/*; do echo "$g"; done | grep -i nvidia
   ```
   Criterion: every card in its own group; with several cards per group the whole-card tier cannot be sold on that node.
2. Two single-card Kata instances at once on an 8-card node (two Pods with `runtimeClassName: kata-qemu` + `nvidia.com/gpu: 1`):
   Criterion: `nvidia-smi` inside each container sees exactly 1 card, neither sees the other.
3. Performance baseline:
   ```bash
   python -c "import torch;print(torch.cuda.is_available())"
   ```
   Run 3 rounds of ResNet-50 training and record the throughput difference against the bare-metal baseline on the same hardware; criterion: loss <5%.

## C. HAMi shared pool

- [ ] 2 instances on the same card (50% compute / 8G VRAM each): `nvidia-smi` in each sees only the quota VRAM
- [ ] Interference load test: one instance at full load, record the other's throughput drop (sets the oversell ratio)
- [ ] VRAM over-limit rejected: an allocation beyond gpumem OOMs inside the container without touching the neighbour
- [ ] HAMi on k3s: `kubeScheduler.image.tag` in `values/light/hami-light.yaml` matches the cluster version, devicePlugin `runtimeClassName=nvidia` is effective, the RuntimeClass `nvidia` exists; the rendered hami-device-plugin DaemonSet nodeSelector is only `node-restriction.kubernetes.io/superdl-pool: hami` (the chart default `gpu: "on"` removed with null)
- [ ] Ordering the shared tier on k3s without HAMi installed: the error names the missing component, not a timeout or 500

## D. Storage

- [ ] CephFS: two Pods (both `hostUsers: false`) on different nodes mount the same PVC with consistent reads and writes; `ceph -s` HEALTH_OK; fio sequential write baseline recorded here: ____
- [ ] TopoLVM: no leftovers in `lvs` after PVC create/delete; the lvmd container's `/etc/lvm/lvm.conf` contains `issue_discards = 1`; measured `lvremove` time of a large LV (≥500Gi) recorded here: ____
- [ ] **The data disk StorageClass `superdl-cephfs` has `reclaimPolicy` `Delete`** (code and docs assume deleting a disk deletes the subvolume). One-off migration of existing clusters: ① SC fields are immutable, `kubectl delete sc superdl-cephfs` (does not affect bound PVs/PVCs) then rebuild with `./apply.sh <full|light> -l name=rook-ceph-cluster`; ② existing PVs carry their own `persistentVolumeReclaimPolicy`, patch each with `kubectl patch pv <pv> -p '{"spec":{"persistentVolumeReclaimPolicy":"Delete"}}'` (`kubectl get pv -o custom-columns=NAME:.metadata.name,SC:.spec.storageClassName,RECLAIM:.spec.persistentVolumeReclaimPolicy,PHASE:.status.phase | grep superdl-cephfs` lists them all); ③ `Released` PVs are leftovers of deleted tenant disks, `kubectl delete pv <pv>` lets the CSI delete the subvolume. `preflight.sh` asserts once on the SC and once on the PVs
- [ ] Data disk hard quota: create a 1GB test disk, mount it on an instance and write beyond 1GB (`dd if=/dev/zero of=/root/data/fill bs=1M count=1200`), which must be refused (No space); the admin dead-letter page has no disk.provision dead letters, Prometheus `superdl_disk_provision_failed_total` is 0; after deleting the disk `kubectl -n tenant-<id> get pvc` shows no leftover

## E. Monitoring and alerts

- [ ] kube-prometheus-stack: DCGM metrics queryable; import the grafana.com dashboard **24450**
- [ ] Fire each of the 7 alerts of the `superdl.gpu` rule group once (trigger GPUHighTemperature by hand or inject with amtool); stop dcgm-exporter for 30 minutes → `GpuTelemetryMissing` appears in the alert stream
- [ ] **The monitoring stack cannot read platform Secrets**: `kubectl auth can-i --as=system:serviceaccount:monitoring:alloy get secrets -n superdl` and the same for operator / prometheus / loki all answer `no`; `kubectl -n superdl get servicemonitor,podmonitor` is empty, the two monitors live in the `monitoring` ns (`08-monitoring.yaml`), Prometheus `up{namespace="superdl"}` shows both scrape pools (Bearer from `monitoring/superdl-metrics-token`); the `preflight.sh` "monitoring SAs must not read Secrets" section all green
- [ ] Alertmanager → platform webhook: `POST /api/v1/webhooks/alertmanager` (with Bearer token) appears in the admin alert stream
- [ ] Stop the HAMi scheduler → HamiSchedulerDown reaches the admin alert stream within 5 minutes
- [ ] `kubectl -n kube-system get svc hami-scheduler -o yaml`: a port named `monitor` exists (the additionalScrapeConfigs in `values/kps.yaml` keep targets by **port name**); fix the values when the name differs
- [ ] Query `hami_container_device_utilization_ratio` / `hami_vgpu_memory_used_bytes` in Prometheus (container-level labels `namespace`/`pod`/`container`): on mismatch change only the constants at the top of `apps/api/app/modules/metering/prom.py` and HAMI_QUERIES; the vGPUmonitor container declares no port, kps scrapes by container name + podIP:9394 (`values/kps.yaml`)
- [ ] The node label of `DCGM_FI_DEV_GPU_UTIL` is lowercase `hostname` (dcgm-exporter 4.x; 3.x uses `Hostname`) and its value equals the K8s node name; on mismatch change DCGM_NODE_LABEL in prom.py. DCGM produces no `DCGM_FI_DEV_XID_ERRORS` for unsupported models (e.g. the CMP series)
- [ ] A shared-tier instance under load: the user console detail page's GPU utilisation curve shows data matching `nvidia-smi`
- [ ] The admin nodes page heat grid shows real util / VRAM / temperature; it falls back within 60 s after removing the load
- [ ] After `helmfile -e light apply` every Pod in the monitoring namespace is Running; record the measured footprint (target Prometheus RSS < 1Gi)
- [ ] Prometheus down (scale 0): the user list shows "monitoring temporarily unavailable", the detail shows the 503 copy, the admin heat grid falls back to the two-state view, no errors anywhere on the site

## F. One-shot node join

- [ ] Run the full flow once for each of the kata / hami / mig pools; the node ends Ready with the right pool label
- [ ] **Pool switch (no node-side action)**: move an empty node hami → kata and back through the admin console without logging in or rebooting; both times check that the pool label and the operand label set converge (leftover keys of the old pool deleted), the components land correctly, the cards bind to `vfio-pci` on the kata side and back to `nvidia` after the return, and an instance of the target tier really starts; steps and checklist in [node-pool-switch.md](./node-pool-switch.md)
- [ ] kata pool reboot checkpoint: after the reboot the systemd oneshot resumes automatically to completion
- [ ] `registries.yaml` landed in `/etc/rancher/<rke2|k3s>/` and is effective (with a self-signed Harbor `harbor-ca.crt` in the same directory, 0644, `configs.tls.ca_file` pointing at it)
- [ ] Admin cordon/uncordon reaches the real node (patch_node)
- [ ] The bootstrap path of entering the server-side agent token (not node-token) into the admin console works
- [ ] Install order: gpu-operator before the nodes join (re-apply the labels once when reversed)
- [ ] GPU Operator workload labels in place (applied by the platform during join reconciliation, contract in [node-pool-switch.md](./node-pool-switch.md) "Verification"): kata pool `nvidia.com/gpu.workload.config=vm-passthrough`, hami pool `nvidia.com/gpu.deploy.device-plugin=false`; in the kata pool `nvidia.com/gpu` is registered by kata-sandbox-device-plugin
- [ ] **GPU visibility forgery defences**: ① application layer: creating a service instance through the admin / API with an explicit `NVIDIA_*` env must answer 422; ② admission layer: `kubectl -n tenant-<uuid> apply` of a Pod with `env: [{name: NVIDIA_VISIBLE_DEVICES, value: all}]` must be refused by `superdl-tenant-pod-baseline`; ③ runtime depth (production only after CDI injection is verified effective on a test cluster): add `ACCEPT_NVIDIA_VISIBLE_DEVICES_ENVVAR_WHEN_UNPRIVILEGED=false` to the toolkit section of `values/gpu-operator.yaml`. Verification matrix: one instance in each of the kata / mig / hami pools, `nvidia-smi` inside the container sees only the allocated cards, the hami pool VRAM over-limit is still refused inside the container

## G. Image cache and prewarm

- [ ] Spegel P2P: after node-A `crictl pull`s a pinned image, node-B pulls the same image within seconds
- [ ] Harbor: admin "Platform configuration · Image registry" test connection green; a Pod referencing `superdl-registry-pull` (prewarm Job / tenant instance) pulls from the private project, an anonymous node-side `crictl pull harbor.<domain>/superdl/<image>` is refused; the public proxy cache project serves `crictl pull docker.io/library/alpine:3.20` through the mirror from Harbor
- [ ] Rotation: save the new robot Secret in the configuration centre → a new instance Pod pulls successfully → after revoking the old Secret in Harbor another instance still pulls
- [ ] The prewarm Job lands in each of the kata/hami/mig pools (tolerations Exists)
- [ ] After kubelet image GC the re-check per `prewarm_recheck_hours` re-pulls automatically
- [ ] A 20GB-class image finishes pulling within `activeDeadlineSeconds=1800`

## H. Scheduling and SKUs

- [ ] The `superdl.io/gpu-model` nodeSelector really matches in each of the kata / mig / hami pools (mixed pools included)
- [ ] With `use-gputype` on, matching by the injected raw model string behaves as expected
- [ ] Ledger data source priority: raw model via nvidia-smi > GFD label > existing value; driver / CUDA versions prefer the GFD labels (`nvidia.com/cuda.{driver,runtime}-version.full`), the install snapshot is the fallback only

## I. Both tier paths

- [ ] Walk each of the full / light paths once end to end from the single page `../README.md`
- [ ] **Light tier gpu-operator (k3s)**: with `toolkit.enabled=false` every operand is Running, and `nvidia.com/gpu.count` and `nvidia.com/cuda.driver-version.full` are still in `kubectl get node -o json | jq '.items[].metadata.labels'`
- [ ] **Light tier kata-deploy (k3s)**: `kubectl get runtimeclass kata-qemu` exists; with kata pool nodes the `kata-deploy` DaemonSet is Ready, the node has the kata drop-in under `/var/lib/rancher/k3s/agent/etc/containerd/`, and a Pod with `runtimeClassName: kata-qemu` really runs
- [ ] All cluster-related environment variables left empty, nodes join only through admin "Platform configuration · Cluster access"
- [ ] The light tier Cilium NetworkPolicy enforces the tenant egress deny-list (the same policy as the full tier)
- [ ] The RKE2 / k3s cn mirror works
- [ ] The RBAC needed by the cluster capability probe suffices on both RKE2 and k3s

## J. North-south entry (Gateway API + Envoy Gateway)

- [ ] `kubectl get crd gateways.gateway.networking.k8s.io -o jsonpath='{.metadata.annotations}'`: `gateway.networking.k8s.io/channel` = **experimental**, `bundle-version` = **v1.6.1**. Stop on mismatch (the channel cannot be switched afterwards); `preflight.sh` runs the same check
- [ ] `kubectl -n superdl get gateway superdl -o yaml`: `Programmed=True`, the 6 listeners (`http` / `api-https` / `console-https` / `admin-https` / `app-https` / `svc-https`) each `Programmed=True`, `attachedRoutes` matching the expected counts (the admin "Cluster" page "Instance entry (gateway)" uses the same criterion)
- [ ] **Policies attached**: `kubectl -n superdl describe securitypolicy superdl-admin-allowlist` / `securitypolicy superdl-svc-extauth` / `backendtrafficpolicy superdl-api-ratelimit` / `backendtrafficpolicy superdl-api-webhooks` / `backendtrafficpolicy superdl-console-api-ratelimit` / `backendtrafficpolicy superdl-admin-ratelimit` / `backendtrafficpolicy superdl-svc-ratelimit` / `backendtrafficpolicy superdl-app-ratelimit` / `clienttrafficpolicy superdl-gateway`, all nine with `status.ancestors[].conditions` `Accepted=True` (a wrong `sectionName` raises no error and is visible only here)
- [ ] **The console domain's `/api/v1` reaches the API through Envoy**: `curl -sI https://console.<domain>/api/v1/catalog/skus` answers with an `x-request-id` header and the `superdl-web` nginx access log has no such line; `curl -sI https://console.<domain>/docs` and `/api/admin/v1/…` answer 404 (`superdl-console-edge-deny`); hitting the console domain's `/api/v1` 30 times from one client produces a 429, same for the admin domain (30/s)
- [ ] **Every upstream layer is registered**: after calling `/api/v1/…` over the real public chain (CDN → front proxy), `audit_log.ip` and the API log `client_ip` are the user's real egress IP, not the CDN / proxy address; otherwise check that `numTrustedHops` in the `ClientTrafficPolicy` and the ConfigMap `FORWARDED_ALLOW_IPS` list every hop
- [ ] `curl -I https://<domain>` on each of the three platform domains shows the right certificate chain; `curl -I http://<domain>` returns 301
- [ ] **The source IP reaches Envoy**: a machine outside the allow-list range hitting `admin.<domain>` gets 403, inside the range works. On failure check whether `kubectl -n superdl get envoyproxy superdl-proxy -o jsonpath='{.spec.provider.kubernetes.envoyService.externalTrafficPolicy}'` is still `Local`
- [ ] Per-source-IP rate limiting works: from one client `for i in $(seq 30); do curl -s -o /dev/null -w '%{http_code} ' https://<api domain>/readyz; done` shows 429; **a second machine hitting at the same time is unaffected**. Local rate limiting counts per Envoy instance; with 2 replicas the global cap is about the configured value × replicas
- [ ] **Jupyter long connections survive 5 minutes**: open an instance's JupyterLab, run a cell with >6 minutes of no output without touching the page, the kernel must not disconnect (without `streamIdleTimeout` EG cuts WebSocket/SSE after 5 minutes by default)
- [ ] Tenant routes attach across namespaces: `kubectl -n tenant-<uuid> get httproute <instance uuid> -o yaml` shows `status.parents[].conditions` `Accepted=True`; the route disappears with the instance (`kubectl get httproute -A -l superdl.io/managed=true` has no orphans)
- [ ] **Route scale and memory**: create as many HTTPRoutes as the target instances per machine, record the RSS of the Envoy data plane and the envoy-gateway control plane, and size the `EnvoyProxy` memory limit or the hard cap of instances per machine from it. Measurement recorded here: ____
- [ ] Data plane rolls without dropping traffic: restart the EG-generated Envoy Deployment under `envoy-gateway-system`; external `/readyz` polling shows no 5xx meanwhile (2 replicas + `envoyPDB.minAvailable: 1` + `shutdown.drainTimeout: 60s`)
- [ ] Envoy Pods land on nodes with `node-restriction.kubernetes.io/superdl-infra=true` and are not blocked by the admission policies (the exemption list in `admission/tenant-restrictions.yaml` includes `envoy-gateway-system`; the denial message appears only in the EG controller log)

### J-1. Service instance endpoints (`svc-https` listener)

- [ ] **The service wildcard certificate is issued**: `kubectl -n superdl get certificate superdl-svc-wildcard` is `Ready=True`. When False for long, check the acme-dns prerequisites of `*.svc.<domain>` (a new account + the `_acme-challenge.svc.<domain>` CNAME delegation + the `svc.<domain>` key in the acmedns.json of `acme-dns-account`, see `05-cert-manager.yaml` and `runbooks/acme-dns.md`)
- [ ] **A valid key passes**: `curl -H 'Authorization: Bearer <plaintext key>' https://svc-<slug>.svc.<domain>/<the container's own path>` returns the container's real response; `-H 'X-API-Key: <plaintext key>'` must pass as well (only Bearer passing means `headersToExtAuth` is missing `x-api-key`)
- [ ] **Invalid / revoked / another user's keys all answer 401** with the platform's unified error body; the auth service's 4xx responses pass through to the public network as-is, confirm the response has no stack trace, internal hostname or `Set-Cookie`
- [ ] **No key must answer 401**; an endpoint with `require_api_key=false` answers 200 without a key (create one endpoint of each kind and hit each once)
- [ ] **A dead control plane answers 503, not 403**: temporarily `kubectl -n superdl scale deploy/superdl-api --replicas=0` (restore right after the check); hitting the endpoint must answer **503**; a 403 means `statusOnError` is missing
- [ ] **The auth callback is not blocked by the edge guard**: after restoring the previous item the endpoint answers 200 at once; a lingering 503 means checking the `superdl-api` log for a 404 on `/api/internal/v1/endpoint-auth` (`headersToExtAuth` containing `x-forwarded-for` triggers the 404 guard in `app/core/edge_guard.py`)
- [ ] **Platform-injected headers cannot be forged**: a client sending `-H 'x-superdl-endpoint: forged' -H 'x-superdl-key-id: 999'` to the endpoint; the container must receive the real values from the auth service (`headersToBackend` override semantics)
- [ ] **Endpoint-level rate limiting works and endpoints are independent**: on one endpoint `for i in $(seq 40); do curl -s -o /dev/null -w '%{http_code} ' -H 'Authorization: Bearer <key>' https://svc-<slug>.svc.<domain>/; done` shows 429; **hitting another endpoint at the same time is unaffected**. Local rate limiting counts per Envoy instance; with 2 replicas the per-endpoint cap is about 20/s × 2
- [ ] **The Jupyter domain is not caught by the auth**: instance Jupyter on `app-https` still works by token (`superdl-svc-extauth` attached to `app-https` by mistake makes every Jupyter 403/503)
- [ ] Service endpoint routes attach across namespaces: in `kubectl -n tenant-<uuid> get httproute -o yaml` the service endpoint route shows `status.parents[].conditions` `Accepted=True`

## K. Release checklist (every release)

- [ ] **CSP and third-party SDK domains**: with the configured `captcha_provider` (turnstile or aliyun) walk the full register / login / password reset chain with no CSP violation in the browser console; new domains first go through `Content-Security-Policy-Report-Only` until clean, then enforce (see `deploy/app/security-headers-web-csp.conf`)
- [ ] The admin site's response headers contain `X-Robots-Tag: noindex, nofollow`; the web site's CSP contains `challenges.cloudflare.com` as well as `o.alicdn.com` and `*.captcha-open.aliyuncs.com`
- [ ] Billing database PITR: managed PG confirmed on (set `SUPERDL_MANAGED_PG_PITR_ACK`), or the cnpg tier enabled with preflight all green (see `preflight.sh`)
- [ ] **One-off cleanup after switching to server-side apply** (`scripts/release.sh` now uses `kubectl apply --server-side --force-conflicts`): if the five TLS Secrets were ever loaded with client-side apply, their `last-applied-configuration` annotation holds a full copy of certificate and private key; remove it from each: `for s in superdl-api-tls superdl-frontends-tls superdl-admin-tls superdl-jupyter-wildcard-tls superdl-svc-wildcard-tls; do kubectl -n superdl annotate secret "$s" kubectl.kubernetes.io/last-applied-configuration-; done` (`kubectl -n superdl get secret <name> -o jsonpath='{.metadata.annotations}'` reads back empty)
