/** 审计检索。detail(JSONB)承载各「原因必填」弹窗收上来的原因、变更前后值与金额。 */

import { adminColors, formatDateTime } from "@superdl/ui";
import { App, Button, DatePicker, Input, Select, Space, Table, Tag, Tooltip, Typography } from "antd";
import type { Dayjs } from "dayjs";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { AUDIT_DEFAULT_LIMIT, type AuditRow, exportAuditCsv, useAuditLog } from "../api";
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

export function AuditTable({
  initial,
}: {
  /** 路由 search 预筛(跳审计链接);非受控输入框经 defaultValue 落值。 */
  initial?: { actor_type?: string; actor_id?: string; q?: string };
}) {
  const { t, i18n } = useTranslation();
  const { message } = App.useApp();
  const [actorType, setActorType] = useState<string | undefined>(initial?.actor_type);
  const [actorId, setActorId] = useState(initial?.actor_id ?? "");
  const [q, setQ] = useState(initial?.q ?? "");
  const [limit, setLimit] = useState<number>(AUDIT_DEFAULT_LIMIT);
  const [range, setRange] = useState<[Dayjs | null, Dayjs | null] | null>(null);
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
  const [exporting, setExporting] = useState(false);
  const doExport = async () => {
    setExporting(true);
    try {
      const lang = i18n.resolvedLanguage === "en-US" ? ("en-US" as const) : ("zh-CN" as const);
      const r = await exportAuditCsv(filters, -new Date().getTimezoneOffset(), lang);
      if (r === "truncated") {
        message.warning(t("common.csvTruncated"));
      } else {
        message.success(t("common.csvExported"));
      }
    } catch {
      message.error(t("common.csvExportFailed"));
    } finally {
      setExporting(false);
    }
  };

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
        defaultValue={initial?.actor_id}
        onSearch={setActorId}
      />
      <Input.Search
        allowClear
        placeholder={t("audit.keywordPlaceholder")}
        style={{ width: 200 }}
        defaultValue={initial?.q}
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
