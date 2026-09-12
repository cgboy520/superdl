/** 状态标统一入口:任何状态枚举都经 packages/ui 的映射表出色与文案;未知值原样回显灰标。表格里用 HexTag(实心色块),行内文字旁用 Badge 点。
 *  map 限定为 packages/ui 导出的状态表(字面量 labelKey),t() 保持强类型。 */

import {
  adjustmentStatusMap,
  announcementStatusMap,
  deletionStatusMap,
  diskStatusMap,
  imageCacheStatusMap,
  instanceStatusMap,
  invoiceStatusMap,
  ledgerTypeMap,
  legalDocStatusMap,
  metaOf,
  nodeEnrollStatusMap,
  orderStatusMap,
  refundStatusMap,
  serviceStatusMap,
  skuTierMap,
  subscriptionStatusMap,
  ticketStatusMap,
} from "@superdl/ui";
import { HexTag } from "@superdl/ui/components";
import { Badge } from "antd";
import { useTranslation } from "react-i18next";

type KnownStatusMap =
  | typeof instanceStatusMap
  | typeof serviceStatusMap
  | typeof subscriptionStatusMap
  | typeof imageCacheStatusMap
  | typeof nodeEnrollStatusMap
  | typeof orderStatusMap
  | typeof adjustmentStatusMap
  | typeof legalDocStatusMap
  | typeof refundStatusMap
  | typeof invoiceStatusMap
  | typeof ticketStatusMap
  | typeof deletionStatusMap
  | typeof diskStatusMap
  | typeof announcementStatusMap
  | typeof ledgerTypeMap
  | typeof skuTierMap;

/** 全部已知状态表的条目并集(labelKey 保持字面量,t() 可校验) */
type KnownMeta = KnownStatusMap extends infer U ? (U extends Record<string, infer V> ? V : never) : never;

export function StatusTag({
  map,
  value,
  variant = "tag",
}: {
  map: KnownStatusMap;
  value: string;
  variant?: "tag" | "badge";
}) {
  const { t } = useTranslation(["admin", "shared"]);
  const meta: KnownMeta | undefined = metaOf(map as Record<string, KnownMeta>, value);
  const label = meta ? t(meta.labelKey) : value;
  // badge 变体:映射表带 antd status 语义的用 status(过渡态有动效),纯色的用色点
  if (variant === "badge") {
    return meta && "badge" in meta ? (
      <Badge status={meta.badge} text={label} />
    ) : (
      <Badge color={meta?.color} text={label} />
    );
  }
  return <HexTag color={meta?.color}>{label}</HexTag>;
}
