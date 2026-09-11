/** 行内「更多 ▾」:行内只留 ≤2 个高频动作,其余(含带弹窗流程的 ReasonAction)收进这里(ui-ux-spec §1 规则 2)。
 *  用 popupRender 而不是 menu.items:子项是自带 Modal 流程的按钮,不能被 Menu 的 onClick 吞掉。 */

import { DownOutlined } from "@ant-design/icons";
import { Button, Dropdown, theme } from "antd";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

export function RowMoreMenu({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const { token } = theme.useToken();
  return (
    <Dropdown
      trigger={["click"]}
      popupRender={() => (
        <div
          style={{
            background: token.colorBgElevated,
            borderRadius: token.borderRadiusLG,
            boxShadow: token.boxShadowSecondary,
            padding: 4,
            display: "flex",
            flexDirection: "column",
            gap: 2,
            minWidth: 140,
          }}
          className="row-more-menu"
        >
          {children}
        </div>
      )}
    >
      <Button size="small">
        {t("common.more")} <DownOutlined />
      </Button>
    </Dropdown>
  );
}
