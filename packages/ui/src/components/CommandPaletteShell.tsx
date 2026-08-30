/** ⌘K / Ctrl+K 命令面板壳(web/admin 两端共用):全局热键 + 事件总线开合 + Modal/cmdk 骨架与样式。
 *  两端薄封装只负责数据组装(groups);事件总线常量两端各自不同,作 openEventName 传入,
 *  顶栏触发器派发同名 CustomEvent。键盘导航/过滤由 cmdk 承担;视觉用 antd token
 *  (bg=colorBgElevated,选中行走各端全局 CSS 的品牌色浅底)。 */

import { QuestionCircleOutlined, SearchOutlined } from "@ant-design/icons";
import { Modal, theme, Typography } from "antd";
import { Command } from "cmdk";
import { useEffect, type CSSProperties, type ReactNode } from "react";

import { fontSize } from "../tokens";

export function isMacPlatform(): boolean {
  return typeof navigator !== "undefined" && /mac/i.test(navigator.platform);
}

/** 顶栏 kbd 提示徽标(两端同一判定) */
export const COMMAND_KBD_HINT = isMacPlatform() ? "⌘K" : "Ctrl K";

export interface CommandPaletteItem {
  key: string;
  label: ReactNode;
  /** 双语关键词:中文标签与英文/拼音检索都能命中 */
  keywords?: string[];
  /** cmdk 过滤值:label 为字符串时默认取 `${label} ${key}`;label 为复合节点(如实例行)时必传 */
  value?: string;
  run: () => void;
}

export interface CommandPaletteGroup {
  heading: string;
  items: CommandPaletteItem[];
}

export interface CommandPaletteShellProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 顶栏触发器与面板之间的事件总线名(两端各自不同) */
  openEventName: string;
  /** aria label 与输入框占位(= 各端 t("command.trigger")) */
  label: string;
  noResultsText: string;
  hintText: string;
  /** 底部第二条快捷键提示(web 端有,admin 无则不传) */
  hintExtraText?: string;
  groups: CommandPaletteGroup[];
}

export function CommandPaletteShell({
  open,
  onOpenChange,
  openEventName,
  label,
  noResultsText,
  hintText,
  hintExtraText,
  groups,
}: CommandPaletteShellProps) {
  const { token } = theme.useToken();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        onOpenChange(!open);
      }
    };
    const onOpen = () => onOpenChange(true);
    window.addEventListener("keydown", onKey);
    window.addEventListener(openEventName, onOpen);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener(openEventName, onOpen);
    };
  }, [open, onOpenChange, openEventName]);

  const go = (run: () => void) => {
    onOpenChange(false);
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

  return (
    <Modal
      open={open}
      onCancel={() => onOpenChange(false)}
      footer={null}
      closable={false}
      width={560}
      style={{ top: "15vh" }}
      styles={{ body: { padding: 0 } }}
      destroyOnHidden
    >
      <Command label={label} className="command-palette">
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
            placeholder={label}
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
        <Command.List label={label} style={{ maxHeight: 360, overflow: "auto", padding: 8 }}>
          <Command.Empty style={{ padding: "24px 12px", textAlign: "center" }}>
            <Typography.Text type="secondary">{noResultsText}</Typography.Text>
          </Command.Empty>
          {groups.map((g) =>
            g.items.length === 0 ? null : (
              <Command.Group key={g.heading} heading={<span style={headingStyle}>{g.heading}</span>}>
                {g.items.map((item) => (
                  <Command.Item
                    key={item.key}
                    value={
                      item.value ??
                      (typeof item.label === "string" ? `${item.label} ${item.key}` : item.key)
                    }
                    keywords={item.keywords}
                    onSelect={() => go(item.run)}
                    style={itemStyle}
                  >
                    {item.label}
                  </Command.Item>
                ))}
              </Command.Group>
            ),
          )}
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
            {hintText}
          </Typography.Text>
          {hintExtraText != null && (
            <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
              {hintExtraText}
            </Typography.Text>
          )}
        </div>
      </Command>
    </Modal>
  );
}
