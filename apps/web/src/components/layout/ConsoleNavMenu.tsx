/** 控制台导航菜单(侧栏与窄屏抽屉共用同一份渲染):分组标题只给多项分组,单项分组前出分隔;通知未读角标不在主导航(铃铛承载)。
 *  条目 label 用 Link(可中键 / 新标签打开,带 aria-current),点击后由调用方决定是否收起抽屉。 */

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
