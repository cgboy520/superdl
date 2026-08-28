/** 读操作薄查询层:生成的 fetcher 函数 + useQuery,查询键与轮询选项集中在此。 */

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
  getServiceEndpointApiV1InstancesUuidServiceGet,
  getSiteConfigApiV1SiteConfigGet,
  getWalletApiV1WalletGet,
  instancesMetricsSummaryApiV1MetricsInstancesGet,
  listDisksApiV1DisksGet,
  listHourlyBillsApiV1BillsHourlyGet,
  listImagesApiV1ImagesGet,
  listExpiringInstancesApiV1InstancesExpiringGet,
  listInstanceEventsApiV1InstancesUuidEventsGet,
  listInstancesApiV1InstancesGet,
  listApiKeysApiV1InstancesUuidApiKeysGet,
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
  InstanceLogsOut,
  InstanceOut,
  ListHourlyBillsApiV1BillsHourlyGetParams,
  PageBillHourlyOut,
  PageInstanceEventOut,
  PageInstanceOut,
  PageInvoiceOut,
  PageLedgerEntryOut,
  PageNotificationOut,
  PageRefundOut,
  PageTicketOut,
  RechargeOut,
  ServiceEndpointOut,
  TicketDetailOut,
} from "@superdl/api-client";
import { isTransientInstanceStatus } from "@superdl/ui";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import type { InfiniteData } from "@tanstack/react-query";

interface QueryOpts<T = unknown> {
  enabled?: boolean;
  /** 数值,或函数式(如「到终态即停轮询」,入参为 query 快照) */
  refetchInterval?:
    | number
    | false
    | ((query: {
        state: { data: T | undefined; status: "pending" | "error" | "success" };
      }) => number | false | undefined);
  retry?: number | boolean;
  staleTime?: number;
}

// queryKey 不做归一化:TanStack hashKey 已按键名排序并忽略 undefined 值,「无参数」统一写成 null 即可。
function useApiQuery<T>(key: unknown[], fn: () => Promise<T>, opts?: QueryOpts<NoInfer<T>>) {
  return useQuery<T, ApiError>({
    queryKey: key,
    queryFn: fn,
    ...opts,
  });
}


export const useMe = (opts?: QueryOpts) => useApiQuery(["me"], () => meApiV1MeGet(), opts);
/** 我的注销申请:pending 或最近一条;null = 从未申请。 */
export const useMyDeletionRequest = (opts?: QueryOpts) =>
  useApiQuery(["deletion-request"], () => getDeletionRequestApiV1MeDeletionRequestGet(), opts);
export const useWallet = (opts?: QueryOpts) => useApiQuery(["wallet"], () => getWalletApiV1WalletGet(), opts);
export const useNotifications = (params?: { unread?: boolean }, opts?: QueryOpts) =>
  useApiQuery(["notifications", params ?? null], () => listNotificationsApiV1NotificationsGet(params), opts);
/** 未读角标轻端点:30s 轮询只拿 count,与列表分页解耦。 */
export const useUnreadCount = (opts?: QueryOpts) =>
  useApiQuery(["notifications", "unread-count"], () => unreadCountApiV1NotificationsUnreadCountGet(), opts);
/** 通知弹层游标分页:「加载更多」向下翻页。 */
export const useNotificationPages = (params?: { unread?: boolean }) =>
  useInfiniteQuery<PageNotificationOut, ApiError, InfiniteData<PageNotificationOut>, unknown[], string | undefined>({
    queryKey: ["notifications", "pages", params ?? null],
    initialPageParam: undefined,
    queryFn: ({ pageParam }) =>
      listNotificationsApiV1NotificationsGet({
        ...params,
        limit: 20,
        ...(pageParam ? { cursor: pageParam } : {}),
      }),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
export const useSkus = (opts?: QueryOpts) => useApiQuery(["skus"], () => listSkusApiV1SkusGet(), opts);
export const useImages = () => useApiQuery(["images"], () => listImagesApiV1ImagesGet());
export const useSshKeys = () => useApiQuery(["ssh-keys"], () => listSshKeysApiV1SshKeysGet());
export const useDisks = (opts?: QueryOpts) => useApiQuery(["disks"], () => listDisksApiV1DisksGet(), opts);
/** 轻量整表视图(首 100 条,dashboard 计数/存储页挂载名/support 关联选择用)。
 *  用户配额上限远小于 100;列表页本身走 useInstancePages 游标分页。 */
export const useInstances = (opts?: QueryOpts<PageInstanceOut>) =>
  useQuery<PageInstanceOut, ApiError, InstanceOut[]>({
    queryKey: ["instances", "first100"],
    queryFn: () => listInstancesApiV1InstancesGet({ limit: 100 }),
    select: (p) => p.items,
    ...opts,
  });
/** 临期包周期实例(到期横幅数据源):服务端按 within_days 过滤,不分页——
 *  列表筛选/翻页/首页截断都不会把临期实例藏掉(D3)。 */
export const useExpiringInstances = (withinDays: number | undefined, opts?: QueryOpts) =>
  useApiQuery(
    ["instances", "expiring", withinDays ?? null],
    () => listExpiringInstancesApiV1InstancesExpiringGet({ within_days: withinDays ?? 7 }),
    { ...opts, enabled: withinDays !== undefined && (opts?.enabled ?? true) },
  );
/** 实例列表游标分页:status 精确/name 模糊服务端过滤,「加载更多」向下翻页。
 *  轮询交给 refetchInterval:过渡态 5s、稳态 30s,窗口失焦自动停(默认
 *  refetchIntervalInBackground=false),失败态由查询自身承担,不必自己拿 setInterval
 *  + setQueryData 手工合并首页。 */
export const useInstancePages = (params?: { status?: string; name?: string }) => {
  const status = params?.status;
  const name = params?.name?.trim() || undefined;
  return useInfiniteQuery<
    PageInstanceOut,
    ApiError,
    InfiniteData<PageInstanceOut>,
    unknown[],
    string | undefined
  >({
    queryKey: ["instances", "pages", { status, name }],
    queryFn: ({ pageParam }) =>
      listInstancesApiV1InstancesGet({ status, name, cursor: pageParam, limit: 20 }),
    initialPageParam: undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    refetchInterval: (q) =>
      (q.state.data?.pages ?? []).some((p) =>
        p.items.some((i) => isTransientInstanceStatus(i.status)),
      )
        ? 5_000
        : 30_000,
  });
};
export const useInstance = (uuid: string, opts?: QueryOpts<InstanceOut>) =>
  useApiQuery(["instances", uuid], () => getInstanceApiV1InstancesUuidGet(uuid), opts);
export const useInstanceEvents = (uuid: string, opts?: QueryOpts<PageInstanceEventOut>) =>
  useApiQuery(
    ["instances", uuid, "events"],
    // 服务端为降序游标分页;取大页以覆盖「失败原因 / 是否运行过」的判定
    () => listInstanceEventsApiV1InstancesUuidEventsGet(uuid, { limit: 200 }),
    opts,
  );
/** 事件时间线游标分页(与费用中心小时账单同构):详情页「事件」Tab 加载更多。 */
export const useInstanceEventPages = (uuid: string) =>
  useInfiniteQuery<
    PageInstanceEventOut,
    ApiError,
    InfiniteData<PageInstanceEventOut>,
    unknown[],
    string | undefined
  >({
    queryKey: ["instances", uuid, "events", "pages"],
    queryFn: ({ pageParam }) =>
      listInstanceEventsApiV1InstancesUuidEventsGet(uuid, { cursor: pageParam, limit: 50 }),
    initialPageParam: undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
export const useInstanceAccess = (uuid: string, opts?: QueryOpts) =>
  useApiQuery(["instances", uuid, "access"], () => getInstanceAccessApiV1InstancesUuidAccessGet(uuid), opts);
/** 服务端点(仅 workload_type='service' 的实例有,dev 实例调用会 404)。
 *  ready 位是「服务起来没有」的唯一真相:持续 not-ready 时 status 仍是 running,不能拿 status 代替。 */
export const useServiceEndpoint = (uuid: string, opts?: QueryOpts<ServiceEndpointOut>) =>
  useApiQuery(
    ["instances", uuid, "service"],
    () => getServiceEndpointApiV1InstancesUuidServiceGet(uuid),
    opts,
  );
/** 实例的 API Key 列表:只有前缀,明文只在创建响应里出现一次。 */
export const useApiKeys = (uuid: string, opts?: QueryOpts<ApiKeyOut[]>) =>
  useApiQuery(["instances", uuid, "api-keys"], () => listApiKeysApiV1InstancesUuidApiKeysGet(uuid), opts);
/** 容器日志:tail/自动刷新由调用方经 params 与 refetchInterval 控制。 */
export const useInstanceLogs = (
  uuid: string,
  params: GetInstanceLogsApiV1InstancesUuidLogsGetParams,
  opts?: QueryOpts<InstanceLogsOut>,
) =>
  useApiQuery(
    ["instances", uuid, "logs", params],
    () => getInstanceLogsApiV1InstancesUuidLogsGet(uuid, params),
    opts,
  );
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
/** 小时账单游标分页(费用中心/实例详情账单 Tab「加载更多」)。 */
export const useHourlyBillPages = (params?: Omit<ListHourlyBillsApiV1BillsHourlyGetParams, "cursor" | "limit">) =>
  useInfiniteQuery<PageBillHourlyOut, ApiError, InfiniteData<PageBillHourlyOut>, unknown[], string | undefined>({
    queryKey: ["bills", "pages", params ?? null],
    queryFn: ({ pageParam }) =>
      listHourlyBillsApiV1BillsHourlyGet({ ...params, cursor: pageParam, limit: 50 }),
    initialPageParam: undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
/** 资金流水游标分页。必须走 useInfiniteQuery:自行把页累积进 useState 后,缓存失效不会刷新。 */
export const useLedgerPages = (limit = 20) =>
  useInfiniteQuery<PageLedgerEntryOut, ApiError, InfiniteData<PageLedgerEntryOut>, unknown[], string | undefined>({
    queryKey: ["ledger", limit],
    queryFn: ({ pageParam }) => getLedgerApiV1WalletLedgerGet({ cursor: pageParam, limit }),
    initialPageParam: undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
/** 月度汇总:窗口按本地月界切,offset 与「今日消费」取同一来源,两个数字才对得上。 */
export const useBillSummary = (month: string, tzOffsetMinutes: number) =>
  useApiQuery(["bill-summary", month, tzOffsetMinutes], () =>
    billSummaryApiV1BillsSummaryGet({ month, tz_offset_minutes: tzOffsetMinutes }),
  );
/** 策略常量(盘价/回收天数等):公开端点,常量性质,5 分钟内不重取。 */
export const usePolicies = () =>
  useApiQuery(["policies"], () => getPoliciesApiV1PoliciesGet(), { staleTime: 5 * 60_000 });
/** 站点公开配置(备案号/可用支付渠道):公开端点,页脚与充值弹窗消费。 */
export const useSiteConfig = () => useApiQuery(["site-config"], () => getSiteConfigApiV1SiteConfigGet());
/** 法务文档:公开端点,按界面语言取当前 published 版(en-US 缺失服务端回落 zh-CN)。 */
export const useLegalDoc = (docKey: string, lang: string) =>
  useApiQuery(["legal-doc", docKey, lang], () => getLegalDocApiV1LegalDocKeyGet(docKey, { lang }));
/** 实例列表 sparkline 批量摘要:断源时 available=false(200),独立于 5s 实例轮询。 */
export const useMetricsSummary = (opts?: QueryOpts) =>
  useApiQuery(["metrics-summary"], () => instancesMetricsSummaryApiV1MetricsInstancesGet(), opts);
/** 当日消费(按用户本地日界):date 由调用方传入本地 YYYY-MM-DD。 */
export const useDailySummary = (date: string, tzOffsetMinutes: number, opts?: QueryOpts) =>
  useApiQuery(
    ["bill-daily-summary", date, tzOffsetMinutes],
    () => billDailySummaryApiV1BillsDailySummaryGet({ date, tz_offset_minutes: tzOffsetMinutes }),
    opts,
  );
export const useRecharge = (orderNo: string, opts?: QueryOpts<RechargeOut>) =>
  useApiQuery(["recharge", orderNo], () => getRechargeApiV1WalletRechargesOrderNoGet(orderNo), opts);
/** 退款表单候选集:可申请口径的充值订单(不可申请行带 reason_code 置灰说明)。 */
export const useRefundableOrders = (opts?: QueryOpts) =>
  useApiQuery(["refundable-orders"], () => listRefundableOrdersApiV1WalletRefundsEligibleOrdersGet(), opts);
/** 我的退款单游标分页(与收支明细同构)。 */
export const useRefundPages = (limit = 20) =>
  useInfiniteQuery<PageRefundOut, ApiError, InfiniteData<PageRefundOut>, unknown[], string | undefined>({
    queryKey: ["refunds", limit],
    queryFn: ({ pageParam }) => listMyRefundsApiV1WalletRefundsGet({ cursor: pageParam, limit }),
    initialPageParam: undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
/** 各账期可开票额度预览(发票 Tab 申请弹窗数据源;仅 amount > 0 的已结束账期)。 */
export const useInvoiceEligible = (opts?: QueryOpts) =>
  useApiQuery(["invoice-eligible"], () => listInvoiceEligibleApiV1BillingInvoicesEligibleGet(), opts);
/** 我的发票申请游标分页(与退款单同构)。 */
export const useInvoicePages = (limit = 20) =>
  useInfiniteQuery<PageInvoiceOut, ApiError, InfiniteData<PageInvoiceOut>, unknown[], string | undefined>({
    queryKey: ["invoices", limit],
    queryFn: ({ pageParam }) => listMyInvoicesApiV1BillingInvoicesGet({ cursor: pageParam, limit }),
    initialPageParam: undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
/** 我的工单游标分页(与退款单同构)。 */
export const useTicketPages = (limit = 20) =>
  useInfiniteQuery<PageTicketOut, ApiError, InfiniteData<PageTicketOut>, unknown[], string | undefined>({
    queryKey: ["tickets", limit],
    queryFn: ({ pageParam }) => listMyTicketsApiV1TicketsGet({ cursor: pageParam, limit }),
    initialPageParam: undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
/** 工单详情 + 消息流(对话页;他人工单 404 由错误页兜底)。 */
export const useTicketDetail = (ticketId: number, opts?: QueryOpts<TicketDetailOut>) =>
  useApiQuery(["tickets", ticketId], () => getMyTicketApiV1TicketsTicketIdGet(ticketId), opts);
