#!/usr/bin/env bash
# 只读前置检查:helmfile apply 之前跑一遍,缺什么一次性列全。
# 用法:./preflight.sh <full|light>   (在 deploy/cluster/ 下执行)
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
check_secret kube-system superdl-juicefs-secret "JuiceFS 元数据/对象存储凭据"
check_secret monitoring superdl-alert-token "Alertmanager→平台告警 webhook token"
check_secret monitoring superdl-smtp-password "Alertmanager 邮件通道"
check_secret cert-manager acme-dns-account "acme-dns 账户凭据(RFC2136 DNS01,建法见 runbooks/acme-dns.md)"
if [[ "$env_name" == "full" ]]; then
  check_secret monitoring grafana-admin "Grafana 管理员口令(light 档关 Grafana,不需要)"
fi

say "== 托管镜像仓(P1-7:registry.superdl.local → ACR/Harbor https)=="
# 集群内自建 registry 已退役(清单已删除):节点 registries.yaml 的 mirror 指向托管仓,
# 地址与拉取凭据必须替换,否则节点无法 pull 平台镜像
if grep -q 'CHANGE_ME_REGISTRY_HOST' rke2/registries.yaml; then
  miss "rke2/registries.yaml mirror 仍是 CHANGE_ME_REGISTRY_HOST 占位(替换为 ACR/Harbor 真实地址)"
elif grep -qE 'CHANGE_ME_REGISTRY_(USERNAME|PASSWORD)' rke2/registries.yaml; then
  miss "rke2/registries.yaml 拉取凭据仍是 CHANGE_ME 占位(ACR 独立访问凭据或 Harbor 机器人账户)"
else
  ok "rke2/registries.yaml 托管仓地址与凭据已替换"
fi

# 只查 helmfile apply 直接消费的 values/ 与 raw manifest;rke2/*.yaml 是分发模板,
# 占位符由 ansible / 一键加入脚本落盘时替换,仓库里保留占位符。
# kps.yaml 的占位是 Alertmanager webhook token / SMTP / 值班接收端:未替换等于全部告警静默(事故盲区)。
say "== values/ 占位符残留(未替换直接 apply 会让组件起不来;kps.yaml 未替换则告警静默)=="
placeholder_files=(values/cilium.yaml values/kps.yaml acme-dns.yaml)
for f in "${placeholder_files[@]}"; do
  [[ -f "$f" ]] || continue
  if grep -qE '<server-ip>|CHANGE_ME|example\.com' "$f"; then
    miss "$f 仍有 <server-ip>/CHANGE_ME/example.com 占位符未替换"
  else
    ok "$f"
  fi
done

say "== 应用入口(../app)=="
app_ingress=../app/k8s/04-ingress.yaml
if [[ -f "$app_ingress" ]]; then
  if grep -q 'CHANGE_ME_OFFICE_CIDR' "$app_ingress"; then
    miss "$app_ingress 管理端白名单仍是 CHANGE_ME_OFFICE_CIDR 占位(替换为办公网/跳板机出口 CIDR)"
  else
    ok "$app_ingress 管理端白名单已配真实网段"
  fi
fi

say "== 资金库 PITR(实际 RPO 保障:cnpg 档或托管 PG 书面确认,二者其一)=="
# 逻辑备份(pg_dump 每日)RPO=24h,分钟级 RPO 只能靠 WAL 连续归档(cnpg)或托管 PG PITR
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
  else
    # 提示不阻断:托管 PG 是否已开 PITR 只有其控制台能证明,脚本查不到;书面确认由人核
    # (runbooks/cluster-validation.md 发布检查单、runbooks/pg-backup-restore.md 上线前强制项)
    say "  ⚠ cnpg.enabled=false 且未登记托管 PG PITR 确认:确认托管 PG 已开 PITR+保留策略后以 SUPERDL_MANAGED_PG_PITR_ACK=yes 重跑可消除本提示;或启用 cnpg 档(environments/$env_name.yaml)。提示项,不阻断"
  fi
fi

say "== 准入策略(ValidatingAdmissionPolicy 必须 Deny 生效)=="
# 首次上线可先 [Audit] 观察一周(见 admission/tenant-restrictions.yaml 头注释),
# 但正式发布前必须改回 Deny——本检查按 Deny 卡。
# superdl-global-pod-guard 仍处 Audit 观察期(面大且覆盖第三方 ns),毕业后再补进本清单。
for binding in superdl-platform-sa-scope superdl-tenant-pod-baseline superdl-node-field-scope; do
  actions=$(kubectl get validatingadmissionpolicybinding "$binding" \
    -o jsonpath='{.spec.validationActions[*]}' 2>/dev/null || true)
  if [[ -z "$actions" ]]; then
    miss "ValidatingAdmissionPolicyBinding $binding 不存在(kubectl apply -f admission/tenant-restrictions.yaml)"
  elif [[ " $actions " == *" Deny "* ]]; then
    ok "ValidatingAdmissionPolicyBinding $binding validationActions=[$actions]"
  else
    miss "ValidatingAdmissionPolicyBinding $binding validationActions=[$actions] 不含 Deny(Audit 观察期结束后改回)"
  fi
done

say "== 应用 NetworkPolicy 出向(提示性)=="
netpol=../app/k8s/09-networkpolicy.yaml
if [[ -f "$netpol" ]]; then
  if grep -q 'CHANGE_ME' "$netpol"; then
    say "  ⚠ $netpol 出向规则含 CHANGE_ME 占位(PG/对象存储/支付·短信网关/K8s API 端点):"
    say "    apply 前必须替换为真实端点,否则平台出向全断(提示项,不阻断)"
  else
    ok "$netpol 出向已收敛为真实端点"
  fi
fi

if [[ "$env_name" == "full" ]]; then
  say "== 控制面 HA(3 server 堆叠 etcd + VIP)=="
  # server 节点数:奇数且 ≥3(etcd 法定人数;偶数台不抗脑裂,双台等于没有 HA)
  cp_nodes=$(kubectl get nodes -l node-role.kubernetes.io/control-plane -o name 2>/dev/null | grep -c . || true)
  if [[ "$cp_nodes" -ge 3 && $((cp_nodes % 2)) -eq 1 ]]; then
    ok "控制面节点 $cp_nodes 台(奇数 ≥3)"
  else
    miss "控制面节点 $cp_nodes 台:堆叠 etcd 需奇数台且 ≥3(单 server 集群禁止公众生产,见 README「路径 A」)"
  fi
  # etcd 静态 Pod 全部 Running(成员与 server 一一对应;少了说明有成员没入环或不健康)
  etcd_running=$(kubectl -n kube-system get pods -l component=etcd,tier=control-plane \
    --field-selector=status.phase=Running -o name 2>/dev/null | grep -c . || true)
  if [[ "$etcd_running" -eq "$cp_nodes" && "$cp_nodes" -gt 0 ]]; then
    ok "etcd Pod Running $etcd_running/$cp_nodes"
  else
    miss "etcd Pod Running $etcd_running/$cp_nodes:有控制面成员的 etcd 未入环或不健康(kubectl -n kube-system get pods -l component=etcd)"
  fi
  # VIP 可达:cilium.yaml 的 k8sServiceHost 是仓内唯一录 VIP 的位置(占位检查已在上方拦截)
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
  # 全节点 Ready:任一 NotReady 都可能是 etcd 成员半死或池节点失联
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
if [[ "$env_name" == "full" ]]; then
  check_sc superdl-juicefs "共享数据盘/监控栈存储"
fi

if [[ "$env_name" == "light" ]]; then
  say "== light(k3s)专项 =="
  if kubectl get runtimeclass nvidia >/dev/null 2>&1; then
    ok "RuntimeClass nvidia 存在"
  else
    miss "RuntimeClass nvidia 不存在(k3s 需已装 NVIDIA 驱动+toolkit;node-join.sh 会就位)"
  fi
  if kubectl -n kube-system get deploy traefik >/dev/null 2>&1; then
    miss "traefik 未禁用(server config 需 disable: traefik,Ingress 统一走 ingress-nginx)"
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
