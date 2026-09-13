/** Cmd+K 命令面板:页面导航(按角色过滤,同侧栏 MENU 源)+ 实体检索(纯数字 → 租户 id;≥6 位十六进制 → 实例 uuid 前缀,输入即查;
 *  节点名 / SKU 名 / 服务名与 slug 只在已缓存的列表里子串匹配,不为面板发新请求)+ 快捷动作;壳在 @superdl/ui CommandPaletteShell。 */

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

/** 顶栏触发器与面板的事件总线名 */
export const COMMAND_PALETTE_OPEN_EVENT = "superdl:admin-command-palette-open";

export { COMMAND_KBD_HINT };

/** 每个实体分组最多列几条 */
const ENTITY_HITS = 8;

/** 在线服务列表按参数分了多份缓存,前缀取全部再按 id 去重。 */
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
  // 实体检索:纯数字当租户 id;≥6 位十六进制当实例 uuid 前缀;都只在面板打开且命中形态时发请求
  const tenantId = /^\d{1,9}$/.test(q) ? q : null;
  const uuidPrefix = /^[0-9a-f]{6,32}$/i.test(q) ? q.toLowerCase() : null;
  const tenantsQ = useTenants(tenantId && canSeeMenu("/tenants", role) ? { q: tenantId } : undefined);
  const tenantHits = tenantId ? (tenantsQ.data?.pages[0]?.items ?? []).filter((t) => String(t.id) === tenantId) : [];
  const instancesQ = useAdminInstances(uuidPrefix ? { q: uuidPrefix } : undefined, {
    enabled: open && uuidPrefix != null && canSeeMenu("/tenants", role),
    limit: 10,
  });
  const instanceHits = uuidPrefix ? (instancesQ.data?.pages[0]?.items ?? []) : [];

  // 页面按侧栏分组分节(与 MENU.group 同源),角色不可见的页不出
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

  // 实体名检索:大小写不敏感子串,只在已缓存的列表里找(列表页访问过才有命中)
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
        value: `${row.id} ${row.phone_masked}`,
        label: (
          <>
            <TeamOutlined />
            <span>
              #{row.id} · {row.phone_masked}
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
        // 未确认告警深链:仅可见 /alerts 的角色
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
            // 失效重取当前页所有查询
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
