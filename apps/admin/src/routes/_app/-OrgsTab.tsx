/** Organizations tab: FilterBar (search by uuid / member user id / name, audited; status) in the URL + organization drawer (?org=). */

import { useNavigate, getRouteApi } from "@tanstack/react-router";
import { Button, Input, Select, Space, Tag, Typography } from "antd";
import { useCallback } from "react";
import { useTranslation } from "react-i18next";

import { controlWidth, flattenPages, formatDateTime, layout, useUrlCommittedInput, useUrlFilters } from "@superdl/ui";
import { CursorTable, EmptyState, FilterBar, Mono } from "@superdl/ui/components";

import { type AdminOrgOut, useAdminOrgs } from "../../api";
import { TenantLink } from "../../components/TenantLink";
import { OrgDrawer } from "./-OrgDrawer";

const routeApi = getRouteApi("/_app/tenants");

export function OrgsTab() {
  const { t } = useTranslation(["admin", "shared"]);
  const navigate = useNavigate({ from: "/tenants" });
  const urlQ = routeApi.useSearch({ select: (s) => s.oq });
  const status = routeApi.useSearch({ select: (s) => s.ostatus });
  const openUuid = routeApi.useSearch({ select: (s) => s.org });
  const commitFilters = useCallback(
    (patch: { oq?: string; ostatus?: "active" | "dissolved" }) =>
      void navigate({ to: "/tenants", replace: true, search: (prev) => ({ ...prev, ...patch }) }),
    [navigate],
  );
  const commitQ = useCallback((next: string | undefined) => commitFilters({ oq: next }), [commitFilters]);
  const { value: input, setValue: setInput } = useUrlCommittedInput(urlQ, commitQ);
  const filters = useUrlFilters({
    search: { oq: urlQ, ostatus: status },
    keys: ["oq", "ostatus"],
    commit: commitFilters,
  });
  const orgsQ = useAdminOrgs({ ...(urlQ ? { q: urlQ } : {}), ...(status ? { status } : {}) });
  const rows = flattenPages(orgsQ.data);
  const open = (uuid: string | undefined) =>
    void navigate({ to: "/tenants", replace: true, search: (prev) => ({ ...prev, org: uuid }) });

  return (
    <>
      <FilterBar hasFilter={filters.hasFilter} onClear={filters.clear}>
        <Input.Search
          allowClear
          placeholder={t("orgs.searchPlaceholder")}
          style={{ width: controlWidth.md }}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onSearch={(v) => {
            setInput(v);
            commitFilters({ oq: v || undefined });
          }}
        />
        <Select
          allowClear
          placeholder={t("common.statusFilter")}
          style={{ width: controlWidth.sm }}
          value={status}
          onChange={(v: "active" | "dissolved" | undefined) => commitFilters({ ostatus: v })}
          options={[
            { value: "active", label: t("orgs.status.active") },
            { value: "dissolved", label: t("orgs.status.dissolved") },
          ]}
        />
      </FilterBar>
      <CursorTable<AdminOrgOut>
        query={orgsQ}
        rows={rows}
        emptyNode={
          <EmptyState
            scene={filters.hasFilter ? "search" : "list"}
            compact
            description={filters.hasFilter ? undefined : t("orgs.empty")}
            secondaryAction={
              filters.hasFilter ? (
                <Button size="small" onClick={filters.clear}>
                  {t("filter.clear", { ns: "shared" })}
                </Button>
              ) : undefined
            }
          />
        }
        scroll={{ x: 1000 }}
        sticky={{ offsetHeader: layout.topBarHeight }}
        rowKey="uuid"
        onRow={(r) => ({ style: { cursor: "pointer" }, onClick: () => open(r.uuid) })}
        columns={[
          {
            title: t("orgs.colOrg"),
            fixed: "left",
            width: 220,
            render: (_, o) => (
              <Space orientation="vertical" size={0}>
                <span>{o.name}</span>
                <Typography.Text type="secondary">
                  <Mono truncate={8}>{o.uuid}</Mono>
                </Typography.Text>
              </Space>
            ),
          },
          {
            title: t("orgs.colStatus"),
            width: 100,
            render: (_, o) => (
              <Tag color={o.status === "active" ? "green" : undefined}>{t(`orgs.status.${o.status}`)}</Tag>
            ),
          },
          {
            title: t("orgs.colOwner"),
            width: 200,
            render: (_, o) =>
              o.owner_user_id ? (
                <span onClick={(e) => e.stopPropagation()}>
                  <TenantLink id={o.owner_user_id} /> {o.owner_email_masked}
                </span>
              ) : (
                "—"
              ),
          },
          { title: t("orgs.colMembers"), width: 90, align: "right", dataIndex: "members" },
          { title: t("orgs.colPending"), width: 110, align: "right", dataIndex: "pending_invitations" },
          { title: t("orgs.colCreated"), width: 170, dataIndex: "created_at", render: formatDateTime },
          {
            title: t("orgs.colDissolved"),
            width: 170,
            render: (_, o) => (o.dissolved_at ? formatDateTime(o.dissolved_at) : "—"),
          },
        ]}
      />
      <OrgDrawer uuid={openUuid ?? null} onClose={() => open(undefined)} onChanged={() => void orgsQ.refetch()} />
    </>
  );
}
