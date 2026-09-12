/** 在线服务:全局服务表(不限租户),检索与「含已删除」入 URL;处置只有强制停止。 */

import { PageContainer } from "@superdl/ui/components";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { Button, Card, Checkbox, Input, Space } from "antd";
import { useState } from "react";
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
  const [input, setInput] = useState(q ?? "");
  // URL 变化回流输入框
  const [prevQ, setPrevQ] = useState(q);
  if (q !== prevQ) {
    setPrevQ(q);
    setInput(q ?? "");
  }
  const setUrl = (next: Partial<ServicesSearch>) =>
    void navigate({ to: "/services", replace: true, search: (prev) => ({ ...prev, ...next }) });

  return (
    <PageContainer
      width="full"
      title={t("menu.services")}
      extra={
        <Space wrap>
          <Input.Search
            allowClear
            placeholder={t("services.searchPlaceholder")}
            style={{ width: 240 }}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onSearch={(v) => setUrl({ q: v || undefined })}
          />
          <Checkbox
            checked={released === "1"}
            onChange={(e) => setUrl({ released: e.target.checked ? "1" : undefined })}
          >
            {t("services.includeReleased")}
          </Checkbox>
          <Button onClick={() => void qc.invalidateQueries({ queryKey: ["admin", "services"] })}>
            {t("common.refresh")}
          </Button>
        </Space>
      }
    >
      <Card>
        <AdminServicesTable userId={userId} q={q} includeReleased={released === "1"} />
      </Card>
    </PageContainer>
  );
}
