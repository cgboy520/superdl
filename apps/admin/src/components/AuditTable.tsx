/** 审计检索。detail(JSONB)承载各「原因必填」弹窗收上来的原因、变更前后值与金额。 */

import { adminColors, fontSize, formatDateTime, useCsvExport } from "@superdl/ui";
import { LoadMore, TableErrorEmpty } from "@superdl/ui/components";
import { Button, DatePicker, Input, Select, Space, Table, Tag, Tooltip, Typography } from "antd";
import dayjs from "dayjs";
import type { Dayjs } from "dayjs";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { AUDIT_DEFAULT_LIMIT, type AuditRow, exportAuditCsv, isApiError, useAuditLog } from "../api";

/** detail 摘要:优先显示 reason,其次 before→after,最后回落原始 JSON。 */
function detailSummary(detail: Record<string, unknown> | null | undefined): string {
  if (!detail || Object.keys(detail).length === 0) return "";
  const parts: string[] = [];
  const before = detail["before"];
  const after = detail["after"];
  if (before && typeof before === "object" && Object.keys(before).length > 0) {
    parts.push(`${JSON.stringify(before)} → ${JSON.stringify(after ?? {})}`);
  }
  const reason = detail["reason"];
  if (typeof reason === "string" && reason) parts.push(reason);
  return parts.length ? parts.join(" · ") : JSON.stringify(detail);
}

export interface AuditFilters {
  actor_type?: string;
  actor_id?: string;
  q?: string;
  /** 时间窗(ISO,分钟级);与 limit 一并入 URL */
  since?: string;
  until?: string;
  limit?: number;
}

export function AuditTable({
  initial,
  onCommit,
}: {
  /** 路由 search 预筛(跳审计链接);变化时回流进筛选框。 */
  initial?: AuditFilters;
  /** 筛选提交后回写 URL(/audit 页传入,replace 不产生历史垃圾);不传则纯本地状态 */
  onCommit?: (filters: AuditFilters) => void;
}) {
  const { t } = useTranslation();
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
  // URL 预筛变化(外部跳入)回流进受控/输入框:渲染期派生态,不进 effect
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
    // showTime:分钟级窗口,不强制整天(startOf/endOf 会把边界外的记录吞掉)
    ...(range?.[0] ? { since: range[0].toISOString() } : {}),
    ...(range?.[1] ? { until: range[1].toISOString() } : {}),
    limit,
  };
  const audit = useAuditLog(filters);
  const rows: AuditRow[] = audit.data?.pages.flatMap((p) => p) ?? [];
  const { doExport, exporting } = useCsvExport((tz, lang) => exportAuditCsv(filters, tz, lang));

  return (
    <>
      <Space wrap style={{ marginBottom: 12 }}>
      <Select
        allowClear
        placeholder={t("audit.actorTypePlaceholder")}
        style={{ width: 140 }}
        value={actorType}
        onChange={(v) => {
          setActorType(v);
          commit({ actor_type: v, actor_id: actorId || undefined, q: q || undefined, since: filters.since, until: filters.until, limit });
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
        style={{ width: 150 }}
        value={actorIdInput}
        onChange={(e) => setActorIdInput(e.target.value)}
        onSearch={(v) => {
          setActorId(v);
          commit({ actor_type: actorType, actor_id: v || undefined, q: q || undefined, since: filters.since, until: filters.until, limit });
        }}
      />
      <Input.Search
        allowClear
        placeholder={t("audit.keywordPlaceholder")}
        style={{ width: 200 }}
        value={qInput}
        onChange={(e) => setQInput(e.target.value)}
        onSearch={(v) => {
          setQ(v);
          commit({ actor_type: actorType, actor_id: actorId || undefined, q: v || undefined, since: filters.since, until: filters.until, limit });
        }}
      />
      <DatePicker.RangePicker
        showTime={{ format: "HH:mm" }}
        value={range}
        onChange={(v) => {
          const next = v as [Dayjs | null, Dayjs | null] | null;
          setRange(next);
          commit({
            actor_type: actorType,
            actor_id: actorId || undefined,
            q: q || undefined,
            since: next?.[0]?.toISOString(),
            until: next?.[1]?.toISOString(),
            limit,
          });
        }}
      />
      <Select<number>
        value={limit}
        style={{ width: 130 }}
        onChange={(v) => {
          setLimit(v);
          commit({ actor_type: actorType, actor_id: actorId || undefined, q: q || undefined, since: filters.since, until: filters.until, limit: v });
        }}
        options={[50, 100, 200, 500].map((v) => ({
          value: v,
          label: t("audit.limitOption", { count: v }),
        }))}
      />
      <Button onClick={() => void doExport()} loading={exporting}>
        {t("common.exportCsv")}
      </Button>
      </Space>
      <Table<AuditRow>
        scroll={{ x: 900 }}
        rowKey="id"
        dataSource={rows}
        size="small"
        loading={audit.isLoading}
        locale={{
          emptyText: (
            <TableErrorEmpty
              isError={audit.isError}
              isForbidden={isApiError(audit.error) && audit.error.status === 403}
              onRetry={() => void audit.refetch()}
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
              // 长 JSON 一行截断 + 悬浮看全文;完整结构化仍在下方展开行
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
        hasNextPage={Boolean(audit.hasNextPage)}
        loading={audit.isFetchingNextPage}
        isError={audit.isFetchNextPageError}
        loadedCount={rows.length}
        onLoadMore={() => void audit.fetchNextPage()}
      />
    </>
  );
}
