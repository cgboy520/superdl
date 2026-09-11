/** Cmd+K 命令面板:页面导航(按角色过滤,同侧栏 MENU 源)+ 实体检索(纯数字 → 租户 id;≥6 位十六进制 → 实例 uuid 前缀,输入即查)+ 快捷动作;壳在 @superdl/ui CommandPaletteShell。 */

import { AlertOutlined, CloudServerOutlined, ReloadOutlined, TeamOutlined } from "@ant-design/icons";
import { COMMAND_KBD_HINT, CommandPaletteShell, type CommandPaletteGroup } from "@superdl/ui/components";
import { useNavigate } from "@tanstack/react-router";
import { App } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useAdminInstances, useTenants } from "../api";
import { MENU, MENU_GROUP_LABEL_KEY, MENU_GROUP_ORDER, canSeeMenu } from "../lib/menu";
import { queryClient } from "../lib/queryClient";
import { useAdminRole } from "../stores/auth";

/** 顶栏触发器与面板的事件总线名 */
export const COMMAND_PALETTE_OPEN_EVENT = "superdl:admin-command-palette-open";

export { COMMAND_KBD_HINT };

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
  ];

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
