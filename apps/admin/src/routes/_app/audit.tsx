import { createFileRoute } from "@tanstack/react-router";
import { Card } from "antd";

import { AuditTable } from "../../components/AuditTable";

export const Route = createFileRoute("/_app/audit")({
  component: AuditPage,
});

function AuditPage() {
  return (
    <Card title="审计日志(谁在什么时候对什么做了什么)">
      <AuditTable />
    </Card>
  );
}
