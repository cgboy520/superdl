/**
 * 数据访问层:消费 @superdl/api-client 的生成 fetcher,自建 TanStack Query hooks。
 * orval 把 GET 也生成为 mutation 形态的 hook,不可直接当查询用,故只取其强类型
 * fetcher 函数(仍是生成 client,非手写 fetch),hook 形状对齐 orval 惯例。
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
  adminCordonNodeApiAdminV1NodesNodeNameCordonPost,
  adminCreateEnrollmentApiAdminV1NodeEnrollmentsPost,
  adminCreateImageApiAdminV1ImagesPost,
  adminCreateSkuApiAdminV1SkusPost,
  adminDeleteImageApiAdminV1ImagesImageIdDelete,
  adminImageNodesApiAdminV1ImagesImageIdNodesGet,
  adminListEnrollmentsApiAdminV1NodeEnrollmentsGet,
  adminListImagesApiAdminV1ImagesGet,
  adminPrewarmImageApiAdminV1ImagesImageIdPrewarmPost,
  adminRegenerateEnrollmentApiAdminV1NodeEnrollmentsEnrollmentIdRegeneratePost,
  adminRevokeEnrollmentApiAdminV1NodeEnrollmentsEnrollmentIdRevokePost,
  adminUncordonNodeApiAdminV1NodesNodeNameUncordonPost,
  adminUpdateImageApiAdminV1ImagesImageIdPatch,
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
  EnrollmentCommandOut,
  EnrollmentCreate,
  EnrollmentRegenerateRequest,
  EnrollmentRevokeRequest,
  ImageCreate,
  ImageDeleteRequest,
  ImageUpdate,
  NodeCordonRequest,
  PrewarmEnqueuedOut,
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
export type {
  ApiError,
  EnrollmentCommandOut,
  EnrollmentCreate,
  ImageCreate,
  ImageUpdate,
  InstanceOut,
  SkuAdminOut,
  SkuCreate,
  SkuUpdate,
} from "@superdl/api-client";

// ---------- 行类型:全部取自生成契约,禁止手写 ----------

export type {
  AdjustmentOut as AdjustmentRow,
  AdminAlertOut as AlertRow,
  AdminImageOut as ImageRow,
  ImageNodeCacheOut as ImageNodeRow,
  NodeEnrollmentOut as EnrollmentRow,
  AdminOrderOut as OrderRow,
  AuditLogOut as AuditRow,
  DeadTaskOut as DeadTaskRow,
  NodeOut as NodeRow,
  OrderVerifyOut as OrderVerifyResult,
  OversellPoolOut as OversellRow,
  PaymentAnomalyOut as AnomalyRow,
  PlatformConfigItemOut as PlatformConfigItem,
  PoliciesAdminOut as PoliciesAdminView,
  ReconciliationOut as ReconciliationReport,
  RevenueReportOut as RevenueReport,
  TenantOut as TenantRow,
} from "@superdl/api-client";

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
    queryFn: () => adminListTenantsApiAdminV1TenantsGet(),
  });
  return { ...q, queryKey };
}

export function useNodes() {
  return useQuery({
    queryKey: ["admin", "nodes"],
    queryFn: () => adminListNodesApiAdminV1NodesGet(),
  });
}

export function useAdminImages(options?: { refetchInterval?: number }) {
  const queryKey = ["admin", "images"] as const;
  const q = useQuery({
    queryKey,
    queryFn: () => adminListImagesApiAdminV1ImagesGet(),
    refetchInterval: options?.refetchInterval,
  });
  return { ...q, queryKey };
}

export function useImageNodes(imageId: number, options?: { refetchInterval?: number }) {
  return useQuery({
    queryKey: ["admin", "images", imageId, "nodes"],
    queryFn: () => adminImageNodesApiAdminV1ImagesImageIdNodesGet(imageId),
    refetchInterval: options?.refetchInterval,
  });
}

export function useOversellReport() {
  return useQuery({
    queryKey: ["admin", "oversell"],
    queryFn: () => oversellReportApiAdminV1ReportsOversellGet(),
  });
}

export function useReconciliation(day: string) {
  return useQuery({
    queryKey: ["admin", "reconciliation", day],
    queryFn: () => reconciliationApiAdminV1ReconciliationGet({ day }),
  });
}

export function useAlerts(options?: { refetchInterval?: number }) {
  return useQuery({
    queryKey: ["admin", "alerts"],
    queryFn: () => adminAlertsApiAdminV1AlertsGet(),
    refetchInterval: options?.refetchInterval,
  });
}

export function useOrders(params?: { status?: string }) {
  const queryKey = ["admin", "orders", params] as const;
  const q = useQuery({
    queryKey,
    queryFn: () => adminListOrdersApiAdminV1OrdersGet(params),
  });
  return { ...q, queryKey };
}

export function useAdjustments() {
  const queryKey = ["admin", "adjustments"] as const;
  const q = useQuery({
    queryKey,
    queryFn: () => adminListAdjustmentsApiAdminV1AdjustmentsGet(),
  });
  return { ...q, queryKey };
}

export function useAuditLog(params?: { actor_type?: string }) {
  return useQuery({
    queryKey: ["admin", "audit", params],
    queryFn: () => adminAuditLogApiAdminV1AuditGet(params),
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

export function useEnrollments(options?: { active?: boolean; refetchInterval?: number }) {
  const queryKey = ["admin", "node-enrollments", options?.active] as const;
  const q = useQuery({
    queryKey,
    queryFn: () =>
      adminListEnrollmentsApiAdminV1NodeEnrollmentsGet(
        options?.active ? { active: true } : undefined,
      ),
    refetchInterval: options?.refetchInterval,
  });
  return { ...q, queryKey };
}

export function useCreateEnrollment(
  opts?: MutOpts<EnrollmentCommandOut, { data: EnrollmentCreate; idempotencyKey?: string }>,
) {
  return useMutation({
    mutationFn: (v: { data: EnrollmentCreate; idempotencyKey?: string }) =>
      adminCreateEnrollmentApiAdminV1NodeEnrollmentsPost(
        v.data,
        v.idempotencyKey ? { headers: { "Idempotency-Key": v.idempotencyKey } } : undefined,
      ),
    ...opts?.mutation,
  });
}

export function useRegenerateEnrollment(
  opts?: MutOpts<EnrollmentCommandOut, { enrollmentId: number; data: EnrollmentRegenerateRequest }>,
) {
  return useMutation({
    mutationFn: (v: { enrollmentId: number; data: EnrollmentRegenerateRequest }) =>
      adminRegenerateEnrollmentApiAdminV1NodeEnrollmentsEnrollmentIdRegeneratePost(
        v.enrollmentId,
        v.data,
      ),
    ...opts?.mutation,
  });
}

export function useRevokeEnrollment(
  opts?: MutOpts<unknown, { enrollmentId: number; data: EnrollmentRevokeRequest }>,
) {
  return useMutation({
    mutationFn: (v: { enrollmentId: number; data: EnrollmentRevokeRequest }) =>
      adminRevokeEnrollmentApiAdminV1NodeEnrollmentsEnrollmentIdRevokePost(v.enrollmentId, v.data),
    ...opts?.mutation,
  });
}

export function useCordonNode(
  opts?: MutOpts<unknown, { nodeName: string; on: boolean; data: NodeCordonRequest }>,
) {
  return useMutation({
    mutationFn: (v: { nodeName: string; on: boolean; data: NodeCordonRequest }) =>
      v.on
        ? adminCordonNodeApiAdminV1NodesNodeNameCordonPost(v.nodeName, v.data)
        : adminUncordonNodeApiAdminV1NodesNodeNameUncordonPost(v.nodeName, v.data),
    ...opts?.mutation,
  });
}

export function useCreateImage(opts?: MutOpts<unknown, { data: ImageCreate }>) {
  return useMutation({
    mutationFn: (v: { data: ImageCreate }) => adminCreateImageApiAdminV1ImagesPost(v.data),
    ...opts?.mutation,
  });
}

export function useUpdateImage(opts?: MutOpts<unknown, { imageId: number; data: ImageUpdate }>) {
  return useMutation({
    mutationFn: (v: { imageId: number; data: ImageUpdate }) =>
      adminUpdateImageApiAdminV1ImagesImageIdPatch(v.imageId, v.data),
    ...opts?.mutation,
  });
}

export function useDeleteImage(
  opts?: MutOpts<unknown, { imageId: number; data: ImageDeleteRequest }>,
) {
  return useMutation({
    mutationFn: (v: { imageId: number; data: ImageDeleteRequest }) =>
      adminDeleteImageApiAdminV1ImagesImageIdDelete(v.imageId, v.data),
    ...opts?.mutation,
  });
}

export function usePrewarmImage(opts?: MutOpts<PrewarmEnqueuedOut, { imageId: number }>) {
  return useMutation({
    mutationFn: (v: { imageId: number }) =>
      adminPrewarmImageApiAdminV1ImagesImageIdPrewarmPost(v.imageId),
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


// ---------- 运营:死信重放 / 收入报表 / 公告 ----------

export function useAnomalies() {
  const queryKey = ["admin", "anomalies"] as const;
  const q = useQuery({
    queryKey,
    queryFn: () => adminPaymentAnomaliesApiAdminV1FinanceAnomaliesGet(),
  });
  return { ...q, queryKey };
}

export function useDeadTasks() {
  const queryKey = ["admin", "outbox-dead"] as const;
  const q = useQuery({
    queryKey,
    queryFn: () => adminListDeadTasksApiAdminV1OutboxDeadGet(),
  });
  return { ...q, queryKey };
}

export function useRevenueReport() {
  const tz = -new Date().getTimezoneOffset();
  return useQuery({
    queryKey: ["admin", "revenue", tz],
    queryFn: () => revenueReportApiAdminV1ReportsRevenueGet({ tz_offset_minutes: tz }),
  });
}

export function useAdminPolicies() {
  const queryKey = ["admin", "policies"] as const;
  const q = useQuery({
    queryKey,
    queryFn: () => adminGetPoliciesApiAdminV1PoliciesGet(),
  });
  return { ...q, queryKey };
}

export function useVerifyOrder() {
  return useMutation({
    mutationFn: (v: { orderNo: string }) =>
      adminVerifyOrderApiAdminV1FinanceOrdersOrderNoVerifyPost(v.orderNo),
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

// ---------- 平台配置(渠道凭据与合规;仅 admin 角色) ----------

export function usePlatformConfig() {
  const queryKey = ["admin", "platform-config"] as const;
  const q = useQuery({
    queryKey,
    queryFn: () => adminGetPlatformConfigApiAdminV1PlatformConfigGet(),
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
