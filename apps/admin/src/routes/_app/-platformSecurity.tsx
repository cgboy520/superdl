/** 安全策略页:开关行(依赖凭据 / 前置开关 / 关闭风险)与面板。 */

import { Collapse, Button, Space, Switch, Tag, Typography } from "antd";
import { useTranslation } from "react-i18next";

import { adminColors, fontSize, formatDateTime, space } from "@superdl/ui";

import { type DeploymentIdentity, type PlatformConfigItem } from "../../api";
import { FieldExtraText, GROUP_INTRO_KEYS, SOURCE_TAG, useFieldLabel } from "./-platformFields";
import { ConfigWarning, GROUP_LABEL_KEY, Group } from "./-platformNav";

/** Credentials a security switch depends on (entered in another group) and switches it requires. */
export const SWITCH_DEPS: Record<string, { keys: string[]; group: Group }> = {
  captcha_enabled: {
    keys: ["captcha_turnstile_site_key", "captcha_turnstile_secret_key"],
    group: "captcha",
  },
  real_name_enabled: {
    keys: ["real_name_access_key_id", "real_name_access_key_secret"],
    group: "real_name",
  },
};
const CAPTCHA_DEPS_BY_PROVIDER: Record<string, string[]> = {
  aliyun: ["captcha_scene_id", "captcha_access_key_id", "captcha_access_key_secret"],
  turnstile: ["captcha_turnstile_site_key", "captcha_turnstile_secret_key"],
};
/** Dependencies follow the effective captcha_provider (draft first, then the saved value). */
export function switchDeps(
  key: string,
  draft: Record<string, string>,
  byKey: Map<string, PlatformConfigItem>,
): { keys: string[]; group: Group } | undefined {
  const base = SWITCH_DEPS[key];
  if (!base || key !== "captcha_enabled") return base;
  const provider = draft.captcha_provider ?? byKey.get("captcha_provider")?.value ?? "turnstile";
  return { keys: CAPTCHA_DEPS_BY_PROVIDER[provider] ?? base.keys, group: base.group };
}
export const SWITCH_REQUIRES: Record<string, string> = {
  real_name_required_for_recharge: "real_name_enabled",
};
/** 关闭安全开关时弹窗复述的风险:值是 locale 键(platform.riskOff.*)。 */
export const RISK_OFF = {
  captcha_enabled: "platform.riskOff.captcha_enabled",
  admin_mfa_enabled: "platform.riskOff.admin_mfa_enabled",
  real_name_enabled: "platform.riskOff.real_name_enabled",
  real_name_required_for_recharge: "platform.riskOff.real_name_required_for_recharge",
} as const satisfies Record<string, string>;
export type RiskOffKey = (typeof RISK_OFF)[keyof typeof RISK_OFF];

const PROFILE_LABEL_KEY = {
  none: "platform.deployment.profileNone",
  cn: "platform.deployment.profileCn",
} as const satisfies Record<string, string>;
type ProfileLabelKey = (typeof PROFILE_LABEL_KEY)[keyof typeof PROFILE_LABEL_KEY];

/** Deployment identity (env-only, locked at first boot): shown read-only above the switches. */
export function DeploymentCard({ deployment }: { deployment: DeploymentIdentity }) {
  const { t } = useTranslation();
  const profileKey = (PROFILE_LABEL_KEY as Record<string, ProfileLabelKey | undefined>)[deployment.compliance_profile];
  const rows: [string, string][] = [
    [t("platform.deployment.complianceProfile"), profileKey ? t(profileKey) : deployment.compliance_profile],
    [t("platform.deployment.currency"), deployment.currency],
    [t("platform.deployment.billingTimezone"), deployment.billing_timezone],
  ];
  return (
    <div style={{ border: `1px solid ${adminColors.divider}`, borderRadius: 6, padding: "12px 16px" }}>
      <Space orientation="vertical" size={space.xs}>
        <Space size={space.sm} wrap>
          <Typography.Text strong>{t("platform.deployment.title")}</Typography.Text>
          <Tag>{t("platform.deployment.envTag")}</Tag>
        </Space>
        <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
          {t("platform.deployment.hint")}
        </Typography.Text>
        {rows.map(([label, value]) => (
          <div key={label}>
            <Typography.Text type="secondary">{label}: </Typography.Text>
            <Typography.Text strong>{value}</Typography.Text>
          </div>
        ))}
      </Space>
    </div>
  );
}

export function SwitchRow({
  item,
  draft,
  setDraft,
  disabled,
  byKey,
  warnings,
  onGoTo,
}: {
  item: PlatformConfigItem;
  draft: Record<string, string>;
  setDraft: React.Dispatch<React.SetStateAction<Record<string, string>>>;
  disabled: boolean;
  byKey: Map<string, PlatformConfigItem>;
  warnings: ConfigWarning[];
  onGoTo: (group: Group) => void;
}) {
  const { t } = useTranslation();
  const fieldLabel = useFieldLabel();
  const effective = (draft[item.key] ?? item.value ?? "false") === "true";
  const deps = switchDeps(item.key, draft, byKey);
  const missing = deps ? deps.keys.filter((k) => !byKey.get(k)?.configured) : [];
  const requires = SWITCH_REQUIRES[item.key];
  const requiresOn = requires ? (draft[requires] ?? byKey.get(requires)?.value) === "true" : true;
  const risks = warnings.filter((w) => w.key === item.key);
  return (
    <div style={{ border: `1px solid ${adminColors.divider}`, borderRadius: 6, padding: "12px 16px" }}>
      <Space align="start" size={space.lg} style={{ width: "100%" }}>
        <Switch
          checked={effective}
          disabled={disabled}
          onChange={(checked) => setDraft((d) => ({ ...d, [item.key]: checked ? "true" : "false" }))}
        />
        <Space orientation="vertical" size={space.xs}>
          <Space size={space.sm} wrap>
            <Typography.Text strong>{fieldLabel(item.key)}</Typography.Text>
            <Tag color={SOURCE_TAG[item.source].color}>{t(SOURCE_TAG[item.source].textKey)}</Tag>
            {item.updated_at && (
              <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
                {t("platform.updatedAt", { time: formatDateTime(item.updated_at) })}
              </Typography.Text>
            )}
            {item.source === "override" && draft[item.key] === undefined && (
              <Button
                size="small"
                type="link"
                disabled={disabled}
                onClick={() => setDraft((d) => ({ ...d, [item.key]: "" }))}
              >
                {t("platform.clearOverride")}
              </Button>
            )}
            {draft[item.key] === "" && item.source === "override" && (
              <Tag color="orange">{t("platform.clearOverrideTag")}</Tag>
            )}
          </Space>
          <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
            <FieldExtraText itemKey={item.key} hint={item.hint} />
          </Typography.Text>
          {deps && (
            <Space size={space.xs}>
              <Typography.Text
                style={{
                  fontSize: fontSize.caption,
                  color: missing.length > 0 ? adminColors.alertAccent : adminColors.positive,
                }}
              >
                {missing.length > 0
                  ? t("platform.depsMissing", {
                      keys: missing.map((k) => fieldLabel(k)).join(", "),
                    })
                  : t("platform.depsOk")}
              </Typography.Text>
              {missing.length > 0 && (
                <Button size="small" type="link" onClick={() => onGoTo(deps.group)}>
                  {t("platform.goTo")}「{t(GROUP_LABEL_KEY[deps.group])}」
                </Button>
              )}
            </Space>
          )}
          {requires && !requiresOn && (
            <Typography.Text style={{ fontSize: fontSize.caption, color: adminColors.alertAccent }}>
              {t("platform.needsSwitch", { label: fieldLabel(requires) })}
            </Typography.Text>
          )}
          {risks.map((w) => (
            <Typography.Text
              key={w.message}
              style={{
                fontSize: fontSize.caption,
                color: w.level === "error" ? adminColors.negative : adminColors.alertAccent,
              }}
            >
              {w.message}
            </Typography.Text>
          ))}
        </Space>
      </Space>
    </div>
  );
}

export function SecurityPanel({
  items,
  deployment,
  draft,
  setDraft,
  disabled,
  byKey,
  warnings,
  onGoTo,
}: {
  items: PlatformConfigItem[];
  deployment?: DeploymentIdentity;
  draft: Record<string, string>;
  setDraft: React.Dispatch<React.SetStateAction<Record<string, string>>>;
  disabled: boolean;
  byKey: Map<string, PlatformConfigItem>;
  warnings: ConfigWarning[];
  onGoTo: (group: Group) => void;
}) {
  const { t } = useTranslation();
  return (
    <Space orientation="vertical" size={space.md} style={{ width: "100%", maxWidth: 760 }}>
      <Collapse
        size="small"
        items={[
          {
            key: "guide",
            label: t("platform.configGuide"),
            children: (
              <Typography.Paragraph style={{ marginBottom: 0 }}>{t(GROUP_INTRO_KEYS.security)}</Typography.Paragraph>
            ),
          },
        ]}
      />
      {deployment && <DeploymentCard deployment={deployment} />}
      {items.map((item) => (
        <SwitchRow
          key={item.key}
          item={item}
          draft={draft}
          setDraft={setDraft}
          disabled={disabled}
          byKey={byKey}
          warnings={warnings}
          onGoTo={onGoTo}
        />
      ))}
    </Space>
  );
}
