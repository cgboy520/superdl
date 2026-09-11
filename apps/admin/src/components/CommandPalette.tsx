/** Cmd+K 命令面板:页面导航(按角色过滤,同侧栏 MENU 源)+ 快捷动作;壳在 @superdl/ui CommandPaletteShell。 */

import { AlertOutlined, ReloadOutlined } from "@ant-design/icons";
import { COMMAND_KBD_HINT, CommandPaletteShell, type CommandPaletteGroup } from "@superdl/ui/components";
import { useNavigate } from "@tanstack/react-router";
import { App } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { MENU, canSeeMenu } from "../lib/menu";
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

  const groups: CommandPaletteGroup[] = [
    {
      heading: t("command.groupPages"),
      items: MENU.filter((m) => canSeeMenu(m.key, role)).map((m) => ({
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
    },
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
    />
  );
}
