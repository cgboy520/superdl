import { formatDateTime } from "@superdl/ui";
import { Select, Table, Tag } from "antd";
import { useState } from "react";

import { type AuditRow, useAuditLog } from "../api";

export function AuditTable() {
  const [actorType, setActorType] = useState<string | undefined>();
  const { data } = useAuditLog(actorType ? { actor_type: actorType } : undefined);
  const rows: AuditRow[] = data ?? [];

  return (
    <>
      <Select
        allowClear
        placeholder="操作者类型"
        style={{ width: 160, marginBottom: 12 }}
        value={actorType}
        onChange={setActorType}
        options={[
          { value: "user", label: "用户" },
          { value: "admin", label: "管理员" },
          { value: "anonymous", label: "匿名" },
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
            title: "操作者",
            render: (_, r) => (
              <>
                <Tag color={r.actor_type === "admin" ? "purple" : "blue"}>{r.actor_type}</Tag>
                {r.actor_id ?? "-"}
              </>
            ),
          },
          { title: "动作", dataIndex: "action" },
          { title: "目标", dataIndex: "target" },
          { title: "IP", dataIndex: "ip" },
          {
            title: "结果",
            dataIndex: "result",
            width: 80,
            render: (v: number) => <Tag color={v < 400 ? "green" : "red"}>{v}</Tag>,
          },
          { title: "时间", dataIndex: "created_at", render: formatDateTime },
        ]}
      />
    </>
  );
}
