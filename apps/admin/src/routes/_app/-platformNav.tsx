/** 平台配置分组导航事实源:业务分组 → 配置组、组标题键、分组状态点色。 */

import { Space } from "antd";

import { adminColors } from "@superdl/ui";

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
    groups: ["captcha", "sms", "real_name", "payment_wechat", "payment_alipay"],
  },
  { labelKey: "platform.navInfra", groups: ["registry", "cluster", "observability"] },
  { labelKey: "platform.navSite", groups: ["compliance", "support"] },
] as const satisfies readonly { labelKey: string; groups: readonly Group[] }[];
export const GROUP_LABEL_KEY = {
  security: "platform.tabSecurity",
  captcha: "platform.tabCaptcha",
  sms: "platform.tabSms",
  real_name: "platform.tabRealName",
  payment_wechat: "platform.tabWechat",
  payment_alipay: "platform.tabAlipay",
  registry: "platform.tabRegistry",
  cluster: "platform.tabCluster",
  observability: "platform.tabObservability",
  compliance: "platform.tabCompliance",
  support: "platform.tabSupport",
} as const satisfies Record<Group, string>;
/** 导航项状态点:红 = 有 error 告警,琥珀 = warning,绿 = 开关已开,青 = 有覆盖/已配凭据,灰 = 未配置。 */
export function groupDotColor(
  group: Group,
  items: PlatformConfigItem[],
  warnings: ConfigWarning[],
  byKey: Map<string, PlatformConfigItem>,
): string {
  const ws = warnings.filter((w) => byKey.get(w.key)?.group === group);
  if (ws.some((w) => w.level === "error")) return adminColors.negative;
  if (ws.length > 0) return adminColors.alertAccent;
  const own = items.filter((i) => i.group === group);
  if (own.some((i) => i.kind === "bool" && i.value === "true")) return adminColors.positive;
  if (own.some((i) => i.source === "override" || (i.kind === "secret" && i.configured))) {
    return adminColors.dataAccent;
  }
  return adminColors.textMuted;
}

export function NavLabel({ color, text }: { color: string; text: string }) {
  return (
    <Space size={8}>
      <span style={{ display: "inline-block", width: 8, height: 8, borderRadius: 4, background: color }} />
      {text}
    </Space>
  );
}
