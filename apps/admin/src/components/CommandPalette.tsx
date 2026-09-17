/** Command palette: role-filtered navigation, entity search and quick actions. */

import {
  AlertOutlined,
  ClusterOutlined,
  CloudServerOutlined,
  ReloadOutlined,
  TagsOutlined,
  TeamOutlined,
} from "@ant-design/icons";
import { COMMAND_KBD_HINT, CommandPaletteShell, type CommandPaletteGroup } from "@superdl/ui/components";
import { useNavigate } from "@tanstack/react-router";
import { App } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { adminKeys, useAdminInstances, useTenants, type AdminServiceOut, type NodeRow, type SkuAdminOut } from "../api";
import { MENU, MENU_GROUP_LABEL_KEY, MENU_GROUP_ORDER, canSeeMenu } from "../lib/menu";
import { queryClient } from "../lib/queryClient";
import { useAdminRole } from "../stores/auth";

/** Event bus name between the top-bar trigger and the palette */
export const COMMAND_PALETTE_OPEN_EVENT = "superdl:admin-command-palette-open";

export { COMMAND_KBD_HINT };

/** Max rows per entity group */
const ENTITY_HITS = 8;

/** The online service list is cached per parameter set; take every prefix match and deduplicate by id. */
function cachedServices(): AdminServiceOut[] {
  const seen = new Set<number>();
  const out: AdminServiceOut[] = [];
  const cached = queryClient.getQueriesData<{ pages: { items: AdminServiceOut[] }[] }>({
    queryKey: adminKeys.services,
  });
  for (const [, data] of cached) {
    for (const page of data?.pages ?? []) {
      for (const svc of page.items) {
        if (seen.has(svc.id)) continue;
        seen.add(svc.id);
        out.push(svc);
      }
    }
  }
  return out;
}

export function CommandPalette() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { message } = App.useApp();
  const role = useAdminRole();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const q = query.trim();
  const tenantId = /^\d{1,9}$/.test(q) ? q : null;
  const uuidPrefix = /^[0-9a-f]{6,32}$/i.test(q) ? q.toLowerCase() : null;
  const tenantsQ = useTenants(tenantId && canSeeMenu("/tenants", role) ? { q: tenantId } : undefined);
  const tenantHits = tenantId ? (tenantsQ.data?.pages[0]?.items ?? []).filter((t) => String(t.id) === tenantId) : [];
  const instancesQ = useAdminInstances(uuidPrefix ? { q: uuidPrefix } : undefined, {
    enabled: open && uuidPrefix != null && canSeeMenu("/tenants", role),
    limit: 10,
  });
  const instanceHits = uuidPrefix ? (instancesQ.data?.pages[0]?.items ?? []) : [];

  const pageGroups: CommandPaletteGroup[] = MENU_GROUP_ORDER.map((g) => ({
    heading: t(MENU_GROUP_LABEL_KEY[g]),
    items: MENU.filter((m) => m.group === g && canSeeMenu(m.key, role)).map((m) => ({
      key: m.key,
      label: (
        <>
          <m.icon />
          {t(m.labelKey)}
        </>
      ),
      value: `${t(m.labelKey)} ${m.key}`,
      keywords: [m.key === "/" ? "overview" : m.key.slice(1)],
      run: () => void navigate({ to: m.key }),
    })),
  })).filter((g) => g.items.length > 0);

  const lower = q.toLowerCase();
  const hit = (...fields: (string | null | undefined)[]) =>
    q.length > 0 && fields.some((f) => (f ?? "").toLowerCase().includes(lower));
  const nodeHits = canSeeMenu("/nodes", role)
    ? (queryClient.getQueryData<NodeRow[]>(adminKeys.nodes) ?? []).filter((n) => hit(n.name)).slice(0, ENTITY_HITS)
    : [];
  const skuHits = canSeeMenu("/skus", role)
    ? (queryClient.getQueryData<SkuAdminOut[]>(adminKeys.skus) ?? []).filter((s) => hit(s.name)).slice(0, ENTITY_HITS)
    : [];
  const serviceHits = canSeeMenu("/services", role)
    ? cachedServices()
        .filter((s) => hit(s.name, s.slug))
        .slice(0, ENTITY_HITS)
    : [];

  const entityGroups: CommandPaletteGroup[] = [
    {
      heading: t("command.groupTenants"),
      items: tenantHits.map((row) => ({
        key: `tenant:${row.id}`,
        value: `${row.id} ${row.email_masked ?? row.phone_masked ?? ""}`,
        label: (
          <>
            <TeamOutlined />
            <span>
              #{row.id} · {row.email_masked ?? row.phone_masked ?? "-"}
            </span>
          </>
        ),
        run: () => void navigate({ to: "/tenants", search: { q: String(row.id), tenant: row.id } }),
      })),
    },
    {
      heading: t("command.groupInstances"),
      items: instanceHits.map((inst) => ({
        key: `inst:${inst.uuid}`,
        value: `${inst.uuid} ${inst.name}`,
        label: (
          <>
            <CloudServerOutlined />
            <span>
              {inst.name} · {inst.uuid.slice(0, 12)}
            </span>
          </>
        ),
        run: () => void navigate({ to: "/tenants", search: { tab: "instances", iq: inst.uuid } }),
      })),
    },
    {
      heading: t("command.groupNodes"),
      items: nodeHits.map((n) => ({
        key: `node:${n.name}`,
        value: `${n.name} ${n.gpu_model}`,
        label: (
          <>
            <ClusterOutlined />
            <span>
              {n.name} · {n.gpu_model}
            </span>
          </>
        ),
        run: () => void navigate({ to: "/nodes", search: { node: n.name } }),
      })),
    },
    {
      heading: t("command.groupSkus"),
      items: skuHits.map((s) => ({
        key: `sku:${s.id}`,
        value: `${s.name} ${s.gpu_model}`,
        label: (
          <>
            <TagsOutlined />
            <span>
              {s.name} · {s.gpu_model}
            </span>
          </>
        ),
        run: () => void navigate({ to: "/skus", search: { q: s.name } }),
      })),
    },
    {
      heading: t("command.groupServices"),
      items: serviceHits.map((s) => ({
        key: `svc:${s.id}`,
        value: `${s.name} ${s.slug}`,
        label: (
          <>
            <CloudServerOutlined />
            <span>
              {s.name} · {s.slug}
            </span>
          </>
        ),
        run: () => void navigate({ to: "/services", search: { q: s.slug } }),
      })),
    },
  ].filter((g) => g.items.length > 0);

  const groups: CommandPaletteGroup[] = [
    ...entityGroups,
    ...pageGroups,
    {
      heading: t("command.groupActions"),
      items: [
        ...(canSeeMenu("/alerts", role)
          ? [
              {
                key: "unacked-alerts",
                label: (
                  <>
                    <AlertOutlined />
                    {t("command.actionUnackedAlerts")}
                  </>
                ),
                value: `${t("command.actionUnackedAlerts")} unacked-alerts`,
                keywords: ["alerts", "unacked", "gaojing"],
                run: () => void navigate({ to: "/alerts", search: { acked: "unacked" } }),
              },
            ]
          : []),
        {
          key: "refresh",
          label: (
            <>
              <ReloadOutlined />
              {t("command.actionRefresh")}
            </>
          ),
          value: `${t("command.actionRefresh")} refresh`,
          keywords: ["refresh", "reload", "shuaxin"],
          run: () => {
            void queryClient.invalidateQueries();
            message.success(t("command.refreshDone"));
          },
        },
      ],
    },
  ];

  return (
    <CommandPaletteShell
      open={open}
      onOpenChange={setOpen}
      openEventName={COMMAND_PALETTE_OPEN_EVENT}
      label={t("command.trigger")}
      noResultsText={t("command.noResults")}
      hintText={t("command.hint")}
      groups={groups}
      onQueryChange={setQuery}
    />
  );
}
