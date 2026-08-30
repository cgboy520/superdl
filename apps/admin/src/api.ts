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
  adminTestRegistryApiAdminV1PlatformConfigTestRegistryPost,
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
  adminPreemptApiAdminV1InstancesUuidPreemptPost,
  adminPrewarmImageApiAdminV1ImagesImageIdPrewarmPost,
  adminRegenerateEnrollmentApiAdminV1NodeEnrollmentsEnrollmentIdRegeneratePost,
  adminRevokeEnrollmentApiAdminV1NodeEnrollmentsEnrollmentIdRevokePost,
  adminUncordonNodeApiAdminV1NodesNodeNameUncordonPost,
  adminUpdateImageApiAdminV1ImagesImageIdPatch,
  adminForceStopApiAdminV1InstancesUuidForceStopPost,
  adminUnfreezeTenantApiAdminV1TenantsUserIdUnfreezePost,
  adminListAdjustmentsApiAdminV1AdjustmentsGet,
  adminAdjustmentsExportApiAdminV1AdjustmentsExportGet,
  adminInvoicesExportApiAdminV1InvoicesExportGet,
  adminRefundsExportApiAdminV1RefundsExportGet,
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
  adminTicketsCountApiAdminV1TicketsCountGet,
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
  AdminAdjustmentsExportApiAdminV1AdjustmentsExportGetParams,
  AdminAlertsApiAdminV1AlertsGetParams,
  AdminAuditExportApiAdminV1AuditExportGetParams,
  AdminAuditLogApiAdminV1AuditGetParams,
  AdminDeletionReject,
  AdminInvoicesExportApiAdminV1InvoicesExportGetParams,
  AdminListAdjustmentsApiAdminV1AdjustmentsGetParams,
  AdminListDeletionRequestsApiAdminV1DeletionRequestsGetParams,
  AdminListInstanceEventsApiAdminV1InstancesUuidEventsGetParams,
  AdminListInstancesApiAdminV1InstancesGetParams,
  AdminListInvoicesApiAdminV1InvoicesGetParams,
  AdminListOrdersApiAdminV1OrdersGetParams,
  AdminListRefundsApiAdminV1RefundsGetParams,
  AdminListSettlementGapsApiAdminV1FinanceSettlementGapsGetParams,
  AdminListTenantsApiAdminV1TenantsGetParams,
  AdminListTicketsApiAdminV1TicketsGetParams,
  AdminOrdersExportApiAdminV1OrdersExportGetParams,
  AdminRefundsExportApiAdminV1RefundsExportGetParams,
  AdminTenantBillsApiAdminV1TenantsUserIdBillsGetParams,
  AdminTenantLedgerApiAdminV1TenantsUserIdLedgerGetParams,
  AdminTicketReply,
  AdminTicketStatusUpdate,
  AdminCreateRequest,
  AdminOut,
  AdminResetPasswordRequest,
  AdminSelfPasswordRequest,
  AdminUpdateRequest,
  AnnouncementCreate,
  AnnouncementRevoke,
  CapacityPreviewOut,
  EnrollmentCreate,
  EnrollmentRegenerateRequest,
  EnrollmentRevokeRequest,
  ImageCreate,
  ImageDeleteRequest,
  ImageUpdate,
  NodeCordonRequest,
  OutboxRetryRequest,
  OrderBackfillRequest,
  OutboxDiscardRequest,
  PlatformConfigUpdateRequest,
  PolicyUpdateRequest,
  SmsTestRequest,
  AdjustmentReview,
  InvoiceIssue,
  InvoiceReject,
  LegalDocVersionArchive,
  LegalDocVersionCreate,
  LegalDocVersionUpdate,
  RefundCancel,
  RefundPayout,
  RefundReview,
  AdminForceStopRequest,
  AdminLoginRequest,
  SettlementGapResolve,
  SkuCapacityPreviewApiAdminV1SkusCapacityPreviewGetParams,
  SkuCreate,
  SkuUpdate,
  TenantFreezeRequest,
  TenantQuotaUpdate,
} from "@superdl/api-client";
import { useInfiniteQuery, useMutation, useQuery, type UseMutationOptions } from "@tanstack/react-query";

import { downloadCsvChecked } from "@superdl/ui";

export { isApiError } from "@superdl/api-client";
export type {
  EnrollmentCommandOut,
  AdminInstanceOut,
  NodeMetricsOut,
  OverviewOut,
  SkuAdminOut,
  SkuCreate,
  SkuUpdate,
} from "@superdl/api-client";

// 行类型:全部取自生成契约,禁止手写

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
  AuditLogOut as AuditRow,
  DeadTaskOut as DeadTaskRow,
  NodeOut as NodeRow,
  OversellPoolOut as OversellRow,
  PaymentAnomalyOut as AnomalyRow,
  PlatformConfigItemOut as PlatformConfigItem,
  ReconciliationOut as ReconciliationReport,
  TenantOut as TenantRow,
  InstanceEventOut as InstanceEvent,
  GpuModelAggregateOut as GpuModelAggregate,
  ClusterComponentOut as ClusterComponent,
  CapacityWarningOut as CapacityWarning,
  RefundPayout,
} from "@superdl/api-client";

// 查询 hooks

type MutOpts<TData, TVars> = { mutation?: UseMutationOptions<TData, unknown, TVars> };

/** 端点变更工厂:收敛「mutationFn + 透传 opts.mutation」的同构样板;TData 取箭头返回(即生成 fetcher 的契约类型)。 */
function adminMutation<TData, TVars>(mutationFn: (v: TVars) => Promise<TData>) {
  return function useBoundMutation(opts?: MutOpts<TData, TVars>) {
    return useMutation({ mutationFn, ...opts?.mutation });
  };
}

/** 游标分页公共形状:params 带 limit/cursor,响应带 next_cursor(audit 的 base64 游标是特例,不走这里)。 */
type CursorParams = { limit?: number; cursor?: string };
type CursorPage = { next_cursor?: string | null };

/** 游标分页骨架:收敛各列表 hook 的 useInfiniteQuery 同构样板(initialPageParam/getNextPageParam/limit+cursor 拼接)。 */
function useCursorPages<TPage extends CursorPage, P extends CursorParams>(
  key: readonly unknown[],
  fetcher: (params?: P) => Promise<TPage>,
  params: Omit<P, "cursor" | "limit"> | undefined,
  opts?: { enabled?: boolean; limit?: number; refetchOnWindowFocus?: boolean },
) {
  const limit = opts?.limit ?? 50;
  return useInfiniteQuery({
    queryKey: key,
    enabled: opts?.enabled ?? true,
    refetchOnWindowFocus: opts?.refetchOnWindowFocus,
    initialPageParam: undefined as string | undefined,
    // 组合对象即 P(页面参数 + limit/cursor);TS 证不出泛型展开,仅此处单点断言
    queryFn: ({ pageParam }) =>
      fetcher({ ...params, limit, ...(pageParam ? { cursor: pageParam } : {}) } as P),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
}

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

export const useTestClusterConnection = adminMutation(
  (_: void) => adminClusterTestConnectionApiAdminV1ClusterTestConnectionPost(),
);

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
  // 入参类型取生成契约:端点增删 query 参数时编译期即报错
  params: SkuCapacityPreviewApiAdminV1SkusCapacityPreviewGetParams | null,
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
  const queryKey = ["admin", "instances", params, options?.limit ?? 50] as const;
  const q = useCursorPages(queryKey, adminListInstancesApiAdminV1InstancesGet, params, options);
  return { ...q, queryKey };
}

/** 租户列表(游标分页)。q = 手机号(完整号码精确,短串按后缀);纯数字额外按 id 命中首页。 */
export function useTenants(params?: Omit<AdminListTenantsApiAdminV1TenantsGetParams, "cursor" | "limit">) {
  const queryKey = ["admin", "tenants", params] as const;
  const q = useCursorPages(queryKey, adminListTenantsApiAdminV1TenantsGet, params, undefined);
  return { ...q, queryKey };
}

/** 租户账单下钻:资金流水与小时账单(游标分页,与用户端同源同实现)。 */
export function useTenantLedger(userId: number | null) {
  const queryKey = ["admin", "tenant-ledger", userId] as const;
  const q = useCursorPages(
    queryKey,
    (p?: AdminTenantLedgerApiAdminV1TenantsUserIdLedgerGetParams) =>
      adminTenantLedgerApiAdminV1TenantsUserIdLedgerGet(userId as number, p),
    undefined,
    { enabled: userId !== null },
  );
  return { ...q, queryKey };
}

/** 租户小时账单;instanceId 非空时按实例过滤(排障:只盯一台机的账)。 */
export function useTenantBills(userId: number | null, instanceId?: number | null) {
  const queryKey = ["admin", "tenant-bills", userId, instanceId ?? null] as const;
  const q = useCursorPages(
    queryKey,
    (p?: AdminTenantBillsApiAdminV1TenantsUserIdBillsGetParams) =>
      adminTenantBillsApiAdminV1TenantsUserIdBillsGet(userId as number, p),
    instanceId ? { instance_id: instanceId } : undefined,
    { enabled: userId !== null },
  );
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

export const useSetTenantQuota = adminMutation((v: { userId: number; data: TenantQuotaUpdate }) =>
  adminSetTenantQuotaApiAdminV1TenantsUserIdQuotaPut(v.userId, v.data),
);

/** 实例事件时间线(排障;管理端不限租户,游标分页)。 */
export function useInstanceEvents(uuid: string | null) {
  const queryKey = ["admin", "instance-events", uuid] as const;
  const q = useCursorPages(
    queryKey,
    (p?: AdminListInstanceEventsApiAdminV1InstancesUuidEventsGetParams) =>
      adminListInstanceEventsApiAdminV1InstancesUuidEventsGet(uuid as string, p),
    undefined,
    { enabled: uuid !== null },
  );
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
    refetchInterval: 30_000,
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
  const q = useQuery({
    queryKey,
    queryFn: () => oversellReportApiAdminV1ReportsOversellGet(),
    refetchInterval: 60_000,
  });
  return { ...q, queryKey };
}

export function useReconciliation(day: string) {
  return useQuery({
    queryKey: ["admin", "reconciliation", day],
    queryFn: () => reconciliationApiAdminV1ReconciliationGet({ day }),
  });
}

/** 告警流:severity 服务端过滤。enabled 关停时不再取数(折叠 UI 不空转)。 */
export function useAlerts(
  params?: AdminAlertsApiAdminV1AlertsGetParams,
  options?: { refetchInterval?: number; enabled?: boolean },
) {
  const queryKey = ["admin", "alerts", params] as const;
  const q = useQuery({
    queryKey,
    queryFn: () => adminAlertsApiAdminV1AlertsGet(params),
    enabled: options?.enabled ?? true,
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

export const useAckAlert = adminMutation((v: { alertId: number }) =>
  adminAckAlertApiAdminV1AlertsAlertIdAckPost(v.alertId),
);

/** 充值订单(游标分页)。order_no 精确;day=YYYY-MM-DD 按下单日(UTC)过滤。 */
export function useOrders(
  params?: Omit<AdminListOrdersApiAdminV1OrdersGetParams, "cursor" | "limit">,
  options?: { enabled?: boolean },
) {
  const queryKey = ["admin", "orders", params] as const;
  const q = useCursorPages(queryKey, adminListOrdersApiAdminV1OrdersGet, params, options);
  return { ...q, queryKey };
}

/** 调账单(游标分页):status/user_id/day 服务端过滤。 */
export function useAdjustments(
  params?: Omit<AdminListAdjustmentsApiAdminV1AdjustmentsGetParams, "cursor" | "limit">,
) {
  const queryKey = ["admin", "adjustments", params] as const;
  const q = useCursorPages(queryKey, adminListAdjustmentsApiAdminV1AdjustmentsGet, params, undefined);
  return { ...q, queryKey };
}

/** 退款单列表(游标分页)。status 服务端过滤;day=YYYY-MM-DD(UTC 日,与对账口径一致)。 */
export function useRefunds(
  params?: Omit<AdminListRefundsApiAdminV1RefundsGetParams, "cursor" | "limit">,
) {
  const queryKey = ["admin", "refunds", params] as const;
  const q = useCursorPages(queryKey, adminListRefundsApiAdminV1RefundsGet, params, undefined);
  return { ...q, queryKey };
}

export const useReviewRefund = adminMutation((v: { refundId: number; data: RefundReview }) =>
  adminReviewRefundApiAdminV1RefundsRefundIdReviewPost(v.refundId, v.data),
);

export const usePayoutRefund = adminMutation(
  (v: { refundId: number; data: RefundPayout; idempotencyKey?: string }) =>
    adminPayoutRefundApiAdminV1RefundsRefundIdPayoutPost(
      v.refundId,
      v.data,
      v.idempotencyKey ? { "Idempotency-Key": v.idempotencyKey } : undefined,
    ),
);

export const useCancelRefund = adminMutation((v: { refundId: number; data: RefundCancel }) =>
  adminCancelRefundApiAdminV1RefundsRefundIdCancelPost(v.refundId, v.data),
);

/** 发票申请列表。status/period(YYYY-MM)服务端过滤。 */
export function useInvoices(params?: AdminListInvoicesApiAdminV1InvoicesGetParams) {
  const queryKey = ["admin", "invoices", params] as const;
  const q = useQuery({
    queryKey,
    queryFn: () => adminListInvoicesApiAdminV1InvoicesGet(params),
  });
  return { ...q, queryKey };
}

export const useIssueInvoice = adminMutation((v: { invoiceId: number; data: InvoiceIssue }) =>
  adminIssueInvoiceApiAdminV1InvoicesInvoiceIdIssuePost(v.invoiceId, v.data),
);

export const useRejectInvoice = adminMutation((v: { invoiceId: number; data: InvoiceReject }) =>
  adminRejectInvoiceApiAdminV1InvoicesInvoiceIdRejectPost(v.invoiceId, v.data),
);

/** 结算缺口列表(游标分页):kind/reason 服务端过滤,unresolved 默认 true(未核销持续曝光)。
 *  不挂 refetchInterval(infinite 轮询 = 每轮 N 页全拉):新鲜度靠焦点重取 + 表上方手动刷新。 */
export function useSettlementGaps(
  params?: Omit<AdminListSettlementGapsApiAdminV1FinanceSettlementGapsGetParams, "cursor" | "limit">,
) {
  const queryKey = ["admin", "settlement-gaps", params] as const;
  const q = useCursorPages(
    queryKey,
    adminListSettlementGapsApiAdminV1FinanceSettlementGapsGet,
    params,
    { refetchOnWindowFocus: true },
  );
  return { ...q, queryKey };
}

export const useReplaySettlementGap = adminMutation((v: { gapId: number }) =>
  adminReplaySettlementGapApiAdminV1FinanceSettlementGapsGapIdReplayPost(v.gapId),
);

export const useResolveSettlementGap = adminMutation(
  (v: { gapId: number; data: SettlementGapResolve }) =>
    adminResolveSettlementGapApiAdminV1FinanceSettlementGapsGapIdResolvePost(v.gapId, v.data),
);

/** 工单列表(游标分页):status/category 过滤,user_id/ticket_no 检索。
 *  不挂 refetchInterval(infinite 轮询 = 每轮 N 页全拉):新鲜度靠焦点重取 + 表上方手动刷新。 */
export function useTickets(
  params?: Omit<AdminListTicketsApiAdminV1TicketsGetParams, "cursor" | "limit">,
) {
  const queryKey = ["admin", "tickets", params] as const;
  const q = useCursorPages(queryKey, adminListTicketsApiAdminV1TicketsGet, params, {
    refetchOnWindowFocus: true,
  });
  return { ...q, queryKey };
}

/** 待客服工单计数(60s 轮询):轻量 count 端点,不拉行。 */
export function useTicketPendingCount() {
  const queryKey = ["admin", "tickets-count"] as const;
  const q = useQuery({
    queryKey,
    queryFn: () => adminTicketsCountApiAdminV1TicketsCountGet({ status: "pending_staff" }),
    refetchInterval: 60_000,
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
export const useApproveDeletion = adminMutation((v: { requestId: number }) =>
  adminApproveDeletionApiAdminV1DeletionRequestsRequestIdApprovePost(v.requestId),
);

export const useRejectDeletion = adminMutation((v: { requestId: number; data: AdminDeletionReject }) =>
  adminRejectDeletionApiAdminV1DeletionRequestsRequestIdRejectPost(v.requestId, v.data),
);

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

// 法务文档(读全角色,写仅 admin)

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

export const useCreateLegalDocVersion = adminMutation(
  (v: { docKey: string; data: LegalDocVersionCreate }) =>
    adminCreateLegalDocVersionApiAdminV1LegalDocsDocKeyVersionsPost(v.docKey, v.data),
);

export const useUpdateLegalDocVersion = adminMutation(
  (v: { versionId: number; data: LegalDocVersionUpdate }) =>
    adminUpdateLegalDocVersionApiAdminV1LegalDocsVersionsVersionIdPut(v.versionId, v.data),
);

export const usePublishLegalDocVersion = adminMutation((v: { versionId: number }) =>
  adminPublishLegalDocVersionApiAdminV1LegalDocsVersionsVersionIdPublishPost(v.versionId),
);

export const useArchiveLegalDocVersion = adminMutation(
  (v: { versionId: number; data: LegalDocVersionArchive }) =>
    adminArchiveLegalDocVersionApiAdminV1LegalDocsVersionsVersionIdArchivePost(v.versionId, v.data),
);

export const useReplyTicket = adminMutation((v: { ticketId: number; data: AdminTicketReply }) =>
  adminReplyTicketApiAdminV1TicketsTicketIdReplyPost(v.ticketId, v.data),
);

export const useUpdateTicketStatus = adminMutation(
  (v: { ticketId: number; data: AdminTicketStatusUpdate }) =>
    adminUpdateTicketStatusApiAdminV1TicketsTicketIdStatusPost(v.ticketId, v.data),
);

/** 审计检索:游标翻页(响应是数组;满页即还有更早,游标=末行 id 的 base64)。 */
type AuditFilters = Omit<AdminAuditLogApiAdminV1AuditGetParams, "cursor">;

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
    // 满页视为还有更早,游标取末行 id
    getNextPageParam: (last) =>
      last.length >= limit ? btoa(String(last[last.length - 1]!.id)) : undefined,
  });
  return { ...q, queryKey };
}

// 变更 hooks

/** 登录只返回二要素挑战票(全角色强制 TOTP);正式 token 经 useMfaSetupConfirm / useMfaVerify。 */
export const useAdminLogin = adminMutation((v: { data: AdminLoginRequest }) =>
  adminLoginApiAdminV1AuthLoginPost(v.data),
);

// TOTP MFA(全部管理角色强制)
export const useMfaSetupBegin = adminMutation((v: { ticket: string }) =>
  mfaSetupBeginApiAdminV1AuthMfaSetupBeginPost({ ticket: v.ticket }),
);

export const useMfaSetupConfirm = adminMutation((v: { ticket: string; code: string }) =>
  mfaSetupConfirmApiAdminV1AuthMfaSetupConfirmPost({ ticket: v.ticket, code: v.code }),
);

export const useMfaVerify = adminMutation((v: { ticket: string; code: string }) =>
  mfaLoginVerifyApiAdminV1AuthLoginMfaPost({ ticket: v.ticket, code: v.code }),
);

export const useRegenerateRecoveryCodes = adminMutation(
  (_: void) => mfaRegenerateRecoveryCodesApiAdminV1MeMfaRecoveryCodesPost(),
);

export const useResetAdminMfa = adminMutation((v: { id: number; reason: string }) =>
  mfaResetApiAdminV1AdminsAdminIdMfaResetPost(v.id, { reason: v.reason }),
);

export const useCreateSku = adminMutation((v: { data: SkuCreate }) =>
  adminCreateSkuApiAdminV1SkusPost(v.data),
);

export const useUpdateSku = adminMutation(
  (v: { skuId: number; data: SkuUpdate; force?: boolean }) =>
    adminUpdateSkuApiAdminV1SkusSkuIdPatch(v.skuId, v.data, v.force ? { force: true } : undefined),
);

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

export const useCreateEnrollment = adminMutation(
  (v: { data: EnrollmentCreate; idempotencyKey?: string }) =>
    adminCreateEnrollmentApiAdminV1NodeEnrollmentsPost(
      v.data,
      v.idempotencyKey ? { "Idempotency-Key": v.idempotencyKey } : undefined,
    ),
);

export const useRegenerateEnrollment = adminMutation(
  (v: { enrollmentId: number; data: EnrollmentRegenerateRequest }) =>
    adminRegenerateEnrollmentApiAdminV1NodeEnrollmentsEnrollmentIdRegeneratePost(
      v.enrollmentId,
      v.data,
    ),
);

export const useRevokeEnrollment = adminMutation(
  (v: { enrollmentId: number; data: EnrollmentRevokeRequest }) =>
    adminRevokeEnrollmentApiAdminV1NodeEnrollmentsEnrollmentIdRevokePost(v.enrollmentId, v.data),
);

export const useCordonNode = adminMutation(
  (v: { nodeName: string; on: boolean; data: NodeCordonRequest }) =>
    v.on
      ? adminCordonNodeApiAdminV1NodesNodeNameCordonPost(v.nodeName, v.data)
      : adminUncordonNodeApiAdminV1NodesNodeNameUncordonPost(v.nodeName, v.data),
);

export const useCreateImage = adminMutation((v: { data: ImageCreate }) =>
  adminCreateImageApiAdminV1ImagesPost(v.data),
);

export const useUpdateImage = adminMutation((v: { imageId: number; data: ImageUpdate }) =>
  adminUpdateImageApiAdminV1ImagesImageIdPatch(v.imageId, v.data),
);

export const useDeleteImage = adminMutation((v: { imageId: number; data: ImageDeleteRequest }) =>
  adminDeleteImageApiAdminV1ImagesImageIdDelete(v.imageId, v.data),
);

export const usePrewarmImage = adminMutation((v: { imageId: number }) =>
  adminPrewarmImageApiAdminV1ImagesImageIdPrewarmPost(v.imageId),
);

export const useForceStop = adminMutation((v: { uuid: string; data: AdminForceStopRequest }) =>
  adminForceStopApiAdminV1InstancesUuidForceStopPost(v.uuid, v.data),
);

/** 强制回收一台竞价实例(腾容量)。与强制停止是两条后端路径,时间线 reason 与竞价可靠性统计据此区分。 */
export const usePreemptInstance = adminMutation((v: { uuid: string; data: AdminForceStopRequest }) =>
  adminPreemptApiAdminV1InstancesUuidPreemptPost(v.uuid, v.data),
);

/** 冻结(原因必填)。响应含 instances_stopped:本次一并停掉的 running 实例台数。 */
export const useFreezeTenant = adminMutation((v: { userId: number; data: TenantFreezeRequest }) =>
  adminFreezeTenantApiAdminV1TenantsUserIdFreezePost(v.userId, v.data),
);

export const useUnfreezeTenant = adminMutation((v: { userId: number; data: TenantFreezeRequest }) =>
  adminUnfreezeTenantApiAdminV1TenantsUserIdUnfreezePost(v.userId, v.data),
);

export const useCreateAdjustment = adminMutation(
  (v: { data: AdjustmentCreate; idempotencyKey?: string }) =>
    adminCreateAdjustmentApiAdminV1AdjustmentsPost(
      v.data,
      v.idempotencyKey ? { "Idempotency-Key": v.idempotencyKey } : undefined,
    ),
);

export const useReviewAdjustment = adminMutation(
  (v: { adjustmentId: number; data: AdjustmentReview }) =>
    adminReviewAdjustmentApiAdminV1AdjustmentsAdjustmentIdReviewPost(v.adjustmentId, v.data),
);

// 运营:死信重放 / 收入报表 / 公告

export function useAnomalies() {
  const queryKey = ["admin", "anomalies"] as const;
  const q = useQuery({
    queryKey,
    queryFn: () => adminPaymentAnomaliesApiAdminV1FinanceAnomaliesGet(),
    refetchInterval: 60_000,
  });
  return { ...q, queryKey };
}

export function useDeadTasks(options?: { enabled?: boolean }) {
  const queryKey = ["admin", "outbox-dead"] as const;
  const q = useQuery({
    queryKey,
    queryFn: () => adminListDeadTasksApiAdminV1OutboxDeadGet(),
    enabled: options?.enabled ?? true,
    refetchInterval: 60_000,
  });
  return { ...q, queryKey };
}

export function useRevenueReport() {
  const tz = -new Date().getTimezoneOffset();
  return useQuery({
    queryKey: ["admin", "revenue", tz],
    queryFn: () => revenueReportApiAdminV1ReportsRevenueGet({ tz_offset_minutes: tz }),
    refetchInterval: 60_000,
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

export const useVerifyOrder = adminMutation((v: { orderNo: string }) =>
  adminVerifyOrderApiAdminV1FinanceOrdersOrderNoVerifyPost(v.orderNo),
);

export const useBackfillOrder = adminMutation(
  (v: { orderNo: string; data: OrderBackfillRequest; idempotencyKey?: string }) =>
    adminBackfillOrderApiAdminV1FinanceOrdersOrderNoBackfillPost(
      v.orderNo,
      v.data,
      v.idempotencyKey ? { "Idempotency-Key": v.idempotencyKey } : undefined,
    ),
);

/** 重放死信:需原因(与忽略对齐,handler 幂等)。 */
export const useRetryDeadTask = adminMutation((v: { taskId: number; data: OutboxRetryRequest }) =>
  adminRetryDeadTaskApiAdminV1OutboxTaskIdRetryPost(v.taskId, v.data),
);

export const useDiscardDeadTask = adminMutation((v: { taskId: number; data: OutboxDiscardRequest }) =>
  adminDiscardDeadTaskApiAdminV1OutboxTaskIdDiscardPost(v.taskId, v.data),
);

export const usePublishAnnouncement = adminMutation(
  (v: { data: AnnouncementCreate; idempotencyKey?: string }) =>
    adminPublishAnnouncementApiAdminV1AnnouncementsPost(
      v.data,
      v.idempotencyKey ? { "Idempotency-Key": v.idempotencyKey } : undefined,
    ),
);

/** 公告历史(含已撤回;固定截断 200,页面用 ListCapNote 提示)。 */
export function useAnnouncements() {
  const queryKey = ["admin", "announcements"] as const;
  const q = useQuery({
    queryKey,
    queryFn: () => adminListAnnouncementsApiAdminV1AnnouncementsGet(),
  });
  return { ...q, queryKey };
}

export const useRevokeAnnouncement = adminMutation(
  (v: { announcementId: number; data: AnnouncementRevoke }) =>
    adminRevokeAnnouncementApiAdminV1AnnouncementsAnnouncementIdRevokePost(v.announcementId, v.data),
);

export const useUpdatePolicies = adminMutation((v: { data: PolicyUpdateRequest }) =>
  adminUpdatePoliciesApiAdminV1PoliciesPut(v.data),
);

// 平台配置(渠道凭据与合规;仅 admin 角色)

export function usePlatformConfig(options?: { enabled?: boolean }) {
  const queryKey = ["admin", "platform-config"] as const;
  const q = useQuery({
    queryKey,
    queryFn: () => adminGetPlatformConfigApiAdminV1PlatformConfigGet(),
    // 非 admin 角色读它必 403:调用方按角色传 enabled
    enabled: options?.enabled ?? true,
    // 关掉焦点重取:避免后台重取覆盖编辑中的表单
    refetchOnWindowFocus: false,
    retry: false,
  });
  return { ...q, queryKey };
}

export const useUpdatePlatformConfig = adminMutation((v: { data: PlatformConfigUpdateRequest }) =>
  adminUpdatePlatformConfigApiAdminV1PlatformConfigPut(v.data),
);

export const useTestRegistry = adminMutation(
  (_: void) => adminTestRegistryApiAdminV1PlatformConfigTestRegistryPost(),
);

export const useTestSms = adminMutation((v: { data: SmsTestRequest }) =>
  adminTestSmsApiAdminV1PlatformConfigTestSmsPost(v.data),
);

// 管理员账号

export function useAdminAccounts() {
  const queryKey = ["admin", "admins"] as const;
  const q = useQuery({ queryKey, queryFn: () => adminListAdminsApiAdminV1AdminsGet() });
  return { ...q, queryKey };
}

export const useCreateAdminAccount = adminMutation((v: { data: AdminCreateRequest }) =>
  adminCreateAdminApiAdminV1AdminsPost(v.data),
);

export const useUpdateAdminAccount = adminMutation((v: { id: number; data: AdminUpdateRequest }) =>
  adminUpdateAdminApiAdminV1AdminsAdminIdPatch(v.id, v.data),
);

export const useResetAdminPassword = adminMutation(
  (v: { id: number; data: AdminResetPasswordRequest }) =>
    adminResetPasswordApiAdminV1AdminsAdminIdResetPasswordPost(v.id, v.data),
);

export const useChangeOwnPassword = adminMutation((v: { data: AdminSelfPasswordRequest }) =>
  adminChangeOwnPasswordApiAdminV1MePasswordPost(v.data),
);

// 总览/上下文(全部走生成 fetcher;类型即契约)

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
    refetchInterval: 60_000,
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

// CSV 导出(生成 fetcher,文本响应;截断判定见 @superdl/ui downloadCsvChecked)

type CsvLang = "zh-CN" | "en-US";

/** CSV 导出工厂:fetcher 取文本响应 → downloadCsvChecked 落盘;name 为串或按入参派生文件名。 */
function makeCsvExporter<A extends unknown[]>(
  fetcher: (...args: A) => Promise<unknown>,
  name: string | ((...args: A) => string),
): (...args: A) => Promise<"ok" | "truncated"> {
  return async (...args: A) => {
    const text = (await fetcher(...args)) as string;
    return downloadCsvChecked(typeof name === "function" ? name(...args) : name, text);
  };
}

/** 列表类导出的参数合并:当前筛选 + 时区 + 语言(导出参数类型已含可选 tz_offset_minutes/lang)。 */
const withTzLang = <P extends object>(params: P | undefined, tz: number, lang: CsvLang): P =>
  ({ ...params, tz_offset_minutes: tz, lang }) as P;

/** 订单导出:跟随当前筛选(status/order_no/user_id/day)。 */
export const exportOrdersCsv = makeCsvExporter(
  (params: AdminOrdersExportApiAdminV1OrdersExportGetParams | undefined, tz: number, lang: CsvLang) =>
    adminOrdersExportApiAdminV1OrdersExportGet(withTzLang(params, tz, lang)),
  (params: AdminOrdersExportApiAdminV1OrdersExportGetParams | undefined) =>
    `superdl-orders-${params?.day ?? "all"}.csv`,
);

/** 调账导出:跟随当前筛选(status)。 */
export const exportAdjustmentsCsv = makeCsvExporter(
  (params: AdminAdjustmentsExportApiAdminV1AdjustmentsExportGetParams | undefined, tz: number, lang: CsvLang) =>
    adminAdjustmentsExportApiAdminV1AdjustmentsExportGet(withTzLang(params, tz, lang)),
  "superdl-adjustments.csv",
);

/** 退款导出:跟随当前筛选(status/channel)。 */
export const exportRefundsCsv = makeCsvExporter(
  (params: AdminRefundsExportApiAdminV1RefundsExportGetParams | undefined, tz: number, lang: CsvLang) =>
    adminRefundsExportApiAdminV1RefundsExportGet(withTzLang(params, tz, lang)),
  "superdl-refunds.csv",
);

/** 发票导出:跟随当前筛选(status)。 */
export const exportInvoicesCsv = makeCsvExporter(
  (params: AdminInvoicesExportApiAdminV1InvoicesExportGetParams | undefined, tz: number, lang: CsvLang) =>
    adminInvoicesExportApiAdminV1InvoicesExportGet(withTzLang(params, tz, lang)),
  "superdl-invoices.csv",
);

/** 租户资金流水导出(抽屉「流水」Tab)。 */
export const exportTenantLedgerCsv = makeCsvExporter(
  (userId: number, tz: number, lang: CsvLang) =>
    adminTenantLedgerExportApiAdminV1TenantsUserIdLedgerExportGet(userId, {
      tz_offset_minutes: tz,
      lang,
    }),
  (userId: number) => `superdl-tenant-${userId}-ledger.csv`,
);

/** 审计检索导出:跟随当前筛选(actor_type/actor_id/q/since/until)。 */
export const exportAuditCsv = makeCsvExporter(
  (filters: AdminAuditExportApiAdminV1AuditExportGetParams | undefined, tz: number, lang: CsvLang) =>
    adminAuditExportApiAdminV1AuditExportGet(withTzLang(filters, tz, lang)),
  "superdl-audit.csv",
);

/** 对账导出(按日,无 tz 参数)。 */
export const exportReconciliationCsv = makeCsvExporter(
  (day: string, lang: CsvLang) =>
    reconciliationExportApiAdminV1ReconciliationExportGet({ day, lang }),
  (day: string) => `superdl-reconciliation-${day}.csv`,
);
