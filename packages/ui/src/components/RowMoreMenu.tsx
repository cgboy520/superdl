import { DownOutlined } from "@ant-design/icons";
import { Button, Dropdown, theme } from "antd";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { space } from "../tokens";
import { GatedButton } from "./GatedButton";

export type RowMenuItem =
  | {
      key: string;
      label: ReactNode;
      onClick: () => void;
      danger?: boolean;
      /** Reason why unavailable; non-empty = greyed (still focusable, tooltip readable) */
      reason?: ReactNode;
      icon?: ReactNode;
    }
  | { type: "divider"; key: string };

/** Row "more" menu: enabled items collapse the menu after a click; custom children do not collapse it automatically. */
export function RowMoreMenu({
  items,
  children,
  size = "small",
  label,
  type,
  icon,
}: {
  items?: RowMenuItem[];
  /** Buttons with their own dialog flow (e.g. admin ReasonAction), alternative or in addition to items */
  children?: ReactNode;
  size?: "small" | "middle";
  label?: ReactNode;
  /** primary when this dropdown itself is the row's primary action (service "Endpoint ▾"), the same look as the instance "Connect ▾" */
  type?: "default" | "primary";
  icon?: ReactNode;
}) {
  const { t } = useTranslation("shared");
  const { token } = theme.useToken();
  const [open, setOpen] = useState(false);
  return (
    <Dropdown
      trigger={["click"]}
      open={open}
      onOpenChange={setOpen}
      popupRender={() => (
        <div
          role="menu"
          className="row-more-menu"
          style={{
            background: token.colorBgElevated,
            borderRadius: token.borderRadiusLG,
            boxShadow: token.boxShadowSecondary,
            padding: space.xs,
            display: "flex",
            flexDirection: "column",
            gap: 2,
            minWidth: 140,
          }}
        >
          {items?.map((it) =>
            "type" in it ? (
              <div
                key={it.key}
                role="separator"
                style={{ height: 1, background: token.colorSplit, margin: `${space.xs}px 0` }}
              />
            ) : (
              <GatedButton
                key={it.key}
                type="text"
                role="menuitem"
                size={size}
                danger={it.danger}
                icon={it.icon}
                reason={it.reason}
                tooltipPlacement="left"
                onClick={() => {
                  setOpen(false);
                  it.onClick();
                }}
              >
                {it.label}
              </GatedButton>
            ),
          )}
          {children}
        </div>
      )}
    >
      <Button size={size} type={type} icon={icon} aria-haspopup="menu" aria-expanded={open}>
        {label ?? t("common.more")} <DownOutlined />
      </Button>
    </Dropdown>
  );
}
