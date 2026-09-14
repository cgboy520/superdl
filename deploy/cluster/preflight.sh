#!/usr/bin/env bash
# 只读前置检查(helmfile apply 之前跑)。用法:./preflight.sh <full|light>(在 deploy/cluster/ 下执行)
set -euo pipefail

env_name="${1:-}"
[[ "$env_name" == "full" || "$env_name" == "light" ]] || {
  echo "用法:$0 <full|light>" >&2
  exit 2
}

fail=0
say() { printf '%s\n' "$*"; }
ok() { say "  ✓ $*"; }
miss() { say "  ✗ $*"; fail=1; }

say "== 工具链 =="
for bin in kubectl helm helmfile; do
  if command -v "$bin" >/dev/null 2>&1; then ok "$bin"; else miss "$bin 未安装"; fi
done

say "== 集群连通 =="
if kubectl version >/dev/null 2>&1; then
  ok "kube-apiserver 可达($(kubectl version 2>/dev/null | grep -i server | head -1 | tr -s ' '))"
else
  miss "kube-apiserver 不可达(检查 KUBECONFIG)"
fi

say "== 前置 Secret(helm 不代建,缺失则组件起不来)=="
check_secret() { # <ns> <name> <用途>
  if kubectl -n "$1" get secret "$2" >/dev/null 2>&1; then
    ok "$1/$2($3)"
  else
    miss "$1/$2($3)—— 建法见 README「前置检查」节"
  fi
}
if grep -qE '^\s*juicefs:\s*\{[^}]*enabled:\s*true' "environments/$env_name.yaml"; then
  check_secret kube-system superdl-juicefs-secret "JuiceFS 元数据/对象存储凭据"
else
  ok "kube-system/superdl-juicefs-secret 不需要(environments/$env_name.yaml juicefs.enabled=false)"
fi
check_secret monitoring superdl-alert-token "Alertmanager→平台告警 webhook token"
check_secret monitoring superdl-smtp-password "Alertmanager 邮件通道"
# JWT 签发密钥占位检测(只报键名,不回显值)
if kubectl -n superdl get secret superdl-auth >/dev/null 2>&1; then
  jwt_secret=$(kubectl -n superdl get secret superdl-auth \
    -o jsonpath='{.data.SUPERDL_JWT_SECRET}' 2>/dev/null | base64 -d 2>/dev/null || true)
  if [[ -z "$jwt_secret" ]]; then
    miss "superdl/superdl-auth 缺 SUPERDL_JWT_SECRET 键(API 不会启动)"
  elif [[ "$jwt_secret" == *CHANGE_ME* || ${#jwt_secret} -lt 32 ]]; then
    miss "superdl/superdl-auth 的 SUPERDL_JWT_SECRET 仍是模板占位或过短(openssl rand -hex 32 生成;prod 启动校验同口径拒启)"
  else
    ok "superdl/superdl-auth JWT 密钥已替换为真值"
  fi
else
  miss "superdl/superdl-auth 不存在(JWT 签发密钥,模板见 ../app/secrets.example.yaml)"
fi
if grep -qE '^\s*acmeDns:\s*\{[^}]*enabled:\s*true' "environments/$env_name.yaml"; then
  check_secret cert-manager acme-dns-account "acme-dns 账户凭据(acmeDNS solver,建法见 runbooks/acme-dns.md)"
  # acmedns.json 以被验证的域为键,两张泛域名证书各要一个键(app.<域> / svc.<域>)
  if kubectl -n cert-manager get secret acme-dns-account >/dev/null 2>&1; then
    acmedns_keys="$(kubectl -n cert-manager get secret acme-dns-account       -o jsonpath='{.data.acmedns\.json}' 2>/dev/null | base64 -d 2>/dev/null || true)"
    for zone in app svc; do
      # 键名与 05-cert-manager.yaml 的域同源,换域一起改
      if [[ "$acmedns_keys" == *"\"$zone."* ]]; then
        ok "acme-dns 账户含 $zone.<域> 的委托键"
      else
        miss "acme-dns 账户缺 $zone.<域> 的键(acmedns.json 以被验证的域为键)—— 缺它那张泛域名证书永远签不出来,见 runbooks/acme-dns.md"
      fi
    done
  fi
else
  ok "cert-manager/acme-dns-account 不需要(environments/$env_name.yaml acmeDns.enabled=false:泛域名证书由 superdl/superdl-jupyter-wildcard-tls 手工灌入)"
fi
if [[ "$env_name" == "full" ]]; then
  check_secret monitoring grafana-admin "Grafana 管理员口令(light 档关 Grafana,不需要)"
fi

# Harbor 不在本脚本校验范围(管理端「平台配置 · 镜像仓库」测试连接;superdl-registry-pull 由 scripts/release.sh 校验)。
# 只查 helmfile apply 直接消费的 values/ 与 raw manifest;rke2/*.yaml 是分发模板,占位由 ansible / node-join.sh 替换
say "== values/ 占位符残留(未替换直接 apply 会让组件起不来;kps.yaml 未替换则告警静默)=="
placeholder_files=(values/cilium.yaml values/kps.yaml acme-dns.yaml)
# light 的 k8sServiceHost 在覆盖文件里(单 server 直连该机 6443,没有 VIP)
[[ "$env_name" == "light" ]] && placeholder_files+=(values/light/cilium-light.yaml)
for f in "${placeholder_files[@]}"; do
  [[ -f "$f" ]] || continue
  if [[ "$f" == values/cilium.yaml || "$f" == values/light/cilium-light.yaml ]] \
    && ! grep -qE '^\s*cilium:\s*\{[^}]*enabled:\s*true' "environments/$env_name.yaml"; then
    ok "$f 不适用(environments/$env_name.yaml cilium.enabled=false)"
    continue
  fi
  if [[ "$f" == acme-dns.yaml ]] && ! grep -qE '^\s*acmeDns:\s*\{[^}]*enabled:\s*true' "environments/$env_name.yaml"; then
    ok "$f 不适用(environments/$env_name.yaml acmeDns.enabled=false)"
    continue
  fi
  # 只扫非注释行
  if grep -vE '^\s*#' "$f" | grep -qE '<server-ip>|CHANGE_ME|example\.com'; then
    miss "$f 仍有 <server-ip>/CHANGE_ME/example.com 占位符未替换"
  else
    ok "$f"
  fi
done

say "== 分发模板卫生(rke2/k3s server-config 是模板,不是渲染产物)=="
# agent-token 必须在模板里且不被注释(否则 agent 回落用 server token);值必须保持 CHANGE_ME 占位,
# 真值经 group_vars/servers.yml(不入 git)或 -e 注入
for tpl in rke2/server-config.yaml k3s/server-config.yaml; do
  [[ -f "$tpl" ]] || continue
  if grep -qE '^\s*#\s*agent-token:' "$tpl"; then
    miss "$tpl 的 agent-token 被注释掉了(agent 回落用 server token 认证 = 一台 GPU 机器失陷即可入 etcd)"
  elif ! grep -qE '^agent-token: "CHANGE_ME_AGENT_TOKEN"$' "$tpl"; then
    miss "$tpl 的 agent-token 不是 CHANGE_ME 占位行(真实 token 不得入库;site.yml 渲染依赖该行原样)"
  else
    ok "$tpl agent-token 已启用且占位完好"
  fi
done
if [[ "$env_name" == "full" ]]; then
  etcd_tpl_bad=0
  for key in SNAPSHOT_BUCKET S3_REGION S3_ENDPOINT S3_ACCESS_KEY S3_SECRET_KEY; do
    if ! grep -qE "CHANGE_ME_ETCD_${key}" rke2/server-config.yaml; then
      miss "rke2/server-config.yaml 的 etcd-s3 ${key} 占位被改动(真实凭据不得入库)"
      etcd_tpl_bad=1
    fi
  done
  [[ "$etcd_tpl_bad" == "0" ]] && ok "rke2/server-config.yaml etcd-s3 占位完好"
fi

say "== Gateway API CRD(channel 首装即定,事后换不回去)=="
# 必须是 experimental channel(standard→experimental 被 safe-upgrades VAP 拒绝,只能删净 CRD 重装)。
# CRD 由 helmfile presync 的 ./gateway-api-crds.sh 装,首装时无 CRD 属正常
gw_crd=gateways.gateway.networking.k8s.io
if kubectl get crd "$gw_crd" >/dev/null 2>&1; then
  gw_channel=$(kubectl get crd "$gw_crd" \
    -o 'go-template={{index .metadata.annotations "gateway.networking.k8s.io/channel"}}' 2>/dev/null || true)
  gw_bundle=$(kubectl get crd "$gw_crd" \
    -o 'go-template={{index .metadata.annotations "gateway.networking.k8s.io/bundle-version"}}' 2>/dev/null || true)
  if [[ "$gw_channel" == "experimental" ]]; then
    ok "Gateway API CRD channel=experimental"
  else
    miss "Gateway API CRD channel=${gw_channel:-未知}(需 experimental)——事后换不回去:safe-upgrades 策略拒绝 standard→experimental,只能删净 CRD 重装,代价是集群内全部 Gateway/HTTPRoute 一并消失"
  fi
  if [[ "$gw_bundle" == "v1.6.1" ]]; then
    ok "Gateway API bundle-version=v1.6.1(对齐 Envoy Gateway v1.9.0)"
  else
    miss "Gateway API bundle-version=${gw_bundle:-未知}(需 v1.6.1,与 helmfile 的 envoy-gateway v1.9.0 对齐;跑 ./gateway-api-crds.sh 升到位)"
  fi
else
  ok "Gateway API CRD 尚未安装(首装正常:helmfile apply 的 presync 会跑 ./gateway-api-crds.sh 按 experimental channel 装入)"
fi

say "== 应用入口(../app)=="
app_gateway=../app/k8s/04-gateway.yaml
# 管理端白名单占位符是 192.0.2.0/24(../app/k8s/04-gateway.yaml superdl-admin-allowlist)
if [[ -f "$app_gateway" ]]; then
  # 只扫非注释行
  if grep -vE '^\s*#' "$app_gateway" | grep -q '192\.0\.2\.0/24'; then
    miss "$app_gateway 管理端白名单仍是 192.0.2.0/24 占位(替换为办公网/跳板机出口 CIDR)"
  else
    ok "$app_gateway 管理端白名单已配真实网段"
  fi
fi

say "== 资金库 PITR(实际 RPO 保障:cnpg 档或托管 PG 书面确认,二者其一)=="
# 每日 pg_dump RPO=24h;分钟级 RPO 靠 cnpg WAL 归档或托管 PG PITR
if grep -qE '^\s*cnpg:\s*\{[^}]*enabled:\s*true' "environments/$env_name.yaml"; then
  # 自建 cnpg 档:S3 归档占位符必须替换 + 集群内 ScheduledBackup 在跑
  if grep -qE 'CHANGE_ME' values/cnpg-cluster.yaml; then
    miss "values/cnpg-cluster.yaml 仍有 CHANGE_ME 占位(S3 endpoint/bucket/region 未替换,PITR 不会真正工作)"
  else
    ok "values/cnpg-cluster.yaml S3 归档配置已替换"
  fi
  sb_count=$(kubectl -n superdl get scheduledbackup --no-headers 2>/dev/null | grep -c . || true)
  if [[ "$sb_count" -ge 1 ]]; then
    ok "ScheduledBackup 在跑($sb_count 条)"
  else
    miss "superdl 命名空间无 ScheduledBackup(cnpg-cluster apply 后应自动生成;缺失则每日备份未在跑)"
  fi
else
  if [[ "${SUPERDL_MANAGED_PG_PITR_ACK:-}" == "yes" ]]; then
    ok "托管 PG PITR 已书面确认(SUPERDL_MANAGED_PG_PITR_ACK=yes)"
  elif [[ "$env_name" == "full" ]]; then
    # full 档阻断:须启用 cnpg 或书面确认托管 PG PITR
    miss "full 档必须启用 cnpg(environments/$env_name.yaml 置 cnpg.enabled=true)或确认托管 PG 已开 PITR 后以 SUPERDL_MANAGED_PG_PITR_ACK=yes 重跑——资金库 24h RPO 不可接受"
  else
    # light 档提示不阻断
    say "  ⚠ cnpg.enabled=false 且未登记托管 PG PITR 确认:确认托管 PG 已开 PITR+保留策略后以 SUPERDL_MANAGED_PG_PITR_ACK=yes 重跑可消除本提示;或启用 cnpg 档(environments/$env_name.yaml)。提示项,不阻断"
  fi
fi

say "== 准入策略(ValidatingAdmissionPolicy 必须 Deny 生效)=="
# 七个 Binding 全部按 Deny 卡(缺 Binding = fail-open)。策略由 ./apply.sh 下发,scripts/release.sh 滚动前二次断言
for binding in superdl-platform-sa-scope superdl-tenant-pod-baseline superdl-node-field-scope \
  superdl-global-pod-guard superdl-platform-pod-secret-scope superdl-platform-job-secret-scope \
  superdl-node-delete-scope; do
  actions=$(kubectl get validatingadmissionpolicybinding "$binding" \
    -o jsonpath='{.spec.validationActions[*]}' 2>/dev/null || true)
  if [[ -z "$actions" ]]; then
    miss "ValidatingAdmissionPolicyBinding $binding 不存在(kubectl apply -f admission/tenant-restrictions.yaml)"
  elif [[ " $actions " == *" Deny "* ]]; then
    ok "ValidatingAdmissionPolicyBinding $binding validationActions=[$actions]"
  else
    miss "ValidatingAdmissionPolicyBinding $binding validationActions=[$actions] 不含 Deny(仓库里七条都是 Deny:集群里被人改成 Audit 了?)"
  fi
  # Binding 在而 Policy 不在 = 静默失效
  if ! kubectl get validatingadmissionpolicy "$binding" >/dev/null 2>&1; then
    miss "ValidatingAdmissionPolicy $binding 不存在而 Binding 在:策略被 apiserver 拒收(多为 CEL 写错),当前等于全放行"
  fi
done

say "== apiserver 准入插件 NodeRestriction(平台落点标签不可被 kubelet 自打的唯一依据)=="
# deploy/app/k8s 的 nodeSelector 用 node-restriction.kubernetes.io/superdl-infra,依赖此插件。
# rke2 读 kube-system 静态 Pod 的 command;k3s 读本机 /etc/rancher/<distro>/config.yaml
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
  miss "无法确认 kube-apiserver 是否启用 NodeRestriction(k3s 的 apiserver 是内嵌进程,集群外看不到):在任一 server 节点上重跑本脚本,或人工确认 /etc/rancher/<distro>/config.yaml 的 kube-apiserver-arg 含 enable-admission-plugins=NodeRestriction"
elif [[ "$nr_ok" == "1" ]]; then
  ok "kube-apiserver 已启用 NodeRestriction"
else
  miss "kube-apiserver 未启用 NodeRestriction:rke2/k3s server-config.yaml 的 kube-apiserver-arg 补 enable-admission-plugins=NodeRestriction 后滚动重启各 server"
fi

say "== 平台组件落点标签(缺了全部平台 Pod 会 Pending)=="
infra_nodes=$(kubectl get nodes -l node-restriction.kubernetes.io/superdl-infra=true \
  -o name 2>/dev/null | grep -c . || true)
if [[ "$infra_nodes" -ge 1 ]]; then
  ok "node-restriction.kubernetes.io/superdl-infra=true 已打在 $infra_nodes 台节点上"
else
  miss "无节点带 node-restriction.kubernetes.io/superdl-infra=true:deploy/app/k8s 的 api/worker/前端/Envoy 数据面全部 Pending。由 deploy/ansible/site.yml 装机后打;手工补:kubectl label nodes -l node-role.kubernetes.io/control-plane node-restriction.kubernetes.io/superdl-infra=true"
fi
# 反向:GPU 池节点禁止带 infra 标签
gpu_infra=$(kubectl get nodes -l 'node-restriction.kubernetes.io/superdl-infra=true,superdl.io/pool' \
  -o name 2>/dev/null | grep -c . || true)
if [[ "$gpu_infra" -eq 0 ]]; then
  ok "无 GPU 池节点带 infra 标签"
else
  miss "$gpu_infra 台带 superdl.io/pool 的 GPU 节点同时带 infra 标签:平台组件会调度到租户计算节点上(kubectl label node <name> node-restriction.kubernetes.io/superdl-infra-)"
fi

say "== 节点加入凭据(agent token 必须 ≠ server node-token)=="
# 两个文件只在 server 节点上,只有在 server 上跑才能自动核对;别处以 SUPERDL_AGENT_TOKEN_ACK=yes 登记人工核对
tok_seen=0
for d in rke2 k3s; do
  cfg="/etc/rancher/$d/config.yaml"
  ntok="/var/lib/rancher/$d/server/node-token"
  [[ -r "$cfg" && -r "$ntok" ]] || continue
  tok_seen=1
  # 取不到即空串
  agent_tok="$(sed -nE 's/^agent-token:[[:space:]]*"([^"]*)".*$/\1/p' "$cfg" | head -1)"
  if [[ -z "$agent_tok" ]]; then   # 未加引号的写法
    agent_tok="$(sed -nE 's/^agent-token:[[:space:]]*([^"[:space:]#]+).*$/\1/p' "$cfg" | head -1)"
  fi
  srv_tok="$(tr -d '\n' < "$ntok")"
  # node-token 形如 K10<ca-hash>::server:<password>,凭据是最后一段
  srv_pass="${srv_tok##*:}"
  if [[ -z "$agent_tok" || "$agent_tok" == *CHANGE_ME* ]]; then
    miss "$cfg 的 agent-token 未设置或仍是占位:agent 会回落用 server token 认证"
  elif [[ "$agent_tok" == "$srv_pass" || "$agent_tok" == "$srv_tok" ]]; then
    miss "$cfg 的 agent-token 与 server node-token 相同:下发给 GPU 节点的凭据可以拉起 server(轮换见 README「server token 与 agent token」)"
  elif [[ ${#agent_tok} -lt 32 ]]; then
    miss "$cfg 的 agent-token 短于 32 字符(openssl rand -hex 32 生成)"
  else
    ok "$d agent-token 已设且与 server node-token 不同"
  fi
done
if [[ "$tok_seen" == "0" ]]; then
  if [[ "${SUPERDL_AGENT_TOKEN_ACK:-}" == "yes" ]]; then
    ok "agent token ≠ server node-token 已人工确认(SUPERDL_AGENT_TOKEN_ACK=yes)"
  else
    miss "本机不是 server 节点,读不到 /etc/rancher/<distro>/config.yaml 与 server/node-token:在任一 server 上重跑本脚本,或人工核对两者不同后以 SUPERDL_AGENT_TOKEN_ACK=yes 重跑"
  fi
fi

say "== 应用 NetworkPolicy 出向(提示性)=="
netpol=../app/k8s/09-networkpolicy.yaml
if [[ -f "$netpol" ]]; then
  if grep -vE '^\s*#' "$netpol" | grep -q 'CHANGE_ME'; then
    say "  ⚠ $netpol 出向规则含 CHANGE_ME 占位(PG/对象存储/支付·短信网关/K8s API 端点):"
    say "    apply 前必须替换为真实端点,否则平台出向全断(提示项,不阻断)"
  else
    ok "$netpol 出向已收敛为真实端点"
  fi
fi

if [[ "$env_name" == "full" ]]; then
  say "== 控制面 HA(3 server 堆叠 etcd + VIP)=="
  cp_nodes=$(kubectl get nodes -l node-role.kubernetes.io/control-plane -o name 2>/dev/null | grep -c . || true)
  if [[ "$cp_nodes" -ge 3 && $((cp_nodes % 2)) -eq 1 ]]; then
    ok "控制面节点 $cp_nodes 台(奇数 ≥3)"
  else
    miss "控制面节点 $cp_nodes 台:堆叠 etcd 需奇数台且 ≥3(单 server 集群禁止公众生产,见 README「路径 A」)"
  fi
  etcd_running=$(kubectl -n kube-system get pods -l component=etcd,tier=control-plane \
    --field-selector=status.phase=Running -o name 2>/dev/null | grep -c . || true)
  if [[ "$etcd_running" -eq "$cp_nodes" && "$cp_nodes" -gt 0 ]]; then
    ok "etcd Pod Running $etcd_running/$cp_nodes"
  else
    miss "etcd Pod Running $etcd_running/$cp_nodes:有控制面成员的 etcd 未入环或不健康(kubectl -n kube-system get pods -l component=etcd)"
  fi
  vip=$(grep -E '^\s*k8sServiceHost:' values/cilium.yaml 2>/dev/null | head -1 | sed -E 's/.*"([^"]+)".*/\1/')
  if [[ -n "$vip" && "$vip" != *CHANGE_ME* && "$vip" != *'<'* ]]; then
    if curl -sk --max-time 5 "https://$vip:6443/healthz" 2>/dev/null | grep -q 'ok'; then
      ok "控制面 VIP $vip:6443 /healthz 可达"
    else
      miss "控制面 VIP $vip:6443 不可达(kube-vip/keepalived/SLB 未就绪,或证书 SAN 未含 VIP——tls-san 见 rke2/server-config.yaml)"
    fi
  else
    miss "values/cilium.yaml k8sServiceHost 未配真实 VIP(HA 集群 Cilium 必须直连 VIP,单 server IP 是数据面单点)"
  fi
  notready=$(kubectl get nodes --no-headers 2>/dev/null | grep -v ' Ready ' | grep -c . || true)
  if [[ "$notready" -eq 0 ]]; then
    ok "全部节点 Ready"
  else
    miss "$notready 台节点非 Ready(kubectl get nodes;HA 集群里控制面 NotReady = etcd 法定人数在缩水)"
  fi
fi

say "== PVC 引用的 StorageClass 存在性(SC 不存在则 PVC 永不绑定,组件静默起不来)=="
check_sc() { # <sc 名称> <用途>
  if kubectl get storageclass "$1" >/dev/null 2>&1; then
    ok "StorageClass $1($2)"
  else
    miss "StorageClass $1 不存在($2)"
  fi
}
check_sc topolvm-provisioner "实例盘/监控组件/acme-dns 存储(full+light 均为强制依赖)"
check_sc superdl-juicefs "共享数据盘/监控栈存储(两档均为强制依赖)"

if [[ "$env_name" == "light" ]]; then
  say "== light(k3s)专项 =="
  # light 档禁止面向公众生产,须显式确认内网定位
  if [[ "${SUPERDL_LIGHT_INTERNAL_ACK:-}" == "yes" ]]; then
    ok "light 档内网定位已确认(SUPERDL_LIGHT_INTERNAL_ACK=yes)"
  else
    miss "light 档仅限内网试点/演示/开发联调,禁止公众生产(租户与控制面同宿主)。确认本集群不对公网开放后,以 SUPERDL_LIGHT_INTERNAL_ACK=yes 重跑"
  fi
  if kubectl get runtimeclass nvidia >/dev/null 2>&1; then
    ok "RuntimeClass nvidia 存在"
  else
    miss "RuntimeClass nvidia 不存在(k3s 需已装 NVIDIA 驱动+toolkit;node-join.sh 会就位)"
  fi
  if kubectl -n kube-system get deploy traefik >/dev/null 2>&1; then
    miss "traefik 未禁用(server config 需 disable: traefik,北向入口统一走 Envoy Gateway)"
  else
    ok "traefik 已禁用"
  fi
fi

say ""
if [[ "$fail" -eq 0 ]]; then
  say "全部就绪:helmfile -e $env_name apply"
else
  say "存在缺项(✗),补齐后重跑本脚本。"
  exit 1
fi
