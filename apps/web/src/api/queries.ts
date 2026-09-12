/** 读查询层:生成 fetcher + useQuery,查询键与轮询选项集中在此。 */

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
  PageInstanceEventOut,
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

interface QueryOpts<T = unknown> {
  enabled?: boolean;
  /** 数值,或函数式(入参为 query 快照) */
  refetchInterval?:
    | number
    | false
    | ((query: {
        state: { data: T | undefined; status: "pending" | "error" | "success" };
      }) => number | false | undefined);
  retry?: number | boolean;
  staleTime?: number;
}

// queryKey 不做归一化;「无参数」统一写 null
function useApiQuery<T>(key: unknown[], fn: () => Promise<T>, opts?: QueryOpts<NoInfer<T>>) {
  return useQuery<T, ApiError>({
    queryKey: key,
    queryFn: fn,
    ...opts,
  });
}

/** 游标分页公共形状:params 带 limit/cursor,响应带 next_cursor。 */
interface CursorParams {
  limit?: number;
  cursor?: string;
}
interface CursorPage {
  next_cursor?: string | null;
}

/** 游标分页骨架(useInfiniteQuery 样板)。infinite 查询禁止轮询与 focus 重拉;新鲜度靠手动刷新。 */
function useCursorPages<TPage extends CursorPage, P extends CursorParams>(
  key: readonly unknown[],
  fetcher: (params?: P) => Promise<TPage>,
  params: Omit<P, "cursor" | "limit"> | undefined,
  limit: number,
) {
  return useInfiniteQuery<TPage, ApiError, InfiniteData<TPage>, readonly unknown[], string | undefined>({
    queryKey: key,
    initialPageParam: undefined,
    // TS 证不出泛型展开,仅此处单点断言
    queryFn: ({ pageParam }) => fetcher({ ...params, limit, ...(pageParam ? { cursor: pageParam } : {}) } as P),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    refetchOnWindowFocus: false,
  });
}

export const useMe = (opts?: QueryOpts) => useApiQuery(["me"], () => meApiV1MeGet(), opts);
/** 我的注销申请:pending 或最近一条;null = 从未申请。 */
export const useMyDeletionRequest = (opts?: QueryOpts) =>
  useApiQuery(["deletion-request"], () => getDeletionRequestApiV1MeDeletionRequestGet(), opts);
export const useWallet = (opts?: QueryOpts) => useApiQuery(["wallet"], () => getWalletApiV1WalletGet(), opts);
export const useNotifications = (params?: { unread?: boolean }, opts?: QueryOpts) =>
  useApiQuery(["notifications", params ?? null], () => listNotificationsApiV1NotificationsGet(params), opts);
/** 未读角标:30s 轮询只拿 count。 */
export const useUnreadCount = (opts?: QueryOpts) =>
  useApiQuery(["notifications", "unread-count"], () => unreadCountApiV1NotificationsUnreadCountGet(), opts);
/** 通知弹层游标分页。 */
export const useNotificationPages = (params?: { unread?: boolean }) =>
  useCursorPages(["notifications", "pages", params ?? null], listNotificationsApiV1NotificationsGet, params, 20);
export const useSkus = (opts?: QueryOpts) => useApiQuery(["skus"], () => listSkusApiV1SkusGet(), opts);
export const useImages = () => useApiQuery(["images"], () => listImagesApiV1ImagesGet());
export const useSshKeys = () => useApiQuery(["ssh-keys"], () => listSshKeysApiV1SshKeysGet());
export const useDisks = (opts?: QueryOpts) => useApiQuery(["disks"], () => listDisksApiV1DisksGet(), opts);
/** 轻量整表视图(首 100 条;dashboard 计数 / 存储页 / support 关联选择用);列表页走 useInstancePages。 */
export const useInstances = (opts?: QueryOpts<PageInstanceOut>) =>
  useQuery<PageInstanceOut, ApiError, InstanceOut[]>({
    queryKey: ["instances", "first100"],
    queryFn: () => listInstancesApiV1InstancesGet({ limit: 100 }),
    select: (p) => p.items,
    ...opts,
  });
/** 临期包周期实例(到期横幅数据源):服务端按 within_days 过滤,不分页。 */
export const useExpiringInstances = (withinDays: number | undefined, opts?: QueryOpts) =>
  useApiQuery(
    ["instances", "expiring", withinDays ?? null],
    () => listExpiringInstancesApiV1InstancesExpiringGet({ within_days: withinDays ?? 7 }),
    { ...opts, enabled: withinDays !== undefined && (opts?.enabled ?? true) },
  );
/** 实例列表游标分页:status 精确 / name 模糊服务端过滤,不挂 refetchInterval;过渡态由 useTransientInstanceRefresh 逐台轮询驱动。 */
export const useInstancePages = (params?: { status?: string; name?: string }) => {
  const status = params?.status;
  const name = params?.name?.trim() || undefined;
  return useCursorPages(["instances", "pages", { status, name }], listInstancesApiV1InstancesGet, { status, name }, 20);
};
/** 过渡态实例逐台轻轮询:从已加载行派生 creating/starting/stopping/releasing uuid,逐台轮询单实例端点(5s,到终态即停);本轮 vs 上轮 status 变化即失效列表查询,首轮只落基线。 */
export function useTransientInstanceRefresh(rows: InstanceOut[]) {
  const queryClient = useQueryClient();
  const transientKey = rows
    .filter((i) => isTransientInstanceStatus(i.status))
    .map((i) => i.uuid)
    .join(",");
  const transientUuids = useMemo(() => (transientKey ? transientKey.split(",") : []), [transientKey]);
  const results = useQueries({
    queries: transientUuids.map((uuid) => ({
      queryKey: ["instances", uuid] as const,
      queryFn: () => getInstanceApiV1InstancesUuidGet(uuid),
      // 到终态即停
      refetchInterval: (q: { state: { data: InstanceOut | undefined } }) =>
        q.state.data && isTransientInstanceStatus(q.state.data.status) ? POLL.transient : false,
    })),
  });
  const statusesKey = results.map((r) => r.data?.status ?? "").join(",");
  const lastSeen = useRef(new Map<string, string>());
  useEffect(() => {
    let changed = false;
    const alive = new Set(transientUuids);
    for (const [i, uuid] of transientUuids.entries()) {
      const status = results[i]?.data?.status;
      if (!status) continue;
      const prev = lastSeen.current.get(uuid);
      lastSeen.current.set(uuid, status);
      if (prev !== undefined && prev !== status) changed = true;
    }
    // 退出过渡态集合的 uuid 清出基线
    for (const uuid of [...lastSeen.current.keys()]) {
      if (!alive.has(uuid)) lastSeen.current.delete(uuid);
    }
    if (changed) void queryClient.invalidateQueries({ queryKey: ["instances", "pages"] });
    // statusesKey 聚合轮询结果变化;results 仅作取值通道
  }, [statusesKey, transientUuids, results, queryClient]);
}
export const useInstance = (uuid: string, opts?: QueryOpts<InstanceOut>) =>
  useApiQuery(["instances", uuid], () => getInstanceApiV1InstancesUuidGet(uuid), opts);
export const useInstanceEvents = (uuid: string, opts?: QueryOpts<PageInstanceEventOut>) =>
  useApiQuery(
    ["instances", uuid, "events"],
    // 服务端降序游标分页;取大页覆盖「失败原因 / 是否运行过」判定
    () => listInstanceEventsApiV1InstancesUuidEventsGet(uuid, { limit: 200 }),
    opts,
  );
/** 事件时间线游标分页:不挂 refetchInterval;详情页轮询检测到 status 迁移后失效 ["instances", uuid, "events"] 前缀(立即 + 延迟各一次)。 */
export const useInstanceEventPages = (uuid: string) =>
  useCursorPages(
    ["instances", uuid, "events", "pages"],
    (p?: ListInstanceEventsApiV1InstancesUuidEventsGetParams) => listInstanceEventsApiV1InstancesUuidEventsGet(uuid, p),
    undefined,
    50,
  );
export const useInstanceAccess = (uuid: string, opts?: QueryOpts) =>
  useApiQuery(["instances", uuid, "access"], () => getInstanceAccessApiV1InstancesUuidAccessGet(uuid), opts);

// ---------- 在线服务 ----------
/** 轻量整表视图(首 100 条;概览计数 / 命令面板用);列表页走 useServicePages。 */
export const useServices = (opts?: QueryOpts<PageServiceOut>) =>
  useQuery<PageServiceOut, ApiError, ServiceOut[]>({
    queryKey: ["services", "first100"],
    queryFn: () => listServicesApiV1ServicesGet({ limit: 100 }),
    select: (p) => p.items,
    ...opts,
  });
/** 服务列表游标分页:status(派生态)精确 / name 模糊(含 slug 前缀)服务端过滤,不挂轮询。 */
export const useServicePages = (params?: { status?: string; name?: string }) => {
  const status = params?.status;
  const name = params?.name?.trim() || undefined;
  return useCursorPages(["services", "pages", { status, name }], listServicesApiV1ServicesGet, { status, name }, 20);
};
/** 过渡态服务逐条轻轮询(deploying / stopping / releasing,5s,到终态即停);判据与 useTransientInstanceRefresh 同款。 */
export function useTransientServiceRefresh(rows: ServiceOut[]) {
  const queryClient = useQueryClient();
  const transientKey = rows
    .filter((s) => isTransientServiceStatus(s.status))
    .map((s) => s.slug)
    .join(",");
  const transientSlugs = useMemo(() => (transientKey ? transientKey.split(",") : []), [transientKey]);
  const results = useQueries({
    queries: transientSlugs.map((slug) => ({
      queryKey: ["services", slug] as const,
      queryFn: () => getServiceApiV1ServicesSlugGet(slug),
      refetchInterval: (q: { state: { data: ServiceOut | undefined } }) =>
        q.state.data && isTransientServiceStatus(q.state.data.status) ? POLL.transient : false,
    })),
  });
  const statusesKey = results.map((r) => r.data?.status ?? "").join(",");
  const lastSeen = useRef(new Map<string, string>());
  useEffect(() => {
    let changed = false;
    const alive = new Set(transientSlugs);
    for (const [i, slug] of transientSlugs.entries()) {
      const status = results[i]?.data?.status;
      if (!status) continue;
      const prev = lastSeen.current.get(slug);
      lastSeen.current.set(slug, status);
      if (prev !== undefined && prev !== status) changed = true;
    }
    for (const slug of [...lastSeen.current.keys()]) {
      if (!alive.has(slug)) lastSeen.current.delete(slug);
    }
    if (changed) void queryClient.invalidateQueries({ queryKey: ["services", "pages"] });
  }, [statusesKey, transientSlugs, results, queryClient]);
}
export const useService = (slug: string, opts?: QueryOpts<ServiceOut>) =>
  useApiQuery(["services", slug], () => getServiceApiV1ServicesSlugGet(slug), opts);
/** 服务级时间线(全部版本实例事件并集)游标分页;不挂轮询,由详情页服务轮询检测到迁移后失效。 */
export const useServiceEventPages = (slug: string) =>
  useCursorPages(
    ["services", slug, "events", "pages"],
    (p?: ListServiceEventsApiV1ServicesSlugEventsGetParams) => listServiceEventsApiV1ServicesSlugEventsGet(slug, p),
    undefined,
    50,
  );
/** 版本历史 = 该服务下全部实例(含已释放),版本号降序。 */
export const useServiceRevisions = (slug: string, opts?: QueryOpts) =>
  useApiQuery(
    ["services", slug, "revisions"],
    () => listRevisionsApiV1ServicesSlugRevisionsGet(slug, { limit: 50 }),
    opts,
  );
/** 当前版本容器日志:tail / 自动刷新由调用方经 params 与 refetchInterval 控制。 */
export const useServiceLogs = (
  slug: string,
  params: GetServiceLogsApiV1ServicesSlugLogsGetParams,
  opts?: QueryOpts<InstanceLogsOut>,
) => useApiQuery(["services", slug, "logs", params], () => getServiceLogsApiV1ServicesSlugLogsGet(slug, params), opts);
/** 服务 API Key 列表:只有前缀。 */
export const useServiceApiKeys = (slug: string, opts?: QueryOpts<ApiKeyOut[]>) =>
  useApiQuery(["services", slug, "api-keys"], () => listApiKeysApiV1ServicesSlugApiKeysGet(slug), opts);
/** 服务小时账单(全部版本实例并集)游标分页。 */
export const useServiceBillPages = (slug: string) =>
  useCursorPages(
    ["services", slug, "bills", "pages"],
    (p?: ListServiceBillsApiV1ServicesSlugBillsGetParams) => listServiceBillsApiV1ServicesSlugBillsGet(slug, p),
    undefined,
    50,
  );
/** 容器日志:tail / 自动刷新由调用方经 params 与 refetchInterval 控制。 */
export const useInstanceLogs = (
  uuid: string,
  params: GetInstanceLogsApiV1InstancesUuidLogsGetParams,
  opts?: QueryOpts<InstanceLogsOut>,
) =>
  useApiQuery(["instances", uuid, "logs", params], () => getInstanceLogsApiV1InstancesUuidLogsGet(uuid, params), opts);
export const useInstanceMetrics = (
  uuid: string,
  params: GetInstanceMetricsApiV1InstancesUuidMetricsGetParams,
  opts?: QueryOpts,
) =>
  useApiQuery(
    ["instances", uuid, "metrics", params],
    () => getInstanceMetricsApiV1InstancesUuidMetricsGet(uuid, params),
    opts,
  );
/** 小时账单游标分页。 */
export const useHourlyBillPages = (params?: Omit<ListHourlyBillsApiV1BillsHourlyGetParams, "cursor" | "limit">) =>
  useCursorPages(["bills", "pages", params ?? null], listHourlyBillsApiV1BillsHourlyGet, params, 50);
/** 资金流水游标分页,必须走 useInfiniteQuery。 */
export const useLedgerPages = (limit = 20) =>
  useCursorPages(["ledger", limit], getLedgerApiV1WalletLedgerGet, undefined, limit);
/** 月度汇总:窗口按本地月界切,offset 与「今日消费」同一来源。 */
export const useBillSummary = (month: string, tzOffsetMinutes: number) =>
  useApiQuery(["bill-summary", month, tzOffsetMinutes], () =>
    billSummaryApiV1BillsSummaryGet({ month, tz_offset_minutes: tzOffsetMinutes }),
  );
/** 策略常量(盘价 / 回收天数等):公开端点,5 分钟内不重取。 */
export const usePolicies = () =>
  useApiQuery(["policies"], () => getPoliciesApiV1PoliciesGet(), { staleTime: 5 * 60_000 });
/** 站点公开配置(备案号 / 可用支付渠道):公开端点。 */
export const useSiteConfig = () => useApiQuery(["site-config"], () => getSiteConfigApiV1SiteConfigGet());
/** 法务文档:公开端点,按界面语言取 published 版(en-US 缺失服务端回落 zh-CN)。 */
export const useLegalDoc = (docKey: string, lang: string) =>
  useApiQuery(["legal-doc", docKey, lang], () => getLegalDocApiV1LegalDocKeyGet(docKey, { lang }));
/** 实例列表 sparkline 批量摘要:断源时 available=false(200),独立于实例轮询。 */
export const useMetricsSummary = (opts?: QueryOpts) =>
  useApiQuery(["metrics-summary"], () => instancesMetricsSummaryApiV1MetricsInstancesGet(), opts);
/** 当日消费:date 为调用方本地 YYYY-MM-DD。 */
export const useDailySummary = (date: string, tzOffsetMinutes: number, opts?: QueryOpts) =>
  useApiQuery(
    ["bill-daily-summary", date, tzOffsetMinutes],
    () => billDailySummaryApiV1BillsDailySummaryGet({ date, tz_offset_minutes: tzOffsetMinutes }),
    opts,
  );
export const useRecharge = (orderNo: string, opts?: QueryOpts<RechargeOut>) =>
  useApiQuery(["recharge", orderNo], () => getRechargeApiV1WalletRechargesOrderNoGet(orderNo), opts);
/** 退款表单候选集:可申请口径的充值订单(不可申请行带 reason_code)。 */
export const useRefundableOrders = (opts?: QueryOpts) =>
  useApiQuery(["refundable-orders"], () => listRefundableOrdersApiV1WalletRefundsEligibleOrdersGet(), opts);
/** 我的退款单游标分页。 */
export const useRefundPages = (limit = 20) =>
  useCursorPages(["refunds", limit], listMyRefundsApiV1WalletRefundsGet, undefined, limit);
/** 各账期可开票额度预览(仅 amount > 0 的已结束账期)。 */
export const useInvoiceEligible = (opts?: QueryOpts) =>
  useApiQuery(["invoice-eligible"], () => listInvoiceEligibleApiV1BillingInvoicesEligibleGet(), opts);
/** 我的发票申请游标分页。 */
export const useInvoicePages = (limit = 20) =>
  useCursorPages(["invoices", limit], listMyInvoicesApiV1BillingInvoicesGet, undefined, limit);
/** 我的工单游标分页。 */
export const useTicketPages = (limit = 20) =>
  useCursorPages(["tickets", limit], listMyTicketsApiV1TicketsGet, undefined, limit);
/** 工单详情 + 消息流;他人工单 404 由错误页兜底。 */
export const useTicketDetail = (ticketId: number, opts?: QueryOpts<TicketDetailOut>) =>
  useApiQuery(["tickets", ticketId], () => getMyTicketApiV1TicketsTicketIdGet(ticketId), opts);
