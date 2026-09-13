/** 行内「更多 ▾」(两端统一):items 声明式条目(灰置项经 GatedButton 带原因,永不隐藏;危险项 danger),或 children 自带弹窗流程的按钮(ReasonAction)。
 *  显式 role=menu / menuitem(e2e 以 menuitem 定位);点击条目后收起。 */

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
      /** 不可用原因;非空即灰置(仍可聚焦,tooltip 可读) */
      reason?: ReactNode;
      icon?: ReactNode;
    }
  | { type: "divider"; key: string };

export function RowMoreMenu({
  items,
  children,
  size = "small",
  label,
  type,
  icon,
}: {
  items?: RowMenuItem[];
  /** 自带弹窗流程的按钮(如管理端 ReasonAction),与 items 二选一或并列 */
  children?: ReactNode;
  size?: "small" | "middle";
  label?: ReactNode;
  /** 当这个下拉本身是行内主动作时(服务「端点 ▾」)给 primary,与实例「连接 ▾」同一外观 */
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
