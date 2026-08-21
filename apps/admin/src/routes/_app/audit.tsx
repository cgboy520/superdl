import { createFileRoute } from "@tanstack/react-router";
import { Card } from "antd";

import { useTranslation } from "react-i18next";

import { AuditTable } from "../../components/AuditTable";

export const Route = createFileRoute("/_app/audit")({
  component: AuditPage,
});

function AuditPage() {
  const { t } = useTranslation();
  return (
    <Card title={t("menu.audit")}>
      <AuditTable />
    </Card>
  );
}
