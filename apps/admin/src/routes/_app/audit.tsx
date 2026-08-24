import { createFileRoute } from "@tanstack/react-router";
import { Card } from "antd";

import { useTranslation } from "react-i18next";

import { AuditTable } from "../../components/AuditTable";

export const Route = createFileRoute("/_app/audit")({
  // 租户抽屉「跳审计」带预筛跳入(F10):actor_type/actor_id/q 落进筛选框
  validateSearch: (search: Record<string, unknown>): {
    actor_type?: string;
    actor_id?: string;
    q?: string;
  } => ({
    actor_type:
      typeof search.actor_type === "string" && search.actor_type ? search.actor_type : undefined,
    actor_id: typeof search.actor_id === "string" && search.actor_id ? search.actor_id : undefined,
    q: typeof search.q === "string" && search.q ? search.q : undefined,
  }),
  component: AuditPage,
});

function AuditPage() {
  const { t } = useTranslation();
  const search = Route.useSearch();
  return (
    <Card title={t("menu.audit")}>
      {/* key 重挂载:在审计页内再次从抽屉跳入时,新预筛值能落进受控/非受控输入 */}
      <AuditTable key={JSON.stringify(search)} initial={search} />
    </Card>
  );
}
