/** ⌘K / Ctrl+K command palette shell (shared by both consoles): global hotkey + event-bus open/close + Modal/cmdk skeleton. Each console only assembles groups and passes its own openEventName. */

import { QuestionCircleOutlined, SearchOutlined } from "@ant-design/icons";
import { Modal, theme, Typography } from "antd";
import { Command } from "cmdk";
import { useEffect, type CSSProperties, type ReactNode } from "react";

import { fontSize } from "../tokens";

export function isMacPlatform(): boolean {
  return typeof navigator !== "undefined" && /mac|iphone|ipad/i.test(navigator.userAgent);
}

/** kbd hint badge in the top bar */
export const COMMAND_KBD_HINT = isMacPlatform() ? "⌘K" : "Ctrl K";

export interface CommandPaletteItem {
  key: string;
  label: ReactNode;
  /** Bilingual search keywords */
  keywords?: string[];
  /** cmdk filter value: defaults to `${label} ${key}` when label is a string; required when label is a composite node (such as an instance row) */
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
  /** Event-bus name between the top-bar trigger and the palette (different per console) */
  openEventName: string;
  /** aria label and input placeholder */
  label: string;
  noResultsText: string;
  hintText: string;
  /** Second shortcut hint at the bottom (optional) */
  hintExtraText?: string;
  groups: CommandPaletteGroup[];
  /** Input change (the caller runs server-side entity search on it, e.g. tenant id / instance uuid prefix) */
  onQueryChange?: (query: string) => void;
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
  onQueryChange,
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
            onValueChange={onQueryChange}
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
                    value={item.value ?? (typeof item.label === "string" ? `${item.label} ${item.key}` : item.key)}
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
