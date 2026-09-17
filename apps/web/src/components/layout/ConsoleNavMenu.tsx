/** Console navigation menu (one rendering for the sidebar and the narrow-screen drawer): group titles only for multi-item groups, a divider before single-item groups; the unread badge is not in the main navigation (the bell carries it).
 *  Item labels are Links (middle-click / new tab, with aria-current); the caller decides whether to close the drawer after a click. */

import { Link, useRouterState } from "@tanstack/react-router";
import { Menu, type MenuProps } from "antd";
import { useTranslation } from "react-i18next";

import { useThemeMode } from "../../stores/theme";
import { CONSOLE_NAV_GROUPS, consoleNavSelected } from "./consoleNav";

type MenuItem = NonNullable<MenuProps["items"]>[number];

export function ConsoleNavMenu({ onNavigate }: { onNavigate?: () => void }) {
  const { t } = useTranslation();
  const mode = useThemeMode();
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const selected = consoleNavSelected(pathname);

  const items: MenuItem[] = [];
  CONSOLE_NAV_GROUPS.forEach((g, gi) => {
    const children: MenuItem[] = g.items.map((n) => ({
      key: n.key,
      icon: n.icon,
      label: (
        <Link to={n.key} aria-current={selected === n.key ? "page" : undefined} onClick={onNavigate}>
          {t(n.labelKey)}
        </Link>
      ),
    }));
    if (g.items.length > 1) {
      items.push({ key: `group:${g.key}`, type: "group", label: t(g.labelKey), children });
    } else {
      if (gi > 0) items.push({ key: `divider:${g.key}`, type: "divider" });
      items.push(...children);
    }
  });

  return (
    <Menu
      mode="inline"
      theme={mode === "dark" ? "dark" : "light"}
      selectedKeys={selected ? [selected] : []}
      items={items}
      style={{ borderInlineEnd: "none", paddingTop: 8 }}
    />
  );
}
