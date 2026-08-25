/** 数据访问层:消费 @superdl/api-client 的生成 fetcher(非手写 fetch),自建 TanStack Query hooks。 */

import {
  adminAckAlertApiAdminV1AlertsAlertIdAckPost,
  adminAlertsApiAdminV1AlertsGet,
  adminAlertsUnreadCountApiAdminV1AlertsUnreadCountGet,
  adminArchiveLegalDocVersionApiAdminV1LegalDocsVersionsVersionIdArchivePost,
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
  adminListAnnouncementsApiAdminV1AnnouncementsGet,
  adminPublishAnnouncementApiAdminV1AnnouncementsPost,
  adminRevokeAnnouncementApiAdminV1AnnouncementsAnnouncementIdRevokePost,
  adminUpdatePoliciesApiAdminV1PoliciesPut,
  adminVerifyOrderApiAdminV1FinanceOrdersOrderNoVerifyPost,
  adminAdjustContextApiAdminV1TenantsUserIdAdjustContextGet,
  adminAuditExportApiAdminV1AuditExportGet,
  adminAuditLogApiAdminV1AuditGet,
  adminFreezeTenantApiAdminV1TenantsUserIdFreezePost,
  adminOrdersExportApiAdminV1OrdersExportGet,
  adminOverviewApiAdminV1OverviewGet,
  adminRetryDeadTaskApiAdminV1OutboxTaskIdRetryPost,
  adminSkuImpactApiAdminV1SkusSkuIdImpactGet,
  adminTenantLedgerExportApiAdminV1TenantsUserIdLedgerExportGet,
  reconciliationExportApiAdminV1ReconciliationExportGet,
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
  adminPortPoolStatsApiAdminV1NodesPortPoolGet,
  adminListOrdersApiAdminV1OrdersGet,
  adminListRefundsApiAdminV1RefundsGet,
  adminReviewRefundApiAdminV1RefundsRefundIdReviewPost,
  adminPayoutRefundApiAdminV1RefundsRefundIdPayoutPost,
  adminCancelRefundApiAdminV1RefundsRefundIdCancelPost,
  adminListInvoicesApiAdminV1InvoicesGet,
  adminIssueInvoiceApiAdminV1InvoicesInvoiceIdIssuePost,
  adminRejectInvoiceApiAdminV1InvoicesInvoiceIdRejectPost,
  adminListSettlementGapsApiAdminV1FinanceSettlementGapsGet,
  adminReplaySettlementGapApiAdminV1FinanceSettlementGapsGapIdReplayPost,
  adminResolveSettlementGapApiAdminV1FinanceSettlementGapsGapIdResolvePost,
  adminGetTicketApiAdminV1TicketsTicketIdGet,
  adminListDeletionRequestsApiAdminV1DeletionRequestsGet,
  adminApproveDeletionApiAdminV1DeletionRequestsRequestIdApprovePost,
  adminRejectDeletionApiAdminV1DeletionRequestsRequestIdRejectPost,
  adminListTicketsApiAdminV1TicketsGet,
  adminReplyTicketApiAdminV1TicketsTicketIdReplyPost,
  adminUpdateTicketStatusApiAdminV1TicketsTicketIdStatusPost,
  adminListSkusApiAdminV1SkusGet,
  adminListTenantsApiAdminV1TenantsGet,
  adminGetTenantQuotaApiAdminV1TenantsUserIdQuotaGet,
  adminSetTenantQuotaApiAdminV1TenantsUserIdQuotaPut,
  adminListInstanceEventsApiAdminV1InstancesUuidEventsGet,
  adminTenantBillsApiAdminV1TenantsUserIdBillsGet,
  adminTenantLedgerApiAdminV1TenantsUserIdLedgerGet,
  adminLoginApiAdminV1AuthLoginPost,
  adminReviewAdjustmentApiAdminV1AdjustmentsAdjustmentIdReviewPost,
  adminUpdateSkuApiAdminV1SkusSkuIdPatch,
  adminListLegalDocsApiAdminV1LegalDocsGet,
  adminListLegalDocVersionsApiAdminV1LegalDocsDocKeyVersionsGet,
  adminPublishLegalDocVersionApiAdminV1LegalDocsVersionsVersionIdPublishPost,
  adminUpdateLegalDocVersionApiAdminV1LegalDocsVersionsVersionIdPut,
  adminCreateLegalDocVersionApiAdminV1LegalDocsDocKeyVersionsPost,
  mfaLoginVerifyApiAdminV1AuthLoginMfaPost,
  mfaResetApiAdminV1AdminsAdminIdMfaResetPost,
  mfaSetupBeginApiAdminV1AuthMfaSetupBeginPost,
  mfaSetupConfirmApiAdminV1AuthMfaSetupConfirmPost,
  mfaRegenerateRecoveryCodesApiAdminV1MeMfaRecoveryCodesPost,
  oversellReportApiAdminV1ReportsOversellGet,
  reconciliationApiAdminV1ReconciliationGet,
  skuCapacityPreviewApiAdminV1SkusCapacityPreviewGet,
} from "@superdl/api-client";
import type {
  AdjustmentCreate,
  AdminAlertsApiAdminV1AlertsGetParams,
  AdminAuditExportApiAdminV1AuditExportGetParams,
  AdminAuditLogApiAdminV1AuditGetParams,
  AdminDeletionReject,
  AdminDeletionRequestOut,
  AdminListAdjustmentsApiAdminV1AdjustmentsGetParams,
  AdminListDeletionRequestsApiAdminV1DeletionRequestsGetParams,
  AdminListInstancesApiAdminV1InstancesGetParams,
  AdminListInvoicesApiAdminV1InvoicesGetParams,
  AdminListOrdersApiAdminV1OrdersGetParams,
  AdminListRefundsApiAdminV1RefundsGetParams,
  AdminListSettlementGapsApiAdminV1FinanceSettlementGapsGetParams,
  AdminListTenantsApiAdminV1TenantsGetParams,
  AdminListTicketsApiAdminV1TicketsGetParams,
  AdminOrdersExportApiAdminV1OrdersExportGetParams,
  AdminSettlementGapOut,
  AdminTicketDetailOut,
  AdminTicketReply,
  AdminTicketStatusUpdate,
  AdminAccountOut,
  AdminCreateRequest,
  AdminOut,
  AdminResetPasswordRequest,
  AdminSelfPasswordRequest,
  AdminUpdateRequest,
  AnnouncementCreate,
  AnnouncementOut,
  AnnouncementResultOut,
  AnnouncementRevoke,
  CapacityPreviewOut,
  EnrollmentCommandOut,
  EnrollmentCreate,
  EnrollmentRegenerateRequest,
  EnrollmentRevokeRequest,
  ImageCreate,
  ImageDeleteRequest,
  ImageUpdate,
  NodeCordonRequest,
  OutboxRetryRequest,
  PrewarmEnqueuedOut,
  OrderBackfillRequest,
  OutboxDiscardRequest,
  PlatformConfigUpdateRequest,
  PolicyUpdateRequest,
  ReconciliationExportApiAdminV1ReconciliationExportGetParams,
  SmsTestOut,
  SmsTestRequest,
  AdjustmentReview,
  InvoiceIssue,
  InvoiceReject,
  LegalDocVersionCreate,
  LegalDocVersionOut,
  LegalDocVersionUpdate,
  RefundCancel,
  RefundPayout,
  RefundReview,
  AdminForceStopRequest,
  AdminLoginRequest,
  AdminToken,
  MfaChallengeOut,
  MfaLoginOut,
  MfaSetupConfirmOut,
  MfaSetupOut,
  RecoveryCodesOut,
  SettlementGapResolve,
  SkuCreate,
  SkuUpdate,
  TenantFreezeRequest,
  TenantQuotaUpdate,
  UpdatedKeysOut,
} from "@superdl/api-client";
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  type UseMutationOptions,
} from "@tanstack/react-query";

import { downloadCsv } from "./lib/csv";

export { isApiError } from "@superdl/api-client";
export type {
  ApiError,
  EnrollmentCommandOut,
  EnrollmentCreate,
  ImageCreate,
  ImageUpdate,
  AdminInstanceOut,
  InstanceOut,
  NodeMetricsOut,
  OverviewOut,
  SkuAdminOut,
  SkuCreate,
  SkuUpdate,
} from "@superdl/api-client";

// ---------- 行类型:全部取自生成契约,禁止手写 ----------

export type {
  AdjustmentOut as AdjustmentRow,
  AdminAlertOut as AlertRow,
  AdminDeletionRequestOut as DeletionRow,
  AnnouncementOut as AnnouncementRow,
  AdminImageOut as ImageRow,
  ImageNodeCacheOut as ImageNodeRow,
  NodeEnrollmentOut as EnrollmentRow,
  AdminInvoiceOut as InvoiceRow,
  AdminOrderOut as OrderRow,
  AdminRefundOut as RefundRow,
  AdminSettlementGapOut,
  AdminTicketDetailOut as TicketDetail,
  AdminTicketOut as TicketRow,
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
  TenantQuotaOut as TenantQuota,
  InstanceEventOut as InstanceEvent,
  GpuModelAggregateOut as GpuModelAggregate,
  ClusterStatusOut as ClusterStatus,
  ClusterComponentOut as ClusterComponent,
  CapacityWarningOut as CapacityWarning,
  CapacityPreviewOut as CapacityPreview,
  RefundPayout,
  RefundReview,
  RefundCancel,
  InvoiceIssue,
  InvoiceReject,
  SettlementGapResolve,
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

/** 管理端实例列表(游标分页):status/user_id/q/node_name 服务端过滤,「加载更多」向下翻页。 */
export function useAdminInstances(
  params?: Omit<AdminListInstancesApiAdminV1InstancesGetParams, "cursor" | "limit">,
  options?: { enabled?: boolean; limit?: number },
) {
  const limit = options?.limit ?? 50;
  const queryKey = ["admin", "instances", params, limit] as const;
  const q = useInfiniteQuery({
    queryKey,
    enabled: options?.enabled ?? true,
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) =>
      adminListInstancesApiAdminV1InstancesGet({
        ...params,
        limit,
        ...(pageParam ? { cursor: pageParam } : {}),
      }),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
  return { ...q, queryKey };
}

/** 租户列表(游标分页)。q = 手机号(完整号码精确,短串按后缀);纯数字额外按 id 命中首页。 */
export function useTenants(params?: Omit<AdminListTenantsApiAdminV1TenantsGetParams, "cursor" | "limit">) {
  const queryKey = ["admin", "tenants", params] as const;
  const q = useInfiniteQuery({
    queryKey,
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) =>
      adminListTenantsApiAdminV1TenantsGet({
        ...params,
        limit: 50,
        ...(pageParam ? { cursor: pageParam } : {}),
      }),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
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

/** 租户小时账单;instanceId 非空时按实例过滤(排障:只盯一台机的账)。 */
export function useTenantBills(userId: number | null, instanceId?: number | null) {
  const queryKey = ["admin", "tenant-bills", userId, instanceId ?? null] as const;
  const q = useInfiniteQuery({
    queryKey,
    enabled: userId !== null,
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) =>
      adminTenantBillsApiAdminV1TenantsUserIdBillsGet(userId as number, {
        limit: 50,
        ...(instanceId ? { instance_id: instanceId } : {}),
        ...(pageParam ? { cursor: pageParam } : {}),
      }),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
  return { ...q, queryKey };
}

/** 租户配额覆盖 + 生效值(抽屉「配额」Tab)。 */
export function useTenantQuota(userId: number | null) {
  const queryKey = ["admin", "tenant-quota", userId] as const;
  const q = useQuery({
    queryKey,
    enabled: userId !== null,
    queryFn: () => adminGetTenantQuotaApiAdminV1TenantsUserIdQuotaGet(userId as number),
  });
  return { ...q, queryKey };
}

export function useSetTenantQuota() {
  return useMutation({
    mutationFn: (v: { userId: number; data: TenantQuotaUpdate }) =>
      adminSetTenantQuotaApiAdminV1TenantsUserIdQuotaPut(v.userId, v.data),
  });
}

/** 实例事件时间线(排障;管理端不限租户,游标分页)。 */
export function useInstanceEvents(uuid: string | null) {
  const queryKey = ["admin", "instance-events", uuid] as const;
  const q = useInfiniteQuery({
    queryKey,
    enabled: uuid !== null,
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) =>
      adminListInstanceEventsApiAdminV1InstancesUuidEventsGet(uuid as string, {
        limit: 50,
        ...(pageParam ? { cursor: pageParam } : {}),
      }),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
  return { ...q, queryKey };
}

export function useNodeMetrics(nodeName: string | null, range: string) {
  return useQuery({
    queryKey: ["admin", "node-metrics", nodeName, range],
    queryFn: () =>
      adminNodeMetricsApiAdminV1NodesNodeNameMetricsGet(nodeName ?? "", {
        range,
      }),
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

export function usePortPool() {
  return useQuery({
    queryKey: ["admin", "port-pool"],
    queryFn: () => adminPortPoolStatsApiAdminV1NodesPortPoolGet(),
    refetchInterval: 60_000,
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

/** 告警流:severity 服务端过滤。 */
export function useAlerts(
  params?: AdminAlertsApiAdminV1AlertsGetParams,
  options?: { refetchInterval?: number },
) {
  const queryKey = ["admin", "alerts", params] as const;
  const q = useQuery({
    queryKey,
    queryFn: () => adminAlertsApiAdminV1AlertsGet(params),
    refetchInterval: options?.refetchInterval,
  });
  return { ...q, queryKey };
}

/** 未确认告警数(顶栏铃铛角标;独立计数端点,不用当页长度推算)。 */
export function useAlertUnreadCount(options?: { refetchInterval?: number }) {
  return useQuery({
    queryKey: ["admin", "alerts", "unread-count"],
    queryFn: () => adminAlertsUnreadCountApiAdminV1AlertsUnreadCountGet(),
    refetchInterval: options?.refetchInterval,
  });
}

export function useAckAlert(opts?: MutOpts<unknown, { alertId: number }>) {
  return useMutation({
    mutationFn: (v: { alertId: number }) =>
      adminAckAlertApiAdminV1AlertsAlertIdAckPost(v.alertId),
    ...opts?.mutation,
  });
}

/** 充值订单(游标分页)。order_no 精确;day=YYYY-MM-DD 按下单日(UTC)过滤。 */
export function useOrders(
  params?: Omit<AdminListOrdersApiAdminV1OrdersGetParams, "cursor" | "limit">,
  options?: { enabled?: boolean },
) {
  const queryKey = ["admin", "orders", params] as const;
  const q = useInfiniteQuery({
    queryKey,
    enabled: options?.enabled ?? true,
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) =>
      adminListOrdersApiAdminV1OrdersGet({
        ...params,
        limit: 50,
        ...(pageParam ? { cursor: pageParam } : {}),
      }),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
  return { ...q, queryKey };
}

/** 调账单(游标分页):status/user_id/day 服务端过滤。 */
export function useAdjustments(
  params?: Omit<AdminListAdjustmentsApiAdminV1AdjustmentsGetParams, "cursor" | "limit">,
) {
  const queryKey = ["admin", "adjustments", params] as const;
  const q = useInfiniteQuery({
    queryKey,
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) =>
      adminListAdjustmentsApiAdminV1AdjustmentsGet({
        ...params,
        limit: 50,
        ...(pageParam ? { cursor: pageParam } : {}),
      }),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
  return { ...q, queryKey };
}

/** 退款单列表(游标分页)。status 服务端过滤;day=YYYY-MM-DD(UTC 日,与对账口径一致)。 */
export function useRefunds(
  params?: Omit<AdminListRefundsApiAdminV1RefundsGetParams, "cursor" | "limit">,
) {
  const queryKey = ["admin", "refunds", params] as const;
  const q = useInfiniteQuery({
    queryKey,
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) =>
      adminListRefundsApiAdminV1RefundsGet({
        ...params,
        limit: 50,
        ...(pageParam ? { cursor: pageParam } : {}),
      }),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
  return { ...q, queryKey };
}

export function useReviewRefund(
  opts?: MutOpts<unknown, { refundId: number; data: RefundReview }>,
) {
  return useMutation({
    mutationFn: (v: { refundId: number; data: RefundReview }) =>
      adminReviewRefundApiAdminV1RefundsRefundIdReviewPost(v.refundId, v.data),
    ...opts?.mutation,
  });
}

export function usePayoutRefund(
  opts?: MutOpts<unknown, { refundId: number; data: RefundPayout }>,
) {
  return useMutation({
    mutationFn: (v: { refundId: number; data: RefundPayout }) =>
      adminPayoutRefundApiAdminV1RefundsRefundIdPayoutPost(v.refundId, v.data),
    ...opts?.mutation,
  });
}

export function useCancelRefund(
  opts?: MutOpts<unknown, { refundId: number; data: RefundCancel }>,
) {
  return useMutation({
    mutationFn: (v: { refundId: number; data: RefundCancel }) =>
      adminCancelRefundApiAdminV1RefundsRefundIdCancelPost(v.refundId, v.data),
    ...opts?.mutation,
  });
}

/** 发票申请列表。status/period(YYYY-MM)服务端过滤。 */
export function useInvoices(params?: AdminListInvoicesApiAdminV1InvoicesGetParams) {
  const queryKey = ["admin", "invoices", params] as const;
  const q = useQuery({
    queryKey,
    queryFn: () => adminListInvoicesApiAdminV1InvoicesGet(params),
  });
  return { ...q, queryKey };
}

export function useIssueInvoice(
  opts?: MutOpts<unknown, { invoiceId: number; data: InvoiceIssue }>,
) {
  return useMutation({
    mutationFn: (v: { invoiceId: number; data: InvoiceIssue }) =>
      adminIssueInvoiceApiAdminV1InvoicesInvoiceIdIssuePost(v.invoiceId, v.data),
    ...opts?.mutation,
  });
}

export function useRejectInvoice(
  opts?: MutOpts<unknown, { invoiceId: number; data: InvoiceReject }>,
) {
  return useMutation({
    mutationFn: (v: { invoiceId: number; data: InvoiceReject }) =>
      adminRejectInvoiceApiAdminV1InvoicesInvoiceIdRejectPost(v.invoiceId, v.data),
    ...opts?.mutation,
  });
}

/** 结算缺口列表(游标分页):kind/reason 服务端过滤,unresolved 默认 true(未核销持续曝光)。 */
export function useSettlementGaps(
  params?: Omit<AdminListSettlementGapsApiAdminV1FinanceSettlementGapsGetParams, "cursor" | "limit">,
) {
  const queryKey = ["admin", "settlement-gaps", params] as const;
  const q = useInfiniteQuery({
    queryKey,
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) =>
      adminListSettlementGapsApiAdminV1FinanceSettlementGapsGet({
        ...params,
        limit: 50,
        ...(pageParam ? { cursor: pageParam } : {}),
      }),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
  return { ...q, queryKey };
}

export function useReplaySettlementGap(opts?: MutOpts<AdminSettlementGapOut, { gapId: number }>) {
  return useMutation({
    mutationFn: (v: { gapId: number }) =>
      adminReplaySettlementGapApiAdminV1FinanceSettlementGapsGapIdReplayPost(v.gapId),
    ...opts?.mutation,
  });
}

export function useResolveSettlementGap(
  opts?: MutOpts<AdminSettlementGapOut, { gapId: number; data: SettlementGapResolve }>,
) {
  return useMutation({
    mutationFn: (v: { gapId: number; data: SettlementGapResolve }) =>
      adminResolveSettlementGapApiAdminV1FinanceSettlementGapsGapIdResolvePost(v.gapId, v.data),
    ...opts?.mutation,
  });
}

/** 工单列表(游标分页,P2):status/category 过滤,user_id/ticket_no 检索。 */
export function useTickets(
  params?: Omit<AdminListTicketsApiAdminV1TicketsGetParams, "cursor" | "limit">,
) {
  const queryKey = ["admin", "tickets", params] as const;
  const q = useInfiniteQuery({
    queryKey,
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) =>
      adminListTicketsApiAdminV1TicketsGet({
        ...params,
        limit: 50,
        ...(pageParam ? { cursor: pageParam } : {}),
      }),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
  return { ...q, queryKey };
}

/** 注销申请列表。status 服务端过滤;行内附执行前校验计数。 */
export function useDeletionRequests(params?: AdminListDeletionRequestsApiAdminV1DeletionRequestsGetParams) {
  const queryKey = ["admin", "deletion-requests", params] as const;
  const q = useQuery({
    queryKey,
    queryFn: () => adminListDeletionRequestsApiAdminV1DeletionRequestsGet(params),
  });
  return { ...q, queryKey };
}

/** 执行注销(仅超管):校验不过 → 409(申请被自动驳回,detail 含残留清单)。 */
export function useApproveDeletion(opts?: MutOpts<AdminDeletionRequestOut, { requestId: number }>) {
  return useMutation({
    mutationFn: (v: { requestId: number }) =>
      adminApproveDeletionApiAdminV1DeletionRequestsRequestIdApprovePost(v.requestId),
    ...opts?.mutation,
  });
}

export function useRejectDeletion(
  opts?: MutOpts<AdminDeletionRequestOut, { requestId: number; data: AdminDeletionReject }>,
) {
  return useMutation({
    mutationFn: (v: { requestId: number; data: AdminDeletionReject }) =>
      adminRejectDeletionApiAdminV1DeletionRequestsRequestIdRejectPost(v.requestId, v.data),
    ...opts?.mutation,
  });
}

/** 工单详情 + 消息流(详情抽屉数据源;开启时 15s 轮询新回复)。 */
export function useTicketDetail(ticketId: number | null) {
  const queryKey = ["admin", "ticket", ticketId] as const;
  const q = useQuery({
    queryKey,
    enabled: ticketId !== null,
    refetchInterval: 15_000,
    queryFn: () => adminGetTicketApiAdminV1TicketsTicketIdGet(ticketId as number),
  });
  return { ...q, queryKey };
}

// ---------- 法务文档(读全角色,写仅 admin) ----------

export type { LegalDocCellOut as LegalDocCell, LegalDocVersionOut as LegalDocVersion } from "@superdl/api-client";

/** 法务文档总览:doc_key × locale 状态格(当前 published + 最新 draft)。 */
export function useLegalDocs() {
  const queryKey = ["admin", "legal-docs"] as const;
  const q = useQuery({ queryKey, queryFn: () => adminListLegalDocsApiAdminV1LegalDocsGet() });
  return { ...q, queryKey };
}

/** 某 (doc_key, locale) 的版本历史(version 倒序)。 */
export function useLegalDocVersions(docKey: string | null, locale: string | null) {
  const queryKey = ["admin", "legal-doc-versions", docKey, locale] as const;
  const q = useQuery({
    queryKey,
    enabled: docKey !== null && locale !== null,
    queryFn: () =>
      adminListLegalDocVersionsApiAdminV1LegalDocsDocKeyVersionsGet(docKey as string, {
        locale: locale as string,
      }),
  });
  return { ...q, queryKey };
}

export function useCreateLegalDocVersion(
  opts?: MutOpts<LegalDocVersionOut, { docKey: string; data: LegalDocVersionCreate }>,
) {
  return useMutation({
    mutationFn: (v: { docKey: string; data: LegalDocVersionCreate }) =>
      adminCreateLegalDocVersionApiAdminV1LegalDocsDocKeyVersionsPost(v.docKey, v.data),
    ...opts?.mutation,
  });
}

export function useUpdateLegalDocVersion(
  opts?: MutOpts<LegalDocVersionOut, { versionId: number; data: LegalDocVersionUpdate }>,
) {
  return useMutation({
    mutationFn: (v: { versionId: number; data: LegalDocVersionUpdate }) =>
      adminUpdateLegalDocVersionApiAdminV1LegalDocsVersionsVersionIdPut(v.versionId, v.data),
    ...opts?.mutation,
  });
}

export function usePublishLegalDocVersion(
  opts?: MutOpts<LegalDocVersionOut, { versionId: number }>,
) {
  return useMutation({
    mutationFn: (v: { versionId: number }) =>
      adminPublishLegalDocVersionApiAdminV1LegalDocsVersionsVersionIdPublishPost(v.versionId),
    ...opts?.mutation,
  });
}

export function useArchiveLegalDocVersion(
  opts?: MutOpts<LegalDocVersionOut, { versionId: number }>,
) {
  return useMutation({
    mutationFn: (v: { versionId: number }) =>
      adminArchiveLegalDocVersionApiAdminV1LegalDocsVersionsVersionIdArchivePost(v.versionId),
    ...opts?.mutation,
  });
}

export function useReplyTicket(
  opts?: MutOpts<AdminTicketDetailOut, { ticketId: number; data: AdminTicketReply }>,
) {
  return useMutation({
    mutationFn: (v: { ticketId: number; data: AdminTicketReply }) =>
      adminReplyTicketApiAdminV1TicketsTicketIdReplyPost(v.ticketId, v.data),
    ...opts?.mutation,
  });
}

export function useUpdateTicketStatus(
  opts?: MutOpts<unknown, { ticketId: number; data: AdminTicketStatusUpdate }>,
) {
  return useMutation({
    mutationFn: (v: { ticketId: number; data: AdminTicketStatusUpdate }) =>
      adminUpdateTicketStatusApiAdminV1TicketsTicketIdStatusPost(v.ticketId, v.data),
    ...opts?.mutation,
  });
}

/** 审计检索:游标翻页(响应是数组;满页即还有更早,游标=末行 id 的 base64)。 */
export type AuditFilters = Omit<AdminAuditLogApiAdminV1AuditGetParams, "cursor">;

export const AUDIT_DEFAULT_LIMIT = 100;

export function useAuditLog(filters: AuditFilters) {
  const limit = filters.limit ?? AUDIT_DEFAULT_LIMIT;
  const queryKey = ["admin", "audit", filters] as const;
  const q = useInfiniteQuery({
    queryKey,
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) =>
      adminAuditLogApiAdminV1AuditGet({
        ...filters,
        limit,
        ...(pageParam ? { cursor: pageParam } : {}),
      }),
    // 后端数组不按 Page 包装:满页视为还有更早,游标取末行 id
    getNextPageParam: (last) =>
      last.length >= limit ? btoa(String(last[last.length - 1]!.id)) : undefined,
  });
  return { ...q, queryKey };
}

// ---------- 变更 hooks ----------

export function useAdminLogin(
  opts?: MutOpts<AdminToken | MfaChallengeOut, { data: AdminLoginRequest }>,
) {
  return useMutation({
    mutationFn: (v: { data: AdminLoginRequest }) => adminLoginApiAdminV1AuthLoginPost(v.data),
    ...opts?.mutation,
  });
}

// ---------- TOTP MFA(admin/finance 强制) ----------
export function useMfaSetupBegin(opts?: MutOpts<MfaSetupOut, { ticket: string }>) {
  return useMutation({
    mutationFn: (v: { ticket: string }) =>
      mfaSetupBeginApiAdminV1AuthMfaSetupBeginPost({ ticket: v.ticket }),
    ...opts?.mutation,
  });
}

export function useMfaSetupConfirm(opts?: MutOpts<MfaSetupConfirmOut, { ticket: string; code: string }>) {
  return useMutation({
    mutationFn: (v: { ticket: string; code: string }) =>
      mfaSetupConfirmApiAdminV1AuthMfaSetupConfirmPost({ ticket: v.ticket, code: v.code }),
    ...opts?.mutation,
  });
}

export function useMfaVerify(opts?: MutOpts<MfaLoginOut, { ticket: string; code: string }>) {
  return useMutation({
    mutationFn: (v: { ticket: string; code: string }) =>
      mfaLoginVerifyApiAdminV1AuthLoginMfaPost({ ticket: v.ticket, code: v.code }),
    ...opts?.mutation,
  });
}

export function useRegenerateRecoveryCodes(opts?: MutOpts<RecoveryCodesOut, void>) {
  return useMutation({
    mutationFn: () => mfaRegenerateRecoveryCodesApiAdminV1MeMfaRecoveryCodesPost(),
    ...opts?.mutation,
  });
}

export function useResetAdminMfa(opts?: MutOpts<AdminAccountOut, { id: number; reason: string }>) {
  return useMutation({
    mutationFn: (v: { id: number; reason: string }) =>
      mfaResetApiAdminV1AdminsAdminIdMfaResetPost(v.id, { reason: v.reason }),
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

/** 冻结(原因必填)。响应含 instances_stopped:本次一并停掉的 running 实例台数。 */
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

export function useCreateAdjustment(
  opts?: MutOpts<unknown, { data: AdjustmentCreate; idempotencyKey?: string }>,
) {
  return useMutation({
    mutationFn: (v: { data: AdjustmentCreate; idempotencyKey?: string }) =>
      adminCreateAdjustmentApiAdminV1AdjustmentsPost(
        v.data,
        v.idempotencyKey ? { "Idempotency-Key": v.idempotencyKey } : undefined,
      ),
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
    mutationFn: (v: { orderNo: string; data: OrderBackfillRequest; idempotencyKey?: string }) =>
      adminBackfillOrderApiAdminV1FinanceOrdersOrderNoBackfillPost(
        v.orderNo,
        v.data,
        v.idempotencyKey ? { "Idempotency-Key": v.idempotencyKey } : undefined,
      ),
  });
}

/** 重放死信:需原因(与忽略对齐,handler 幂等)。 */
export function useRetryDeadTask() {
  return useMutation({
    mutationFn: (v: { taskId: number; data: OutboxRetryRequest }) =>
      adminRetryDeadTaskApiAdminV1OutboxTaskIdRetryPost(v.taskId, v.data),
  });
}

export function useDiscardDeadTask() {
  return useMutation({
    mutationFn: (v: { taskId: number; data: OutboxDiscardRequest }) =>
      adminDiscardDeadTaskApiAdminV1OutboxTaskIdDiscardPost(v.taskId, v.data),
  });
}

export function usePublishAnnouncement(
  opts?: MutOpts<AnnouncementResultOut, { data: AnnouncementCreate; idempotencyKey?: string }>,
) {
  return useMutation({
    mutationFn: (v: { data: AnnouncementCreate; idempotencyKey?: string }) =>
      adminPublishAnnouncementApiAdminV1AnnouncementsPost(
        v.data,
        v.idempotencyKey ? { "Idempotency-Key": v.idempotencyKey } : undefined,
      ),
    ...opts?.mutation,
  });
}

/** 公告历史(含已撤回;固定截断 200,页面用 ListCapNote 提示)。 */
export function useAnnouncements() {
  const queryKey = ["admin", "announcements"] as const;
  const q = useQuery({
    queryKey,
    queryFn: () => adminListAnnouncementsApiAdminV1AnnouncementsGet(),
  });
  return { ...q, queryKey };
}

export function useRevokeAnnouncement(
  opts?: MutOpts<AnnouncementOut, { announcementId: number; data: AnnouncementRevoke }>,
) {
  return useMutation({
    mutationFn: (v: { announcementId: number; data: AnnouncementRevoke }) =>
      adminRevokeAnnouncementApiAdminV1AnnouncementsAnnouncementIdRevokePost(
        v.announcementId,
        v.data,
      ),
    ...opts?.mutation,
  });
}

export function useUpdatePolicies(opts?: MutOpts<UpdatedKeysOut, { data: PolicyUpdateRequest }>) {
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
  opts?: MutOpts<UpdatedKeysOut, { data: PlatformConfigUpdateRequest }>,
) {
  return useMutation({
    mutationFn: (v: { data: PlatformConfigUpdateRequest }) =>
      adminUpdatePlatformConfigApiAdminV1PlatformConfigPut(v.data),
    ...opts?.mutation,
  });
}

export function useTestSms(opts?: MutOpts<SmsTestOut, { data: SmsTestRequest }>) {
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

// ---------- 总览/上下文(全部走生成 fetcher;类型即契约) ----------

/** 路由守卫用:/me 校准角色(角色只信服务端响应)。 */
export function fetchAdminMe(): Promise<AdminOut> {
  return adminMeApiAdminV1MeGet();
}

/** 总览聚合:精确 COUNT(全角色可读),替代在截断列表里数数。 */
export function useOverview() {
  const queryKey = ["admin", "overview"] as const;
  const q = useQuery({
    queryKey,
    queryFn: () => adminOverviewApiAdminV1OverviewGet(),
  });
  return { ...q, queryKey };
}

/** 调账前置上下文:回显租户身份与资金现状;不存在 → 404(调用方据 error 阻止提交)。 */
export function useAdjustContext(userId: number | null) {
  return useQuery({
    queryKey: ["admin", "adjust-context", userId],
    enabled: userId !== null,
    retry: 0,
    staleTime: 30_000,
    queryFn: () => adminAdjustContextApiAdminV1TenantsUserIdAdjustContextGet(userId as number),
  });
}

/** 改价影响面:该 SKU 当前活跃实例数/用户数/卡数。 */
export function useSkuImpact(skuId: number | null) {
  return useQuery({
    queryKey: ["admin", "sku-impact", skuId],
    enabled: skuId !== null,
    staleTime: 30_000,
    queryFn: () => adminSkuImpactApiAdminV1SkusSkuIdImpactGet(skuId as number),
  });
}

// ---------- CSV 导出(生成 fetcher,文本响应;截断标记见 lib/csv.ts) ----------

/** 服务端 CSV 截断标记(与 apps/api core/csvexport.py TRUNCATED_MARKER 一致)。 */
export const TRUNCATED_MARKER = "#SUPERDL_EXPORT_TRUNCATED#";

async function downloadCsvText(filename: string, text: string): Promise<"ok" | "truncated"> {
  downloadCsv(filename, text);
  return text.includes(TRUNCATED_MARKER) ? "truncated" : "ok";
}

/** 订单导出:跟随当前筛选(status/order_no/user_id/day)。 */
export async function exportOrdersCsv(
  params: AdminOrdersExportApiAdminV1OrdersExportGetParams | undefined,
  tz: number,
  lang: "zh-CN" | "en-US",
) {
  const text = (await adminOrdersExportApiAdminV1OrdersExportGet({
    ...params,
    tz_offset_minutes: tz,
    lang,
  })) as string;
  return downloadCsvText(`superdl-orders-${params?.day ?? "all"}.csv`, text);
}

/** 租户资金流水导出(抽屉「流水」Tab)。 */
export async function exportTenantLedgerCsv(userId: number, tz: number, lang: "zh-CN" | "en-US") {
  const text = (await adminTenantLedgerExportApiAdminV1TenantsUserIdLedgerExportGet(userId, {
    tz_offset_minutes: tz,
    lang,
  })) as string;
  return downloadCsvText(`superdl-tenant-${userId}-ledger.csv`, text);
}

/** 审计检索导出:跟随当前筛选(actor_type/actor_id/q/since/until)。 */
export async function exportAuditCsv(
  filters: AdminAuditExportApiAdminV1AuditExportGetParams | undefined,
  tz: number,
  lang: "zh-CN" | "en-US",
) {
  const text = (await adminAuditExportApiAdminV1AuditExportGet({
    ...filters,
    tz_offset_minutes: tz,
    lang,
  })) as string;
  return downloadCsvText("superdl-audit.csv", text);
}

/** 日对账导出。 */
export async function exportReconciliationCsv(
  day: string,
  lang: "zh-CN" | "en-US",
) {
  const params: ReconciliationExportApiAdminV1ReconciliationExportGetParams = { day, lang };
  const text = (await reconciliationExportApiAdminV1ReconciliationExportGet(params)) as string;
  return downloadCsvText(`superdl-reconciliation-${day}.csv`, text);
}
