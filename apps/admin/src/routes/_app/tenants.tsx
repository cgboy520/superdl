/** Tenants and instances: tabs (tenants -TenantsTab / instances -InstancesTab / deletion requests -DeletionsTab) + tenant drawer (-TenantDrawer, ?tenant= deep link); filters and search all in the URL. */

import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { Card, Tabs } from "antd";
import { useTranslation } from "react-i18next";

import { deletionStatusMap, instanceStatusMap } from "@superdl/ui";
import { PageContainer } from "@superdl/ui/components";

import { DRAWER_TABS, type DrawerTab } from "./-TenantDrawer";
import { TenantsTab } from "./-TenantsTab";
import { InstancesTab } from "./-InstancesTab";
import { DeletionsTab } from "./-DeletionsTab";

const TENANTS_TABS = ["tenants", "instances", "deletions"] as const;
type TenantsTabKey = (typeof TENANTS_TABS)[number];
export const Route = createFileRoute("/_app/tenants")({
  validateSearch: (
    search: Record<string, unknown>,
  ): {
    q?: string;
    tab?: TenantsTabKey;
    dtab?: DrawerTab;
    istatus?: string;
    inode?: string;
    iq?: string;
    tstatus?: string;
    order?: "asc";
    dstatus?: string;
    /** Tenant id whose drawer is open (shareable view, in the URL) */
    tenant?: number;
  } => ({
    q: typeof search.q === "string" && search.q ? search.q : undefined,
    tab: TENANTS_TABS.includes(search.tab as TenantsTabKey) ? (search.tab as TenantsTabKey) : undefined,
    dtab: DRAWER_TABS.includes(search.dtab as DrawerTab) ? (search.dtab as DrawerTab) : undefined,
    istatus: typeof search.istatus === "string" && search.istatus in instanceStatusMap ? search.istatus : undefined,
    inode: typeof search.inode === "string" && search.inode ? search.inode : undefined,
    iq: typeof search.iq === "string" && search.iq ? search.iq : undefined,
    tstatus: search.tstatus === "active" || search.tstatus === "frozen" ? search.tstatus : undefined,
    order: search.order === "asc" ? "asc" : undefined,
    dstatus: typeof search.dstatus === "string" && search.dstatus in deletionStatusMap ? search.dstatus : undefined,
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
