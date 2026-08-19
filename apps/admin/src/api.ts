/**
 * 数据访问层:基于 @superdl/api-client 的生成 fetcher 自建 TanStack Query hooks。
 *
 * 说明:当前 orval 配置对 GET/POST 的 hook 变体生成有误(GET 出成 mutation),
 * 故此处直接消费生成的强类型 fetcher 函数(仍是生成 client,非手写 fetch),
 * hook 形状对齐 orval 惯例({ data } / { skuId, data } 变量、mutation options)。
 */

import {
  adminAlertsApiAdminV1AlertsGet,
  adminBackfillOrderApiAdminV1FinanceOrdersOrderNoBackfillPost,
  adminDiscardDeadTaskApiAdminV1OutboxTaskIdDiscardPost,
  adminGetPlatformConfigApiAdminV1PlatformConfigGet,
  adminGetPoliciesApiAdminV1PoliciesGet,
  adminTestSmsApiAdminV1PlatformConfigTestSmsPost,
  adminUpdatePlatformConfigApiAdminV1PlatformConfigPut,
  adminListDeadTasksApiAdminV1OutboxDeadGet,
  adminPaymentAnomaliesApiAdminV1FinanceAnomaliesGet,
  adminPublishAnnouncementApiAdminV1AnnouncementsPost,
  adminRetryDeadTaskApiAdminV1OutboxTaskIdRetryPost,
  adminUpdatePoliciesApiAdminV1PoliciesPut,
  adminVerifyOrderApiAdminV1FinanceOrdersOrderNoVerifyPost,
  revenueReportApiAdminV1ReportsRevenueGet,
  adminAuditLogApiAdminV1AuditGet,
  adminCreateAdjustmentApiAdminV1AdjustmentsPost,
  adminCreateSkuApiAdminV1SkusPost,
  adminForceStopApiAdminV1InstancesUuidForceStopPost,
  adminFreezeTenantApiAdminV1TenantsUserIdFreezePost,
  adminListAdjustmentsApiAdminV1AdjustmentsGet,
  adminListInstancesApiAdminV1InstancesGet,
  adminListNodesApiAdminV1NodesGet,
  adminListOrdersApiAdminV1OrdersGet,
  adminListSkusApiAdminV1SkusGet,
  adminListTenantsApiAdminV1TenantsGet,
  adminLoginApiAdminV1AuthLoginPost,
  adminReviewAdjustmentApiAdminV1AdjustmentsAdjustmentIdReviewPost,
  adminUnfreezeTenantApiAdminV1TenantsUserIdUnfreezePost,
  adminUpdateSkuApiAdminV1SkusSkuIdPatch,
  oversellReportApiAdminV1ReportsOversellGet,
  reconciliationApiAdminV1ReconciliationGet,
} from "@superdl/api-client";
import type {
  AdjustmentCreate,
  AnnouncementCreate,
  OrderBackfillRequest,
  OutboxDiscardRequest,
  PlatformConfigUpdateRequest,
  PolicyUpdateRequest,
  SmsTestRequest,
  AdjustmentReview,
  AdminForceStopRequest,
  AdminLoginRequest,
  AdminToken,
  SkuCreate,
  SkuUpdate,
  TenantFreezeRequest,
} from "@superdl/api-client";
import {
  useMutation,
  useQuery,
  type UseMutationOptions,
} from "@tanstack/react-query";

export { isApiError } from "@superdl/api-client";
export type { ApiError, InstanceOut, SkuAdminOut, SkuCreate, SkuUpdate } from "@superdl/api-client";

// ---------- 精确行类型(后端 dict 端点) ----------

export interface TenantRow {
  id: number;
  phone_masked: string;
  status: string;
  balance: string;
  total_consumed: string;
  instances: number;
  disk_gb: number;
  created_at: string;
}

export interface NodeRow {
  name: string;
  pool_label: string;
  gpu_model: string;
  gpu_total: number;
  gpu_used: number;
  status: string;
}

export interface OversellRow {
  pool: string;
  physical_gpus: number;
  sold_share: number;
  oversell_ratio: number;
  util_avg_24h: number | null;
}

export interface ReconciliationReport {
  day: string;
  billed_total: string;
  estimated_total: string;
  diff_pct: number;
  outliers: { instance_id: number; billed: string; estimated: string; diff_pct: number }[];
}

export interface AdjustmentRow {
  id: number;
  user_id: number;
  amount: string;
  reason: string;
  status: string;
  created_by: number;
  reviewed_by: number | null;
  review_comment: string | null;
  created_at: string;
}

export interface AuditRow {
  id: number;
  actor_type: string;
  actor_id: string | null;
  action: string;
  target: string | null;
  ip: string | null;
  result: number;
  created_at: string;
}

export interface AlertRow {
  id: number;
  type: string;
  title: string;
  content: string;
  severity: string;
  created_at: string;
}

export interface OrderRow {
  order_no: string;
  amount: string;
  channel: string;
  status: string;
  qr_url: string | null;
  expires_at: string;
  created_at: string;
  user_id: number;
}

export interface AnomalyRow {
  kind: "lost_callback" | "closed_order" | "negative_balance";
  order_no: string | null;
  user_id: number;
  amount: string;
  detail: string;
  created_at: string;
}

export interface DeadTaskRow {
  id: number;
  type: string;
  payload: Record<string, unknown>;
  retries: number;
  last_error: string | null;
  created_at: string;
  updated_at: string;
}

export interface RevenueReport {
  today_revenue: string;
  yesterday_revenue: string;
  month_revenue: string;
  today_signups: number;
  yesterday_signups: number;
}

export interface PoliciesAdminView {
  effective: Record<string, string>;
  overrides: Record<string, string>;
  specs: Record<string, { kind: string; min: string; max: string }>;
}

export interface OrderVerifyResult {
  order_no: string;
  order_status: string;
  order_amount: string;
  channel_status: string;
  channel_txn_id: string | null;
  channel_amount: string | null;
  matches: boolean;
}

// ---------- 查询 hooks ----------

type MutOpts<TData, TVars> = { mutation?: UseMutationOptions<TData, unknown, TVars> };

export function useAdminSkus() {
  const queryKey = ["admin", "skus"] as const;
  const q = useQuery({ queryKey, queryFn: () => adminListSkusApiAdminV1SkusGet() });
  return { ...q, queryKey };
}

export function useAdminInstances(params?: { status?: string; user_id?: number }) {
  const queryKey = ["admin", "instances", params] as const;
  const q = useQuery({
    queryKey,
    queryFn: () => adminListInstancesApiAdminV1InstancesGet(params),
  });
  return { ...q, queryKey };
}

export function useTenants() {
  const queryKey = ["admin", "tenants"] as const;
  const q = useQuery({
    queryKey,
    queryFn: async () => (await adminListTenantsApiAdminV1TenantsGet()) as unknown as TenantRow[],
  });
  return { ...q, queryKey };
}

export function useNodes() {
  return useQuery({
    queryKey: ["admin", "nodes"],
    queryFn: async () => (await adminListNodesApiAdminV1NodesGet()) as unknown as NodeRow[],
  });
}

export function useOversellReport() {
  return useQuery({
    queryKey: ["admin", "oversell"],
    queryFn: async () =>
      (await oversellReportApiAdminV1ReportsOversellGet()) as unknown as OversellRow[],
  });
}

export function useReconciliation(day: string) {
  return useQuery({
    queryKey: ["admin", "reconciliation", day],
    queryFn: async () =>
      (await reconciliationApiAdminV1ReconciliationGet({
        day,
      })) as unknown as ReconciliationReport,
  });
}

export function useAlerts(options?: { refetchInterval?: number }) {
  return useQuery({
    queryKey: ["admin", "alerts"],
    queryFn: async () => (await adminAlertsApiAdminV1AlertsGet()) as unknown as AlertRow[],
    refetchInterval: options?.refetchInterval,
  });
}

export function useOrders(params?: { status?: string }) {
  const queryKey = ["admin", "orders", params] as const;
  const q = useQuery({
    queryKey,
    queryFn: async () =>
      (await adminListOrdersApiAdminV1OrdersGet(params)) as unknown as OrderRow[],
  });
  return { ...q, queryKey };
}

export function useAdjustments() {
  const queryKey = ["admin", "adjustments"] as const;
  const q = useQuery({
    queryKey,
    queryFn: async () =>
      (await adminListAdjustmentsApiAdminV1AdjustmentsGet()) as unknown as AdjustmentRow[],
  });
  return { ...q, queryKey };
}

export function useAuditLog(params?: { actor_type?: string }) {
  return useQuery({
    queryKey: ["admin", "audit", params],
    queryFn: async () => (await adminAuditLogApiAdminV1AuditGet(params)) as unknown as AuditRow[],
  });
}

// ---------- 变更 hooks ----------

export function useAdminLogin(opts?: MutOpts<AdminToken, { data: AdminLoginRequest }>) {
  return useMutation({
    mutationFn: (v: { data: AdminLoginRequest }) => adminLoginApiAdminV1AuthLoginPost(v.data),
    ...opts?.mutation,
  });
}

export function useCreateSku(opts?: MutOpts<unknown, { data: SkuCreate }>) {
  return useMutation({
    mutationFn: (v: { data: SkuCreate }) => adminCreateSkuApiAdminV1SkusPost(v.data),
    ...opts?.mutation,
  });
}

export function useUpdateSku(opts?: MutOpts<unknown, { skuId: number; data: SkuUpdate }>) {
  return useMutation({
    mutationFn: (v: { skuId: number; data: SkuUpdate }) =>
      adminUpdateSkuApiAdminV1SkusSkuIdPatch(v.skuId, v.data),
    ...opts?.mutation,
  });
}

export function useForceStop() {
  return useMutation({
    mutationFn: (v: { uuid: string; data: AdminForceStopRequest }) =>
      adminForceStopApiAdminV1InstancesUuidForceStopPost(v.uuid, v.data),
  });
}

export function useFreezeTenant() {
  return useMutation({
    mutationFn: (v: { userId: number; data: TenantFreezeRequest }) =>
      adminFreezeTenantApiAdminV1TenantsUserIdFreezePost(v.userId, v.data),
  });
}

export function useUnfreezeTenant() {
  return useMutation({
    mutationFn: (v: { userId: number; data: TenantFreezeRequest }) =>
      adminUnfreezeTenantApiAdminV1TenantsUserIdUnfreezePost(v.userId, v.data),
  });
}

export function useCreateAdjustment(opts?: MutOpts<unknown, { data: AdjustmentCreate }>) {
  return useMutation({
    mutationFn: (v: { data: AdjustmentCreate }) =>
      adminCreateAdjustmentApiAdminV1AdjustmentsPost(v.data),
    ...opts?.mutation,
  });
}

export function useReviewAdjustment(
  opts?: MutOpts<unknown, { adjustmentId: number; data: AdjustmentReview }>,
) {
  return useMutation({
    mutationFn: (v: { adjustmentId: number; data: AdjustmentReview }) =>
      adminReviewAdjustmentApiAdminV1AdjustmentsAdjustmentIdReviewPost(v.adjustmentId, v.data),
    ...opts?.mutation,
  });
}


// ---------- WP16 运营刚需 ----------

export function useAnomalies() {
  const queryKey = ["admin", "anomalies"] as const;
  const q = useQuery({
    queryKey,
    queryFn: async () =>
      (await adminPaymentAnomaliesApiAdminV1FinanceAnomaliesGet()) as unknown as AnomalyRow[],
  });
  return { ...q, queryKey };
}

export function useDeadTasks() {
  const queryKey = ["admin", "outbox-dead"] as const;
  const q = useQuery({
    queryKey,
    queryFn: async () =>
      (await adminListDeadTasksApiAdminV1OutboxDeadGet()) as unknown as DeadTaskRow[],
  });
  return { ...q, queryKey };
}

export function useRevenueReport() {
  const tz = -new Date().getTimezoneOffset();
  return useQuery({
    queryKey: ["admin", "revenue", tz],
    queryFn: async () =>
      (await revenueReportApiAdminV1ReportsRevenueGet({
        tz_offset_minutes: tz,
      })) as unknown as RevenueReport,
  });
}

export function useAdminPolicies() {
  const queryKey = ["admin", "policies"] as const;
  const q = useQuery({
    queryKey,
    queryFn: async () =>
      (await adminGetPoliciesApiAdminV1PoliciesGet()) as unknown as PoliciesAdminView,
  });
  return { ...q, queryKey };
}

export function useVerifyOrder() {
  return useMutation({
    mutationFn: async (v: { orderNo: string }) =>
      (await adminVerifyOrderApiAdminV1FinanceOrdersOrderNoVerifyPost(
        v.orderNo,
      )) as unknown as OrderVerifyResult,
  });
}

export function useBackfillOrder() {
  return useMutation({
    mutationFn: (v: { orderNo: string; data: OrderBackfillRequest }) =>
      adminBackfillOrderApiAdminV1FinanceOrdersOrderNoBackfillPost(v.orderNo, v.data),
  });
}

export function useRetryDeadTask() {
  return useMutation({
    mutationFn: (v: { taskId: number }) => adminRetryDeadTaskApiAdminV1OutboxTaskIdRetryPost(v.taskId),
  });
}

export function useDiscardDeadTask() {
  return useMutation({
    mutationFn: (v: { taskId: number; data: OutboxDiscardRequest }) =>
      adminDiscardDeadTaskApiAdminV1OutboxTaskIdDiscardPost(v.taskId, v.data),
  });
}

export function usePublishAnnouncement(opts?: MutOpts<unknown, { data: AnnouncementCreate }>) {
  return useMutation({
    mutationFn: (v: { data: AnnouncementCreate }) =>
      adminPublishAnnouncementApiAdminV1AnnouncementsPost(v.data),
    ...opts?.mutation,
  });
}

export function useUpdatePolicies(opts?: MutOpts<unknown, { data: PolicyUpdateRequest }>) {
  return useMutation({
    mutationFn: (v: { data: PolicyUpdateRequest }) => adminUpdatePoliciesApiAdminV1PoliciesPut(v.data),
    ...opts?.mutation,
  });
}

// ---------- WP20 平台配置(渠道凭据与合规;仅 admin 角色) ----------

export interface PlatformConfigItem {
  key: string;
  group: "payment_wechat" | "payment_alipay" | "sms" | "real_name" | "compliance";
  kind: "str" | "text" | "bool" | "choice" | "secret";
  choices: string[];
  hint: string;
  source: "override" | "env" | "unset";
  configured: boolean;
  value: string | null;
  preview: string | null;
  updated_at: string | null;
}

export function usePlatformConfig() {
  const queryKey = ["admin", "platform-config"] as const;
  const q = useQuery({
    queryKey,
    queryFn: async () =>
      (await adminGetPlatformConfigApiAdminV1PlatformConfigGet()) as unknown as {
        items: PlatformConfigItem[];
      },
    // 表单页:禁用全局 60s 轮询,避免编辑中被刷新
    refetchInterval: false,
    refetchOnWindowFocus: false,
    retry: false,
  });
  return { ...q, queryKey };
}

export function useUpdatePlatformConfig(
  opts?: MutOpts<unknown, { data: PlatformConfigUpdateRequest }>,
) {
  return useMutation({
    mutationFn: (v: { data: PlatformConfigUpdateRequest }) =>
      adminUpdatePlatformConfigApiAdminV1PlatformConfigPut(v.data),
    ...opts?.mutation,
  });
}

export function useTestSms(opts?: MutOpts<unknown, { data: SmsTestRequest }>) {
  return useMutation({
    mutationFn: (v: { data: SmsTestRequest }) =>
      adminTestSmsApiAdminV1PlatformConfigTestSmsPost(v.data),
    ...opts?.mutation,
  });
}
