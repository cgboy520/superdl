/** Data access layer: TanStack Query hooks over the fetchers generated in @superdl/api-client. */

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
  adminGetDeploymentApiAdminV1DeploymentGet,
  adminGetPlatformConfigApiAdminV1PlatformConfigGet,
  adminClusterStatusApiAdminV1ClusterStatusGet,
  adminClusterTestConnectionApiAdminV1ClusterTestConnectionPost,
  adminComponentProbeApiAdminV1ClusterComponentsComponentKeyProbeGet,
  adminGpuModelAggregatesApiAdminV1ClusterGpuModelsGet,
  adminGetPoliciesApiAdminV1PoliciesGet,
  adminTestRegistryApiAdminV1PlatformConfigTestRegistryPost,
  adminTestEmailApiAdminV1PlatformConfigTestEmailPost,
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
  adminDecommissionNodeApiAdminV1NodesNodeNameDecommissionPost,
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
  adminSwitchNodePoolApiAdminV1NodesNodeNameSwitchPoolPost,
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
  NodeDecommissionRequest,
  NodeSwitchPoolRequest,
  OutboxRetryRequest,
  OrderBackfillRequest,
  OutboxDiscardRequest,
  PlatformConfigUpdateRequest,
  PolicyUpdateRequest,
  EmailTestRequest,
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
  type InfiniteData,
  type Query,
  useMutation,
  useQuery,
  type SkipToken,
  type UseMutationOptions,
} from "@tanstack/react-query";

import { downloadCsvChecked, POLL } from "@superdl/ui";

export { isApiError } from "@superdl/api-client";
export type {
  EnrollmentCommandOut,
  NodeSwitchPoolOut,
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
  DeploymentIdentityOut as DeploymentIdentity,
  NodeOut as NodeRow,
  OversellPoolOut as OversellRow,
  PaymentAnomalyOut as AnomalyRow,
  PlatformConfigItemOut as PlatformConfigItem,
  ReconciliationOut as ReconciliationReport,
  TenantOut as TenantRow,
  InstanceEventOut as InstanceEvent,
  GpuModelAggregateOut as GpuModelAggregate,
  ClusterComponentOut as ClusterComponent,
  ComponentFactOut as ComponentFact,
  ComponentObjectOut as ComponentObject,
  ComponentProbeOut as ComponentProbe,
  CapacityWarningOut as CapacityWarning,
  RefundPayout,
} from "@superdl/api-client";

interface MutOpts<TData, TVars> {
  mutation?: UseMutationOptions<TData, unknown, TVars>;
}

/** Mutation hook factory: mutationFn + pass-through opts.mutation. Argument-less mutations omit the variables (TVars defaults to void, mutate() is called bare). */
function adminMutation<TData, TVars = void>(mutationFn: (v: TVars) => Promise<TData>) {
  return function useBoundMutation(opts?: MutOpts<TData, TVars>) {
    return useMutation({ mutationFn, ...opts?.mutation });
  };
}

/** Common cursor pagination shape: params carry limit/cursor, the response carries next_cursor (audit does not use it). */
interface CursorParams {
  limit?: number;
  cursor?: string;
}
interface CursorPage {
  next_cursor?: string | null;
}

/** Query keys, detail-key factories and invalidation prefixes shared across files. */
export const adminKeys = {
  alerts: ["admin", "alerts"],
  tickets: { all: ["admin", "tickets"], detail: (id: number | null) => ["admin", "ticket", id] },
  services: ["admin", "services"],
  nodes: ["admin", "nodes"],
  enrollments: ["admin", "node-enrollments"],
  skus: ["admin", "skus"],
};

/** Query hook: the result carries its queryKey, queryFn supports skipToken. */
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

/** Cursor-paginated useInfiniteQuery skeleton; a null fetcher = precondition unmet, nothing is fetched (skipToken). The result carries its queryKey.
 *  Polling runs only while staying on the first page: refetch re-pulls every loaded page, so after paging the auto refresh stops and manual refresh takes over. */
function useCursorPages<TPage extends CursorPage, P extends CursorParams>(
  key: readonly unknown[],
  fetcher: ((params?: P) => Promise<TPage>) | null,
  params: Omit<P, "cursor" | "limit"> | undefined,
  opts?: {
    enabled?: boolean;
    limit?: number;
    refetchOnWindowFocus?: boolean;
    refetchInterval?: number | false;
  },
) {
  const limit = opts?.limit ?? 50;
  const q = useInfiniteQuery({
    queryKey: key,
    enabled: opts?.enabled ?? true,
    refetchOnWindowFocus: opts?.refetchOnWindowFocus,
    refetchInterval: (query: Query<TPage, Error, InfiniteData<TPage, string | undefined>>) =>
      (query.state.data?.pages.length ?? 1) > 1 ? false : (opts?.refetchInterval ?? false),
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
  return useKeyedQuery(adminKeys.skus, () => adminListSkusApiAdminV1SkusGet());
}

export function useClusterStatus() {
  return useKeyedQuery(["admin", "cluster-status"], () => adminClusterStatusApiAdminV1ClusterStatusGet(), {
    refetchInterval: POLL.steady,
  });
}

export const useTestClusterConnection = adminMutation(() =>
  adminClusterTestConnectionApiAdminV1ClusterTestConnectionPost(),
);

/** Live deep probe of a health item, no retry on failure. */
export function useComponentProbe(componentKey: string) {
  return useKeyedQuery(
    ["admin", "component-probe", componentKey],
    () => adminComponentProbeApiAdminV1ClusterComponentsComponentKeyProbeGet(componentKey),
    { retry: false, staleTime: POLL.steady },
  );
}

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

/** Instance list (cursor pagination): status/user_id/q/node_name server-side filters. */
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

/** Online service list (all tenants); the response carries total when filtered by user_id. */
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

/** Tenant list (cursor pagination). q: `@` → exact email, leading `+` → exact E.164 phone, digits → phone suffix, otherwise email prefix. */
export function useTenants(params?: Omit<AdminListTenantsApiAdminV1TenantsGetParams, "cursor" | "limit">) {
  return useCursorPages(["admin", "tenants", params], adminListTenantsApiAdminV1TenantsGet, params, undefined);
}

/** Tenant ledger (cursor pagination). */
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

/** Tenant hourly bills; filtered by instance when instanceId is set. */
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

/** Tenant quota overrides + effective values (drawer "Quota" tab). */
export function useTenantQuota(userId: number | null) {
  return useKeyedQuery(
    ["admin", "tenant-quota", userId],
    userId === null ? skipToken : () => adminGetTenantQuotaApiAdminV1TenantsUserIdQuotaGet(userId),
  );
}

export const useSetTenantQuota = adminMutation((v: { userId: number; data: TenantQuotaUpdate }) =>
  adminSetTenantQuotaApiAdminV1TenantsUserIdQuotaPut(v.userId, v.data),
);

/** Instance event timeline (all tenants, cursor pagination). */
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

/** Node ledger; the polling interval is given by the page (pausable), default POLL.steady. */
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

/** Alert stream front page (top-bar bell / overview card): server-side filters, first page only; enabled=false fetches nothing. */
export function useAlerts(
  params?: AdminAlertsApiAdminV1AlertsGetParams,
  options?: { refetchInterval?: number | false; enabled?: boolean },
) {
  return useKeyedQuery([...adminKeys.alerts, params], () => adminAlertsApiAdminV1AlertsGet(params), {
    enabled: options?.enabled,
    refetchInterval: options?.refetchInterval,
  });
}

/** Alert centre: cursor pagination, severity / type / acknowledgement state all filtered server-side. */
export function useAlertPages(
  params?: Omit<AdminAlertsApiAdminV1AlertsGetParams, "cursor" | "limit">,
  options?: { refetchInterval?: number | false },
) {
  return useCursorPages([...adminKeys.alerts, "pages", params], adminAlertsApiAdminV1AlertsGet, params, {
    refetchInterval: options?.refetchInterval,
  });
}

/** Unacknowledged alert count (top-bar bell badge / overview pending bar). */
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

/** Recharge orders (cursor pagination). order_no exact; day=YYYY-MM-DD filters by UTC order date. */
export function useOrders(
  params?: Omit<AdminListOrdersApiAdminV1OrdersGetParams, "cursor" | "limit">,
  options?: { enabled?: boolean },
) {
  return useCursorPages(["admin", "orders", params], adminListOrdersApiAdminV1OrdersGet, params, options);
}

/** Adjustments (cursor pagination): status/user_id/day server-side filters. */
export function useAdjustments(params?: Omit<AdminListAdjustmentsApiAdminV1AdjustmentsGetParams, "cursor" | "limit">) {
  return useCursorPages(
    ["admin", "adjustments", params],
    adminListAdjustmentsApiAdminV1AdjustmentsGet,
    params,
    undefined,
  );
}

/** Refund list (cursor pagination). status server-side filter; day=YYYY-MM-DD (UTC day); enabled=false fetches nothing (roles without access). */
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

/** Invoice request list (finance/admin). status/period (YYYY-MM) server-side filters; title and email masked by default, reveal=true + reason returns plaintext (part of the queryKey). */
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

/** Settlement gap list (cursor pagination): kind/reason server-side filters, unresolved defaults to true; no refetchInterval. */
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

/** Ticket list (cursor pagination): status/category filters, user_id/ticket_no search; no refetchInterval. */
export function useTickets(params?: Omit<AdminListTicketsApiAdminV1TicketsGetParams, "cursor" | "limit">) {
  return useCursorPages([...adminKeys.tickets.all, params], adminListTicketsApiAdminV1TicketsGet, params, {
    refetchOnWindowFocus: true,
  });
}

/** Count of tickets awaiting support (60 s polling). */
export function useTicketPendingCount() {
  return useKeyedQuery(
    ["admin", "tickets-count"],
    () => adminTicketsCountApiAdminV1TicketsCountGet({ status: "pending_staff" }),
    {
      refetchInterval: POLL.daily,
    },
  );
}

/** Deletion request list. status server-side filter; rows carry the pre-execution check counts. */
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

/** Execute a deletion (super admin only): failed checks → 409, detail lists the leftovers. */
export const useApproveDeletion = adminMutation((v: { requestId: number; data: AdminDeletionApprove }) =>
  adminApproveDeletionApiAdminV1DeletionRequestsRequestIdApprovePost(v.requestId, v.data),
);

export const useRejectDeletion = adminMutation((v: { requestId: number; data: AdminDeletionReject }) =>
  adminRejectDeletionApiAdminV1DeletionRequestsRequestIdRejectPost(v.requestId, v.data),
);

/** Ticket detail + message stream (15 s polling while enabled). */
export function useTicketDetail(ticketId: number | null) {
  return useKeyedQuery(
    adminKeys.tickets.detail(ticketId),
    ticketId === null ? skipToken : () => adminGetTicketApiAdminV1TicketsTicketIdGet(ticketId),
    { refetchInterval: POLL.ticket },
  );
}

export type {
  LegalDocCellOut as LegalDocCell,
  LegalDocVersionOut as LegalDocVersion,
  LegalDocVersionCreateLocale as LegalLocale,
} from "@superdl/api-client";

/** Legal document overview: doc_key × locale status grid (current published + latest draft). */
export function useLegalDocs() {
  return useKeyedQuery(["admin", "legal-docs"], () => adminListLegalDocsApiAdminV1LegalDocsGet());
}

/** Version history of one (doc_key, locale) (version descending). */
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

/** Audit search: the response is an array, the cursor is the base64 of the last row id. */
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

/** Login request. */
export const useAdminLogin = adminMutation((v: { data: AdminLoginRequest }) =>
  adminLoginApiAdminV1AuthLoginPost(v.data),
);

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

/** Pool switch: the desired pool goes to the ledger, label convergence runs through the outbox; no rows are created, repeated submits are blocked by the same-pool gate. */
export const useSwitchNodePool = adminMutation((v: { nodeName: string; data: NodeSwitchPoolRequest }) =>
  adminSwitchNodePoolApiAdminV1NodesNodeNameSwitchPoolPost(v.nodeName, v.data),
);

/** Decommission (irreversible); the backend returns 409 while the node holds unreleased instances, force lets it through. */
export const useDecommissionNode = adminMutation(
  (v: { nodeName: string; data: NodeDecommissionRequest; force?: boolean }) =>
    adminDecommissionNodeApiAdminV1NodesNodeNameDecommissionPost(
      v.nodeName,
      v.data,
      v.force ? { force: true } : undefined,
    ),
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

/** Force-reclaim one spot instance (a separate backend path from force stop). */
export const usePreemptInstance = adminMutation((v: { uuid: string; data: AdminForceStopRequest }) =>
  adminPreemptApiAdminV1InstancesUuidPreemptPost(v.uuid, v.data),
);

/** Freeze (reason required); the response instances_stopped = running instances stopped along the way. */
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

/** Replay a dead-letter task (reason required). */
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

/** Announcement history (withdrawn included; fixed cap 200). */
export function useAnnouncements() {
  return useKeyedQuery(["admin", "announcements"], () => adminListAnnouncementsApiAdminV1AnnouncementsGet());
}

export const useRevokeAnnouncement = adminMutation((v: { announcementId: number; data: AnnouncementRevoke }) =>
  adminRevokeAnnouncementApiAdminV1AnnouncementsAnnouncementIdRevokePost(v.announcementId, v.data),
);

export const useUpdatePolicies = adminMutation((v: { data: PolicyUpdateRequest }) =>
  adminUpdatePoliciesApiAdminV1PoliciesPut(v.data),
);

/** Deployment identity readable by every console role (currency for the CurrencyProvider). */
export function useDeployment(options?: { enabled?: boolean }) {
  return useKeyedQuery(["admin", "deployment"], () => adminGetDeploymentApiAdminV1DeploymentGet(), {
    enabled: options?.enabled,
    refetchOnWindowFocus: false,
    staleTime: 60 * 60_000,
  });
}

export function usePlatformConfig(options?: { enabled?: boolean }) {
  return useKeyedQuery(["admin", "platform-config"], () => adminGetPlatformConfigApiAdminV1PlatformConfigGet(), {
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
export const useTestEmail = adminMutation((v: { data: EmailTestRequest }) =>
  adminTestEmailApiAdminV1PlatformConfigTestEmailPost(v.data),
);

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

/** /me role calibration (route guard). */
export function fetchAdminMe(): Promise<AdminOut> {
  return adminMeApiAdminV1MeGet();
}

/** Overview aggregates (exact COUNT, readable by every role); the polling interval is given by the page (pausable), default POLL.daily. */
export function useOverview(options?: { refetchInterval?: number | false }) {
  return useKeyedQuery(["admin", "overview"], () => adminOverviewApiAdminV1OverviewGet(), {
    refetchInterval: options?.refetchInterval ?? POLL.daily,
  });
}

/** Adjustment pre-context: tenant identity and funds; missing → 404. */
export function useAdjustContext(userId: number | null) {
  return useQuery({
    queryKey: ["admin", "adjust-context", userId],
    retry: 0,
    staleTime: 30_000,
    queryFn: userId === null ? skipToken : () => adminAdjustContextApiAdminV1TenantsUserIdAdjustContextGet(userId),
  });
}

/** Reprice impact: the SKU's current active instances / users / cards. */
export function useSkuImpact(skuId: number | null) {
  return useQuery({
    queryKey: ["admin", "sku-impact", skuId],
    staleTime: 30_000,
    queryFn: skuId === null ? skipToken : () => adminSkuImpactApiAdminV1SkusSkuIdImpactGet(skuId),
  });
}

type CsvLang = "zh-CN" | "en-US";

/** CSV export factory: fetcher text response → downloadCsvChecked; name is a string or derived from the arguments. */
function makeCsvExporter<A extends unknown[]>(
  fetcher: (...args: A) => Promise<unknown>,
  name: string | ((...args: A) => string),
): (...args: A) => Promise<"ok" | "truncated"> {
  return async (...args: A) => {
    const text = (await fetcher(...args)) as string;
    return downloadCsvChecked(typeof name === "function" ? name(...args) : name, text);
  };
}

/** Export parameter merge: current filters + timezone + language. */
const withTzLang = <P extends object>(params: P | undefined, tz: number, lang: CsvLang): P =>
  ({ ...params, tz_offset_minutes: tz, lang }) as P;

/** Order export: follows the current filters (status/order_no/user_id/day). */
export const exportOrdersCsv = makeCsvExporter(
  (params: AdminOrdersExportApiAdminV1OrdersExportGetParams | undefined, tz: number, lang: CsvLang) =>
    adminOrdersExportApiAdminV1OrdersExportGet(withTzLang(params, tz, lang)),
  (params: AdminOrdersExportApiAdminV1OrdersExportGetParams | undefined) =>
    `superdl-orders-${params?.day ?? "all"}.csv`,
);

/** Export the adjustment CSV for the given filters, with timezone and language. */
export const exportAdjustmentsCsv = makeCsvExporter(
  (params: AdminAdjustmentsExportApiAdminV1AdjustmentsExportGetParams | undefined, tz: number, lang: CsvLang) =>
    adminAdjustmentsExportApiAdminV1AdjustmentsExportGet(withTzLang(params, tz, lang)),
  "superdl-adjustments.csv",
);

/** Export the refund CSV for the given filters, with timezone and language. */
export const exportRefundsCsv = makeCsvExporter(
  (params: AdminRefundsExportApiAdminV1RefundsExportGetParams | undefined, tz: number, lang: CsvLang) =>
    adminRefundsExportApiAdminV1RefundsExportGet(withTzLang(params, tz, lang)),
  "superdl-refunds.csv",
);

/** Invoice export: follows the current filters (status/period) and the plaintext level (reveal/reason). */
export const exportInvoicesCsv = makeCsvExporter(
  (params: AdminInvoicesExportApiAdminV1InvoicesExportGetParams | undefined, tz: number, lang: CsvLang) =>
    adminInvoicesExportApiAdminV1InvoicesExportGet(withTzLang(params, tz, lang)),
  "superdl-invoices.csv",
);

/** Tenant ledger export (drawer "Ledger" tab). */
export const exportTenantLedgerCsv = makeCsvExporter(
  (userId: number, tz: number, lang: CsvLang) =>
    adminTenantLedgerExportApiAdminV1TenantsUserIdLedgerExportGet(userId, {
      tz_offset_minutes: tz,
      lang,
    }),
  (userId: number) => `superdl-tenant-${userId}-ledger.csv`,
);

/** Audit search export: follows the current filters (actor_type/actor_id/q/since/until). */
export const exportAuditCsv = makeCsvExporter(
  (filters: AdminAuditExportApiAdminV1AuditExportGetParams | undefined, tz: number, lang: CsvLang) =>
    adminAuditExportApiAdminV1AuditExportGet(withTzLang(filters, tz, lang)),
  "superdl-audit.csv",
);

/** Reconciliation export (by day, no tz parameter). */
export const exportReconciliationCsv = makeCsvExporter(
  (day: string, lang: CsvLang) => reconciliationExportApiAdminV1ReconciliationExportGet({ day, lang }),
  (day: string) => `superdl-reconciliation-${day}.csv`,
);
