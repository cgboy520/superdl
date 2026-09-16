/** 平台配置字段:标签 / 附加说明 / 来源标 / 单字段控件 / 分组面板。 */

import { ArrowLeftOutlined } from "@ant-design/icons";
import { Collapse, Button, Form, Input, Select, Space, Switch, Tag, Typography } from "antd";
import { useTranslation } from "react-i18next";

import { fontSize, formatDateTime, space } from "@superdl/ui";

import { type PlatformConfigItem } from "../../api";
import { GROUP_LABEL_KEY, Group } from "./-platformNav";

/** 字段标签:值是 locale 键(platform.field.*),取用经 useFieldLabel。 */
export const FIELD_LABELS = {
  admin_mfa_enabled: "platform.field.admin_mfa_enabled",
  captcha_enabled: "platform.field.captcha_enabled",
  captcha_scene_id: "platform.field.captcha_scene_id",
  captcha_prefix: "platform.field.captcha_prefix",
  captcha_access_key_id: "platform.field.captcha_access_key_id",
  captcha_access_key_secret: "platform.field.captcha_access_key_secret",
  captcha_provider: "platform.field.captcha_provider",
  captcha_turnstile_site_key: "platform.field.captcha_turnstile_site_key",
  captcha_turnstile_secret_key: "platform.field.captcha_turnstile_secret_key",
  grafana_url: "platform.field.grafana_url",
  oncall_phone: "platform.field.oncall_phone",
  payment_wechat_enabled: "platform.field.payment_wechat_enabled",
  wechat_mchid: "platform.field.wechat_mchid",
  wechat_appid: "platform.field.wechat_appid",
  wechat_cert_serial_no: "platform.field.wechat_cert_serial_no",
  wechat_private_key: "platform.field.wechat_private_key",
  wechat_apiv3_key: "platform.field.wechat_apiv3_key",
  wechat_public_key_id: "platform.field.wechat_public_key_id",
  wechat_public_key: "platform.field.wechat_public_key",
  payment_alipay_enabled: "platform.field.payment_alipay_enabled",
  payment_stripe_enabled: "platform.field.payment_stripe_enabled",
  stripe_secret_key: "platform.field.stripe_secret_key",
  stripe_webhook_secret: "platform.field.stripe_webhook_secret",
  alipay_app_id: "platform.field.alipay_app_id",
  alipay_private_key: "platform.field.alipay_private_key",
  alipay_public_key: "platform.field.alipay_public_key",
  sms_provider: "platform.field.sms_provider",
  sms_access_key_id: "platform.field.sms_access_key_id",
  sms_access_key_secret: "platform.field.sms_access_key_secret",
  sms_sign_name: "platform.field.sms_sign_name",
  sms_template_verify: "platform.field.sms_template_verify",
  sms_template_notice: "platform.field.sms_template_notice",
  sms_twilio_account_sid: "platform.field.sms_twilio_account_sid",
  sms_twilio_auth_token: "platform.field.sms_twilio_auth_token",
  sms_twilio_from: "platform.field.sms_twilio_from",
  email_provider: "platform.field.email_provider",
  smtp_host: "platform.field.smtp_host",
  smtp_port: "platform.field.smtp_port",
  smtp_security: "platform.field.smtp_security",
  smtp_username: "platform.field.smtp_username",
  smtp_password: "platform.field.smtp_password",
  email_from: "platform.field.email_from",
  email_reply_to: "platform.field.email_reply_to",
  real_name_enabled: "platform.field.real_name_enabled",
  real_name_required_for_recharge: "platform.field.real_name_required_for_recharge",
  real_name_access_key_id: "platform.field.real_name_access_key_id",
  real_name_access_key_secret: "platform.field.real_name_access_key_secret",
  kyc_provider: "platform.field.kyc_provider",
  icp_number: "platform.field.icp_number",
  police_record_number: "platform.field.police_record_number",
  company_name: "platform.field.company_name",
  company_address: "platform.field.company_address",
  company_phone: "platform.field.company_phone",
  business_license_url: "platform.field.business_license_url",
  support_email: "platform.field.support_email",
  support_wechat: "platform.field.support_wechat",
  cluster_server_url: "platform.field.cluster_server_url",
  cluster_join_token: "platform.field.cluster_join_token",
  cluster_agent_version: "platform.field.cluster_agent_version",
  node_driver_version: "platform.field.node_driver_version",
  node_registries_yaml: "platform.field.node_registries_yaml",
  node_install_mirror: "platform.field.node_install_mirror",
  registry_host: "platform.field.registry_host",
  registry_project: "platform.field.registry_project",
  registry_robot_name: "platform.field.registry_robot_name",
  registry_robot_secret: "platform.field.registry_robot_secret",
  registry_ca_pem: "platform.field.registry_ca_pem",
  registry_proxy_projects: "platform.field.registry_proxy_projects",
  image_allowed_registries: "platform.field.image_allowed_registries",
} as const satisfies Record<string, string>;
export type FieldLabelKey = (typeof FIELD_LABELS)[keyof typeof FIELD_LABELS];

/** 字段标签查表:未登记的键回落键名本身。 */
export function useFieldLabel(): (key: string) => string {
  const { t } = useTranslation();
  return (key: string) => {
    const labelKey = (FIELD_LABELS as Record<string, FieldLabelKey>)[key];
    return labelKey ? t(labelKey) : key;
  };
}

export const FIELD_EXTRA_KEYS = {
  admin_mfa_enabled: "platform.fieldExtra.admin_mfa_enabled",
  captcha_enabled: "platform.fieldExtra.captcha_enabled",
  oncall_phone: "platform.fieldExtra.oncall_phone",
  payment_wechat_enabled: "platform.fieldExtra.payment_wechat_enabled",
  payment_alipay_enabled: "platform.fieldExtra.payment_alipay_enabled",
  payment_stripe_enabled: "platform.fieldExtra.payment_stripe_enabled",
  wechat_public_key_id: "platform.fieldExtra.wechat_public_key_id",
  real_name_enabled: "platform.fieldExtra.real_name_enabled",
  real_name_required_for_recharge: "platform.fieldExtra.real_name_required_for_recharge",
  sms_template_verify: "platform.fieldExtra.sms_template_verify",
  sms_template_notice: "platform.fieldExtra.sms_template_notice",
  icp_number: "platform.fieldExtra.icp_number",
  police_record_number: "platform.fieldExtra.police_record_number",
  company_name: "platform.fieldExtra.company_name",
  company_address: "platform.fieldExtra.company_address",
  company_phone: "platform.fieldExtra.company_phone",
  business_license_url: "platform.fieldExtra.business_license_url",
  support_email: "platform.fieldExtra.support_email",
  support_wechat: "platform.fieldExtra.support_wechat",
} as const;
export type FieldExtraKey = (typeof FIELD_EXTRA_KEYS)[keyof typeof FIELD_EXTRA_KEYS];

/** 选项标签:值是 locale 键(platform.provider.*)。 */
export const PROVIDER_LABELS = {
  mock: "platform.provider.mock",
  aliyun: "platform.provider.aliyun",
  twilio: "platform.provider.twilio",
  aliyun_mobile3: "platform.provider.aliyun_mobile3",
  smtp: "platform.provider.smtp",
  starttls: "platform.provider.starttls",
  tls: "platform.provider.tls",
  none: "platform.provider.none",
  cn: "platform.provider.cn",
  official: "platform.provider.official",
} as const satisfies Record<string, string>;
export type ProviderLabelKey = (typeof PROVIDER_LABELS)[keyof typeof PROVIDER_LABELS];

export const GROUP_INTRO_KEYS = {
  registry: "platform.groupIntro.registry",
  security: "platform.groupIntro.security",
  observability: "platform.groupIntro.observability",
  payment_wechat: "platform.groupIntro.payment_wechat",
  payment_alipay: "platform.groupIntro.payment_alipay",
  payment_stripe: "platform.groupIntro.payment_stripe",
  sms: "platform.groupIntro.sms",
  email: "platform.groupIntro.email",
  real_name: "platform.groupIntro.real_name",
  captcha: "platform.groupIntro.captcha",
  support: "platform.groupIntro.support",
  compliance: "platform.groupIntro.compliance",
  cluster: "platform.groupIntro.cluster",
} as const satisfies Record<Group, string>;

/** 字段级指引文案:locale 查表(fieldExtra.*) + 服务端 hint 拼一行。 */
export function FieldExtraText({ itemKey, hint }: { itemKey: string; hint?: string | null }) {
  const { t } = useTranslation();
  const key = (FIELD_EXTRA_KEYS as Record<string, FieldExtraKey>)[itemKey];
  return <>{[key ? t(key) : null, hint].filter(Boolean).join(";")}</>;
}

export const SOURCE_TAG = {
  override: { color: "cyan", textKey: "platform.sourceDb" },
  env: { color: undefined, textKey: "platform.sourceEnv" },
  unset: { color: "warning", textKey: "platform.sourceUnset" },
} as const satisfies Record<PlatformConfigItem["source"], { color?: string; textKey: string }>;

export function FieldControl({
  item,
  draft,
  disabled,
  onChange,
}: {
  item: PlatformConfigItem;
  draft: string | undefined;
  disabled: boolean;
  onChange: (v: string) => void;
}) {
  const { t } = useTranslation();
  if (item.kind === "bool") {
    const effective = (draft ?? item.value ?? "false") === "true";
    return (
      <Switch checked={effective} disabled={disabled} onChange={(checked) => onChange(checked ? "true" : "false")} />
    );
  }
  if (item.kind === "choice") {
    return (
      <Select
        style={{ width: 260 }}
        disabled={disabled}
        value={draft ?? item.value ?? undefined}
        options={item.choices.map((c) => {
          const labelKey = (PROVIDER_LABELS as Record<string, ProviderLabelKey>)[c];
          return { value: c, label: labelKey ? t(labelKey) : c };
        })}
        onChange={(v) => onChange(v)}
      />
    );
  }
  if (item.kind === "secret") {
    return (
      <Input.Password
        style={{ maxWidth: 480 }}
        disabled={disabled}
        value={draft ?? ""}
        placeholder={
          item.configured
            ? t("platform.secretConfigured", { preview: item.preview ? `(${item.preview})` : "" })
            : t("platform.sourceUnset")
        }
        autoComplete="new-password"
        onChange={(e) => onChange(e.target.value)}
      />
    );
  }
  if (item.kind === "text") {
    return (
      <Input.TextArea
        style={{ maxWidth: 640, fontFamily: "monospace", fontSize: fontSize.caption }}
        rows={4}
        disabled={disabled}
        value={draft ?? item.value ?? ""}
        onChange={(e) => onChange(e.target.value)}
      />
    );
  }
  return (
    <Input
      style={{ maxWidth: 480 }}
      disabled={disabled}
      value={draft ?? item.value ?? ""}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

export function GroupPanel({
  group,
  items,
  draft,
  setDraft,
  disabled,
  extraContent,
  origin,
}: {
  group: Group;
  items: PlatformConfigItem[];
  draft: Record<string, string>;
  setDraft: React.Dispatch<React.SetStateAction<Record<string, string>>>;
  disabled: boolean;
  extraContent?: React.ReactNode;
  /** 安全组「前往」跳入的来源分组(渲染回链) */
  origin?: { group: Group; onBack: () => void };
}) {
  const { t } = useTranslation();
  const fieldLabel = useFieldLabel();
  return (
    <Space orientation="vertical" size={space.lg} style={{ width: "100%", maxWidth: 760 }}>
      {origin && (
        <Button
          type="link"
          size="small"
          icon={<ArrowLeftOutlined />}
          style={{ paddingInline: 0 }}
          onClick={origin.onBack}
        >
          {t("platform.backToGroup", { group: t(GROUP_LABEL_KEY[origin.group]) })}
        </Button>
      )}
      <Collapse
        size="small"
        items={[
          {
            key: "guide",
            label: t("platform.configGuide"),
            children: (
              <Typography.Paragraph style={{ marginBottom: 0 }}>{t(GROUP_INTRO_KEYS[group])}</Typography.Paragraph>
            ),
          },
        ]}
      />
      <Form layout="vertical">
        {items.map((item) => (
          <Form.Item
            key={item.key}
            label={
              <Space size={space.sm}>
                {fieldLabel(item.key)}
                <Tag color={SOURCE_TAG[item.source].color}>{t(SOURCE_TAG[item.source].textKey)}</Tag>
                {item.updated_at && (
                  <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
                    {t("platform.updatedAt", { time: formatDateTime(item.updated_at) })}
                  </Typography.Text>
                )}
              </Space>
            }
            extra={
              <span style={{ fontSize: fontSize.caption }}>
                <FieldExtraText itemKey={item.key} hint={item.hint} />
              </span>
            }
          >
            <Space size={space.sm} align="start">
              <FieldControl
                item={item}
                draft={draft[item.key]}
                disabled={disabled}
                onChange={(v) => setDraft((d) => ({ ...d, [item.key]: v }))}
              />
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
          </Form.Item>
        ))}
      </Form>
      {extraContent}
    </Space>
  );
}
