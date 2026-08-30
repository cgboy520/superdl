/** Cmd+K 命令面板(cmdk,与 web 端同范式):静态页面导航(按角色过滤,与侧栏同一 MENU 源)+ 快捷动作。
 *  触发:顶栏触发器(派发自定义事件)或全局 ⌘K / Ctrl+K;键盘导航/过滤由 cmdk 承担。
 *  视觉用 antd token(bg=colorBgElevated,选中行走 global.css 的 --admin-accent 浅底)。 */

import { AlertOutlined, QuestionCircleOutlined, ReloadOutlined, SearchOutlined } from "@ant-design/icons";
import { fontSize } from "@superdl/ui";
import { useNavigate } from "@tanstack/react-router";
import { App, Modal, theme, Typography } from "antd";
import { Command } from "cmdk";
import { useEffect, useState, type CSSProperties, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { MENU, canSeeMenu } from "../lib/menu";
import { queryClient } from "../lib/queryClient";
import { useAdminRole } from "../stores/auth";

/** 顶栏触发器与面板之间的事件总线(免全局 store) */
export const COMMAND_PALETTE_OPEN_EVENT = "superdl:admin-command-palette-open";

export const COMMAND_KBD_HINT =
  typeof navigator !== "undefined" && /mac/i.test(navigator.platform) ? "⌘K" : "Ctrl K";

interface ActionItem {
  key: string;
  label: string;
  /** 双语关键词:中文标签与英文/拼音检索都能命中 */
  keywords: string[];
  icon?: ReactNode;
  run: () => void;
}

export function CommandPalette() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { token } = theme.useToken();
  const { message } = App.useApp();
  const role = useAdminRole();
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((v) => !v);
      }
    };
    const onOpen = () => setOpen(true);
    window.addEventListener("keydown", onKey);
    window.addEventListener(COMMAND_PALETTE_OPEN_EVENT, onOpen);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener(COMMAND_PALETTE_OPEN_EVENT, onOpen);
    };
  }, []);

  const go = (run: () => void) => {
    setOpen(false);
    run();
  };

  const itemStyle: CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 8,
    padding: "8px 12px",
    borderRadius: token.borderRadius,
    cursor: "pointer",
    fontSize: fontSize.body,
  };
  const headingStyle: CSSProperties = {
    padding: "8px 12px 4px",
    fontSize: fontSize.caption,
    color: token.colorTextSecondary,
  };

  // 页面导航组与侧栏同一 MENU 源:角色过滤一致,新增菜单项自动进面板
  const pageItems: ActionItem[] = MENU.filter((m) => canSeeMenu(m.key, role)).map((m) => ({
    key: m.key,
    label: t(m.labelKey),
    keywords: [m.key === "/" ? "overview" : m.key.slice(1)],
    icon: <m.icon />,
    run: () => void navigate({ to: m.key }),
  }));
  const actionItems: ActionItem[] = [
    // 未确认告警深链:仅对可见 /alerts 的角色展示(finance 不出现)
    ...(canSeeMenu("/alerts", role)
      ? [
          {
            key: "unacked-alerts",
            label: t("command.actionUnackedAlerts"),
            keywords: ["alerts", "unacked", "gaojing"],
            icon: <AlertOutlined />,
            run: () => void navigate({ to: "/alerts", search: { acked: "unacked" } }),
          },
        ]
      : []),
    {
      key: "refresh",
      label: t("command.actionRefresh"),
      keywords: ["refresh", "reload", "shuaxin"],
      icon: <ReloadOutlined />,
      run: () => {
        // 当前页所有查询失效重取(NOC 高频动作);不导航,给即时反馈避免无感知
        void queryClient.invalidateQueries();
        message.success(t("command.refreshDone"));
      },
    },
  ];

  const renderItems = (list: ActionItem[]) =>
    list.map((item) => (
      <Command.Item
        key={item.key}
        value={`${item.label} ${item.key}`}
        keywords={item.keywords}
        onSelect={() => go(item.run)}
        style={itemStyle}
      >
        {item.icon}
        <span>{item.label}</span>
      </Command.Item>
    ));

  return (
    <Modal
      open={open}
      onCancel={() => setOpen(false)}
      footer={null}
      closable={false}
      width={560}
      style={{ top: "15vh" }}
      styles={{ body: { padding: 0 } }}
      destroyOnHidden
    >
      <Command label={t("command.trigger")} className="command-palette">
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "12px 16px",
            borderBottom: `1px solid ${token.colorBorderSecondary}`,
          }}
        >
          <SearchOutlined style={{ color: token.colorTextSecondary }} />
          <Command.Input
            autoFocus
            placeholder={t("command.trigger")}
            style={{
              flex: 1,
              border: "none",
              outline: "none",
              background: "transparent",
              color: token.colorText,
              fontSize: fontSize.sectionTitle,
            }}
          />
          <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
            esc
          </Typography.Text>
        </div>
        <Command.List label={t("command.trigger")} style={{ maxHeight: 360, overflow: "auto", padding: 8 }}>
          <Command.Empty style={{ padding: "24px 12px", textAlign: "center" }}>
            <Typography.Text type="secondary">{t("command.noResults")}</Typography.Text>
          </Command.Empty>
          <Command.Group heading={<span style={headingStyle}>{t("command.groupPages")}</span>}>
            {renderItems(pageItems)}
          </Command.Group>
          <Command.Group heading={<span style={headingStyle}>{t("command.groupActions")}</span>}>
            {renderItems(actionItems)}
          </Command.Group>
        </Command.List>
        <div
          style={{
            padding: "8px 16px",
            borderTop: `1px solid ${token.colorBorderSecondary}`,
            display: "flex",
            alignItems: "center",
            gap: 8,
          }}
        >
          <QuestionCircleOutlined style={{ color: token.colorTextSecondary }} />
          <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
            {t("command.hint")}
          </Typography.Text>
        </div>
      </Command>
    </Modal>
  );
}
