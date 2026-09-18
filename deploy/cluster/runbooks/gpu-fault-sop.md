# GPU fault SOP

## Trigger
Alertmanager `GPUXidCriticalError` (Xid 48/63/64/79/94/95) → platform alert stream + on-call SMS.

## Response (on-call executes, every action is audited)
1. **Isolate**: `kubectl cordon <node>`; the admin nodes page shows Cordoned at once
2. **Find the affected instances**: admin nodes page → the node's instance list (across tenants); or
   `kubectl get pod -A -l superdl.io/managed=true -o wide | grep <node>`
3. **Stop and settle**: run the admin "Force stop" on the affected instances (reason: GPU hardware fault); when the reconciler saw pod_lost first, billing has already stopped automatically
4. **Notify**: the affected tenants have already received the gpu_fault in-app notification + SMS; on-call adds progress updates
5. **Compensate**: create an adjustment (positive) under admin "Finance · Adjustments", amount = that tenant's spend of the day; a second administrator must review it before it takes effect, ledger remark "GPU fault compensation"
6. **Repair and return**: after the hardware work `kubectl uncordon <node>`; close the incident after 24 h without recurrence

## Drill
- Simulate: `nvidia-smi drain` / pull a card, or inject GPUXidCriticalError with amtool
- Acceptance: the cordon → notify → stop billing → compensate chain within 15 minutes, events and audit complete and replayable
