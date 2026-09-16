/** Query keys and bulk invalidation prefixes; all is the resource-domain prefix. */

import type {
  GetInstanceLogsApiV1InstancesUuidLogsGetParams,
  GetInstanceMetricsApiV1InstancesUuidMetricsGetParams,
  GetServiceLogsApiV1ServicesSlugLogsGetParams,
  ListHourlyBillsApiV1BillsHourlyGetParams,
} from "@superdl/api-client";

export const keys = {
  me: ["me"],
  deletionRequest: ["deletion-request"],
  wallet: ["wallet"],
  notifications: {
    all: ["notifications"],
    list: (params?: { unread?: boolean }) => ["notifications", params ?? null],
    unreadCount: ["notifications", "unread-count"],
    pages: (params?: { unread?: boolean }) => ["notifications", "pages", params ?? null],
  },
  skus: ["skus"],
  images: ["images"],
  sshKeys: ["ssh-keys"],
  disks: ["disks"],
  policies: ["policies"],
  siteConfig: ["site-config"],
  legalDoc: (docKey: string, lang: string) => ["legal-doc", docKey, lang],
  metricsSummary: ["metrics-summary"],
  instances: {
    all: ["instances"],
    first100: ["instances", "first100"],
    expiring: (withinDays: number | undefined) => ["instances", "expiring", withinDays ?? null],
    pagesPrefix: ["instances", "pages"],
    pages: (params: { status?: string; name?: string }) => ["instances", "pages", params],
    detail: (uuid: string) => ["instances", uuid],
    events: (uuid: string) => ["instances", uuid, "events"],
    eventPages: (uuid: string) => ["instances", uuid, "events", "pages"],
    access: (uuid: string) => ["instances", uuid, "access"],
    logs: (uuid: string, params: GetInstanceLogsApiV1InstancesUuidLogsGetParams) => ["instances", uuid, "logs", params],
    metrics: (uuid: string, params: GetInstanceMetricsApiV1InstancesUuidMetricsGetParams) => [
      "instances",
      uuid,
      "metrics",
      params,
    ],
  },
  services: {
    all: ["services"],
    first100: ["services", "first100"],
    pagesPrefix: ["services", "pages"],
    pages: (params: { status?: string; name?: string }) => ["services", "pages", params],
    detail: (slug: string) => ["services", slug],
    events: (slug: string) => ["services", slug, "events"],
    eventPages: (slug: string) => ["services", slug, "events", "pages"],
    revisions: (slug: string) => ["services", slug, "revisions"],
    logs: (slug: string, params: GetServiceLogsApiV1ServicesSlugLogsGetParams) => ["services", slug, "logs", params],
    apiKeys: (slug: string) => ["services", slug, "api-keys"],
    billPages: (slug: string) => ["services", slug, "bills", "pages"],
  },
  bills: {
    all: ["bills"],
    pages: (params?: Omit<ListHourlyBillsApiV1BillsHourlyGetParams, "cursor" | "limit">) => [
      "bills",
      "pages",
      params ?? null,
    ],
  },
  billSummary: {
    of: (month: string, tzOffsetMinutes: number) => ["bill-summary", month, tzOffsetMinutes],
  },
  billDailySummary: {
    all: ["bill-daily-summary"],
    of: (date: string, tzOffsetMinutes: number) => ["bill-daily-summary", date, tzOffsetMinutes],
  },
  ledger: {
    all: ["ledger"],
    pages: (limit: number) => ["ledger", limit],
  },
  recharge: {
    all: ["recharge"],
    detail: (orderNo: string) => ["recharge", orderNo],
  },
  refundableOrders: ["refundable-orders"],
  refunds: {
    all: ["refunds"],
    pages: (limit: number) => ["refunds", limit],
  },
  invoiceEligible: ["invoice-eligible"],
  invoices: {
    all: ["invoices"],
    pages: (limit: number) => ["invoices", limit],
  },
  tickets: {
    all: ["tickets"],
    pages: (limit: number) => ["tickets", "pages", limit],
    detail: (ticketId: number) => ["tickets", ticketId],
  },
};
