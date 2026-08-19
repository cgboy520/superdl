# GPU 故障 SOP(development-plan §1.3-4;演练=人工事项 #9)

故障是必然事件。目标:分钟级止损 + 客户补偿透明。

## 触发
Alertmanager `GPUXidCriticalError`(Xid 48/63/64/79/94/95)→ 平台告警流 + 值班短信。

## 处置(值班执行,全程动作入审计)
1. **隔离**:`kubectl cordon <node>`(阻止新调度;管理端节点页同步显示 Cordoned)
2. **定位受影响实例**:管理端 节点页 → 该节点实例列表(跨租户);或
   `kubectl get pod -A -l superdl.io/managed=true -o wide | grep <node>`
3. **停机结算**:对受影响实例执行管理端「强制停止」(原因:GPU 硬件故障)——
   running→stopping 触发尾账,计费即刻停止;reconciler 若先发现 pod_lost 也会自动停费
4. **通知**:受影响租户已自动收到 gpu_fault 站内信+短信(WP9);值班补充处理进展
5. **补偿**:财务对账页发起调账(正数,代金券性质),额度=该租户当日消费,上限可配;
   第二管理员复核后生效,ledger 备注「GPU 故障补偿」
6. **修复回归**:硬件处理后 `kubectl uncordon <node>`;观察 24h 无复发关闭事件

## 演练(上线前必做)
- 模拟:`nvidia-smi drain`/拔卡 或 amtool 注入 GPUXidCriticalError
- 验收:cordon→通知→停费→补偿全链路 ≤15 分钟,事件与审计完整可回放
