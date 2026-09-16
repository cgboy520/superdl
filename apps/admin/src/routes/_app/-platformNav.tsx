/** 平台配置分组导航事实源:业务分组 → 配置组、组标题键、分组状态点。 */

import { Space, Tooltip } from "antd";
import { useTranslation } from "react-i18next";

import { adminColors, fontSize, fontWeight, space } from "@superdl/ui";

import { type PlatformConfigItem } from "../../api";

export type Group = PlatformConfigItem["group"];
export interface ConfigWarning {
  key: string;
  level: "error" | "warning";
  message: string;
}

/** 左侧分组导航:业务分组 → 配置组;顺序即展示顺序,新增配置组须归入某分组。 */
export const NAV = [
  { labelKey: "platform.navSecurity", groups: ["security"] },
  {
    labelKey: "platform.navChannels",
    groups: ["captcha", "email", "sms", "real_name", "payment_wechat", "payment_alipay", "payment_stripe"],
  },
  { labelKey: "platform.navInfra", groups: ["registry", "cluster", "observability"] },
  { labelKey: "platform.navSite", groups: ["compliance", "support"] },
] as const satisfies readonly { labelKey: string; groups: readonly Group[] }[];
export const GROUP_LABEL_KEY = {
  security: "platform.tabSecurity",
  captcha: "platform.tabCaptcha",
  sms: "platform.tabSms",
  email: "platform.tabEmail",
  real_name: "platform.tabRealName",
  payment_wechat: "platform.tabWechat",
  payment_alipay: "platform.tabAlipay",
  payment_stripe: "platform.tabStripe",
  registry: "platform.tabRegistry",
  cluster: "platform.tabCluster",
  observability: "platform.tabObservability",
  compliance: "platform.tabCompliance",
  support: "platform.tabSupport",
} as const satisfies Record<Group, string>;

/** 导航项状态点语义(颜色非唯一线索:每点带 tooltip + aria-label,导航下方另出图例)。 */
export type DotStatus = "error" | "warning" | "on" | "configured" | "off";
export const DOT_COLOR = {
  error: adminColors.negative,
  warning: adminColors.alertAccent,
  on: adminColors.positive,
  configured: adminColors.dataAccent,
  off: adminColors.textMuted,
} as const satisfies Record<DotStatus, string>;
export const DOT_TEXT_KEY = {
  error: "platform.dotError",
  warning: "platform.dotWarning",
  on: "platform.dotOn",
  configured: "platform.dotConfigured",
  off: "platform.dotOff",
} as const satisfies Record<DotStatus, string>;

export function groupDotStatus(
  group: Group,
  items: PlatformConfigItem[],
  warnings: ConfigWarning[],
  byKey: Map<string, PlatformConfigItem>,
): DotStatus {
  const ws = warnings.filter((w) => byKey.get(w.key)?.group === group);
  if (ws.some((w) => w.level === "error")) return "error";
  if (ws.length > 0) return "warning";
  const own = items.filter((i) => i.group === group);
  if (own.some((i) => i.kind === "bool" && i.value === "true")) return "on";
  if (own.some((i) => i.source === "override" || (i.kind === "secret" && i.configured))) return "configured";
  return "off";
}

export function NavLabel({ status, text, dirty }: { status: DotStatus; text: string; dirty?: boolean }) {
  const { t } = useTranslation();
  const dotText = t(DOT_TEXT_KEY[status]);
  const dirtyText = t("platform.groupDirty");
  return (
    <Space size={space.sm}>
      <Tooltip title={dotText}>
        <span
          role="img"
          aria-label={dotText}
          style={{ display: "inline-block", width: 8, height: 8, borderRadius: 4, background: DOT_COLOR[status] }}
        />
      </Tooltip>
      {text}
      {dirty && (
        <Tooltip title={dirtyText}>
          <span
            role="img"
            aria-label={dirtyText}
            style={{ color: adminColors.alertAccent, fontWeight: fontWeight.semibold }}
          >
            *
          </span>
        </Tooltip>
      )}
    </Space>
  );
}

/** 状态点图例(导航下方一行):颜色含义写明,不只靠颜色。 */
export function NavDotLegend() {
  const { t } = useTranslation();
  return <div style={{ fontSize: fontSize.caption, color: adminColors.textMuted }}>{t("platform.dotLegend")}</div>;
}
