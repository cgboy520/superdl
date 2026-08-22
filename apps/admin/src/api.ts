/** 数据访问层:消费 @superdl/api-client 的生成 fetcher(非手写 fetch),自建 TanStack Query hooks。 */

import {
  adminAlertsApiAdminV1AlertsGet,
  adminChangeOwnPasswordApiAdminV1MePasswordPost,
  adminCreateAdminApiAdminV1AdminsPost,
  adminListAdminsApiAdminV1AdminsGet,
  adminMeApiAdminV1MeGet,
  adminResetPasswordApiAdminV1AdminsAdminIdResetPasswordPost,
  adminUpdateAdminApiAdminV1AdminsAdminIdPatch,
  adminBackfillOrderApiAdminV1FinanceOrdersOrderNoBackfillPost,
  adminDiscardDeadTaskApiAdminV1OutboxTaskIdDiscardPost,
  adminGetPlatformConfigApiAdminV1PlatformConfigGet,
  adminClusterStatusApiAdminV1ClusterStatusGet,
  adminClusterTestConnectionApiAdminV1ClusterTestConnectionPost,
  adminGpuModelAggregatesApiAdminV1ClusterGpuModelsGet,
  adminGetPoliciesApiAdminV1PoliciesGet,
  adminTestSmsApiAdminV1PlatformConfigTestSmsPost,
  adminUpdatePlatformConfigApiAdminV1PlatformConfigPut,
  adminListDeadTasksApiAdminV1OutboxDeadGet,
  adminPaymentAnomaliesApiAdminV1FinanceAnomaliesGet,
  adminPublishAnnouncementApiAdminV1AnnouncementsPost,
  adminUpdatePoliciesApiAdminV1PoliciesPut,
  adminVerifyOrderApiAdminV1FinanceOrdersOrderNoVerifyPost,
  customFetch,
  revenueReportApiAdminV1ReportsRevenueGet,
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
  adminUnfreezeTenantApiAdminV1TenantsUserIdUnfreezePost,
  adminListAdjustmentsApiAdminV1AdjustmentsGet,
  adminListInstancesApiAdminV1InstancesGet,
  adminListNodesApiAdminV1NodesGet,
  adminNodeMetricsApiAdminV1NodesNodeNameMetricsGet,
  adminListOrdersApiAdminV1OrdersGet,
  adminListSkusApiAdminV1SkusGet,
  adminListTenantsApiAdminV1TenantsGet,
  adminTenantBillsApiAdminV1TenantsUserIdBillsGet,
  adminTenantLedgerApiAdminV1TenantsUserIdLedgerGet,
  adminLoginApiAdminV1AuthLoginPost,
  adminReviewAdjustmentApiAdminV1AdjustmentsAdjustmentIdReviewPost,
  adminUpdateSkuApiAdminV1SkusSkuIdPatch,
  oversellReportApiAdminV1ReportsOversellGet,
  reconciliationApiAdminV1ReconciliationGet,
  skuCapacityPreviewApiAdminV1SkusCapacityPreviewGet,
} from "@superdl/api-client";
import type {
  AdjustmentCreate,
  AdminListInstancesApiAdminV1InstancesGetParams,
  AdminListOrdersApiAdminV1OrdersGetParams,
  AdminListTenantsApiAdminV1TenantsGetParams,
  AdminAccountOut,
  AdminCreateRequest,
  AdminOut,
  AdminResetPasswordRequest,
  AdminSelfPasswordRequest,
  AdminUpdateRequest,
  AnnouncementCreate,
  AuditLogOut,
  CapacityPreviewOut,
  EnrollmentCommandOut,
  EnrollmentCreate,
  EnrollmentRegenerateRequest,
  EnrollmentRevokeRequest,
  ImageCreate,
  ImageDeleteRequest,
  ImageUpdate,
  LedgerEntryOut,
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
  useInfiniteQuery,
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
  AdminInstanceOut,
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
  GpuModelAggregateOut as GpuModelAggregate,
  ClusterStatusOut as ClusterStatus,
  ClusterComponentOut as ClusterComponent,
  CapacityWarningOut as CapacityWarning,
  CapacityPreviewOut as CapacityPreview,
} from "@superdl/api-client";

// ---------- 查询 hooks ----------

type MutOpts<TData, TVars> = { mutation?: UseMutationOptions<TData, unknown, TVars> };

export function useAdminSkus() {
  const queryKey = ["admin", "skus"] as const;
  const q = useQuery({ queryKey, queryFn: () => adminListSkusApiAdminV1SkusGet() });
  return { ...q, queryKey };
}

export function useClusterStatus() {
  const queryKey = ["admin", "cluster-status"] as const;
  const q = useQuery({
    queryKey,
    queryFn: () => adminClusterStatusApiAdminV1ClusterStatusGet(),
    refetchInterval: 30_000,
  });
  return { ...q, queryKey };
}

export function useTestClusterConnection(opts?: MutOpts<unknown, void>) {
  return useMutation({
    mutationFn: () => adminClusterTestConnectionApiAdminV1ClusterTestConnectionPost(),
    ...opts?.mutation,
  });
}

export function useGpuModelAggregates(options?: { enabled?: boolean }) {
  const queryKey = ["admin", "gpu-models"] as const;
  const q = useQuery({
    queryKey,
    queryFn: () => adminGpuModelAggregatesApiAdminV1ClusterGpuModelsGet(),
    enabled: options?.enabled ?? true,
  });
  return { ...q, queryKey };
}

export function useSkuCapacityPreview(
  params: {
    gpu_model: string;
    pool_label: string;
    tier: string;
    gpu_cores_pct?: number;
    oversell_cores?: string;
    vram_gb?: number;
  } | null,
) {
  return useQuery<CapacityPreviewOut>({
    queryKey: ["admin", "sku-capacity-preview", params],
    queryFn: () => skuCapacityPreviewApiAdminV1SkusCapacityPreviewGet(params!),
    enabled: params !== null,
    placeholderData: (prev) => prev,
  });
}

export function useAdminInstances(params?: AdminListInstancesApiAdminV1InstancesGetParams) {
  const queryKey = ["admin", "instances", params] as const;
  const q = useQuery({
    queryKey,
    queryFn: () => adminListInstancesApiAdminV1InstancesGet(params),
  });
  return { ...q, queryKey };
}

/** 租户列表。q = 手机号(完整号码精确,短串按后缀)。 */
export function useTenants(params?: AdminListTenantsApiAdminV1TenantsGetParams) {
  const queryKey = ["admin", "tenants", params] as const;
  const q = useQuery({
    queryKey,
    queryFn: () => adminListTenantsApiAdminV1TenantsGet(params),
  });
  return { ...q, queryKey };
}

/** 租户账单下钻:资金流水与小时账单(游标分页,与用户端同源同实现)。 */
export function useTenantLedger(userId: number | null) {
  const queryKey = ["admin", "tenant-ledger", userId] as const;
  const q = useInfiniteQuery({
    queryKey,
    enabled: userId !== null,
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) =>
      adminTenantLedgerApiAdminV1TenantsUserIdLedgerGet(userId as number, {
        limit: 50,
        ...(pageParam ? { cursor: pageParam } : {}),
      }),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
  return { ...q, queryKey };
}

export function useTenantBills(userId: number | null) {
  const queryKey = ["admin", "tenant-bills", userId] as const;
  const q = useInfiniteQuery({
    queryKey,
    enabled: userId !== null,
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) =>
      adminTenantBillsApiAdminV1TenantsUserIdBillsGet(userId as number, {
        limit: 50,
        ...(pageParam ? { cursor: pageParam } : {}),
      }),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
  return { ...q, queryKey };
}

export interface NodeGpuSeriesOut {
  index: string;
  util?: [number, number][];
  mem_used_mb?: [number, number][];
  temp?: [number, number][];
}
export interface NodeMetricsOut {
  available: boolean;
  range: string;
  gpus: NodeGpuSeriesOut[];
  xid_count_24h: number;
  grafana_url?: string | null;
}

export function useNodeMetrics(nodeName: string | null, range: string) {
  return useQuery({
    queryKey: ["admin", "node-metrics", nodeName, range],
    queryFn: async () =>
      (await adminNodeMetricsApiAdminV1NodesNodeNameMetricsGet(nodeName ?? "", {
        range,
      })) as unknown as NodeMetricsOut,
    enabled: Boolean(nodeName),
    refetchInterval: 30_000,
    retry: 0,
  });
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
  const queryKey = ["admin", "oversell"] as const;
  const q = useQuery({ queryKey, queryFn: () => oversellReportApiAdminV1ReportsOversellGet() });
  return { ...q, queryKey };
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

/** 充值订单。order_no 为精确匹配。 */
export function useOrders(params?: AdminListOrdersApiAdminV1OrdersGetParams) {
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

/** 审计检索:游标翻页(响应是数组;满页即还有更早,游标=末行 id 的 base64)。 */
export interface AuditFilters {
  actor_type?: string;
  actor_id?: string;
  q?: string;
  since?: string;
  until?: string;
  limit?: number;
}

export const AUDIT_DEFAULT_LIMIT = 100;

export function useAuditLog(filters: AuditFilters) {
  const limit = filters.limit ?? AUDIT_DEFAULT_LIMIT;
  const queryKey = ["admin", "audit", filters] as const;
  const q = useInfiniteQuery({
    queryKey,
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) => {
      const sp = new URLSearchParams();
      for (const [k, v] of Object.entries(filters)) {
        if (v !== undefined && v !== "") sp.set(k, String(v));
      }
      if (pageParam) sp.set("cursor", pageParam);
      const suffix = sp.toString();
      return customFetch<AuditLogOut[]>(`/api/admin/v1/audit${suffix ? `?${suffix}` : ""}`, {
        method: "GET",
      });
    },
    // 后端数组不按 Page 包装:满页视为还有更早,游标取末行 id
    getNextPageParam: (last) =>
      last.length >= limit ? btoa(String(last[last.length - 1]!.id)) : undefined,
  });
  return { ...q, queryKey };
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

export function useUpdateSku(
  opts?: MutOpts<unknown, { skuId: number; data: SkuUpdate; force?: boolean }>,
) {
  return useMutation({
    mutationFn: (v: { skuId: number; data: SkuUpdate; force?: boolean }) =>
      adminUpdateSkuApiAdminV1SkusSkuIdPatch(v.skuId, v.data, v.force ? { force: true } : undefined),
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
        v.idempotencyKey ? { "Idempotency-Key": v.idempotencyKey } : undefined,
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

/** 冻结响应:比生成契约多 instances_stopped(本批新增,orval 重生成后回收手写类型)。 */
export interface TenantStatusOut {
  id: number;
  status: string;
  /** 仅冻结时返回:本次一并停掉的 running 实例台数 */
  instances_stopped?: number | null;
}

export function useFreezeTenant() {
  return useMutation({
    mutationFn: (v: { userId: number; data: TenantFreezeRequest }) =>
      customFetch<TenantStatusOut>(`/api/admin/v1/tenants/${v.userId}/freeze`, {
        method: "POST",
        body: JSON.stringify(v.data),
      }),
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

export function useDeadTasks(options?: { enabled?: boolean }) {
  const queryKey = ["admin", "outbox-dead"] as const;
  const q = useQuery({
    queryKey,
    queryFn: () => adminListDeadTasksApiAdminV1OutboxDeadGet(),
    enabled: options?.enabled ?? true,
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

/** 重放死信:需原因(与忽略对齐,本批起生成契约已过期,手写 fetcher)。 */
export function useRetryDeadTask() {
  return useMutation({
    mutationFn: (v: { taskId: number; data: { reason: string } }) =>
      customFetch<{ id: number; status: string }>(`/api/admin/v1/outbox/${v.taskId}/retry`, {
        method: "POST",
        body: JSON.stringify(v.data),
      }),
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
    // 表单页必须禁用全局 60s 轮询,否则编辑中的表单会被刷新覆盖
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

// ---------- 管理员账号 ----------

export function useAdminAccounts() {
  const queryKey = ["admin", "admins"] as const;
  const q = useQuery({ queryKey, queryFn: () => adminListAdminsApiAdminV1AdminsGet() });
  return { ...q, queryKey };
}

export function useCreateAdminAccount(opts?: MutOpts<AdminAccountOut, { data: AdminCreateRequest }>) {
  return useMutation({
    mutationFn: (v: { data: AdminCreateRequest }) => adminCreateAdminApiAdminV1AdminsPost(v.data),
    ...opts?.mutation,
  });
}

export function useUpdateAdminAccount(
  opts?: MutOpts<AdminAccountOut, { id: number; data: AdminUpdateRequest }>,
) {
  return useMutation({
    mutationFn: (v: { id: number; data: AdminUpdateRequest }) =>
      adminUpdateAdminApiAdminV1AdminsAdminIdPatch(v.id, v.data),
    ...opts?.mutation,
  });
}

export function useResetAdminPassword(
  opts?: MutOpts<AdminAccountOut, { id: number; data: AdminResetPasswordRequest }>,
) {
  return useMutation({
    mutationFn: (v: { id: number; data: AdminResetPasswordRequest }) =>
      adminResetPasswordApiAdminV1AdminsAdminIdResetPasswordPost(v.id, v.data),
    ...opts?.mutation,
  });
}

export function useChangeOwnPassword(opts?: MutOpts<void, { data: AdminSelfPasswordRequest }>) {
  return useMutation({
    mutationFn: (v: { data: AdminSelfPasswordRequest }) =>
      adminChangeOwnPasswordApiAdminV1MePasswordPost(v.data),
    ...opts?.mutation,
  });
}

// ---------- 本批新端点(orval 重生成前的手写 fetcher;统一 customFetch,生成后回收) ----------

/** 路由守卫用:/me 校准角色(角色只信服务端响应)。 */
export function fetchAdminMe(): Promise<AdminOut> {
  return adminMeApiAdminV1MeGet();
}

export interface OverviewPoolOut {
  pool: string;
  /** 物理卡(含非 Ready 节点) */
  gpu_total: number;
  gpu_used: number;
  ready_gpu_total: number;
}

export interface OverviewOut {
  /** 非终态分状态计数(不含 released) */
  instances_by_status: Record<string, number>;
  tenants_total: number;
  paying_tenants: number;
  nodes_total: number;
  nodes_ready: number;
  nodes_missing: number;
  pools: OverviewPoolOut[];
}

/** 总览聚合:精确 COUNT(全角色可读),替代在截断列表里数数。 */
export function useOverview() {
  const queryKey = ["admin", "overview"] as const;
  const q = useQuery({
    queryKey,
    queryFn: () => customFetch<OverviewOut>("/api/admin/v1/overview", { method: "GET" }),
  });
  return { ...q, queryKey };
}

export interface AdjustContextOut {
  user_id: number;
  phone_masked: string;
  status: string;
  balance: string;
  running_instances: number;
  recent_ledger: LedgerEntryOut[];
}

/** 调账前置上下文:回显租户身份与资金现状;不存在 → 404(调用方据 error 阻止提交)。 */
export function useAdjustContext(userId: number | null) {
  return useQuery({
    queryKey: ["admin", "adjust-context", userId],
    enabled: userId !== null,
    retry: 0,
    staleTime: 30_000,
    queryFn: () =>
      customFetch<AdjustContextOut>(`/api/admin/v1/tenants/${userId}/adjust-context`, {
        method: "GET",
      }),
  });
}

export interface SkuImpactOut {
  sku_id: number;
  active_instances: number;
  active_users: number;
  active_gpus: number;
}

/** 改价影响面:该 SKU 当前活跃实例数/用户数/卡数。 */
export function useSkuImpact(skuId: number | null) {
  return useQuery({
    queryKey: ["admin", "sku-impact", skuId],
    enabled: skuId !== null,
    staleTime: 30_000,
    queryFn: () =>
      customFetch<SkuImpactOut>(`/api/admin/v1/skus/${skuId}/impact`, { method: "GET" }),
  });
}
