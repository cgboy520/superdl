/** 租户与实例:Tab(租户 -TenantsTab / 实例 -InstancesTab / 注销申请 -DeletionsTab)+ 租户抽屉(-TenantDrawer,?tenant= 深链);筛选与检索全部入 URL。 */

import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { Card, Tabs } from "antd";
import { useTranslation } from "react-i18next";

import { instanceStatusMap } from "@superdl/ui";
import { PageContainer } from "@superdl/ui/components";

import { DRAWER_TABS, type DrawerTab } from "./-TenantDrawer";
import { TenantsTab } from "./-TenantsTab";
import { InstancesTab } from "./-InstancesTab";
import { DeletionsTab } from "./-DeletionsTab";

const TENANTS_TABS = ["tenants", "instances", "deletions"] as const;
type TenantsTabKey = (typeof TENANTS_TABS)[number];
export const Route = createFileRoute("/_app/tenants")({
  // q:检索;tab/dtab:页内与抽屉 Tab;istatus/inode/iq:实例 Tab 筛选;tstatus:租户状态;order:注册排序
  validateSearch: (search: Record<string, unknown>): {
    q?: string;
    tab?: TenantsTabKey;
    dtab?: DrawerTab;
    istatus?: string;
    inode?: string;
    iq?: string;
    tstatus?: string;
    order?: "asc";
    /** 打开抽屉的租户 id(可转达的视图,入 URL) */
    tenant?: number;
  } => ({
    q: typeof search.q === "string" && search.q ? search.q : undefined,
    tab: TENANTS_TABS.includes(search.tab as TenantsTabKey) ? (search.tab as TenantsTabKey) : undefined,
    dtab: DRAWER_TABS.includes(search.dtab as DrawerTab) ? (search.dtab as DrawerTab) : undefined,
    istatus:
      typeof search.istatus === "string" && search.istatus in instanceStatusMap
        ? search.istatus
        : undefined,
    inode: typeof search.inode === "string" && search.inode ? search.inode : undefined,
    iq: typeof search.iq === "string" && search.iq ? search.iq : undefined,
    tstatus:
      search.tstatus === "active" || search.tstatus === "frozen" ? search.tstatus : undefined,
    order: search.order === "asc" ? "asc" : undefined,
    tenant: Number.isInteger(Number(search.tenant)) && Number(search.tenant) > 0 ? Number(search.tenant) : undefined,
  }),
  component: TenantsPage,
});

function TenantsPage() {
  const { t } = useTranslation();
  const navigate = useNavigate({ from: "/tenants" });
  const tab = Route.useSearch({ select: (s) => s.tab });
  return (
    <PageContainer width="full" title={t("menu.tenants")}>
      <Card>
        <Tabs
          activeKey={tab ?? "tenants"}
          onChange={(key) =>
            void navigate({
              to: "/tenants",
              replace: true,
              search: (prev) => ({ ...prev, tab: key === "tenants" ? undefined : (key as TenantsTabKey) }),
            })
          }
          items={[
            { key: "tenants", label: t("tenants.tabTenants"), children: <TenantsTab /> },
            { key: "instances", label: t("tenants.tabInstances"), children: <InstancesTab /> },
            { key: "deletions", label: t("tenants.tabDeletions"), children: <DeletionsTab /> },
          ]}
        />
      </Card>
    </PageContainer>
  );
}

