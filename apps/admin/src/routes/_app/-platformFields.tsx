/** 平台配置字段:标签 / 附加说明 / 来源标 / 单字段控件 / 分组面板。 */

import { ArrowLeftOutlined } from "@ant-design/icons";
import { Collapse, Button, Form, Input, Select, Space, Switch, Tag, Typography } from "antd";
import { useTranslation } from "react-i18next";

import { fontSize, formatDateTime } from "@superdl/ui";

import { type PlatformConfigItem } from "../../api";
import { GROUP_LABEL_KEY, Group } from "./-platformNav";

// i18n-exempt(至 GROUP_INTRO 为止):中国渠道字段名与操作指引不译
export const FIELD_LABELS: Record<string, string> = {
  admin_mfa_enabled: "启用管理端两步验证(TOTP)",
  captcha_enabled: "启用人机验证(阿里云验证码 2.0)",
  captcha_scene_id: "场景 ID",
  captcha_prefix: "身份标(prefix)",
  captcha_access_key_id: "AccessKey ID",
  captcha_access_key_secret: "AccessKey Secret",
  grafana_url: "Grafana 地址(可选,外链)",
  oncall_phone: "值班手机号(critical 告警短信)",
  payment_wechat_enabled: "启用微信支付渠道",
  wechat_mchid: "商户号(mchid)",
  wechat_appid: "应用 AppID",
  wechat_cert_serial_no: "商户 API 证书序列号",
  wechat_private_key: "商户 API 私钥(apiclient_key.pem)",
  wechat_apiv3_key: "APIv3 密钥",
  wechat_public_key_id: "微信支付公钥 ID(PUB_KEY_ID_…)",
  wechat_public_key: "微信支付公钥(pub_key.pem)",
  payment_alipay_enabled: "启用支付宝渠道",
  alipay_app_id: "应用 APPID",
  alipay_private_key: "应用私钥(纯 base64,不含 PEM 头尾)",
  alipay_public_key: "支付宝公钥(纯 base64,不含 PEM 头尾)",
  sms_provider: "短信 Provider",
  sms_access_key_id: "AccessKey ID",
  sms_access_key_secret: "AccessKey Secret",
  sms_sign_name: "短信签名名称",
  sms_template_verify: "验证码模板码",
  sms_template_notice: "通知模板码",
  real_name_enabled: "启用实名认证(阿里云三要素核验)",
  real_name_required_for_recharge: "充值前强制实名认证",
  real_name_access_key_id: "AccessKey ID",
  real_name_access_key_secret: "AccessKey Secret",
  icp_number: "ICP 备案号",
  police_record_number: "公安联网备案号",
  company_name: "公司全称(营业执照)",
  company_address: "公司注册地址",
  company_phone: "对外联系电话",
  business_license_url: "营业执照电子版链接(亮照)",
  support_email: "客服邮箱",
  support_wechat: "企业微信/微信客服号",
  cluster_server_url: "Server 地址",
  cluster_join_token: "Join Token",
  cluster_agent_version: "Agent 版本(装机脚本钉死)",
  node_driver_version: "NVIDIA 驱动主版本",
  node_registries_yaml: "registries.yaml(高级覆盖)",
  node_install_mirror: "装机安装源",
  registry_host: "Harbor 地址",
  registry_project: "平台镜像项目",
  registry_robot_name: "机器人账户",
  registry_robot_secret: "机器人 Secret",
  registry_ca_pem: "CA 证书 PEM(自签时)",
  registry_proxy_projects: "代理缓存项目(每行 上游=项目)",
  image_allowed_registries: "镜像来源白名单(每行一个前缀)",
};

// 指引 prose 入 locale(platform.fieldExtra.*);FIELD_LABELS/PROVIDER_LABELS/RISK_OFF 维持 i18n-exempt
export const FIELD_EXTRA_KEYS = {
  admin_mfa_enabled: "platform.fieldExtra.admin_mfa_enabled",
  captcha_enabled: "platform.fieldExtra.captcha_enabled",
  oncall_phone: "platform.fieldExtra.oncall_phone",
  payment_wechat_enabled: "platform.fieldExtra.payment_wechat_enabled",
  payment_alipay_enabled: "platform.fieldExtra.payment_alipay_enabled",
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

export const PROVIDER_LABELS: Record<string, string> = {
  mock: "开发模式(不发短信,固定码 123456 写日志;仅开发环境)",
  aliyun: "阿里云",
  cn: "国内镜像(rancher-mirror.rancher.cn)",
  official: "官方源",
};

export const GROUP_INTRO_KEYS = {
  registry: "platform.groupIntro.registry",
  security: "platform.groupIntro.security",
  observability: "platform.groupIntro.observability",
  payment_wechat: "platform.groupIntro.payment_wechat",
  payment_alipay: "platform.groupIntro.payment_alipay",
  sms: "platform.groupIntro.sms",
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
      <Switch
        checked={effective}
        disabled={disabled}
        onChange={(checked) => onChange(checked ? "true" : "false")}
      />
    );
  }
  if (item.kind === "choice") {
    return (
      <Select
        style={{ width: 260 }}
        disabled={disabled}
        value={draft ?? item.value ?? undefined}
        options={item.choices.map((c) => ({ value: c, label: PROVIDER_LABELS[c] ?? c }))}
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
  return (
    <Space orientation="vertical" size={16} style={{ width: "100%", maxWidth: 760 }}>
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
              <Typography.Paragraph style={{ marginBottom: 0 }}>
                {t(GROUP_INTRO_KEYS[group])}
              </Typography.Paragraph>
            ),
          },
        ]}
      />
      <Form layout="vertical">
        {items.map((item) => (
          <Form.Item
            key={item.key}
            label={
              <Space size={8}>
                {FIELD_LABELS[item.key] ?? item.key}
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
            <Space size={8} align="start">
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

