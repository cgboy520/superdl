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
# JuiceFS 只在 juicefs.enabled=true 的档位需要(light 默认关:无数据盘即无 JuiceFS,凭据也不必存在)
if grep -qE '^\s*juicefs:\s*\{[^}]*enabled:\s*true' "environments/$env_name.yaml"; then
  check_secret kube-system superdl-juicefs-secret "JuiceFS 元数据/对象存储凭据"
else
  ok "kube-system/superdl-juicefs-secret 不需要(environments/$env_name.yaml juicefs.enabled=false)"
fi
check_secret monitoring superdl-alert-token "Alertmanager→平台告警 webhook token"
check_secret monitoring superdl-smtp-password "Alertmanager 邮件通道"
if grep -qE '^\s*acmeDns:\s*\{[^}]*enabled:\s*true' "environments/$env_name.yaml"; then
  check_secret cert-manager acme-dns-account "acme-dns 账户凭据(acmeDNS solver,建法见 runbooks/acme-dns.md)"
  # 光有 secret 不够:acmedns.json 以**被验证的域**为键,两张泛域名证书各要一个键
  # (*.app.<域> 的挑战名是 app.<域>,*.svc.<域> 是 svc.<域>)。少一个键时 cert-manager
  # 不报错也不告警,只有那张 Certificate 长期 Ready=False、该域 TLS 握手直接失败。
  if kubectl -n cert-manager get secret acme-dns-account >/dev/null 2>&1; then
    acmedns_keys="$(kubectl -n cert-manager get secret acme-dns-account       -o jsonpath='{.data.acmedns\.json}' 2>/dev/null | base64 -d 2>/dev/null || true)"
    for zone in app svc; do
      # 键名按清单里的占位域推;换真实域后这里跟着改(与 05-cert-manager.yaml 同源)
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

# 镜像仓库(Harbor)不在本脚本校验范围:地址/机器人/CA 在管理端「平台配置 · 镜像仓库」录入并「测试连接」;
# 平台自身镜像的拉取 Secret superdl-registry-pull 由 scripts/release.sh 发布前校验。

# 只查 helmfile apply 直接消费的 values/ 与 raw manifest;rke2/*.yaml 是分发模板,占位符由
# ansible / 一键加入脚本落盘时替换。kps.yaml 的占位是 Alertmanager webhook token / SMTP /
# 值班接收端,未替换等于全部告警静默。
say "== values/ 占位符残留(未替换直接 apply 会让组件起不来;kps.yaml 未替换则告警静默)=="
placeholder_files=(values/cilium.yaml values/kps.yaml acme-dns.yaml)
for f in "${placeholder_files[@]}"; do
  [[ -f "$f" ]] || continue
  # cilium 只在 full 档装(light 用 k3s 内置 flannel):未启用时它的占位符与本环境无关
  if [[ "$f" == values/cilium.yaml ]] && ! grep -qE '^\s*cilium:\s*\{[^}]*enabled:\s*true' "environments/$env_name.yaml"; then
    ok "$f 不适用(environments/$env_name.yaml cilium.enabled=false)"
    continue
  fi
  if [[ "$f" == acme-dns.yaml ]] && ! grep -qE '^\s*acmeDns:\s*\{[^}]*enabled:\s*true' "environments/$env_name.yaml"; then
    ok "$f 不适用(environments/$env_name.yaml acmeDns.enabled=false)"
    continue
  fi
  # 只扫有效行:文件头注释本身会提到 CHANGE_ME/example.com,不算残留
  if grep -vE '^\s*#' "$f" | grep -qE '<server-ip>|CHANGE_ME|example\.com'; then
    miss "$f 仍有 <server-ip>/CHANGE_ME/example.com 占位符未替换"
  else
    ok "$f"
  fi
done

say "== 分发模板卫生(rke2/k3s server-config 是模板,不是渲染产物)=="
# 反向检查:模板里 agent-token/etcd-s3 必须保持注释/占位。取消注释意味着真实凭据
# 被提交进仓库(任何能读仓库的人即持集群加入凭据);site.yml 的渲染前断言要求
# agent_token 经 group_vars/servers.yml 或 -e 注入,绝不落模板。
for tpl in rke2/server-config.yaml k3s/server-config.yaml; do
  [[ -f "$tpl" ]] || continue
  if grep -qE '^agent-token:' "$tpl"; then
    miss "$tpl 的 agent-token 被取消了注释(真实 token 不得入库;经 ansible 变量注入)"
  elif ! grep -qE '^#agent-token: "CHANGE_ME_AGENT_TOKEN"$' "$tpl"; then
    miss "$tpl 缺少 #agent-token CHANGE_ME 占位行(模板被改动?site.yml 渲染依赖该行)"
  else
    ok "$tpl agent-token 占位完好"
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
# 平台用到的策略对象(BackendTrafficPolicy 的每源 IP 本地限流等)落在 experimental channel。
# CRD 由 helmfile presync 的 ./gateway-api-crds.sh 装,首装时集群里还没有 CRD 属正常。
# 装成 standard 就换不回来:safe-upgrades VAP 用 CEL 拒绝 standard→experimental,唯一出路是
# 删净 CRD 重装,而删 CRD 会连带删掉集群内全部 Gateway/HTTPRoute。本项不符当场停。
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
# 管理端白名单的占位符是 192.0.2.0/24(RFC 5737 文档网段)而非 CHANGE_ME_*,见
# ../app/k8s/04-gateway.yaml 的 superdl-admin-allowlist;这道检查在 apply 前拦住未替换。
if [[ -f "$app_gateway" ]]; then
  if grep -q '192\.0\.2\.0/24' "$app_gateway"; then
    miss "$app_gateway 管理端白名单仍是 192.0.2.0/24 占位(替换为办公网/跳板机出口 CIDR)"
  else
    ok "$app_gateway 管理端白名单已配真实网段"
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
    # 提示不阻断:托管 PG 是否已开 PITR 只有其控制台能证明,脚本查不到,书面确认由人核
    # (runbooks/cluster-validation.md 发布检查单、runbooks/pg-backup-restore.md 上线前强制项)
    say "  ⚠ cnpg.enabled=false 且未登记托管 PG PITR 确认:确认托管 PG 已开 PITR+保留策略后以 SUPERDL_MANAGED_PG_PITR_ACK=yes 重跑可消除本提示;或启用 cnpg 档(environments/$env_name.yaml)。提示项,不阻断"
  fi
fi

say "== 准入策略(ValidatingAdmissionPolicy 必须 Deny 生效)=="
# 正式发布前三个 Binding 必须是 Deny,本检查按 Deny 卡。
# superdl-global-pod-guard 面大且覆盖第三方 ns,仍在 Audit 观察期,转 Deny 后补进本清单。
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
  # server 节点数须奇数且 ≥3(etcd 法定人数;偶数台不抗脑裂,双台等于没有 HA)
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
  # light 档租户计算与控制面同宿主:恶意租户的内核/GPU 驱动攻击或资源耗尽直接命中
  # 控制面。禁止面向公众生产;部署方必须显式书面确认本集群不公网开放。
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
