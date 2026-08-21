import { formatDateTime } from "@superdl/ui";
import { Select, Table, Tag } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { type AuditRow, useAuditLog } from "../api";

export function AuditTable() {
  const { t } = useTranslation();
  const [actorType, setActorType] = useState<string | undefined>();
  const { data } = useAuditLog(actorType ? { actor_type: actorType } : undefined);
  const rows: AuditRow[] = data ?? [];

  return (
    <>
      <Select
        allowClear
        placeholder={t("audit.actorTypePlaceholder")}
        style={{ width: 160, marginBottom: 12 }}
        value={actorType}
        onChange={setActorType}
        options={[
          { value: "user", label: t("audit.actorUser") },
          { value: "admin", label: t("audit.actorAdmin") },
          { value: "anonymous", label: t("audit.actorAnonymous") },
        ]}
      />
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
          { title: t("audit.colTime"), dataIndex: "created_at", render: formatDateTime },
        ]}
      />
    </>
  );
}
