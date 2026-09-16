/** Read query layer: generated fetcher + useQuery; query keys only in ./keys.ts, polling options centralised here. The error type is registered globally as ApiError via ./register.d.ts. */

import { POLL } from "@superdl/ui";
import {
  getDeletionRequestApiV1MeDeletionRequestGet,
  getLegalDocApiV1LegalDocKeyGet,
  billDailySummaryApiV1BillsDailySummaryGet,
  billSummaryApiV1BillsSummaryGet,
  getInstanceAccessApiV1InstancesUuidAccessGet,
  getInstanceApiV1InstancesUuidGet,
  getInstanceLogsApiV1InstancesUuidLogsGet,
  getInstanceMetricsApiV1InstancesUuidMetricsGet,
  getLedgerApiV1WalletLedgerGet,
  getPoliciesApiV1PoliciesGet,
  getRechargeApiV1WalletRechargesOrderNoGet,
  getServiceApiV1ServicesSlugGet,
  getServiceLogsApiV1ServicesSlugLogsGet,
  getSiteConfigApiV1SiteConfigGet,
  getWalletApiV1WalletGet,
  instancesMetricsSummaryApiV1MetricsInstancesGet,
  listDisksApiV1DisksGet,
  listHourlyBillsApiV1BillsHourlyGet,
  listImagesApiV1ImagesGet,
  listExpiringInstancesApiV1InstancesExpiringGet,
  listInstanceEventsApiV1InstancesUuidEventsGet,
  listInstancesApiV1InstancesGet,
  listApiKeysApiV1ServicesSlugApiKeysGet,
  listRevisionsApiV1ServicesSlugRevisionsGet,
  listServiceBillsApiV1ServicesSlugBillsGet,
  listServiceEventsApiV1ServicesSlugEventsGet,
  listServicesApiV1ServicesGet,
  listInvoiceEligibleApiV1BillingInvoicesEligibleGet,
  listMyInvoicesApiV1BillingInvoicesGet,
  listMyRefundsApiV1WalletRefundsGet,
  listMyTicketsApiV1TicketsGet,
  getMyTicketApiV1TicketsTicketIdGet,
  listNotificationsApiV1NotificationsGet,
  unreadCountApiV1NotificationsUnreadCountGet,
  listRefundableOrdersApiV1WalletRefundsEligibleOrdersGet,
  listSkusApiV1SkusGet,
  listSshKeysApiV1SshKeysGet,
  meApiV1MeGet,
} from "@superdl/api-client";
import type {
  ApiError,
  ApiKeyOut,
  GetInstanceLogsApiV1InstancesUuidLogsGetParams,
  GetInstanceMetricsApiV1InstancesUuidMetricsGetParams,
  GetServiceLogsApiV1ServicesSlugLogsGetParams,
  InstanceLogsOut,
  InstanceOut,
  ListHourlyBillsApiV1BillsHourlyGetParams,
  ListInstanceEventsApiV1InstancesUuidEventsGetParams,
  ListServiceBillsApiV1ServicesSlugBillsGetParams,
  ListServiceEventsApiV1ServicesSlugEventsGetParams,
  PageInstanceOut,
  PageServiceOut,
  RechargeOut,
  ServiceOut,
  TicketDetailOut,
} from "@superdl/api-client";
import { isTransientInstanceStatus, isTransientServiceStatus } from "@superdl/ui";
import { useInfiniteQuery, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import type { InfiniteData } from "@tanstack/react-query";
import { useEffect, useMemo, useRef } from "react";

import { keys } from "./keys";

/** Query options: only these four pass; select and other shape transforms are fixed inside the hooks. The error type is pinned globally to ApiError by register.d.ts. */
export interface QueryOpts<T = unknown> {
  enabled?: boolean;
  /** Polling interval: a number, false, or a function of the query state snapshot. */
  refetchInterval?:
    | number
    | false
    | ((query: {
        state: { data: T | undefined; status: "pending" | "error" | "success" };
      }) => number | false | undefined);
  retry?: number | boolean;
  staleTime?: number;
}

/** Common cursor pagination shape: params carry limit/cursor, the response next_cursor. */
interface CursorParams {
  limit?: number;
  cursor?: string;
}
interface CursorPage {
  next_cursor?: string | null;
}

/** Cursor-paginated query with interval polling and window-focus refetch off. */
function useCursorPages<TPage extends CursorPage, P extends CursorParams>(
  key: readonly unknown[],
  fetcher: (params?: P) => Promise<TPage>,
  params: Omit<P, "cursor" | "limit"> | undefined,
  limit: number,
) {
  return useInfiniteQuery<TPage, ApiError, InfiniteData<TPage>, readonly unknown[], string | undefined>({
    queryKey: key,
    initialPageParam: undefined,
    queryFn: ({ pageParam }) => fetcher({ ...params, limit, ...(pageParam ? { cursor: pageParam } : {}) } as P),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    refetchOnWindowFocus: false,
  });
}

export const useMe = (opts?: QueryOpts) => useQuery({ queryKey: keys.me, queryFn: () => meApiV1MeGet(), ...opts });
/** My deletion request: pending or the most recent; null = never requested. */
export const useMyDeletionRequest = (opts?: QueryOpts) =>
  useQuery({ queryKey: keys.deletionRequest, queryFn: () => getDeletionRequestApiV1MeDeletionRequestGet(), ...opts });
export const useWallet = (opts?: QueryOpts) =>
  useQuery({ queryKey: keys.wallet, queryFn: () => getWalletApiV1WalletGet(), ...opts });
export const useNotifications = (params?: { unread?: boolean }, opts?: QueryOpts) =>
  useQuery({
    queryKey: keys.notifications.list(params),
    queryFn: () => listNotificationsApiV1NotificationsGet(params),
    ...opts,
  });
/** Unread badge: 30 s polling of count only. */
export const useUnreadCount = (opts?: QueryOpts) =>
  useQuery({
    queryKey: keys.notifications.unreadCount,
    queryFn: () => unreadCountApiV1NotificationsUnreadCountGet(),
    ...opts,
  });
/** Notification popover cursor pagination. */
export const useNotificationPages = (params?: { unread?: boolean }) =>
  useCursorPages(keys.notifications.pages(params), listNotificationsApiV1NotificationsGet, params, 20);
export const useSkus = (opts?: QueryOpts) =>
  useQuery({ queryKey: keys.skus, queryFn: () => listSkusApiV1SkusGet(), ...opts });
export const useImages = () => useQuery({ queryKey: keys.images, queryFn: () => listImagesApiV1ImagesGet() });
export const useSshKeys = () => useQuery({ queryKey: keys.sshKeys, queryFn: () => listSshKeysApiV1SshKeysGet() });
export const useDisks = (opts?: QueryOpts) =>
  useQuery({ queryKey: keys.disks, queryFn: () => listDisksApiV1DisksGet(), ...opts });
/** Lightweight whole-table view (first 100; dashboard counts / storage page / support link selection); the list page uses useInstancePages. */
export const useInstances = (opts?: QueryOpts<PageInstanceOut>) =>
  useQuery<PageInstanceOut, ApiError, InstanceOut[]>({
    queryKey: keys.instances.first100,
    queryFn: () => listInstancesApiV1InstancesGet({ limit: 100 }),
    select: (p) => p.items,
    ...opts,
  });
/** Expiring subscription instances (expiry banner data source): filtered server-side by within_days, no pagination. */
export const useExpiringInstances = (withinDays: number | undefined, opts?: QueryOpts) =>
  useQuery({
    queryKey: keys.instances.expiring(withinDays),
    queryFn: () => listExpiringInstancesApiV1InstancesExpiringGet({ within_days: withinDays ?? 7 }),
    ...opts,
    enabled: withinDays !== undefined && (opts?.enabled ?? true),
  });
/** Instance list cursor pagination: status exact / name fuzzy server-side filters, no refetchInterval; transitional states are polled per instance by useTransientInstanceRefresh. */
export const useInstancePages = (params?: { status?: string; name?: string }) => {
  const status = params?.status;
  const name = params?.name?.trim() || undefined;
  return useCursorPages(keys.instances.pages({ status, name }), listInstancesApiV1InstancesGet, { status, name }, 20);
};

/** Light per-item polling of transitional rows: derive transitional ids from the loaded rows, poll the single-item endpoint per row (POLL.transient, stopping at a terminal state); a status change between rounds invalidates the list query, the first round only records the baseline. */
export function useTransientRefresh<T>(opts: {
  rows: T[];
  idOf: (row: T) => string;
  statusOf: (row: T) => string;
  isTransient: (status: string) => boolean;
  detailKey: (id: string) => readonly unknown[];
  fetchOne: (id: string) => Promise<{ status: string }>;
  listPrefix: readonly unknown[];
}) {
  const { rows, idOf, statusOf, isTransient, detailKey, fetchOne, listPrefix } = opts;
  const queryClient = useQueryClient();
  const transientKey = rows
    .filter((r) => isTransient(statusOf(r)))
    .map(idOf)
    .join(",");
  const transientIds = useMemo(() => (transientKey ? transientKey.split(",") : []), [transientKey]);
  const results = useQueries({
    queries: transientIds.map((id) => ({
      queryKey: detailKey(id),
      queryFn: () => fetchOne(id),
      refetchInterval: (q: { state: { data: { status: string } | undefined } }) =>
        q.state.data && isTransient(q.state.data.status) ? POLL.transient : false,
    })),
  });
  const statusesKey = results.map((r) => r.data?.status ?? "").join(",");
  const lastSeen = useRef(new Map<string, string>());
  useEffect(() => {
    let changed = false;
    const alive = new Set(transientIds);
    for (const [i, id] of transientIds.entries()) {
      const status = results[i]?.data?.status;
      if (!status) continue;
      const prev = lastSeen.current.get(id);
      lastSeen.current.set(id, status);
      if (prev !== undefined && prev !== status) changed = true;
    }
    for (const id of [...lastSeen.current.keys()]) {
      if (!alive.has(id)) lastSeen.current.delete(id);
    }
    if (changed) void queryClient.invalidateQueries({ queryKey: listPrefix });
  }, [statusesKey, transientIds, results, queryClient, listPrefix]);
}

/** Light per-instance polling of transitional instances (creating/starting/stopping/releasing). */
export function useTransientInstanceRefresh(rows: InstanceOut[]) {
  useTransientRefresh({
    rows,
    idOf: (i) => i.uuid,
    statusOf: (i) => i.status,
    isTransient: isTransientInstanceStatus,
    detailKey: keys.instances.detail,
    fetchOne: getInstanceApiV1InstancesUuidGet,
    listPrefix: keys.instances.pagesPrefix,
  });
}

export const useInstance = (uuid: string, opts?: QueryOpts<InstanceOut>) =>
  useQuery({ queryKey: keys.instances.detail(uuid), queryFn: () => getInstanceApiV1InstancesUuidGet(uuid), ...opts });
export const useInstanceEvents = (uuid: string, opts?: QueryOpts) =>
  useQuery({
    queryKey: keys.instances.events(uuid),
    queryFn: () => listInstanceEventsApiV1InstancesUuidEventsGet(uuid, { limit: 200 }),
    ...opts,
  });
/** Event timeline cursor pagination: no refetchInterval; the detail poll invalidates the keys.instances.events prefix after a status transition (at once + delayed). */
export const useInstanceEventPages = (uuid: string) =>
  useCursorPages(
    keys.instances.eventPages(uuid),
    (p?: ListInstanceEventsApiV1InstancesUuidEventsGetParams) => listInstanceEventsApiV1InstancesUuidEventsGet(uuid, p),
    undefined,
    50,
  );
export const useInstanceAccess = (uuid: string, opts?: QueryOpts) =>
  useQuery({
    queryKey: keys.instances.access(uuid),
    queryFn: () => getInstanceAccessApiV1InstancesUuidAccessGet(uuid),
    ...opts,
  });

/** Lightweight whole-table view (first 100; overview counts / command palette); the list page uses useServicePages. */
export const useServices = (opts?: QueryOpts<PageServiceOut>) =>
  useQuery<PageServiceOut, ApiError, ServiceOut[]>({
    queryKey: keys.services.first100,
    queryFn: () => listServicesApiV1ServicesGet({ limit: 100 }),
    select: (p) => p.items,
    ...opts,
  });
/** Service list cursor pagination: status (derived) exact / name fuzzy (slug prefix included) server-side filters, no polling. */
export const useServicePages = (params?: { status?: string; name?: string }) => {
  const status = params?.status;
  const name = params?.name?.trim() || undefined;
  return useCursorPages(keys.services.pages({ status, name }), listServicesApiV1ServicesGet, { status, name }, 20);
};
/** Light per-item polling of transitional services (deploying / stopping / releasing). */
export function useTransientServiceRefresh(rows: ServiceOut[]) {
  useTransientRefresh({
    rows,
    idOf: (s) => s.slug,
    statusOf: (s) => s.status,
    isTransient: isTransientServiceStatus,
    detailKey: keys.services.detail,
    fetchOne: getServiceApiV1ServicesSlugGet,
    listPrefix: keys.services.pagesPrefix,
  });
}
export const useService = (slug: string, opts?: QueryOpts<ServiceOut>) =>
  useQuery({ queryKey: keys.services.detail(slug), queryFn: () => getServiceApiV1ServicesSlugGet(slug), ...opts });
/** Service-level timeline (union of every revision instance's events) cursor pagination; no polling, invalidated by the detail page's service poll after a transition. */
export const useServiceEventPages = (slug: string) =>
  useCursorPages(
    keys.services.eventPages(slug),
    (p?: ListServiceEventsApiV1ServicesSlugEventsGetParams) => listServiceEventsApiV1ServicesSlugEventsGet(slug, p),
    undefined,
    50,
  );
/** Revision history first 50 (released instances included), by instance ID descending; no further pages. */
export const useServiceRevisions = (slug: string, opts?: QueryOpts) =>
  useQuery({
    queryKey: keys.services.revisions(slug),
    queryFn: () => listRevisionsApiV1ServicesSlugRevisionsGet(slug, { limit: 50 }),
    ...opts,
  });
/** Current revision container log: tail / auto-refresh controlled by the caller via params and refetchInterval. */
export const useServiceLogs = (
  slug: string,
  params: GetServiceLogsApiV1ServicesSlugLogsGetParams,
  opts?: QueryOpts<InstanceLogsOut>,
) =>
  useQuery({
    queryKey: keys.services.logs(slug, params),
    queryFn: () => getServiceLogsApiV1ServicesSlugLogsGet(slug, params),
    ...opts,
  });
/** Service API key list: prefixes, names and created / used / revoked metadata, no plaintext keys. */
export const useServiceApiKeys = (slug: string, opts?: QueryOpts<ApiKeyOut[]>) =>
  useQuery({
    queryKey: keys.services.apiKeys(slug),
    queryFn: () => listApiKeysApiV1ServicesSlugApiKeysGet(slug),
    ...opts,
  });
/** Service hourly bills (union of every revision instance) cursor pagination. */
export const useServiceBillPages = (slug: string) =>
  useCursorPages(
    keys.services.billPages(slug),
    (p?: ListServiceBillsApiV1ServicesSlugBillsGetParams) => listServiceBillsApiV1ServicesSlugBillsGet(slug, p),
    undefined,
    50,
  );
/** Container log: tail / auto-refresh controlled by the caller via params and refetchInterval. */
export const useInstanceLogs = (
  uuid: string,
  params: GetInstanceLogsApiV1InstancesUuidLogsGetParams,
  opts?: QueryOpts<InstanceLogsOut>,
) =>
  useQuery({
    queryKey: keys.instances.logs(uuid, params),
    queryFn: () => getInstanceLogsApiV1InstancesUuidLogsGet(uuid, params),
    ...opts,
  });
export const useInstanceMetrics = (
  uuid: string,
  params: GetInstanceMetricsApiV1InstancesUuidMetricsGetParams,
  opts?: QueryOpts,
) =>
  useQuery({
    queryKey: keys.instances.metrics(uuid, params),
    queryFn: () => getInstanceMetricsApiV1InstancesUuidMetricsGet(uuid, params),
    ...opts,
  });
/** Workload identity: instance (uuid) or service (slug; targets the current revision instance). */
export type WorkloadSubject = { kind: "instance"; uuid: string } | { kind: "service"; slug: string };

/** Container log (instance, or the service's current revision instance): tail / auto-refresh controlled by the caller via params and refetchInterval. */
export const useWorkloadLogs = (
  subject: WorkloadSubject,
  params: GetInstanceLogsApiV1InstancesUuidLogsGetParams,
  opts?: QueryOpts<InstanceLogsOut>,
) =>
  useQuery({
    queryKey:
      subject.kind === "instance"
        ? keys.instances.logs(subject.uuid, params)
        : keys.services.logs(subject.slug, params),
    queryFn: () =>
      subject.kind === "instance"
        ? getInstanceLogsApiV1InstancesUuidLogsGet(subject.uuid, params)
        : getServiceLogsApiV1ServicesSlugLogsGet(subject.slug, params),
    ...opts,
  });

/** Event timeline cursor pagination (an instance, or the union of a service's revision instances); no polling, invalidated by the outer poll after a transition. */
export const useWorkloadEventPages = (subject: WorkloadSubject) =>
  useCursorPages(
    subject.kind === "instance" ? keys.instances.eventPages(subject.uuid) : keys.services.eventPages(subject.slug),
    (p?: { cursor?: string | null; limit?: number | null }) =>
      subject.kind === "instance"
        ? listInstanceEventsApiV1InstancesUuidEventsGet(subject.uuid, p)
        : listServiceEventsApiV1ServicesSlugEventsGet(subject.slug, p),
    undefined,
    50,
  );

/** Hourly bill cursor pagination. */
export const useHourlyBillPages = (params?: Omit<ListHourlyBillsApiV1BillsHourlyGetParams, "cursor" | "limit">) =>
  useCursorPages(keys.bills.pages(params), listHourlyBillsApiV1BillsHourlyGet, params, 50);
/** Ledger cursor pagination. */
export const useLedgerPages = (limit = 20) =>
  useCursorPages(keys.ledger.pages(limit), getLedgerApiV1WalletLedgerGet, undefined, limit);
/** Monthly summary: window cut at the local month boundary, the offset shares its source with "today's spend". */
export const useBillSummary = (month: string, tzOffsetMinutes: number) =>
  useQuery({
    queryKey: keys.billSummary.of(month, tzOffsetMinutes),
    queryFn: () => billSummaryApiV1BillsSummaryGet({ month, tz_offset_minutes: tzOffsetMinutes }),
  });
/** Policy constants (disk price / reclamation days ...): public endpoint, not refetched within 5 minutes. */
export const usePolicies = () =>
  useQuery({ queryKey: keys.policies, queryFn: () => getPoliciesApiV1PoliciesGet(), staleTime: 5 * 60_000 });
/** Public site configuration (filing numbers / enabled payment channels): public endpoint. */
export const useSiteConfig = () =>
  useQuery({ queryKey: keys.siteConfig, queryFn: () => getSiteConfigApiV1SiteConfigGet() });
/** Legal documents: public endpoint, the published version by UI language (the server falls back along the profile locale chain). */
export const useLegalDoc = (docKey: string, lang: string) =>
  useQuery({ queryKey: keys.legalDoc(docKey, lang), queryFn: () => getLegalDocApiV1LegalDocKeyGet(docKey, { lang }) });
/** Instance list sparkline batch summary: available=false (200) when the source is down, independent of instance polling. */
export const useMetricsSummary = (opts?: QueryOpts) =>
  useQuery({
    queryKey: keys.metricsSummary,
    queryFn: () => instancesMetricsSummaryApiV1MetricsInstancesGet(),
    ...opts,
  });
/** Today's consumption: date is the caller's local YYYY-MM-DD. */
export const useDailySummary = (date: string, tzOffsetMinutes: number, opts?: QueryOpts) =>
  useQuery({
    queryKey: keys.billDailySummary.of(date, tzOffsetMinutes),
    queryFn: () => billDailySummaryApiV1BillsDailySummaryGet({ date, tz_offset_minutes: tzOffsetMinutes }),
    ...opts,
  });
export const useRecharge = (orderNo: string, opts?: QueryOpts<RechargeOut>) =>
  useQuery({
    queryKey: keys.recharge.detail(orderNo),
    queryFn: () => getRechargeApiV1WalletRechargesOrderNoGet(orderNo),
    ...opts,
  });
/** Refund form candidates: top-up orders under the refundable definition (non-refundable rows carry reason_code). */
export const useRefundableOrders = (opts?: QueryOpts) =>
  useQuery({
    queryKey: keys.refundableOrders,
    queryFn: () => listRefundableOrdersApiV1WalletRefundsEligibleOrdersGet(),
    ...opts,
  });
/** My refund requests cursor pagination. */
export const useRefundPages = (limit = 20) =>
  useCursorPages(keys.refunds.pages(limit), listMyRefundsApiV1WalletRefundsGet, undefined, limit);
/** Invoiceable amount preview per period (finished periods with amount > 0 only). */
export const useInvoiceEligible = (opts?: QueryOpts) =>
  useQuery({
    queryKey: keys.invoiceEligible,
    queryFn: () => listInvoiceEligibleApiV1BillingInvoicesEligibleGet(),
    ...opts,
  });
/** My invoice requests cursor pagination. */
export const useInvoicePages = (limit = 20) =>
  useCursorPages(keys.invoices.pages(limit), listMyInvoicesApiV1BillingInvoicesGet, undefined, limit);
/** My tickets cursor pagination. */
export const useTicketPages = (limit = 20) =>
  useCursorPages(keys.tickets.pages(limit), listMyTicketsApiV1TicketsGet, undefined, limit);
/** Ticket detail + message stream; someone else's ticket is 404, caught by the error page. */
export const useTicketDetail = (ticketId: number, opts?: QueryOpts<TicketDetailOut>) =>
  useQuery({
    queryKey: keys.tickets.detail(ticketId),
    queryFn: () => getMyTicketApiV1TicketsTicketIdGet(ticketId),
    ...opts,
  });
