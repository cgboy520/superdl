# Runbook index

By type: **incident response** (trigger → steps → acceptance / drill), **SOP** (one-off or periodic procedure), **checklist** (tick item by item), **reference** (how to query, definitions).
Write new incident-response runbooks in that structure and add `runbook_url` to the matching alert rule.

| File | Type | Scenario |
|---|---|---|
| [gpu-fault-sop.md](./gpu-fault-sop.md) | Incident response | GPU Xid fatal error: isolate → stop and settle → notify → compensate → return to service |
| [pg-backup-restore.md](./pg-backup-restore.md) | Incident response + SOP | Billing database backup layers, logical backup restore, quarterly drill and RTO log |
| [node-pool-switch.md](./node-pool-switch.md) | SOP | Moving a node between the kata / hami / mig pools: prerequisites, execution, verification |
| [image-prewarm.md](./image-prewarm.md) | SOP | Harbor onboarding and pull credentials, platform image release, Spegel P2P and prewarming |
| [acme-dns.md](./acme-dns.md) | SOP | Wildcard certificate DNS01 (acme-dns) deployment and credential rotation; **full tier only** |
| [loki-logging.md](./loki-logging.md) | Reference | Log retention definitions, LogQL troubleshooting queries, collection self-check |
| [key-rotation.md](./key-rotation.md) | SOP | Platform master key (crypto) two-key read rotation and the conditions for dropping PREVIOUS |
| [cluster-validation.md](./cluster-validation.md) | Checklist | Real-hardware validation that CI cannot cover and the per-release checklist |
| [hardware-notes.md](./hardware-notes.md) | Reference | Platform-relevant hardware facts: Grace superchips (GB10 / GB200) cannot be passed through and use unified memory, x86 IOMMU arguments per vendor, distribution and installer source baseline |

Releases are in [`../../README.md`](../../README.md); cluster install and token rotation in [`../README.md`](../README.md).

## Alert → first step

The alert rules live in `deploy/cluster/values/kps.yaml` (the `superdl.platform` and GPU rule groups); critical alerts go through the webhook + external SMTP dual channel, the optional Slack / PagerDuty / DingTalk receivers and on-call SMS are in `docs/reference/observability.md`.
Alerts with a dedicated runbook carry a `runbook_url` annotation on the rule; the first step of the others is in the summary and the table below. The `runbook_url` values in `values/kps.yaml` point at the placeholder repository `CHANGE_ME_ORG`; replace it with the repository that hosts these runbooks (`preflight.sh` refuses the placeholder).

| Alert | Meaning | First step | Docs |
|---|---|---|---|
| GPUXidCriticalError | GPU hardware fatal error | `kubectl cordon <node>`, follow the SOP | [gpu-fault-sop.md](./gpu-fault-sop.md) |
| GPUHighTemperature | GPU >85°C for 5 minutes | Check room cooling; cordon and observe if it persists | [gpu-fault-sop.md](./gpu-fault-sop.md) |
| NodeGPUUnavailable | GPU node NotReady | Check the node; running instances are judged node_lost by the reconciler and billing stops | `docs/reference/orchestrator.md`, `docs/reference/nodes.md` |
| SharedPoolUtilSaturated | Shared pool saturated continuously | Review the SKU's oversell parameters and capacity | `docs/reference/catalog.md` |
| CephUnhealthy | Ceph not HEALTH_OK for over 15 minutes | `kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph -s` | [cluster-validation.md](./cluster-validation.md) section D |
| DiskProvisionFailed | Data disk PVC create/expand dead-lettered | Check the disk-ops worker logs and the CephFS CSI; unready disks cannot be mounted | [cluster-validation.md](./cluster-validation.md) section D |
| HamiSchedulerDown | Shared pool scheduler metrics missing | Check the `hami-scheduler` Pod; shared-tier orders report CLUSTER_NOT_READY meanwhile | [cluster-validation.md](./cluster-validation.md) section C |
| CertExpiringSoon / CertExpiringCritical / CertNotReady / CertManagerMetricsMissing | Certificate renewal chain abnormal | `kubectl describe certificate`; full tier checks the DNS01 delegation and the acme-dns account, light tier confirms the manually loaded wildcard certificate has not expired | [acme-dns.md](./acme-dns.md) |
| OutboxTaskDead | Orchestration task dead-lettered | Admin overview dead-letter card: read the reason, then replay or ignore (reason required) | `docs/reference/orchestrator.md` |
| OutboxTaskTimeout | Outbox task execution timed out | Find the stuck task type in the worker logs | [loki-logging.md](./loki-logging.md) query 3 |
| SettlementFailed / SettlementLagging | Settlement failed / watermark lagging | Find the failing instances in the worker logs; settlement is idempotent and can be re-run | `docs/reference/billing.md` |
| SettlementGapUnresolved | Settlement gap unresolved for over 15 minutes (DB gauge) | Admin Finance › Settlement gaps: replay or write off manually | `docs/reference/billing.md` |
| PaymentCallbackMismatch | Callback amount differs from the order | Check the finance anomaly list; keep the payload if an attack is suspected | `docs/reference/payment.md` |
| PaymentClosedOrderRescued | Callback credited after the order was closed | Check whether the local close TTL and the channel expiry are aligned | `docs/reference/payment.md` |
| PaymentRecoverFailed | Query-based recovery failed to credit one order | Check that order by hand per the error label | `docs/reference/payment.md` |
| PaymentChannelReversed | A credited order received a channel close / refund notice | The platform does not reverse automatically; write off manually in the finance anomaly list | `docs/reference/payment.md` |
| FundReconcileMismatch | Funds do not reconcile | Freeze outgoing payments (refund payouts) first, then locate the broken link by ledger id | `docs/reference/billing.md` |
| InstanceNodeLost | Instance stopped because its node was lost | Check whether to refund; the user can restart once the node recovers | `docs/reference/orchestrator.md` |
| LeakedPodsReclaimed / ReconcileLeakAborted | Leaked Pods reclaimed in bulk / reclamation circuit broken | Compare leftover Pods on the nodes with the DB records | `docs/reference/orchestrator.md` |
| ReconcileStuckInstances | Instances stuck for over 15 minutes | Intervene from the admin instance detail (force stop / release) | `docs/reference/orchestrator.md` |
| ApiHighErrorRate | API 5xx >5% for 5 minutes | Find the unhandled exceptions by request_id | [loki-logging.md](./loki-logging.md) query 2 |
| WorkerDown | Worker heartbeat missing >2 minutes | `kubectl -n superdl rollout status` on the five worker Deployments | `../../README.md`, release and migration conventions |
| PgBackupFailed / PgBackupStale | Daily backup failed / no success for over 28 h | Check the CronJob logs and the S3 credentials; the RPO is growing | [pg-backup-restore.md](./pg-backup-restore.md) |
| AuditWriteFailed | Audit row write failed | Check the DB and the audit write path; fail-open operations may leave no trace meanwhile | `docs/reference/security.md` |
| PatrolFailed | A balance patrol stage failed | Check the stage label in the worker logs | `docs/reference/billing.md` |
