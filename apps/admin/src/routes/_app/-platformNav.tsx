/** Source of truth of the platform configuration navigation: business sections → configuration groups, group title keys, section status dots. */

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

/** Left navigation: business sections → configuration groups; the order is the display order, a new configuration group must join a section. */
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

/** Navigation item status dot semantics (colour is not the only cue: each dot carries a tooltip + aria-label, a legend sits below the navigation). */
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

/** Status dot legend (one line below the navigation): the colour meanings are spelled out, not colour alone. */
export function NavDotLegend() {
  const { t } = useTranslation();
  return <div style={{ fontSize: fontSize.caption, color: adminColors.textMuted }}>{t("platform.dotLegend")}</div>;
}
