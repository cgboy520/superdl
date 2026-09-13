/** 数据访问层:基于 @superdl/api-client 生成 fetcher 的 TanStack Query hooks。 */

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
  adminListServicesApiAdminV1ServicesGet,
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
  LegalDocVersionCreateLocale as LegalLocale,
  AdminAlertsApiAdminV1AlertsGetParams,
  AdminAuditExportApiAdminV1AuditExportGetParams,
  AdminAuditLogApiAdminV1AuditGetParams,
  AdminDeletionApprove,
  AdminDeletionReject,
  AdminInvoicesExportApiAdminV1InvoicesExportGetParams,
  AdminListAdjustmentsApiAdminV1AdjustmentsGetParams,
  AdminListDeletionRequestsApiAdminV1DeletionRequestsGetParams,
  AdminListInstanceEventsApiAdminV1InstancesUuidEventsGetParams,
  AdminListInstancesApiAdminV1InstancesGetParams,
  AdminListServicesApiAdminV1ServicesGetParams,
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
import {
  skipToken,
  useInfiniteQuery,
  useMutation,
  useQuery,
  type SkipToken,
  type UseMutationOptions,
} from "@tanstack/react-query";

import { downloadCsvChecked, POLL } from "@superdl/ui";

export { isApiError } from "@superdl/api-client";
export type {
  EnrollmentCommandOut,
  AdminInstanceOut,
  AdminServiceOut,
  NodeMetricsOut,
  OverviewOut,
  SkuAdminOut,
  SkuCreate,
  SkuUpdate,
} from "@superdl/api-client";

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

interface MutOpts<TData, TVars> {
  mutation?: UseMutationOptions<TData, unknown, TVars>;
}

/** 变更 hook 工厂:mutationFn + 透传 opts.mutation。无参变更省略变量(TVars 默认 void,mutate() 直调)。 */
function adminMutation<TData, TVars = void>(mutationFn: (v: TVars) => Promise<TData>) {
  return function useBoundMutation(opts?: MutOpts<TData, TVars>) {
    return useMutation({ mutationFn, ...opts?.mutation });
  };
}

/** 游标分页公共形状:params 带 limit/cursor,响应带 next_cursor(audit 不走这里)。 */
interface CursorParams {
  limit?: number;
  cursor?: string;
}
interface CursorPage {
  next_cursor?: string | null;
}

/** 跨文件共享的查询键前缀:定义在本文件下方各 hook,失效在页面/lib,两边只认这里。 */
export const adminKeys = {
  alerts: ["admin", "alerts"],
  tickets: { all: ["admin", "tickets"], detail: (id: number | null) => ["admin", "ticket", id] },
  services: ["admin", "services"],
  nodes: ["admin", "nodes"],
};

/** 查询 hook 骨架:结果附带 queryKey 供调用方失效/刷新;条件查询的 queryFn 传 skipToken(禁止断言配 enabled)。 */
function useKeyedQuery<T>(
  queryKey: readonly unknown[],
  queryFn: (() => Promise<T>) | SkipToken,
  opts?: {
    enabled?: boolean;
    refetchInterval?: number | false;
    retry?: number | boolean;
    staleTime?: number;
    refetchOnWindowFocus?: boolean;
  },
) {
  const q = useQuery<T>({ queryKey, queryFn, ...opts });
  return { ...q, queryKey };
}

/** 游标分页 useInfiniteQuery 骨架;fetcher 传 null = 条件不满足不取数(skipToken)。结果附带 queryKey。 */
function useCursorPages<TPage extends CursorPage, P extends CursorParams>(
  key: readonly unknown[],
  fetcher: ((params?: P) => Promise<TPage>) | null,
  params: Omit<P, "cursor" | "limit"> | undefined,
  opts?: { enabled?: boolean; limit?: number; refetchOnWindowFocus?: boolean },
) {
  const limit = opts?.limit ?? 50;
  const q = useInfiniteQuery({
    queryKey: key,
    enabled: opts?.enabled ?? true,
    refetchOnWindowFocus: opts?.refetchOnWindowFocus,
    initialPageParam: undefined as string | undefined,
    queryFn:
      fetcher === null
        ? skipToken
        : ({ pageParam }) => fetcher({ ...params, limit, ...(pageParam ? { cursor: pageParam } : {}) } as P),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
  return { ...q, queryKey: key };
}

export function useAdminSkus() {
  return useKeyedQuery(["admin", "skus"], () => adminListSkusApiAdminV1SkusGet());
}

export function useClusterStatus() {
  return useKeyedQuery(["admin", "cluster-status"], () => adminClusterStatusApiAdminV1ClusterStatusGet(), {
    refetchInterval: POLL.steady,
  });
}

export const useTestClusterConnection = adminMutation(() =>
  adminClusterTestConnectionApiAdminV1ClusterTestConnectionPost(),
);

export function useGpuModelAggregates(options?: { enabled?: boolean }) {
  return useKeyedQuery(["admin", "gpu-models"], () => adminGpuModelAggregatesApiAdminV1ClusterGpuModelsGet(), {
    enabled: options?.enabled,
  });
}

export function useSkuCapacityPreview(params: SkuCapacityPreviewApiAdminV1SkusCapacityPreviewGetParams | null) {
  return useQuery<CapacityPreviewOut>({
    queryKey: ["admin", "sku-capacity-preview", params],
    queryFn: params === null ? skipToken : () => skuCapacityPreviewApiAdminV1SkusCapacityPreviewGet(params),
    placeholderData: (prev) => prev,
  });
}

/** 实例列表(游标分页):status/user_id/q/node_name 服务端过滤。 */
export function useAdminInstances(
  params?: Omit<AdminListInstancesApiAdminV1InstancesGetParams, "cursor" | "limit">,
  options?: { enabled?: boolean; limit?: number },
) {
  return useCursorPages(
    ["admin", "instances", params, options?.limit ?? 50],
    adminListInstancesApiAdminV1InstancesGet,
    params,
    options,
  );
}

/** 在线服务列表(不限租户);user_id 过滤时响应带 total。 */
export function useAdminServices(
  params?: Omit<AdminListServicesApiAdminV1ServicesGetParams, "cursor" | "limit">,
  options?: { enabled?: boolean; limit?: number },
) {
  return useCursorPages(
    [...adminKeys.services, params, options?.limit ?? 50],
    adminListServicesApiAdminV1ServicesGet,
    params,
    options,
  );
}

/** 租户列表(游标分页)。q = 手机号(完整精确,短串后缀);纯数字另按 id 命中。 */
export function useTenants(params?: Omit<AdminListTenantsApiAdminV1TenantsGetParams, "cursor" | "limit">) {
  return useCursorPages(["admin", "tenants", params], adminListTenantsApiAdminV1TenantsGet, params, undefined);
}

/** 租户资金流水(游标分页)。 */
export function useTenantLedger(userId: number | null) {
  return useCursorPages(
    ["admin", "tenant-ledger", userId],
    userId === null
      ? null
      : (p?: AdminTenantLedgerApiAdminV1TenantsUserIdLedgerGetParams) =>
          adminTenantLedgerApiAdminV1TenantsUserIdLedgerGet(userId, p),
    undefined,
  );
}

/** 租户小时账单;instanceId 非空时按实例过滤。 */
export function useTenantBills(userId: number | null, instanceId?: number | null) {
  return useCursorPages(
    ["admin", "tenant-bills", userId, instanceId ?? null],
    userId === null
      ? null
      : (p?: AdminTenantBillsApiAdminV1TenantsUserIdBillsGetParams) =>
          adminTenantBillsApiAdminV1TenantsUserIdBillsGet(userId, p),
    instanceId ? { instance_id: instanceId } : undefined,
  );
}

/** 租户配额覆盖 + 生效值(抽屉「配额」Tab)。 */
export function useTenantQuota(userId: number | null) {
  return useKeyedQuery(
    ["admin", "tenant-quota", userId],
    userId === null ? skipToken : () => adminGetTenantQuotaApiAdminV1TenantsUserIdQuotaGet(userId),
  );
}

export const useSetTenantQuota = adminMutation((v: { userId: number; data: TenantQuotaUpdate }) =>
  adminSetTenantQuotaApiAdminV1TenantsUserIdQuotaPut(v.userId, v.data),
);

/** 实例事件时间线(不限租户,游标分页)。 */
export function useInstanceEvents(uuid: string | null) {
  return useCursorPages(
    ["admin", "instance-events", uuid],
    uuid === null
      ? null
      : (p?: AdminListInstanceEventsApiAdminV1InstancesUuidEventsGetParams) =>
          adminListInstanceEventsApiAdminV1InstancesUuidEventsGet(uuid, p),
    undefined,
  );
}

export function useNodeMetrics(nodeName: string | null, range: string) {
  return useQuery({
    queryKey: ["admin", "node-metrics", nodeName, range],
    queryFn: !nodeName ? skipToken : () => adminNodeMetricsApiAdminV1NodesNodeNameMetricsGet(nodeName, { range }),
    refetchInterval: POLL.steady,
    retry: 0,
  });
}

/** 节点台账;轮询周期由页面给(可暂停),默认 POLL.steady。 */
export function useNodes(options?: { refetchInterval?: number | false }) {
  return useQuery({
    queryKey: adminKeys.nodes,
    queryFn: () => adminListNodesApiAdminV1NodesGet(),
    refetchInterval: options?.refetchInterval ?? POLL.steady,
  });
}

export function usePortPool() {
  return useQuery({
    queryKey: ["admin", "port-pool"],
    queryFn: () => adminPortPoolStatsApiAdminV1NodesPortPoolGet(),
    refetchInterval: POLL.daily,
  });
}

export function useAdminImages(options?: { refetchInterval?: number | false }) {
  return useKeyedQuery(["admin", "images"], () => adminListImagesApiAdminV1ImagesGet(), {
    refetchInterval: options?.refetchInterval,
  });
}

export function useImageNodes(imageId: number, options?: { refetchInterval?: number }) {
  return useQuery({
    queryKey: ["admin", "images", imageId, "nodes"],
    queryFn: () => adminImageNodesApiAdminV1ImagesImageIdNodesGet(imageId),
    refetchInterval: options?.refetchInterval,
  });
}

export function useOversellReport() {
  return useKeyedQuery(["admin", "oversell"], () => oversellReportApiAdminV1ReportsOversellGet(), {
    refetchInterval: POLL.daily,
  });
}

export function useReconciliation(day: string) {
  return useQuery({
    queryKey: ["admin", "reconciliation", day],
    queryFn: () => reconciliationApiAdminV1ReconciliationGet({ day }),
  });
}

/** 告警流:severity 服务端过滤;enabled=false 不取数。 */
export function useAlerts(
  params?: AdminAlertsApiAdminV1AlertsGetParams,
  options?: { refetchInterval?: number | false; enabled?: boolean },
) {
  return useKeyedQuery([...adminKeys.alerts, params], () => adminAlertsApiAdminV1AlertsGet(params), {
    enabled: options?.enabled,
    refetchInterval: options?.refetchInterval,
  });
}

/** 未确认告警数(顶栏铃铛角标 / 总览待处理条)。 */
export function useAlertUnreadCount(options?: { refetchInterval?: number | false }) {
  return useQuery({
    queryKey: [...adminKeys.alerts, "unread-count"],
    queryFn: () => adminAlertsUnreadCountApiAdminV1AlertsUnreadCountGet(),
    refetchInterval: options?.refetchInterval,
  });
}

export const useAckAlert = adminMutation((v: { alertId: number }) =>
  adminAckAlertApiAdminV1AlertsAlertIdAckPost(v.alertId),
);

/** 充值订单(游标分页)。order_no 精确;day=YYYY-MM-DD 按 UTC 下单日过滤。 */
export function useOrders(
  params?: Omit<AdminListOrdersApiAdminV1OrdersGetParams, "cursor" | "limit">,
  options?: { enabled?: boolean },
) {
  return useCursorPages(["admin", "orders", params], adminListOrdersApiAdminV1OrdersGet, params, options);
}

/** 调账单(游标分页):status/user_id/day 服务端过滤。 */
export function useAdjustments(params?: Omit<AdminListAdjustmentsApiAdminV1AdjustmentsGetParams, "cursor" | "limit">) {
  return useCursorPages(
    ["admin", "adjustments", params],
    adminListAdjustmentsApiAdminV1AdjustmentsGet,
    params,
    undefined,
  );
}

/** 退款单列表(游标分页)。status 服务端过滤;day=YYYY-MM-DD(UTC 日);enabled=false 不取数(无权角色)。 */
export function useRefunds(
  params?: Omit<AdminListRefundsApiAdminV1RefundsGetParams, "cursor" | "limit">,
  options?: { enabled?: boolean },
) {
  return useCursorPages(["admin", "refunds", params], adminListRefundsApiAdminV1RefundsGet, params, {
    enabled: options?.enabled,
  });
}

export const useReviewRefund = adminMutation((v: { refundId: number; data: RefundReview }) =>
  adminReviewRefundApiAdminV1RefundsRefundIdReviewPost(v.refundId, v.data),
);

export const usePayoutRefund = adminMutation((v: { refundId: number; data: RefundPayout; idempotencyKey?: string }) =>
  adminPayoutRefundApiAdminV1RefundsRefundIdPayoutPost(
    v.refundId,
    v.data,
    v.idempotencyKey ? { "Idempotency-Key": v.idempotencyKey } : undefined,
  ),
);

export const useCancelRefund = adminMutation((v: { refundId: number; data: RefundCancel }) =>
  adminCancelRefundApiAdminV1RefundsRefundIdCancelPost(v.refundId, v.data),
);

/** 发票申请列表(finance/admin)。status/period(YYYY-MM)服务端过滤;抬头与邮箱默认脱敏,reveal=true + reason 回明文(进 queryKey)。 */
export function useInvoices(params?: AdminListInvoicesApiAdminV1InvoicesGetParams, options?: { enabled?: boolean }) {
  return useKeyedQuery(["admin", "invoices", params], () => adminListInvoicesApiAdminV1InvoicesGet(params), {
    enabled: options?.enabled,
  });
}

export const useIssueInvoice = adminMutation((v: { invoiceId: number; data: InvoiceIssue }) =>
  adminIssueInvoiceApiAdminV1InvoicesInvoiceIdIssuePost(v.invoiceId, v.data),
);

export const useRejectInvoice = adminMutation((v: { invoiceId: number; data: InvoiceReject }) =>
  adminRejectInvoiceApiAdminV1InvoicesInvoiceIdRejectPost(v.invoiceId, v.data),
);

/** 结算缺口列表(游标分页):kind/reason 服务端过滤,unresolved 默认 true;不挂 refetchInterval。 */
export function useSettlementGaps(
  params?: Omit<AdminListSettlementGapsApiAdminV1FinanceSettlementGapsGetParams, "cursor" | "limit">,
  options?: { enabled?: boolean },
) {
  return useCursorPages(
    ["admin", "settlement-gaps", params],
    adminListSettlementGapsApiAdminV1FinanceSettlementGapsGet,
    params,
    { refetchOnWindowFocus: true, enabled: options?.enabled },
  );
}

export const useReplaySettlementGap = adminMutation((v: { gapId: number }) =>
  adminReplaySettlementGapApiAdminV1FinanceSettlementGapsGapIdReplayPost(v.gapId),
);

export const useResolveSettlementGap = adminMutation((v: { gapId: number; data: SettlementGapResolve }) =>
  adminResolveSettlementGapApiAdminV1FinanceSettlementGapsGapIdResolvePost(v.gapId, v.data),
);

/** 工单列表(游标分页):status/category 过滤,user_id/ticket_no 检索;不挂 refetchInterval。 */
export function useTickets(params?: Omit<AdminListTicketsApiAdminV1TicketsGetParams, "cursor" | "limit">) {
  return useCursorPages([...adminKeys.tickets.all, params], adminListTicketsApiAdminV1TicketsGet, params, {
    refetchOnWindowFocus: true,
  });
}

/** 待客服工单计数(60s 轮询)。 */
export function useTicketPendingCount() {
  return useKeyedQuery(
    ["admin", "tickets-count"],
    () => adminTicketsCountApiAdminV1TicketsCountGet({ status: "pending_staff" }),
    {
      refetchInterval: POLL.daily,
    },
  );
}

/** 注销申请列表。status 服务端过滤;行内附执行前校验计数。 */
export function useDeletionRequests(
  params?: AdminListDeletionRequestsApiAdminV1DeletionRequestsGetParams,
  options?: { enabled?: boolean },
) {
  return useKeyedQuery(
    ["admin", "deletion-requests", params],
    () => adminListDeletionRequestsApiAdminV1DeletionRequestsGet(params),
    { enabled: options?.enabled },
  );
}

/** 执行注销(仅超管):校验不过 → 409,detail 含残留清单。 */
export const useApproveDeletion = adminMutation((v: { requestId: number; data: AdminDeletionApprove }) =>
  adminApproveDeletionApiAdminV1DeletionRequestsRequestIdApprovePost(v.requestId, v.data),
);

export const useRejectDeletion = adminMutation((v: { requestId: number; data: AdminDeletionReject }) =>
  adminRejectDeletionApiAdminV1DeletionRequestsRequestIdRejectPost(v.requestId, v.data),
);

/** 工单详情 + 消息流(开启时 15s 轮询)。 */
export function useTicketDetail(ticketId: number | null) {
  return useKeyedQuery(
    adminKeys.tickets.detail(ticketId),
    ticketId === null ? skipToken : () => adminGetTicketApiAdminV1TicketsTicketIdGet(ticketId),
    { refetchInterval: POLL.ticket },
  );
}

// 法务文档(读全角色,写仅 admin)

export type {
  LegalDocCellOut as LegalDocCell,
  LegalDocVersionOut as LegalDocVersion,
  LegalDocVersionCreateLocale as LegalLocale,
} from "@superdl/api-client";

/** 法务文档总览:doc_key × locale 状态格(当前 published + 最新 draft)。 */
export function useLegalDocs() {
  return useKeyedQuery(["admin", "legal-docs"], () => adminListLegalDocsApiAdminV1LegalDocsGet());
}

/** 某 (doc_key, locale) 的版本历史(version 倒序)。 */
export function useLegalDocVersions(docKey: string | null, locale: LegalLocale | null) {
  return useKeyedQuery(
    ["admin", "legal-doc-versions", docKey, locale],
    docKey === null || locale === null
      ? skipToken
      : () => adminListLegalDocVersionsApiAdminV1LegalDocsDocKeyVersionsGet(docKey, { locale }),
  );
}

export const useCreateLegalDocVersion = adminMutation((v: { docKey: string; data: LegalDocVersionCreate }) =>
  adminCreateLegalDocVersionApiAdminV1LegalDocsDocKeyVersionsPost(v.docKey, v.data),
);

export const useUpdateLegalDocVersion = adminMutation((v: { versionId: number; data: LegalDocVersionUpdate }) =>
  adminUpdateLegalDocVersionApiAdminV1LegalDocsVersionsVersionIdPut(v.versionId, v.data),
);

export const usePublishLegalDocVersion = adminMutation((v: { versionId: number }) =>
  adminPublishLegalDocVersionApiAdminV1LegalDocsVersionsVersionIdPublishPost(v.versionId),
);

export const useArchiveLegalDocVersion = adminMutation((v: { versionId: number; data: LegalDocVersionArchive }) =>
  adminArchiveLegalDocVersionApiAdminV1LegalDocsVersionsVersionIdArchivePost(v.versionId, v.data),
);

export const useReplyTicket = adminMutation((v: { ticketId: number; data: AdminTicketReply }) =>
  adminReplyTicketApiAdminV1TicketsTicketIdReplyPost(v.ticketId, v.data),
);

export const useUpdateTicketStatus = adminMutation((v: { ticketId: number; data: AdminTicketStatusUpdate }) =>
  adminUpdateTicketStatusApiAdminV1TicketsTicketIdStatusPost(v.ticketId, v.data),
);

/** 审计检索:响应是数组,游标 = 末行 id 的 base64。 */
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
    getNextPageParam: (last) => {
      const tail = last[last.length - 1];
      return last.length >= limit && tail ? btoa(String(tail.id)) : undefined;
    },
  });
  return { ...q, queryKey };
}

// 变更 hooks

/** 登录返回二要素挑战票;正式 token 经 useMfaSetupConfirm / useMfaVerify。 */
export const useAdminLogin = adminMutation((v: { data: AdminLoginRequest }) =>
  adminLoginApiAdminV1AuthLoginPost(v.data),
);

// TOTP MFA
export const useMfaSetupBegin = adminMutation((v: { ticket: string }) =>
  mfaSetupBeginApiAdminV1AuthMfaSetupBeginPost({ ticket: v.ticket }),
);

export const useMfaSetupConfirm = adminMutation((v: { ticket: string; code: string }) =>
  mfaSetupConfirmApiAdminV1AuthMfaSetupConfirmPost({ ticket: v.ticket, code: v.code }),
);

export const useMfaVerify = adminMutation((v: { ticket: string; code: string }) =>
  mfaLoginVerifyApiAdminV1AuthLoginMfaPost({ ticket: v.ticket, code: v.code }),
);

export const useRegenerateRecoveryCodes = adminMutation(() =>
  mfaRegenerateRecoveryCodesApiAdminV1MeMfaRecoveryCodesPost(),
);

export const useResetAdminMfa = adminMutation((v: { id: number; reason: string }) =>
  mfaResetApiAdminV1AdminsAdminIdMfaResetPost(v.id, { reason: v.reason }),
);

export const useCreateSku = adminMutation((v: { data: SkuCreate }) => adminCreateSkuApiAdminV1SkusPost(v.data));

export const useUpdateSku = adminMutation((v: { skuId: number; data: SkuUpdate; force?: boolean }) =>
  adminUpdateSkuApiAdminV1SkusSkuIdPatch(v.skuId, v.data, v.force ? { force: true } : undefined),
);

export function useEnrollments(options?: { active?: boolean; refetchInterval?: number }) {
  return useKeyedQuery(
    ["admin", "node-enrollments", options?.active],
    () => adminListEnrollmentsApiAdminV1NodeEnrollmentsGet(options?.active ? { active: true } : undefined),
    { refetchInterval: options?.refetchInterval },
  );
}

export const useCreateEnrollment = adminMutation((v: { data: EnrollmentCreate; idempotencyKey?: string }) =>
  adminCreateEnrollmentApiAdminV1NodeEnrollmentsPost(
    v.data,
    v.idempotencyKey ? { "Idempotency-Key": v.idempotencyKey } : undefined,
  ),
);

export const useRegenerateEnrollment = adminMutation((v: { enrollmentId: number; data: EnrollmentRegenerateRequest }) =>
  adminRegenerateEnrollmentApiAdminV1NodeEnrollmentsEnrollmentIdRegeneratePost(v.enrollmentId, v.data),
);

export const useRevokeEnrollment = adminMutation((v: { enrollmentId: number; data: EnrollmentRevokeRequest }) =>
  adminRevokeEnrollmentApiAdminV1NodeEnrollmentsEnrollmentIdRevokePost(v.enrollmentId, v.data),
);

export const useCordonNode = adminMutation((v: { nodeName: string; on: boolean; data: NodeCordonRequest }) =>
  v.on
    ? adminCordonNodeApiAdminV1NodesNodeNameCordonPost(v.nodeName, v.data)
    : adminUncordonNodeApiAdminV1NodesNodeNameUncordonPost(v.nodeName, v.data),
);

export const useCreateImage = adminMutation((v: { data: ImageCreate }) => adminCreateImageApiAdminV1ImagesPost(v.data));

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

/** 强制回收一台竞价实例(与强制停止是两条后端路径)。 */
export const usePreemptInstance = adminMutation((v: { uuid: string; data: AdminForceStopRequest }) =>
  adminPreemptApiAdminV1InstancesUuidPreemptPost(v.uuid, v.data),
);

/** 冻结(原因必填);响应 instances_stopped = 一并停掉的 running 实例数。 */
export const useFreezeTenant = adminMutation((v: { userId: number; data: TenantFreezeRequest }) =>
  adminFreezeTenantApiAdminV1TenantsUserIdFreezePost(v.userId, v.data),
);

export const useUnfreezeTenant = adminMutation((v: { userId: number; data: TenantFreezeRequest }) =>
  adminUnfreezeTenantApiAdminV1TenantsUserIdUnfreezePost(v.userId, v.data),
);

export const useCreateAdjustment = adminMutation((v: { data: AdjustmentCreate; idempotencyKey?: string }) =>
  adminCreateAdjustmentApiAdminV1AdjustmentsPost(
    v.data,
    v.idempotencyKey ? { "Idempotency-Key": v.idempotencyKey } : undefined,
  ),
);

export const useReviewAdjustment = adminMutation((v: { adjustmentId: number; data: AdjustmentReview }) =>
  adminReviewAdjustmentApiAdminV1AdjustmentsAdjustmentIdReviewPost(v.adjustmentId, v.data),
);

// 运营:死信重放 / 收入报表 / 公告

export function useAnomalies() {
  return useKeyedQuery(["admin", "anomalies"], () => adminPaymentAnomaliesApiAdminV1FinanceAnomaliesGet(), {
    refetchInterval: POLL.daily,
  });
}

export function useDeadTasks(options?: { enabled?: boolean }) {
  return useKeyedQuery(["admin", "outbox-dead"], () => adminListDeadTasksApiAdminV1OutboxDeadGet(), {
    enabled: options?.enabled,
    refetchInterval: POLL.daily,
  });
}

export function useRevenueReport() {
  const tz = -new Date().getTimezoneOffset();
  return useQuery({
    queryKey: ["admin", "revenue", tz],
    queryFn: () => revenueReportApiAdminV1ReportsRevenueGet({ tz_offset_minutes: tz }),
    refetchInterval: POLL.daily,
  });
}

export function useAdminPolicies() {
  return useKeyedQuery(["admin", "policies"], () => adminGetPoliciesApiAdminV1PoliciesGet());
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

/** 重放死信(原因必填)。 */
export const useRetryDeadTask = adminMutation((v: { taskId: number; data: OutboxRetryRequest }) =>
  adminRetryDeadTaskApiAdminV1OutboxTaskIdRetryPost(v.taskId, v.data),
);

export const useDiscardDeadTask = adminMutation((v: { taskId: number; data: OutboxDiscardRequest }) =>
  adminDiscardDeadTaskApiAdminV1OutboxTaskIdDiscardPost(v.taskId, v.data),
);

export const usePublishAnnouncement = adminMutation((v: { data: AnnouncementCreate; idempotencyKey?: string }) =>
  adminPublishAnnouncementApiAdminV1AnnouncementsPost(
    v.data,
    v.idempotencyKey ? { "Idempotency-Key": v.idempotencyKey } : undefined,
  ),
);

/** 公告历史(含已撤回;固定截断 200)。 */
export function useAnnouncements() {
  return useKeyedQuery(["admin", "announcements"], () => adminListAnnouncementsApiAdminV1AnnouncementsGet());
}

export const useRevokeAnnouncement = adminMutation((v: { announcementId: number; data: AnnouncementRevoke }) =>
  adminRevokeAnnouncementApiAdminV1AnnouncementsAnnouncementIdRevokePost(v.announcementId, v.data),
);

export const useUpdatePolicies = adminMutation((v: { data: PolicyUpdateRequest }) =>
  adminUpdatePoliciesApiAdminV1PoliciesPut(v.data),
);

// 平台配置(仅 admin 角色)

export function usePlatformConfig(options?: { enabled?: boolean }) {
  return useKeyedQuery(["admin", "platform-config"], () => adminGetPlatformConfigApiAdminV1PlatformConfigGet(), {
    // 非 admin 角色 403,调用方按角色传 enabled
    enabled: options?.enabled,
    refetchOnWindowFocus: false,
    retry: false,
  });
}

export const useUpdatePlatformConfig = adminMutation((v: { data: PlatformConfigUpdateRequest }) =>
  adminUpdatePlatformConfigApiAdminV1PlatformConfigPut(v.data),
);

export const useTestRegistry = adminMutation(() => adminTestRegistryApiAdminV1PlatformConfigTestRegistryPost());

export const useTestSms = adminMutation((v: { data: SmsTestRequest }) =>
  adminTestSmsApiAdminV1PlatformConfigTestSmsPost(v.data),
);

// 管理员账号

export function useAdminAccounts() {
  return useKeyedQuery(["admin", "admins"], () => adminListAdminsApiAdminV1AdminsGet());
}

export const useCreateAdminAccount = adminMutation((v: { data: AdminCreateRequest }) =>
  adminCreateAdminApiAdminV1AdminsPost(v.data),
);

export const useUpdateAdminAccount = adminMutation((v: { id: number; data: AdminUpdateRequest }) =>
  adminUpdateAdminApiAdminV1AdminsAdminIdPatch(v.id, v.data),
);

export const useResetAdminPassword = adminMutation((v: { id: number; data: AdminResetPasswordRequest }) =>
  adminResetPasswordApiAdminV1AdminsAdminIdResetPasswordPost(v.id, v.data),
);

export const useChangeOwnPassword = adminMutation((v: { data: AdminSelfPasswordRequest }) =>
  adminChangeOwnPasswordApiAdminV1MePasswordPost(v.data),
);

// 总览/上下文

/** /me 校准角色(路由守卫用)。 */
export function fetchAdminMe(): Promise<AdminOut> {
  return adminMeApiAdminV1MeGet();
}

/** 总览聚合(精确 COUNT,全角色可读);轮询周期由页面给(可暂停),默认 POLL.daily。 */
export function useOverview(options?: { refetchInterval?: number | false }) {
  return useKeyedQuery(["admin", "overview"], () => adminOverviewApiAdminV1OverviewGet(), {
    refetchInterval: options?.refetchInterval ?? POLL.daily,
  });
}

/** 调账前置上下文:租户身份与资金现状;不存在 → 404。 */
export function useAdjustContext(userId: number | null) {
  return useQuery({
    queryKey: ["admin", "adjust-context", userId],
    retry: 0,
    staleTime: 30_000,
    queryFn: userId === null ? skipToken : () => adminAdjustContextApiAdminV1TenantsUserIdAdjustContextGet(userId),
  });
}

/** 改价影响面:该 SKU 当前活跃实例数/用户数/卡数。 */
export function useSkuImpact(skuId: number | null) {
  return useQuery({
    queryKey: ["admin", "sku-impact", skuId],
    staleTime: 30_000,
    queryFn: skuId === null ? skipToken : () => adminSkuImpactApiAdminV1SkusSkuIdImpactGet(skuId),
  });
}

// CSV 导出(截断判定见 @superdl/ui downloadCsvChecked)

type CsvLang = "zh-CN" | "en-US";

/** CSV 导出工厂:fetcher 文本响应 → downloadCsvChecked;name 为串或按入参派生。 */
function makeCsvExporter<A extends unknown[]>(
  fetcher: (...args: A) => Promise<unknown>,
  name: string | ((...args: A) => string),
): (...args: A) => Promise<"ok" | "truncated"> {
  return async (...args: A) => {
    const text = (await fetcher(...args)) as string;
    return downloadCsvChecked(typeof name === "function" ? name(...args) : name, text);
  };
}

/** 导出参数合并:当前筛选 + 时区 + 语言。 */
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

/** 发票导出:跟随当前筛选(status/period)与明文档位(reveal/reason)。 */
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
  (day: string, lang: CsvLang) => reconciliationExportApiAdminV1ReconciliationExportGet({ day, lang }),
  (day: string) => `superdl-reconciliation-${day}.csv`,
);
