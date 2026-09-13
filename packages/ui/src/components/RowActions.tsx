/** 行内动作槽位(两端统一,ui-ux-spec §1 规则 2):至多 1 个主动作 + 1 个次动作 + 「更多 ▾」;主 / 次动作随状态变,其余进更多。 */

import { Space } from "antd";
import type { ReactNode } from "react";

import { space } from "../tokens";
import { RowMoreMenu, type RowMenuItem } from "./RowMoreMenu";

export function RowActions({
  primary,
  secondary,
  more,
  size = "small",
}: {
  primary?: ReactNode;
  secondary?: ReactNode;
  /** 条目数组走 RowMoreMenu;也可传自带的 RowMoreMenu 节点 */
  more?: RowMenuItem[] | ReactNode;
  size?: "small" | "middle";
}) {
  return (
    <Space size={space.xs}>
      {primary}
      {secondary}
      {Array.isArray(more) ? more.length > 0 && <RowMoreMenu items={more} size={size} /> : more}
    </Space>
  );
}
