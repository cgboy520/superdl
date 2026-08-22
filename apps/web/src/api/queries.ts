/** 读操作薄查询层:生成的 fetcher 函数 + useQuery,查询键与轮询选项集中在此。 */

import {
  billDailySummaryApiV1BillsDailySummaryGet,
  billSummaryApiV1BillsSummaryGet,
  getInstanceAccessApiV1InstancesUuidAccessGet,
  getInstanceApiV1InstancesUuidGet,
  getInstanceMetricsApiV1InstancesUuidMetricsGet,
  getLedgerApiV1WalletLedgerGet,
  getPoliciesApiV1PoliciesGet,
  getRechargeApiV1WalletRechargesOrderNoGet,
  getSiteConfigApiV1SiteConfigGet,
  getWalletApiV1WalletGet,
  instancesMetricsSummaryApiV1MetricsInstancesGet,
  listDisksApiV1DisksGet,
  listHourlyBillsApiV1BillsHourlyGet,
  listImagesApiV1ImagesGet,
  listInstanceEventsApiV1InstancesUuidEventsGet,
  listInstancesApiV1InstancesGet,
  listNotificationsApiV1NotificationsGet,
  listSkusApiV1SkusGet,
  listSshKeysApiV1SshKeysGet,
  meApiV1MeGet,
} from "@superdl/api-client";
import type {
  ApiError,
  GetInstanceMetricsApiV1InstancesUuidMetricsGetParams,
  InstanceOut,
  ListHourlyBillsApiV1BillsHourlyGetParams,
  ListSkusApiV1SkusGetParams,
  PageBillHourlyOut,
  PageInstanceEventOut,
  PageLedgerEntryOut,
  RechargeOut,
} from "@superdl/api-client";
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
}

/**
 * queryKey 归一化:undefined 与 {} 视为同一查询,剔除值为 undefined 的参数,键序不影响缓存身份。
 */
function normalizeKey(key: unknown[]): unknown[] {
  return key.map((part) => {
    if (part === undefined) return null;
    if (part !== null && typeof part === "object" && !Array.isArray(part)) {
      const entries = Object.entries(part as Record<string, unknown>)
        .filter(([, v]) => v !== undefined)
        .sort(([a], [b]) => a.localeCompare(b));
      return entries.length ? Object.fromEntries(entries) : null;
    }
    return part;
  });
}

function useApiQuery<T>(key: unknown[], fn: () => Promise<T>, opts?: QueryOpts<NoInfer<T>>) {
  return useQuery<T, ApiError>({
    queryKey: normalizeKey(key),
    queryFn: fn,
    ...opts,
  });
}



export const useMe = (opts?: QueryOpts) => useApiQuery(["me"], () => meApiV1MeGet(), opts);
export const useWallet = (opts?: QueryOpts) => useApiQuery(["wallet"], () => getWalletApiV1WalletGet(), opts);
export const useNotifications = (params?: { unread?: boolean }, opts?: QueryOpts) =>
  useApiQuery(["notifications", params], () => listNotificationsApiV1NotificationsGet(params), opts);
export const useSkus = (params?: ListSkusApiV1SkusGetParams, opts?: QueryOpts) =>
  useApiQuery(["skus", params], () => listSkusApiV1SkusGet(params), opts);
export const useImages = () => useApiQuery(["images"], () => listImagesApiV1ImagesGet());
export const useSshKeys = () => useApiQuery(["ssh-keys"], () => listSshKeysApiV1SshKeysGet());
export const useDisks = (opts?: QueryOpts) => useApiQuery(["disks"], () => listDisksApiV1DisksGet(), opts);
export const useInstances = (opts?: QueryOpts<InstanceOut[]>) =>
  useApiQuery(["instances"], () => listInstancesApiV1InstancesGet(), opts);
export const useInstance = (uuid: string, opts?: QueryOpts<InstanceOut>) =>
  useApiQuery(["instances", uuid], () => getInstanceApiV1InstancesUuidGet(uuid), opts);
export const useInstanceEvents = (uuid: string, opts?: QueryOpts<PageInstanceEventOut>) =>
  useApiQuery(
    ["instances", uuid, "events"],
    // 服务端为降序游标分页;取大页以覆盖「失败原因 / 是否运行过」的判定
    () => listInstanceEventsApiV1InstancesUuidEventsGet(uuid, { limit: 200 }),
    opts,
  );
export const useInstanceAccess = (uuid: string, opts?: QueryOpts) =>
  useApiQuery(["instances", uuid, "access"], () => getInstanceAccessApiV1InstancesUuidAccessGet(uuid), opts);
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
export const useHourlyBills = (params?: ListHourlyBillsApiV1BillsHourlyGetParams) =>
  useApiQuery(["bills", params], () => listHourlyBillsApiV1BillsHourlyGet(params));
/** 小时账单游标分页(费用中心「加载更多」);queryKey 与单页版同属 bills 域,失效一并命中。 */
export const useHourlyBillPages = (params?: Omit<ListHourlyBillsApiV1BillsHourlyGetParams, "cursor" | "limit">) =>
  useInfiniteQuery<PageBillHourlyOut, ApiError, InfiniteData<PageBillHourlyOut>, unknown[], string | undefined>({
    queryKey: normalizeKey(["bills", "pages", params]),
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
/** 策略常量(盘价/回收天数等):公开端点,常量性质给长缓存。 */
export const usePolicies = () =>
  useApiQuery(["policies"], () => getPoliciesApiV1PoliciesGet(), { retry: 1 });
/** 站点公开配置(备案号/可用支付渠道):公开端点,页脚与充值弹窗消费。 */
export const useSiteConfig = () =>
  useApiQuery(["site-config"], () => getSiteConfigApiV1SiteConfigGet(), { retry: 1 });
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
