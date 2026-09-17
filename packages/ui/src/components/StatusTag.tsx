/** Status badge: tag, badge, dot or text form, with icon and hint; unknown statuses are echoed as-is. */

import {
  CheckCircleFilled,
  ClockCircleFilled,
  CloseCircleFilled,
  ExclamationCircleFilled,
  MinusCircleFilled,
  PauseCircleFilled,
  SyncOutlined,
} from "@ant-design/icons";
import { Badge, Space, Tag, Tooltip } from "antd";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { isColorMeta, isStatusMeta, metaOf, type AnyStatusMap, type StatusIcon, type StatusMeta } from "../status";
import { iconSize, space } from "../tokens";
import { HexTag } from "./HexTag";

/** Union of the entries of every known status table (labelKey / hintKey stay literals so t() can check them) */
type KnownMeta = AnyStatusMap extends infer U ? (U extends Record<string, infer V> ? V : never) : never;

const ICONS: Record<StatusIcon, ReactNode> = {
  check: <CheckCircleFilled />,
  sync: <SyncOutlined spin />,
  pause: <PauseCircleFilled />,
  warning: <ExclamationCircleFilled />,
  close: <CloseCircleFilled />,
  clock: <ClockCircleFilled />,
  minus: <MinusCircleFilled />,
};

export function StatusTag({
  map,
  value,
  variant = "tag",
  icon = false,
  hint = true,
  extra,
}: {
  map: AnyStatusMap;
  value: string;
  variant?: "tag" | "badge" | "dot" | "text";
  /** Icon declared in the table before the text */
  icon?: boolean;
  /** Tooltip when hintKey exists (default on) */
  hint?: boolean;
  /** Trailing extra (freeze countdown etc.) */
  extra?: ReactNode;
}) {
  const { t: typedT } = useTranslation();
  const t = typedT as unknown as (key: string) => string;
  const meta: KnownMeta | undefined = metaOf(map as Record<string, KnownMeta>, value);
  const label = meta ? t(meta.labelKey) : value;
  const color = meta && isColorMeta(meta) ? meta.color : undefined;
  const statusMeta: StatusMeta | undefined = meta && isStatusMeta(meta) ? meta : undefined;
  const iconNode = icon && statusMeta?.icon ? ICONS[statusMeta.icon] : null;
  const hintText = hint && meta && "hintKey" in meta ? t(meta.hintKey) : undefined;

  let node: ReactNode;
  if (variant === "badge") {
    node =
      meta && isStatusMeta(meta) ? <Badge status={meta.badge} text={label} /> : <Badge color={color} text={label} />;
  } else if (variant === "dot") {
    node = <Badge color={color} aria-label={label} title={label} />;
  } else if (variant === "text") {
    node = <span style={{ color }}>{label}</span>;
  } else if (color) {
    node = <HexTag color={color}>{label}</HexTag>;
  } else {
    node = <Tag>{label}</Tag>;
  }
  const withIcon = iconNode ? (
    <Space size={space.xs} align="center" style={{ fontSize: iconSize.sm }}>
      <span style={{ color, display: "inline-flex" }}>{iconNode}</span>
      {node}
    </Space>
  ) : (
    node
  );
  const wrapped = hintText ? (
    <Tooltip title={hintText}>
      <span tabIndex={0} className="focus-ring" style={{ display: "inline-flex", cursor: "help" }}>
        {withIcon}
      </span>
    </Tooltip>
  ) : (
    withIcon
  );
  return extra ? (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
      {wrapped}
      {extra}
    </span>
  ) : (
    wrapped
  );
}
