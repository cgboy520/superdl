/** 安全策略页:开关行(依赖凭据 / 前置开关 / 关闭风险)与面板。 */

import { Collapse, Button, Space, Switch, Tag, Typography } from "antd";
import { useTranslation } from "react-i18next";

import { adminColors, fontSize, formatDateTime } from "@superdl/ui";

import { type PlatformConfigItem } from "../../api";
import { FIELD_LABELS, FieldExtraText, GROUP_INTRO_KEYS, SOURCE_TAG } from "./-platformFields";
import { ConfigWarning, GROUP_LABEL_KEY, Group } from "./-platformNav";

/** 安全开关的依赖凭据(在别的分组录入)与「须先开启」的前置开关。 */
export const SWITCH_DEPS: Record<string, { keys: string[]; group: Group }> = {
  captcha_enabled: {
    keys: ["captcha_scene_id", "captcha_access_key_id", "captcha_access_key_secret"],
    group: "captcha",
  },
  real_name_enabled: {
    keys: ["real_name_access_key_id", "real_name_access_key_secret"],
    group: "real_name",
  },
};
export const SWITCH_REQUIRES: Record<string, string> = {
  real_name_required_for_recharge: "real_name_enabled",
};
// i18n-exempt:关闭安全开关时弹窗复述的风险
export const RISK_OFF: Record<string, string> = {
  captcha_enabled: "关闭后 /auth/sms-code 不做人机校验,仅剩 IP/手机号限流",
  admin_mfa_enabled: "关闭后管理端仅凭口令即可登录,已绑定的 TOTP 也不再校验",
  real_name_enabled: "关闭后用户无法完成实名;若「充值前强制实名」开着,保存会被拒绝",
  real_name_required_for_recharge: "关闭后未实名用户可以充值与开通实例",
};

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
  const effective = (draft[item.key] ?? item.value ?? "false") === "true";
  const deps = SWITCH_DEPS[item.key];
  const missing = deps ? deps.keys.filter((k) => !byKey.get(k)?.configured) : [];
  const requires = SWITCH_REQUIRES[item.key];
  const requiresOn = requires ? (draft[requires] ?? byKey.get(requires)?.value) === "true" : true;
  const risks = warnings.filter((w) => w.key === item.key);
  return (
    <div style={{ border: `1px solid ${adminColors.divider}`, borderRadius: 6, padding: "12px 16px" }}>
      <Space align="start" size={16} style={{ width: "100%" }}>
        <Switch
          checked={effective}
          disabled={disabled}
          onChange={(checked) => setDraft((d) => ({ ...d, [item.key]: checked ? "true" : "false" }))}
        />
        <Space orientation="vertical" size={4}>
          <Space size={8} wrap>
            <Typography.Text strong>{FIELD_LABELS[item.key] ?? item.key}</Typography.Text>
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
            <Space size={4}>
              <Typography.Text
                style={{
                  fontSize: fontSize.caption,
                  color: missing.length > 0 ? adminColors.alertAccent : adminColors.positive,
                }}
              >
                {missing.length > 0
                  ? t("platform.depsMissing", {
                      keys: missing.map((k) => FIELD_LABELS[k] ?? k).join(", "),
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
              {t("platform.needsSwitch", { label: FIELD_LABELS[requires] ?? requires })}
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
  draft,
  setDraft,
  disabled,
  byKey,
  warnings,
  onGoTo,
}: {
  items: PlatformConfigItem[];
  draft: Record<string, string>;
  setDraft: React.Dispatch<React.SetStateAction<Record<string, string>>>;
  disabled: boolean;
  byKey: Map<string, PlatformConfigItem>;
  warnings: ConfigWarning[];
  onGoTo: (group: Group) => void;
}) {
  const { t } = useTranslation();
  return (
    <Space orientation="vertical" size={12} style={{ width: "100%", maxWidth: 760 }}>
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
