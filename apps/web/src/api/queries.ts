/** 读查询层:生成 fetcher + useQuery;查询键只在 ./keys.ts,轮询选项集中在此。错误类型经 ./register.d.ts 全局注册为 ApiError。 */

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

/** 查询可选项:只放行这四个;select 等结构变换在 hook 内固定。错误类型已由 register.d.ts 全局钉成 ApiError。 */
export interface QueryOpts<T = unknown> {
  enabled?: boolean;
  /** 数值,或函数式。入参是结构化的最小快照(TanStack 的 Query 类在泛型上不变,不能直接当参数类型用) */
  refetchInterval?:
    | number
    | false
    | ((query: {
        state: { data: T | undefined; status: "pending" | "error" | "success" };
      }) => number | false | undefined);
  retry?: number | boolean;
  staleTime?: number;
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

export const useMe = (opts?: QueryOpts) => useQuery({ queryKey: keys.me, queryFn: () => meApiV1MeGet(), ...opts });
/** 我的注销申请:pending 或最近一条;null = 从未申请。 */
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
/** 未读角标:30s 轮询只拿 count。 */
export const useUnreadCount = (opts?: QueryOpts) =>
  useQuery({
    queryKey: keys.notifications.unreadCount,
    queryFn: () => unreadCountApiV1NotificationsUnreadCountGet(),
    ...opts,
  });
/** 通知弹层游标分页。 */
export const useNotificationPages = (params?: { unread?: boolean }) =>
  useCursorPages(keys.notifications.pages(params), listNotificationsApiV1NotificationsGet, params, 20);
export const useSkus = (opts?: QueryOpts) =>
  useQuery({ queryKey: keys.skus, queryFn: () => listSkusApiV1SkusGet(), ...opts });
export const useImages = () => useQuery({ queryKey: keys.images, queryFn: () => listImagesApiV1ImagesGet() });
export const useSshKeys = () => useQuery({ queryKey: keys.sshKeys, queryFn: () => listSshKeysApiV1SshKeysGet() });
export const useDisks = (opts?: QueryOpts) =>
  useQuery({ queryKey: keys.disks, queryFn: () => listDisksApiV1DisksGet(), ...opts });
/** 轻量整表视图(首 100 条;dashboard 计数 / 存储页 / support 关联选择用);列表页走 useInstancePages。 */
export const useInstances = (opts?: QueryOpts<PageInstanceOut>) =>
  useQuery<PageInstanceOut, ApiError, InstanceOut[]>({
    queryKey: keys.instances.first100,
    queryFn: () => listInstancesApiV1InstancesGet({ limit: 100 }),
    select: (p) => p.items,
    ...opts,
  });
/** 临期包周期实例(到期横幅数据源):服务端按 within_days 过滤,不分页。 */
export const useExpiringInstances = (withinDays: number | undefined, opts?: QueryOpts) =>
  useQuery({
    queryKey: keys.instances.expiring(withinDays),
    queryFn: () => listExpiringInstancesApiV1InstancesExpiringGet({ within_days: withinDays ?? 7 }),
    ...opts,
    enabled: withinDays !== undefined && (opts?.enabled ?? true),
  });
/** 实例列表游标分页:status 精确 / name 模糊服务端过滤,不挂 refetchInterval;过渡态由 useTransientInstanceRefresh 逐台轮询驱动。 */
export const useInstancePages = (params?: { status?: string; name?: string }) => {
  const status = params?.status;
  const name = params?.name?.trim() || undefined;
  return useCursorPages(keys.instances.pages({ status, name }), listInstancesApiV1InstancesGet, { status, name }, 20);
};

/** 过渡态条目逐条轻轮询:从已加载行派生过渡态 id,逐条轮询单条端点(POLL.transient,到终态即停);本轮 vs 上轮 status 变化即失效列表查询,首轮只落基线。 */
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
      // 到终态即停
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
    // 退出过渡态集合的 id 清出基线
    for (const id of [...lastSeen.current.keys()]) {
      if (!alive.has(id)) lastSeen.current.delete(id);
    }
    if (changed) void queryClient.invalidateQueries({ queryKey: listPrefix });
    // statusesKey 聚合轮询结果变化;results 仅作取值通道
  }, [statusesKey, transientIds, results, queryClient, listPrefix]);
}

/** 过渡态实例逐台轻轮询(creating/starting/stopping/releasing)。 */
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
    // 服务端降序游标分页;取大页覆盖「失败原因 / 是否运行过」判定
    queryFn: () => listInstanceEventsApiV1InstancesUuidEventsGet(uuid, { limit: 200 }),
    ...opts,
  });
/** 事件时间线游标分页:不挂 refetchInterval;详情页轮询检测到 status 迁移后失效 keys.instances.events 前缀(立即 + 延迟各一次)。 */
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

// ---------- 在线服务 ----------
/** 轻量整表视图(首 100 条;概览计数 / 命令面板用);列表页走 useServicePages。 */
export const useServices = (opts?: QueryOpts<PageServiceOut>) =>
  useQuery<PageServiceOut, ApiError, ServiceOut[]>({
    queryKey: keys.services.first100,
    queryFn: () => listServicesApiV1ServicesGet({ limit: 100 }),
    select: (p) => p.items,
    ...opts,
  });
/** 服务列表游标分页:status(派生态)精确 / name 模糊(含 slug 前缀)服务端过滤,不挂轮询。 */
export const useServicePages = (params?: { status?: string; name?: string }) => {
  const status = params?.status;
  const name = params?.name?.trim() || undefined;
  return useCursorPages(keys.services.pages({ status, name }), listServicesApiV1ServicesGet, { status, name }, 20);
};
/** 过渡态服务逐条轻轮询(deploying / stopping / releasing)。 */
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
/** 服务级时间线(全部版本实例事件并集)游标分页;不挂轮询,由详情页服务轮询检测到迁移后失效。 */
export const useServiceEventPages = (slug: string) =>
  useCursorPages(
    keys.services.eventPages(slug),
    (p?: ListServiceEventsApiV1ServicesSlugEventsGetParams) => listServiceEventsApiV1ServicesSlugEventsGet(slug, p),
    undefined,
    50,
  );
/** 版本历史 = 该服务下全部实例(含已释放),版本号降序。 */
export const useServiceRevisions = (slug: string, opts?: QueryOpts) =>
  useQuery({
    queryKey: keys.services.revisions(slug),
    queryFn: () => listRevisionsApiV1ServicesSlugRevisionsGet(slug, { limit: 50 }),
    ...opts,
  });
/** 当前版本容器日志:tail / 自动刷新由调用方经 params 与 refetchInterval 控制。 */
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
/** 服务 API Key 列表:只有前缀。 */
export const useServiceApiKeys = (slug: string, opts?: QueryOpts<ApiKeyOut[]>) =>
  useQuery({
    queryKey: keys.services.apiKeys(slug),
    queryFn: () => listApiKeysApiV1ServicesSlugApiKeysGet(slug),
    ...opts,
  });
/** 服务小时账单(全部版本实例并集)游标分页。 */
export const useServiceBillPages = (slug: string) =>
  useCursorPages(
    keys.services.billPages(slug),
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
// ---------- 实例 / 服务共用 ----------
/** 工作负载标识:实例(uuid)或服务(slug;打当前版本实例)。 */
export type WorkloadSubject = { kind: "instance"; uuid: string } | { kind: "service"; slug: string };

/** 容器日志(实例或服务当前版本实例):tail / 自动刷新由调用方经 params 与 refetchInterval 控制。 */
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

/** 事件时间线游标分页(实例,或服务全部版本实例并集);不挂轮询,由外层轮询检测到迁移后失效。 */
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

/** 小时账单游标分页。 */
export const useHourlyBillPages = (params?: Omit<ListHourlyBillsApiV1BillsHourlyGetParams, "cursor" | "limit">) =>
  useCursorPages(keys.bills.pages(params), listHourlyBillsApiV1BillsHourlyGet, params, 50);
/** 资金流水游标分页,必须走 useInfiniteQuery。 */
export const useLedgerPages = (limit = 20) =>
  useCursorPages(keys.ledger.pages(limit), getLedgerApiV1WalletLedgerGet, undefined, limit);
/** 月度汇总:窗口按本地月界切,offset 与「今日消费」同一来源。 */
export const useBillSummary = (month: string, tzOffsetMinutes: number) =>
  useQuery({
    queryKey: keys.billSummary.of(month, tzOffsetMinutes),
    queryFn: () => billSummaryApiV1BillsSummaryGet({ month, tz_offset_minutes: tzOffsetMinutes }),
  });
/** 策略常量(盘价 / 回收天数等):公开端点,5 分钟内不重取。 */
export const usePolicies = () =>
  useQuery({ queryKey: keys.policies, queryFn: () => getPoliciesApiV1PoliciesGet(), staleTime: 5 * 60_000 });
/** 站点公开配置(备案号 / 可用支付渠道):公开端点。 */
export const useSiteConfig = () =>
  useQuery({ queryKey: keys.siteConfig, queryFn: () => getSiteConfigApiV1SiteConfigGet() });
/** 法务文档:公开端点,按界面语言取 published 版(en-US 缺失服务端回落 zh-CN)。 */
export const useLegalDoc = (docKey: string, lang: string) =>
  useQuery({ queryKey: keys.legalDoc(docKey, lang), queryFn: () => getLegalDocApiV1LegalDocKeyGet(docKey, { lang }) });
/** 实例列表 sparkline 批量摘要:断源时 available=false(200),独立于实例轮询。 */
export const useMetricsSummary = (opts?: QueryOpts) =>
  useQuery({
    queryKey: keys.metricsSummary,
    queryFn: () => instancesMetricsSummaryApiV1MetricsInstancesGet(),
    ...opts,
  });
/** 当日消费:date 为调用方本地 YYYY-MM-DD。 */
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
/** 退款表单候选集:可申请口径的充值订单(不可申请行带 reason_code)。 */
export const useRefundableOrders = (opts?: QueryOpts) =>
  useQuery({
    queryKey: keys.refundableOrders,
    queryFn: () => listRefundableOrdersApiV1WalletRefundsEligibleOrdersGet(),
    ...opts,
  });
/** 我的退款单游标分页。 */
export const useRefundPages = (limit = 20) =>
  useCursorPages(keys.refunds.pages(limit), listMyRefundsApiV1WalletRefundsGet, undefined, limit);
/** 各账期可开票额度预览(仅 amount > 0 的已结束账期)。 */
export const useInvoiceEligible = (opts?: QueryOpts) =>
  useQuery({
    queryKey: keys.invoiceEligible,
    queryFn: () => listInvoiceEligibleApiV1BillingInvoicesEligibleGet(),
    ...opts,
  });
/** 我的发票申请游标分页。 */
export const useInvoicePages = (limit = 20) =>
  useCursorPages(keys.invoices.pages(limit), listMyInvoicesApiV1BillingInvoicesGet, undefined, limit);
/** 我的工单游标分页。 */
export const useTicketPages = (limit = 20) =>
  useCursorPages(keys.tickets.pages(limit), listMyTicketsApiV1TicketsGet, undefined, limit);
/** 工单详情 + 消息流;他人工单 404 由错误页兜底。 */
export const useTicketDetail = (ticketId: number, opts?: QueryOpts<TicketDetailOut>) =>
  useQuery({
    queryKey: keys.tickets.detail(ticketId),
    queryFn: () => getMyTicketApiV1TicketsTicketIdGet(ticketId),
    ...opts,
  });
