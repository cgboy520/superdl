#!/usr/bin/env bash
set -euo pipefail

env_name="${1:-}"
[[ "$env_name" == "full" || "$env_name" == "light" ]] || {
  echo "usage: $0 <full|light>" >&2
  exit 2
}

fail=0
say() { printf '%s\n' "$*"; }
ok() { say "  ✓ $*"; }
miss() { say "  ✗ $*"; fail=1; }

say "== Toolchain =="
for bin in kubectl helm helmfile; do
  if command -v "$bin" >/dev/null 2>&1; then ok "$bin"; else miss "$bin is not installed"; fi
done

say "== Cluster connectivity =="
if kubectl version >/dev/null 2>&1; then
  ok "kube-apiserver reachable ($(kubectl version 2>/dev/null | grep -i server | head -1 | tr -s ' '))"
else
  miss "kube-apiserver unreachable (check KUBECONFIG)"
fi

say "== Prerequisite Secrets (helm does not create them; components stay down while missing) =="
check_secret() {
  if kubectl -n "$1" get secret "$2" >/dev/null 2>&1; then
    ok "$1/$2($3)"
  else
    miss "$1/$2 ($3): see the README section Preflight for how to create it"
  fi
}
check_secret monitoring superdl-alert-token "Alertmanager → platform alert webhook token"
check_secret monitoring superdl-smtp-password "Alertmanager mail channel"
check_secret monitoring superdl-metrics-token "Bearer for Prometheus scraping API/worker /metrics (token key = SUPERDL_METRICS_TOKEN; the monitors live in the monitoring ns)"
if kubectl -n superdl get secret superdl-auth >/dev/null 2>&1; then
  jwt_secret=$(kubectl -n superdl get secret superdl-auth \
    -o jsonpath='{.data.SUPERDL_JWT_SECRET}' 2>/dev/null | base64 -d 2>/dev/null || true)
  if [[ -z "$jwt_secret" ]]; then
    miss "superdl/superdl-auth lacks the SUPERDL_JWT_SECRET key (the API will not start)"
  elif [[ "$jwt_secret" == *CHANGE_ME* || ${#jwt_secret} -lt 32 ]]; then
    miss "SUPERDL_JWT_SECRET in superdl/superdl-auth is still the template placeholder or too short (generate with openssl rand -hex 32; the prod startup check refuses it the same way)"
  else
    ok "superdl/superdl-auth JWT secret replaced with a real value"
  fi
else
  miss "superdl/superdl-auth missing (JWT signing secret, template in ../app/secrets.example.yaml)"
fi
if grep -qE '^\s*acmeDns:\s*\{[^}]*enabled:\s*true' "environments/$env_name.yaml"; then
  check_secret cert-manager acme-dns-account "acme-dns account credentials (acmeDNS solver, see runbooks/acme-dns.md)"
  if kubectl -n cert-manager get secret acme-dns-account >/dev/null 2>&1; then
    acmedns_keys="$(kubectl -n cert-manager get secret acme-dns-account       -o jsonpath='{.data.acmedns\.json}' 2>/dev/null | base64 -d 2>/dev/null || true)"
    for zone in app svc; do
      if [[ "$acmedns_keys" == *"\"$zone."* ]]; then
        ok "acme-dns account has the delegation key for $zone.<domain>"
      else
        miss "acme-dns account lacks the key for $zone.<domain> (acmedns.json is keyed by the validated domain): without it that wildcard certificate is never issued, see runbooks/acme-dns.md"
      fi
    done
  fi
else
  ok "cert-manager/acme-dns-account not needed (environments/$env_name.yaml acmeDns.enabled=false: the wildcard certificate is loaded by hand as superdl/superdl-jupyter-wildcard-tls)"
fi
if [[ "$env_name" == "full" ]]; then
  check_secret monitoring grafana-admin "Grafana admin password (the light tier turns Grafana off, not needed)"
fi

say "== Leftover placeholders in values/ (applying unreplaced values keeps components down; an unreplaced kps.yaml silences alerts) =="
placeholder_files=(values/kps.yaml acme-dns.yaml)
if grep -qE '^\s*cilium:\s*\{[^}]*enabled:\s*true' "environments/$env_name.yaml"; then
  if [[ "$env_name" == "light" ]]; then
    placeholder_files+=(values/light/cilium-light.yaml)
  else
    placeholder_files+=(values/cilium.yaml)
  fi
else
  ok "values/cilium.yaml not applicable (environments/$env_name.yaml cilium.enabled=false)"
fi
for f in "${placeholder_files[@]}"; do
  [[ -f "$f" ]] || continue
  if [[ "$f" == acme-dns.yaml ]] && ! grep -qE '^\s*acmeDns:\s*\{[^}]*enabled:\s*true' "environments/$env_name.yaml"; then
    ok "$f not applicable (environments/$env_name.yaml acmeDns.enabled=false)"
    continue
  fi
  if grep -vE '^\s*#' "$f" | grep -qE '<server-ip>|CHANGE_ME|example\.com'; then
    miss "$f still has unreplaced <server-ip>/CHANGE_ME/example.com placeholders"
  else
    ok "$f"
  fi
done

say "== Distributed template hygiene (rke2/k3s server-config are templates, not rendered output) =="
for tpl in rke2/server-config.yaml k3s/server-config.yaml; do
  [[ -f "$tpl" ]] || continue
  if grep -qE '^\s*#\s*agent-token:' "$tpl"; then
    miss "agent-token in $tpl is commented out (agents fall back to server token authentication = one compromised GPU machine can enter etcd)"
  elif ! grep -qE '^agent-token: "CHANGE_ME_AGENT_TOKEN"$' "$tpl"; then
    miss "agent-token in $tpl is not the CHANGE_ME placeholder line (real tokens must not be committed; site.yml rendering depends on that line as-is)"
  else
    ok "$tpl agent-token enabled with the placeholder intact"
  fi
done
if [[ "$env_name" == "full" ]]; then
  etcd_tpl_bad=0
  for key in SNAPSHOT_BUCKET S3_REGION S3_ENDPOINT S3_ACCESS_KEY S3_SECRET_KEY; do
    if ! grep -qE "CHANGE_ME_ETCD_${key}" rke2/server-config.yaml; then
      miss "the etcd-s3 ${key} placeholder in rke2/server-config.yaml was changed (real credentials must not be committed)"
      etcd_tpl_bad=1
    fi
  done
  [[ "$etcd_tpl_bad" == "0" ]] && ok "rke2/server-config.yaml etcd-s3 placeholders intact"
fi

say "== Gateway API CRDs (the channel is fixed at first install and cannot be switched) =="
gw_crd=gateways.gateway.networking.k8s.io
if kubectl get crd "$gw_crd" >/dev/null 2>&1; then
  gw_channel=$(kubectl get crd "$gw_crd" \
    -o 'go-template={{index .metadata.annotations "gateway.networking.k8s.io/channel"}}' 2>/dev/null || true)
  gw_bundle=$(kubectl get crd "$gw_crd" \
    -o 'go-template={{index .metadata.annotations "gateway.networking.k8s.io/bundle-version"}}' 2>/dev/null || true)
  if [[ "$gw_channel" == "experimental" ]]; then
    ok "Gateway API CRD channel=experimental"
  else
    miss "Gateway API CRD channel=${gw_channel:-unknown} (experimental required): it cannot be switched afterwards, the safe-upgrades policy refuses standard→experimental; the only way out is deleting every CRD and reinstalling, which removes every Gateway/HTTPRoute in the cluster"
  fi
  if [[ "$gw_bundle" == "v1.6.1" ]]; then
    ok "Gateway API bundle-version=v1.6.1 (aligned with Envoy Gateway v1.9.0)"
  else
    miss "Gateway API bundle-version=${gw_bundle:-unknown} (v1.6.1 required, aligned with the helmfile envoy-gateway v1.9.0; run ./gateway-api-crds.sh to upgrade)"
  fi
else
  ok "Gateway API CRDs not installed yet (normal at first install: the helmfile apply presync runs ./gateway-api-crds.sh with the experimental channel)"
fi

say "== Application entry (../app) =="
app_gateway=../app/k8s/04-gateway.yaml
if [[ -f "$app_gateway" ]]; then
  if grep -vE '^\s*#' "$app_gateway" | grep -q '192\.0\.2\.0/24'; then
    miss "the admin allow-list in $app_gateway is still the 192.0.2.0/24 placeholder (replace it with the office / bastion egress CIDRs)"
  else
    ok "$app_gateway admin allow-list holds real ranges"
  fi
  admin_cidrs="$(awk '/^  name: superdl-admin-allowlist$/{f=1} f && /^---/{exit} f' "$app_gateway" \
    | grep -vE '^\s*#' | grep -oE '([0-9]{1,3}\.){3}[0-9]{1,3}/[0-9]{1,2}' || true)"
  admin_wide=0
  for cidr in 0.0.0.0/0 100.64.0.0/10 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16; do
    if grep -qxF "$cidr" <<< "$admin_cidrs"; then
      miss "the admin allow-list in $app_gateway contains the whole range $cidr (only specific egress /32s or office ranges are allowed; a whole CGNAT/private range = any tenant or upstream layer in that range can reach the admin console)"
      admin_wide=1
    fi
  done
  [[ "$admin_wide" == "0" ]] && ok "$app_gateway admin allow-list has no whole CGNAT/private range"
fi

say "== Billing database PITR (the real RPO guarantee: cnpg tier or a written managed-PG confirmation, one of the two) =="
if grep -qE '^\s*cnpg:\s*\{[^}]*enabled:\s*true' "environments/$env_name.yaml"; then
  if grep -qE 'CHANGE_ME' values/cnpg-cluster.yaml; then
    miss "values/cnpg-cluster.yaml still has CHANGE_ME placeholders (S3 endpoint/bucket/region unreplaced, PITR will not really work)"
  else
    ok "values/cnpg-cluster.yaml S3 archive configuration replaced"
  fi
  sb_count=$(kubectl -n superdl get scheduledbackup --no-headers 2>/dev/null | grep -c . || true)
  if [[ "$sb_count" -ge 1 ]]; then
    ok "ScheduledBackup running ($sb_count)"
  else
    miss "no ScheduledBackup in the superdl namespace (it should be generated after the cnpg-cluster apply; without it the daily backup is not running)"
  fi
else
  if [[ "${SUPERDL_MANAGED_PG_PITR_ACK:-}" == "yes" ]]; then
    ok "managed PG PITR confirmed in writing (SUPERDL_MANAGED_PG_PITR_ACK=yes)"
  elif [[ "$env_name" == "full" ]]; then
    miss "the full tier must enable cnpg (set cnpg.enabled=true in environments/$env_name.yaml) or confirm managed PG PITR and re-run with SUPERDL_MANAGED_PG_PITR_ACK=yes: a 24 h RPO on the billing database is not acceptable"
  else
    say "  ⚠ cnpg.enabled=false and no managed PG PITR confirmation recorded: after confirming PITR + retention on the managed PG, re-run with SUPERDL_MANAGED_PG_PITR_ACK=yes to clear this notice, or enable the cnpg tier (environments/$env_name.yaml). Notice only, not blocking"
  fi
fi

say "== Admission policies (ValidatingAdmissionPolicy must be effective as Deny) =="
for binding in superdl-platform-sa-scope superdl-tenant-pod-baseline superdl-node-field-scope \
  superdl-global-pod-guard superdl-platform-pod-secret-scope superdl-platform-job-secret-scope \
  superdl-node-delete-scope; do
  actions=$(kubectl get validatingadmissionpolicybinding "$binding" \
    -o jsonpath='{.spec.validationActions[*]}' 2>/dev/null || true)
  if [[ -z "$actions" ]]; then
    miss "ValidatingAdmissionPolicyBinding $binding missing (kubectl apply -f admission/tenant-restrictions.yaml)"
  elif [[ " $actions " == *" Deny "* ]]; then
    ok "ValidatingAdmissionPolicyBinding $binding validationActions=[$actions]"
  else
    miss "ValidatingAdmissionPolicyBinding $binding validationActions=[$actions] lacks Deny (all seven are Deny in the repository: changed to Audit in the cluster?)"
  fi
  if ! kubectl get validatingadmissionpolicy "$binding" >/dev/null 2>&1; then
    miss "ValidatingAdmissionPolicy $binding missing while its Binding exists: the policy was rejected by the apiserver (usually a CEL error), which currently means everything is allowed"
  fi
done

say "== apiserver admission plugin NodeRestriction (the only reason kubelets cannot self-apply the platform placement label) =="
nr_seen=0
nr_ok=0
api_cmd="$(kubectl -n kube-system get pods -l component=kube-apiserver,tier=control-plane \
  -o jsonpath='{.items[*].spec.containers[*].command}' 2>/dev/null || true)"
if [[ -n "$api_cmd" ]]; then
  nr_seen=1
  [[ "$api_cmd" == *NodeRestriction* ]] && nr_ok=1
else
  for cfg in /etc/rancher/rke2/config.yaml /etc/rancher/k3s/config.yaml; do
    [[ -r "$cfg" ]] || continue
    nr_seen=1
    grep -qE '^[[:space:]]*-[[:space:]]*enable-admission-plugins=.*NodeRestriction' "$cfg" && nr_ok=1
  done
fi
if [[ "$nr_seen" == "0" ]]; then
  miss "cannot confirm whether kube-apiserver enables NodeRestriction (the k3s apiserver is an embedded process, invisible from outside the cluster): re-run this script on any server node, or confirm by hand that kube-apiserver-arg in /etc/rancher/<distro>/config.yaml contains enable-admission-plugins=NodeRestriction"
elif [[ "$nr_ok" == "1" ]]; then
  ok "kube-apiserver has NodeRestriction enabled"
else
  miss "kube-apiserver does not enable NodeRestriction: add enable-admission-plugins=NodeRestriction to kube-apiserver-arg in the rke2/k3s server-config.yaml and restart the servers one by one"
fi

say "== Platform component placement label (without it every platform Pod stays Pending) =="
infra_nodes=$(kubectl get nodes -l node-restriction.kubernetes.io/superdl-infra=true \
  -o name 2>/dev/null | grep -c . || true)
if [[ "$infra_nodes" -ge 1 ]]; then
  ok "node-restriction.kubernetes.io/superdl-infra=true is on $infra_nodes node(s)"
else
  miss "no node carries node-restriction.kubernetes.io/superdl-infra=true: the api/worker/frontend/Envoy data plane of deploy/app/k8s all stay Pending. Applied by deploy/ansible/site.yml after install; manual fix: kubectl label nodes -l node-role.kubernetes.io/control-plane node-restriction.kubernetes.io/superdl-infra=true"
fi
gpu_infra=$(kubectl get nodes -l 'node-restriction.kubernetes.io/superdl-infra=true,node-restriction.kubernetes.io/superdl-pool' \
  -o name 2>/dev/null | grep -c . || true)
if [[ "$gpu_infra" -eq 0 ]]; then
  ok "no GPU pool node carries the infra label"
else
  miss "$gpu_infra GPU node(s) with node-restriction.kubernetes.io/superdl-pool also carry the infra label: platform components would schedule onto tenant compute nodes (kubectl label node <name> node-restriction.kubernetes.io/superdl-infra-)"
fi
legacy_pool=$(kubectl get nodes -l 'superdl.io/pool' -o name 2>/dev/null | grep -c . || true)
if [[ "$legacy_pool" -eq 0 ]]; then
  ok "no node carries the legacy pool label superdl.io/pool"
else
  miss "$legacy_pool node(s) still carry the legacy pool label superdl.io/pool (the pool label key is now node-restriction.kubernetes.io/superdl-pool, rewritten by the platform patrol; the hami/kata-deploy nodeSelectors accept only the new key; migration order in runbooks/node-pool-switch.md, Pool label key migration)"
fi
if [[ "$infra_nodes" -lt 2 ]]; then
  say "  ⚠ only $infra_nodes infra placement node: the 2 replicas of api / worker / frontends / Envoy share one machine without redundancy; with a second node the replicas split by hostname DoNotSchedule, after which a Pending replacement replica while one infra node is lost is the expected signal (notice only, not blocking)"
fi

say "== Monitoring SAs must not read Secrets (narrowed by values + monitoring-rbac.yaml; the DaemonSets run on tenant GPU nodes, where node root can read their tokens) =="
for sa in alloy loki kube-prometheus-stack-operator kube-prometheus-stack-prometheus kube-prometheus-stack-kube-state-metrics; do
  subj="system:serviceaccount:monitoring:$sa"
  if kubectl auth can-i --as="$subj" get secrets -n superdl >/dev/null 2>&1; then
    miss "$subj can get secrets -n superdl (chart RBAC not turned off by values? compare monitoring-rbac.yaml)"
  elif kubectl auth can-i --as="$subj" list secrets --all-namespaces >/dev/null 2>&1; then
    miss "$subj can list secrets --all-namespaces"
  else
    ok "$subj cannot read superdl Secrets"
  fi
done
for ns in monitoring kube-system; do
  while IFS=$'\t' read -r ds_name ds_sa ds_automount; do
    [[ -n "$ds_name" ]] || continue
    ds_sa="${ds_sa:-default}"
    if [[ -z "$ds_automount" ]]; then
      ds_automount="$(kubectl -n "$ns" get serviceaccount "$ds_sa" \
        -o jsonpath='{.automountServiceAccountToken}' 2>/dev/null || true)"
    fi
    subj="system:serviceaccount:$ns:$ds_sa"
    if [[ "$ds_automount" == "false" ]]; then
      ok "DaemonSet $ns/$ds_name mounts no SA token"
    elif kubectl auth can-i --as="$subj" get secrets -n superdl >/dev/null 2>&1 \
      || kubectl auth can-i --as="$subj" list secrets --all-namespaces >/dev/null 2>&1; then
      miss "DaemonSet $ns/$ds_name mounts the token of $subj and that SA can read platform Secrets: root on any node can take the platform keys"
    else
      ok "DaemonSet $ns/$ds_name mounts the $subj token, but that SA has no secrets read access"
    fi
  done < <(kubectl -n "$ns" get daemonsets \
    -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.template.spec.serviceAccountName}{"\t"}{.spec.template.spec.automountServiceAccountToken}{"\n"}{end}' 2>/dev/null)
done

say "== Node join credential (the agent token must differ from the server node-token) =="
tok_seen=0
for d in rke2 k3s; do
  cfg="/etc/rancher/$d/config.yaml"
  ntok="/var/lib/rancher/$d/server/node-token"
  [[ -r "$cfg" && -r "$ntok" ]] || continue
  tok_seen=1
  agent_tok="$(sed -nE 's/^agent-token:[[:space:]]*"([^"]*)".*$/\1/p' "$cfg" | head -1)"
  if [[ -z "$agent_tok" ]]; then
    agent_tok="$(sed -nE 's/^agent-token:[[:space:]]*([^"[:space:]#]+).*$/\1/p' "$cfg" | head -1)"
  fi
  srv_tok="$(tr -d '\n' < "$ntok")"
  srv_pass="${srv_tok##*:}"
  if [[ -z "$agent_tok" || "$agent_tok" == *CHANGE_ME* ]]; then
    miss "agent-token in $cfg unset or still the placeholder: agents fall back to server token authentication"
  elif [[ "$agent_tok" == "$srv_pass" || "$agent_tok" == "$srv_tok" ]]; then
    miss "agent-token in $cfg equals the server node-token: the credential handed to GPU nodes can start a server (rotation in the README section Server token and agent token)"
  elif [[ ${#agent_tok} -lt 32 ]]; then
    miss "agent-token in $cfg is shorter than 32 characters (generate with openssl rand -hex 32)"
  else
    ok "$d agent-token set and different from the server node-token"
  fi
done
if [[ "$tok_seen" == "0" ]]; then
  if [[ "${SUPERDL_AGENT_TOKEN_ACK:-}" == "yes" ]]; then
    ok "agent token ≠ server node-token confirmed by hand (SUPERDL_AGENT_TOKEN_ACK=yes)"
  else
    miss "this machine is not a server node, /etc/rancher/<distro>/config.yaml and server/node-token are unreadable: re-run this script on any server, or verify by hand that they differ and re-run with SUPERDL_AGENT_TOKEN_ACK=yes"
  fi
fi

say "== Application NetworkPolicy egress (advisory) =="
netpol=../app/k8s/09-networkpolicy.yaml
if [[ -f "$netpol" ]]; then
  if grep -vE '^\s*#' "$netpol" | grep -q 'CHANGE_ME'; then
    say "  ⚠ the egress rules of $netpol contain CHANGE_ME placeholders (PG / object storage / payment and SMS gateways / K8s API endpoints):"
    say "    replace them with real endpoints before applying, otherwise all platform egress is cut (notice only, not blocking)"
  else
    ok "$netpol egress narrowed to real endpoints"
  fi
fi

if [[ "$env_name" == "full" ]]; then
  say "== Control-plane HA (3 servers with stacked etcd + VIP) =="
  cp_nodes=$(kubectl get nodes -l node-role.kubernetes.io/control-plane -o name 2>/dev/null | grep -c . || true)
  if [[ "$cp_nodes" -ge 3 && $((cp_nodes % 2)) -eq 1 ]]; then
    ok "$cp_nodes control-plane node(s) (odd, ≥3)"
  else
    miss "$cp_nodes control-plane node(s): stacked etcd needs an odd count ≥3 (a single-server cluster is forbidden for public production, see the README section Path A)"
  fi
  etcd_running=$(kubectl -n kube-system get pods -l component=etcd,tier=control-plane \
    --field-selector=status.phase=Running -o name 2>/dev/null | grep -c . || true)
  if [[ "$etcd_running" -eq "$cp_nodes" && "$cp_nodes" -gt 0 ]]; then
    ok "etcd Pod Running $etcd_running/$cp_nodes"
  else
    miss "etcd Pods Running $etcd_running/$cp_nodes: some control-plane member's etcd has not joined or is unhealthy (kubectl -n kube-system get pods -l component=etcd)"
  fi
  vip=$(grep -E '^\s*k8sServiceHost:' values/cilium.yaml 2>/dev/null | head -1 | sed -E 's/.*"([^"]+)".*/\1/')
  if [[ -n "$vip" && "$vip" != *CHANGE_ME* && "$vip" != *'<'* ]]; then
    if curl -sk --max-time 5 "https://$vip:6443/healthz" 2>/dev/null | grep -q 'ok'; then
      ok "control-plane VIP $vip:6443 /healthz reachable"
    else
      miss "control-plane VIP $vip:6443 unreachable (kube-vip/keepalived/SLB not ready, or the certificate SAN lacks the VIP; tls-san in rke2/server-config.yaml)"
    fi
  else
    miss "values/cilium.yaml k8sServiceHost is not the real VIP (in an HA cluster Cilium must connect to the VIP directly; a single server IP is a data-plane single point of failure)"
  fi
  notready=$(kubectl get nodes --no-headers 2>/dev/null | grep -v ' Ready ' | grep -c . || true)
  if [[ "$notready" -eq 0 ]]; then
    ok "every node Ready"
  else
    miss "$notready node(s) not Ready (kubectl get nodes; a NotReady control plane in an HA cluster = the etcd quorum is shrinking)"
  fi
fi

say "== StorageClasses referenced by PVCs exist (a missing SC leaves PVCs unbound forever and components silently down) =="
check_sc() {
  if kubectl get storageclass "$1" >/dev/null 2>&1; then
    ok "StorageClass $1($2)"
  else
    miss "StorageClass $1 missing ($2)"
  fi
}
check_sc topolvm-provisioner "instance disks / monitoring components / acme-dns storage (mandatory on full and light)"
if grep -qE '^\s*rookCeph:\s*\{[^}]*enabled:\s*true' "environments/$env_name.yaml"; then
  check_sc superdl-cephfs "data disks (CephFS; the only shared filesystem supporting idmapped mounts)"
  if kubectl get storageclass superdl-cephfs >/dev/null 2>&1; then
    cephfs_rp="$(kubectl get storageclass superdl-cephfs -o jsonpath='{.reclaimPolicy}' 2>/dev/null || true)"
    if [[ "$cephfs_rp" == "Delete" ]]; then
      ok "StorageClass superdl-cephfs reclaimPolicy=Delete"
    else
      miss "StorageClass superdl-cephfs reclaimPolicy=${cephfs_rp:-unknown} (the code assumes Delete: deleting a disk deletes the subvolume; SC fields are immutable, kubectl delete sc superdl-cephfs first, then rebuild with ./apply.sh $env_name -l name=rook-ceph-cluster, see runbooks/cluster-validation.md section D. Storage)"
    fi
    cephfs_pvs="$(kubectl get pv -o jsonpath='{range .items[?(@.spec.storageClassName=="superdl-cephfs")]}{.metadata.name}{" "}{.spec.persistentVolumeReclaimPolicy}{" "}{.status.phase}{"\n"}{end}' 2>/dev/null || true)"
    pv_retain="$(awk '$2=="Retain"' <<< "$cephfs_pvs" | grep -c . || true)"
    pv_released="$(awk '$3=="Released"' <<< "$cephfs_pvs" | grep -c . || true)"
    if [[ "$pv_retain" -eq 0 && "$pv_released" -eq 0 ]]; then
      ok "superdl-cephfs PVs are all Delete with no Released leftovers"
    else
      miss "superdl-cephfs PVs: $pv_retain still Retain, $pv_released Released (subvolumes of deleted tenant disks still occupy Ceph capacity; patch each with kubectl patch pv <pv> -p '{\"spec\":{\"persistentVolumeReclaimPolicy\":\"Delete\"}}', kubectl delete pv the Released ones, see runbooks/cluster-validation.md section D. Storage)"
    fi
  fi
fi

if [[ "$env_name" == "light" ]]; then
  say "== light (k3s) specifics =="
  if [[ "${SUPERDL_LIGHT_INTERNAL_ACK:-}" == "yes" ]]; then
    ok "light tier internal-only positioning confirmed (SUPERDL_LIGHT_INTERNAL_ACK=yes)"
  else
    miss "the light tier is limited to internal pilots / demos / development integration and forbidden for public production (tenants and control plane share the host). After confirming this cluster is not exposed to the public internet, re-run with SUPERDL_LIGHT_INTERNAL_ACK=yes"
  fi
  if kubectl get runtimeclass nvidia >/dev/null 2>&1; then
    ok "RuntimeClass nvidia exists"
  else
    miss "RuntimeClass nvidia missing (k3s needs the NVIDIA driver + toolkit installed; node-join.sh puts it in place)"
  fi
  if kubectl -n kube-system get deploy traefik >/dev/null 2>&1; then
    miss "traefik not disabled (the server config needs disable: traefik; the north-south entry is Envoy Gateway only)"
  else
    ok "traefik disabled"
  fi
fi

say ""
if [[ "$fail" -eq 0 ]]; then
  say "all ready: helmfile -e $env_name apply"
else
  say "items missing (✗); fix them and re-run this script."
  exit 1
fi
