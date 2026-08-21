/**
 * 审计检索。
 *
 * detail(JSONB)里装着全站那些「原因必填」弹窗收上来的原因、变更前后值、金额 ——
 * 此前一个字段都不透出,页面上只能看到「admin.POST /tenants/5/freeze → user:5 → 200」,
 * 看不到为什么冻结、单价从多少改到多少,复盘必须连库查 JSONB。
 */

import { adminColors, formatDateTime } from "@superdl/ui";
import { DatePicker, Input, Select, Space, Table, Tag, Typography } from "antd";
import type { Dayjs } from "dayjs";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { type AuditRow, useAuditLog } from "../api";

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
  const [range, setRange] = useState<[Dayjs | null, Dayjs | null] | null>(null);
  const { data } = useAuditLog({
    ...(actorType ? { actor_type: actorType } : {}),
    ...(actorId ? { actor_id: actorId } : {}),
    ...(q ? { q } : {}),
    ...(range?.[0] ? { since: range[0].startOf("day").toISOString() } : {}),
    ...(range?.[1] ? { until: range[1].endOf("day").toISOString() } : {}),
  });
  const rows: AuditRow[] = data ?? [];

  return (
    <>
      <Space wrap style={{ marginBottom: 12 }}>
      <Select
        allowClear
        placeholder={t("audit.actorTypePlaceholder")}
        style={{ width: 160 }}
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
        style={{ width: 160 }}
        onSearch={setActorId}
      />
      <Input.Search
        allowClear
        placeholder={t("audit.keywordPlaceholder")}
        style={{ width: 220 }}
        onSearch={setQ}
      />
      <DatePicker.RangePicker onChange={(v) => setRange(v as [Dayjs | null, Dayjs | null] | null)} />
      </Space>
      <Table<AuditRow>
        scroll={{ x: 900 }}
        rowKey="id"
        dataSource={rows}
        size="small"
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
          // 摘要之外的完整 detail:调账金额、变更键清单、平台配置改了哪些键
          rowExpandable: (r) => r.detail != null && Object.keys(r.detail).length > 0,
          expandedRowRender: (r) => (
            <pre style={{ margin: 0, fontSize: 12, whiteSpace: "pre-wrap" }}>
              {JSON.stringify(r.detail, null, 2)}
            </pre>
          ),
        }}
      />
    </>
  );
}
