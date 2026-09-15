/** 审计检索:FilterBar(操作者类型 / ID / 关键字 / 分钟级时间窗 / limit)+ 游标翻页。detail(JSONB)承载原因、变更前后值与金额。 */

import { adminColors, controlWidth, fontSize, formatDateTime, useCsvExport } from "@superdl/ui";
import { EmptyState, FilterBar, LoadMore, TableErrorEmpty } from "@superdl/ui/components";
import { Button, DatePicker, Input, Select, Table, Tag, Tooltip, Typography } from "antd";
import dayjs from "dayjs";
import type { Dayjs } from "dayjs";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { AUDIT_DEFAULT_LIMIT, type AuditRow, exportAuditCsv, isApiError, useAuditLog } from "../api";

/** detail 摘要:优先显示 reason,其次 before→after,最后回落原始 JSON。 */
function detailSummary(detail: Record<string, unknown> | null | undefined): string {
  if (!detail || Object.keys(detail).length === 0) return "";
  const parts: string[] = [];
  const before = detail.before;
  const after = detail.after;
  if (before && typeof before === "object" && Object.keys(before).length > 0) {
    parts.push(`${JSON.stringify(before)} → ${JSON.stringify(after ?? {})}`);
  }
  const reason = detail.reason;
  if (typeof reason === "string" && reason) parts.push(reason);
  return parts.length ? parts.join(" · ") : JSON.stringify(detail);
}

export interface AuditFilters {
  actor_type?: string;
  actor_id?: string;
  q?: string;
  /** 时间窗(ISO,分钟级) */
  since?: string;
  until?: string;
  limit?: number;
}

export function AuditTable({
  initial,
  onCommit,
}: {
  /** 路由 search 预筛;变化时回流进筛选框。 */
  initial?: AuditFilters;
  /** 筛选提交后回写 URL;不传则纯本地状态 */
  onCommit?: (filters: AuditFilters) => void;
}) {
  const { t } = useTranslation(["admin", "shared"]);
  const [actorType, setActorType] = useState<string | undefined>(initial?.actor_type);
  const [actorIdInput, setActorIdInput] = useState(initial?.actor_id ?? "");
  const [actorId, setActorId] = useState(initial?.actor_id ?? "");
  const [qInput, setQInput] = useState(initial?.q ?? "");
  const [q, setQ] = useState(initial?.q ?? "");
  const [limit, setLimit] = useState<number>(initial?.limit ?? AUDIT_DEFAULT_LIMIT);
  const [range, setRange] = useState<[Dayjs | null, Dayjs | null] | null>(() =>
    initial?.since || initial?.until
      ? [initial.since ? dayjs(initial.since) : null, initial.until ? dayjs(initial.until) : null]
      : null,
  );
  const initialKey = `${initial?.actor_type ?? ""} ${initial?.actor_id ?? ""} ${initial?.q ?? ""} ${initial?.since ?? ""} ${initial?.until ?? ""} ${initial?.limit ?? ""}`;
  const [prevKey, setPrevKey] = useState(initialKey);
  if (initialKey !== prevKey) {
    setPrevKey(initialKey);
    setActorType(initial?.actor_type);
    setActorIdInput(initial?.actor_id ?? "");
    setActorId(initial?.actor_id ?? "");
    setQInput(initial?.q ?? "");
    setQ(initial?.q ?? "");
    setLimit(initial?.limit ?? AUDIT_DEFAULT_LIMIT);
    setRange(
      initial?.since || initial?.until
        ? [initial.since ? dayjs(initial.since) : null, initial.until ? dayjs(initial.until) : null]
        : null,
    );
  }
  const commit = (next: AuditFilters) => onCommit?.(next);
  const filters = {
    ...(actorType ? { actor_type: actorType } : {}),
    ...(actorId ? { actor_id: actorId } : {}),
    ...(q ? { q } : {}),
    ...(range?.[0] ? { since: range[0].toISOString() } : {}),
    ...(range?.[1] ? { until: range[1].toISOString() } : {}),
    limit,
  };
  const hasFilter = Boolean(actorType || actorId || q || range?.[0] || range?.[1]);
  const clearFilters = () => {
    setActorType(undefined);
    setActorIdInput("");
    setActorId("");
    setQInput("");
    setQ("");
    setRange(null);
    commit({ limit });
  };
  const audit = useAuditLog(filters);
  const rows: AuditRow[] = audit.data?.pages.flatMap((p) => p) ?? [];
  const { doExport, exporting } = useCsvExport((tz, lang) => exportAuditCsv(filters, tz, lang));

  return (
    <>
      <FilterBar
        hasFilter={hasFilter}
        onClear={clearFilters}
        extra={
          <Button onClick={() => void doExport()} loading={exporting}>
            {t("common.exportCsv")}
          </Button>
        }
      >
        <Select
          allowClear
          placeholder={t("audit.actorTypePlaceholder")}
          style={{ width: controlWidth.sm }}
          value={actorType}
          onChange={(v) => {
            setActorType(v);
            commit({ ...filters, actor_type: v });
          }}
          options={[
            { value: "user", label: t("audit.actorUser") },
            { value: "admin", label: t("audit.actorAdmin") },
            { value: "anonymous", label: t("audit.actorAnonymous") },
          ]}
        />
        <Input.Search
          allowClear
          placeholder={t("audit.actorIdPlaceholder")}
          style={{ width: controlWidth.sm }}
          value={actorIdInput}
          onChange={(e) => setActorIdInput(e.target.value)}
          onSearch={(v) => {
            setActorId(v);
            commit({ ...filters, actor_id: v || undefined });
          }}
        />
        <Input.Search
          allowClear
          placeholder={t("audit.keywordPlaceholder")}
          style={{ width: controlWidth.md }}
          value={qInput}
          onChange={(e) => setQInput(e.target.value)}
          onSearch={(v) => {
            setQ(v);
            commit({ ...filters, q: v || undefined });
          }}
        />
        <DatePicker.RangePicker
          showTime={{ format: "HH:mm" }}
          value={range}
          onChange={(v) => {
            const next = v;
            setRange(next);
            commit({
              ...filters,
              since: next?.[0]?.toISOString(),
              until: next?.[1]?.toISOString(),
            });
          }}
        />
        <Select<number>
          value={limit}
          style={{ width: controlWidth.sm }}
          onChange={(v) => {
            setLimit(v);
            commit({ ...filters, limit: v });
          }}
          options={[50, 100, 200, 500].map((v) => ({
            value: v,
            label: t("audit.limitOption", { count: v }),
          }))}
        />
      </FilterBar>
      <Table<AuditRow>
        scroll={{ x: 900 }}
        rowKey="id"
        dataSource={rows}
        loading={audit.isLoading}
        locale={{
          emptyText: audit.isError ? (
            <TableErrorEmpty
              isError
              isForbidden={isApiError(audit.error) && audit.error.status === 403}
              onRetry={() => void audit.refetch()}
            />
          ) : (
            <EmptyState
              scene={hasFilter ? "search" : "list"}
              compact
              secondaryAction={
                hasFilter ? (
                  <Button size="small" onClick={clearFilters}>
                    {t("filter.clear", { ns: "shared" })}
                  </Button>
                ) : undefined
              }
            />
          ),
        }}
        pagination={false}
        columns={[
          { title: "ID", dataIndex: "id", width: 80 },
          {
            title: t("audit.colActor"),
            render: (_, r) => (
              <>
                <Tag color={r.actor_type === "admin" ? "purple" : "blue"}>{r.actor_type}</Tag>
                {r.actor_id ?? "-"}
              </>
            ),
          },
          { title: t("audit.colAction"), dataIndex: "action" },
          { title: t("audit.colTarget"), dataIndex: "target" },
          { title: "IP", dataIndex: "ip" },
          {
            title: t("audit.colResult"),
            dataIndex: "result",
            width: 80,
            render: (v: number) => <Tag color={v < 400 ? "green" : "red"}>{v}</Tag>,
          },
          {
            title: t("audit.colDetail"),
            dataIndex: "detail",
            render: (d: Record<string, unknown> | null) => {
              const text = detailSummary(d);
              return text ? (
                <Tooltip title={text}>
                  <Typography.Text
                    style={{
                      color: adminColors.textSecondary,
                      display: "block",
                      maxWidth: 320,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {text}
                  </Typography.Text>
                </Tooltip>
              ) : (
                "-"
              );
            },
          },
          { title: t("audit.colTime"), dataIndex: "created_at", render: formatDateTime },
        ]}
        expandable={{
          rowExpandable: (r) => r.detail != null && Object.keys(r.detail).length > 0,
          expandedRowRender: (r) => (
            <pre style={{ margin: 0, fontSize: fontSize.caption, whiteSpace: "pre-wrap" }}>
              {JSON.stringify(r.detail, null, 2)}
            </pre>
          ),
        }}
      />
      <LoadMore
        hasNextPage={audit.hasNextPage}
        loading={audit.isFetchingNextPage}
        isError={audit.isFetchNextPageError}
        loadedCount={rows.length}
        onLoadMore={() => void audit.fetchNextPage()}
      />
    </>
  );
}
