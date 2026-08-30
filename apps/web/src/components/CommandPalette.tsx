/** Cmd+K 命令面板(cmdk):静态导航(控制台各页 + 帮助)+ 实例缓存模糊匹配 + 快捷动作。
 *  触发:顶栏触发器(派发自定义事件)或全局 ⌘K / Ctrl+K;键盘导航/过滤由 cmdk 承担。
 *  视觉用 antd token(bg=colorBgElevated,选中行走 styles.css 的 colorPrimary 浅底),
 *  实例分组只在已有缓存数据时渲染(不显示空分组)。 */

import { CloudServerOutlined, QuestionCircleOutlined, SearchOutlined } from "@ant-design/icons";
import { fontSize, instanceStatusMap, metaOf } from "@superdl/ui";
import { useNavigate } from "@tanstack/react-router";
import { Modal, theme, Typography } from "antd";
import { Command } from "cmdk";
import { useEffect, useState, type CSSProperties, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { useInstances } from "../api/queries";
import { CONSOLE_NAV } from "./layout/consoleNav";

/** 顶栏触发器与面板之间的事件总线(免全局 store) */
export const COMMAND_PALETTE_OPEN_EVENT = "superdl:command-palette-open";

export const COMMAND_KBD_HINT =
  typeof navigator !== "undefined" && /mac/i.test(navigator.platform) ? "⌘K" : "Ctrl K";

interface ActionItem {
  key: string;
  label: string;
  /** 双语关键词:中文标签与英文/拼音检索都能命中 */
  keywords: string[];
  run: () => void;
}

export function CommandPalette() {
  const { t } = useTranslation(["web", "shared"]);
  const navigate = useNavigate();
  const { token } = theme.useToken();
  const [open, setOpen] = useState(false);
  // 实例数据只取已缓存/打开后才拉(首 100 条);无缓存时实例分组不渲染
  const { data: instances } = useInstances({ enabled: open });

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

  const pageItems: ActionItem[] = [
    ...CONSOLE_NAV.map((n) => ({
      key: n.key,
      label: t(n.labelKey),
      keywords: [n.key.slice(1)],
      run: () => void navigate({ to: n.key }),
    })),
    {
      key: "/help",
      label: t("topbar.help"),
      keywords: ["help", "faq"],
      run: () => void navigate({ to: "/help" }),
    },
  ];
  const actionItems: ActionItem[] = [
    {
      key: "rent",
      label: t("command.actionRent"),
      keywords: ["rent", "market", "new", "instance"],
      run: () => void navigate({ to: "/market" }),
    },
    {
      key: "recharge",
      label: t("command.actionRecharge"),
      keywords: ["recharge", "billing", "topup", "chongzhi"],
      run: () => void navigate({ to: "/billing" }),
    },
    {
      key: "ticket",
      label: t("command.actionTicket"),
      keywords: ["ticket", "support", "gongdan"],
      run: () => void navigate({ to: "/support", search: { new: "1" } }),
    },
  ];

  const renderItems = (list: ActionItem[], icon: ReactNode) =>
    list.map((item) => (
      <Command.Item
        key={item.key}
        value={`${item.label} ${item.key}`}
        keywords={item.keywords}
        onSelect={() => go(item.run)}
        style={itemStyle}
      >
        {icon}
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
            {renderItems(pageItems, null)}
          </Command.Group>
          {instances && instances.length > 0 && (
            <Command.Group heading={<span style={headingStyle}>{t("command.groupInstances")}</span>}>
              {instances.map((i) => {
                const meta = metaOf(instanceStatusMap, i.status);
                return (
                  <Command.Item
                    key={i.uuid}
                    value={`${i.name} ${i.uuid}`}
                    keywords={[i.name, i.uuid]}
                    onSelect={() =>
                      go(() =>
                        void navigate({ to: "/instances/$uuid", params: { uuid: i.uuid } }),
                      )
                    }
                    style={itemStyle}
                  >
                    <CloudServerOutlined />
                    <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis" }}>
                      {i.name}
                    </span>
                    <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
                      {meta ? t(meta.labelKey) : i.status} · {i.spec["gpu_model"] as string} ×{" "}
                      {i.gpu_count}
                    </Typography.Text>
                  </Command.Item>
                );
              })}
            </Command.Group>
          )}
          <Command.Group heading={<span style={headingStyle}>{t("command.groupActions")}</span>}>
            {renderItems(actionItems, null)}
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
          <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
            {t("command.hintShortcuts")}
          </Typography.Text>
        </div>
      </Command>
    </Modal>
  );
}
