import { PageContainer } from "@superdl/ui/components";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { Card } from "antd";

import { useTranslation } from "react-i18next";

import { AUDIT_DEFAULT_LIMIT } from "../../api";
import { AuditTable } from "../../components/AuditTable";

export const Route = createFileRoute("/_app/audit")({
  // actor_type/actor_id/q/时间窗/limit 入 URL
  validateSearch: (search: Record<string, unknown>): {
    actor_type?: string;
    actor_id?: string;
    q?: string;
    since?: string;
    until?: string;
    limit?: number;
  } => {
    const iso = (v: unknown) =>
      typeof v === "string" && v !== "" && !Number.isNaN(Date.parse(v)) ? v : undefined;
    const limit = Number(search.limit);
    return {
      actor_type:
        typeof search.actor_type === "string" && search.actor_type ? search.actor_type : undefined,
      actor_id: typeof search.actor_id === "string" && search.actor_id ? search.actor_id : undefined,
      q: typeof search.q === "string" && search.q ? search.q : undefined,
      since: iso(search.since),
      until: iso(search.until),
      // 默认值剥离出 URL
      limit:
        [50, 100, 200, 500].includes(limit) && limit !== AUDIT_DEFAULT_LIMIT ? limit : undefined,
    };
  },
  component: AuditPage,
});

function AuditPage() {
  const { t } = useTranslation();
  const search = Route.useSearch();
  const navigate = useNavigate();
  return (
    <PageContainer title={t("menu.audit")}>
      <Card>
        <AuditTable
          initial={search}
          onCommit={(filters) => void navigate({ to: "/audit", replace: true, search: filters })}
        />
      </Card>
    </PageContainer>
  );
}
