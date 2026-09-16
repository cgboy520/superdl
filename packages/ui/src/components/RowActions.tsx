/** Row action slots (shared by both consoles, ui-ux-spec §1 rule 2): at most 1 primary + 1 secondary + "More ▾"; primary / secondary follow the status, the rest go into more. */

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
  /** An item array goes through RowMoreMenu; a ready RowMoreMenu node works too */
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
