/** 在线服务:全局服务表(不限租户),FilterBar(检索 / 含已删除,入 URL);处置只有强制停止。 */

import { FilterBar, PageContainer } from "@superdl/ui/components";
import { adminKeys } from "../../api";
import { controlWidth, useUrlCommittedInput, useUrlFilters } from "@superdl/ui";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { Button, Card, Checkbox, Input } from "antd";
import { useCallback } from "react";
import { useTranslation } from "react-i18next";

import { useQueryClient } from "@tanstack/react-query";
import { AdminServicesTable } from "./-AdminServicesTable";

export interface ServicesSearch {
  q?: string;
  user_id?: number;
  released?: "1";
}

/** q:名称或 slug 前缀;user_id:租户过滤;released=1:含已删除。 */
export function servicesValidateSearch(search: Record<string, unknown>): ServicesSearch {
  const out: ServicesSearch = {};
  if (typeof search.q === "string" && search.q.trim()) out.q = search.q;
  const uid = Number(search.user_id);
  if (Number.isInteger(uid) && uid > 0) out.user_id = uid;
  if (search.released === "1" || search.released === 1 || search.released === true) out.released = "1";
  return out;
}

export const Route = createFileRoute("/_app/services")({
  validateSearch: servicesValidateSearch,
  component: ServicesPage,
});

function ServicesPage() {
  const { t } = useTranslation(["admin", "shared"]);
  const navigate = useNavigate({ from: "/services" });
  const qc = useQueryClient();
  const { q, user_id: userId, released } = Route.useSearch();
  const commitQ = useCallback(
    (next: string | undefined) =>
      void navigate({ to: "/services", replace: true, search: (prev) => ({ ...prev, q: next }) }),
    [navigate],
  );
  const { value: input, setValue: setInput } = useUrlCommittedInput(q, commitQ);
  const setUrl = useCallback(
    (next: Partial<ServicesSearch>) =>
      void navigate({ to: "/services", replace: true, search: (prev) => ({ ...prev, ...next }) }),
    [navigate],
  );
  // user_id 由抽屉「看全部」带入,也算筛选(清除筛选一并清掉)
  const filters = useUrlFilters({
    search: { q, user_id: userId, released },
    keys: ["q", "user_id", "released"],
    commit: setUrl,
  });

  return (
    <PageContainer
      width="full"
      title={t("menu.services")}
      extra={
        <Button onClick={() => void qc.invalidateQueries({ queryKey: adminKeys.services })}>
          {t("common.refresh")}
        </Button>
      }
    >
      <Card>
        <FilterBar hasFilter={filters.hasFilter} onClear={filters.clear}>
          <Input.Search
            allowClear
            placeholder={t("services.searchPlaceholder")}
            style={{ width: controlWidth.md }}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onSearch={(v) => commitQ(v || undefined)}
          />
          <Checkbox
            checked={released === "1"}
            onChange={(e) => setUrl({ released: e.target.checked ? "1" : undefined })}
          >
            {t("services.includeReleased")}
          </Checkbox>
        </FilterBar>
        <AdminServicesTable userId={userId} q={q} includeReleased={released === "1"} hasFilter={filters.hasFilter} />
      </Card>
    </PageContainer>
  );
}
