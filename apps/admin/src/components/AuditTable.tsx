/** 审计检索。detail(JSONB)承载各「原因必填」弹窗收上来的原因、变更前后值与金额。 */

import { adminColors, formatDateTime } from "@superdl/ui";
import { Button, DatePicker, Input, Select, Space, Table, Tag, Typography } from "antd";
import type { Dayjs } from "dayjs";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { AUDIT_DEFAULT_LIMIT, type AuditRow, useAuditLog } from "../api";
import { ListCapNote } from "./ListCapNote";

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

export function AuditTable() {
  const { t } = useTranslation();
  const [actorType, setActorType] = useState<string | undefined>();
  const [actorId, setActorId] = useState("");
  const [q, setQ] = useState("");
  const [limit, setLimit] = useState<number>(AUDIT_DEFAULT_LIMIT);
  const [range, setRange] = useState<[Dayjs | null, Dayjs | null] | null>(null);
  const audit = useAuditLog({
    ...(actorType ? { actor_type: actorType } : {}),
    ...(actorId ? { actor_id: actorId } : {}),
    ...(q ? { q } : {}),
    // showTime:分钟级窗口,不再强制整天(startOf/endOf 会把边界外的记录吞掉)
    ...(range?.[0] ? { since: range[0].toISOString() } : {}),
    ...(range?.[1] ? { until: range[1].toISOString() } : {}),
    limit,
  });
  const rows: AuditRow[] = audit.data?.pages.flatMap((p) => p) ?? [];

  return (
    <>
      <Space wrap style={{ marginBottom: 12 }}>
      <Select
        allowClear
        placeholder={t("audit.actorTypePlaceholder")}
        style={{ width: 140 }}
        value={actorType}
        onChange={setActorType}
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
        onSearch={setActorId}
      />
      <Input.Search
        allowClear
        placeholder={t("audit.keywordPlaceholder")}
        style={{ width: 200 }}
        onSearch={setQ}
      />
      <DatePicker.RangePicker
        showTime={{ format: "HH:mm" }}
        onChange={(v) => setRange(v as [Dayjs | null, Dayjs | null] | null)}
      />
      <Select<number>
        value={limit}
        style={{ width: 130 }}
        onChange={setLimit}
        options={[50, 100, 200, 500].map((v) => ({
          value: v,
          label: t("audit.limitOption", { count: v }),
        }))}
      />
      </Space>
      <Table<AuditRow>
        scroll={{ x: 900 }}
        rowKey="id"
        dataSource={rows}
        size="small"
        loading={audit.isLoading}
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
                <Typography.Text style={{ color: adminColors.textSecondary }}>{text}</Typography.Text>
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
            <pre style={{ margin: 0, fontSize: 12, whiteSpace: "pre-wrap" }}>
              {JSON.stringify(r.detail, null, 2)}
            </pre>
          ),
        }}
      />
      {audit.hasNextPage && (
        <Button
          block
          size="small"
          style={{ marginTop: 8 }}
          loading={audit.isFetchingNextPage}
          onClick={() => void audit.fetchNextPage()}
        >
          {t("common.loadMore")}
        </Button>
      )}
      {/* 单页满额 = 还有更早的记录;可继续翻页或用筛选缩小范围 */}
      {audit.hasNextPage && <ListCapNote rows={limit} cap={limit} />}
    </>
  );
}
