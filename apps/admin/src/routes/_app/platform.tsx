/**
 * 平台配置:左侧分组导航(安全 / 第三方渠道 / 基础设施 / 站点信息)+ 顶部服务端配置风险告警 + 右侧分组表单。
 * 安全策略页是开关行(开关 / 依赖凭据状态 / 风险);其余分组沿用字段表单。
 * 仅超级管理员可读写;env 为默认值层,DB 覆盖即时生效(免重启发版)。
 * secret 类永不回显明文:只显示"已配置 + 尾 4 位",输入留空 = 保持不变。
 */

import { ArrowLeftOutlined } from "@ant-design/icons";
import { adminColors, fontSize, formatDateTime, useFormDraft } from "@superdl/ui";
import { PageContainer } from "@superdl/ui/components";
import { useQueryClient } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import {
  Alert,
  Collapse,
  App,
  Button,
  Card,
  Form,
  Grid,
  Input,
  Menu,
  Modal,
  Select,
  Space,
  Switch,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import {
  isApiError,
  type PlatformConfigItem,
  usePlatformConfig,
  useTestRegistry,
  useTestSms,
  useUpdatePlatformConfig,
} from "../../api";
import { useApiErrorText } from "@superdl/ui";
import { useAdminRole } from "../../stores/auth";

export const Route = createFileRoute("/_app/platform")({
  component: PlatformConfigPage,
});

// i18n-exempt(至 GROUP_INTRO 为止):中国渠道(微信/支付宝/阿里云/工信部)字段名与操作指引,决策不译
const FIELD_LABELS: Record<string, string> = {
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

// 指引性 prose 入 locale(platform.fieldExtra.*,双语);字段名表(FIELD_LABELS/
// PROVIDER_LABELS/RISK_OFF)维持 i18n-exempt 豁免(中国渠道运营域术语,不译)。
// as const 保留字面量键类型:admin 的 t() 是严格键类型,字符串键表过不了 tsc
const FIELD_EXTRA_KEYS = {
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
type FieldExtraKey = (typeof FIELD_EXTRA_KEYS)[keyof typeof FIELD_EXTRA_KEYS];
const FIELD_EXTRA_BY_KEY: Record<string, FieldExtraKey> = FIELD_EXTRA_KEYS;

const PROVIDER_LABELS: Record<string, string> = {
  mock: "开发模式(不发短信,固定码 123456 写日志;仅开发环境)",
  aliyun: "阿里云",
  cn: "国内镜像(rancher-mirror.rancher.cn)",
  official: "官方源",
};

const GROUP_INTRO_KEYS = {
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
function FieldExtraText({ itemKey, hint }: { itemKey: string; hint?: string | null }) {
  const { t } = useTranslation();
  const key = FIELD_EXTRA_BY_KEY[itemKey];
  return <>{[key ? t(key) : null, hint].filter(Boolean).join(";")}</>;
}

const SOURCE_TAG = {
  override: { color: "cyan", textKey: "platform.sourceDb" },
  env: { color: undefined, textKey: "platform.sourceEnv" },
  unset: { color: "warning", textKey: "platform.sourceUnset" },
} as const satisfies Record<PlatformConfigItem["source"], { color?: string; textKey: string }>;

function FieldControl({
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

function GroupPanel({
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
  /** 从安全组开关「前往」跳入时带来源分组,渲染返回回链 */
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

function SmsTestCard({ disabled }: { disabled: boolean }) {
  const { t } = useTranslation();
  const errText = useApiErrorText();
  const { message } = App.useApp();
  const [phone, setPhone] = useState("");
  const testSms = useTestSms({
    mutation: {
      onSuccess: (d) => {
        message.success(t("platform.testSmsSent", { provider: PROVIDER_LABELS[d.provider] ?? d.provider }));
      },
      onError: (e) => message.error(errText(e, t("platform.sendFailed"))),
    },
  });
  return (
    <Card size="small" title={t("platform.testSmsTitle")}>
      <Space.Compact style={{ width: 360 }}>
        <Input
          placeholder={t("platform.testSmsPhone")}
          value={phone}
          maxLength={11}
          disabled={disabled}
          onChange={(e) => setPhone(e.target.value)}
        />
        <Button
          type="primary"
          disabled={disabled || !/^1\d{10}$/.test(phone)}
          loading={testSms.isPending}
          onClick={() => testSms.mutate({ data: { phone } })}
        >
          {t("platform.testSmsSend")}
        </Button>
      </Space.Compact>
      <div style={{ color: adminColors.textSecondary, fontSize: fontSize.caption, marginTop: 8 }}>
        {t("platform.testSmsNote")}
      </div>
    </Card>
  );
}

function RegistryTestCard({ disabled }: { disabled: boolean }) {
  const { t } = useTranslation();
  const errText = useApiErrorText();
  const { message } = App.useApp();
  const test = useTestRegistry({
    mutation: { onError: (e) => message.error(errText(e, t("platform.sendFailed"))) },
  });
  const r = test.data;
  return (
    <Card size="small" title={t("platform.testRegistryTitle")}>
      <Space orientation="vertical" size={8}>
        <Button type="primary" disabled={disabled} loading={test.isPending} onClick={() => test.mutate()}>
          {t("platform.testRegistryRun")}
        </Button>
        {r && (
          <Typography.Text style={{ color: r.ok ? adminColors.positive : adminColors.negative }}>
            {r.ok
              ? t("platform.registryOk", {
                  version: r.harbor_version ? `(Harbor ${r.harbor_version})` : "",
                  repos: r.repositories ?? "?",
                })
              : t("platform.registryFailed", { step: r.step, detail: r.detail })}
          </Typography.Text>
        )}
        <div style={{ color: adminColors.textSecondary, fontSize: fontSize.caption }}>{t("platform.testRegistryNote")}</div>
      </Space>
    </Card>
  );
}

type Group = PlatformConfigItem["group"];
type ConfigWarning = { key: string; level: "error" | "warning"; message: string };

/** 左侧分组导航:业务分组 → 配置组;顺序即展示顺序(新增配置组必须归入某个分组,否则 TS 报缺键)。 */
const NAV = [
  { labelKey: "platform.navSecurity", groups: ["security"] },
  {
    labelKey: "platform.navChannels",
    groups: ["captcha", "sms", "real_name", "payment_wechat", "payment_alipay"],
  },
  { labelKey: "platform.navInfra", groups: ["registry", "cluster", "observability"] },
  { labelKey: "platform.navSite", groups: ["compliance", "support"] },
] as const satisfies readonly { labelKey: string; groups: readonly Group[] }[];
const GROUP_LABEL_KEY = {
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
/** 安全开关的依赖凭据(在别的分组录入)与「须先开启」的前置开关。 */
const SWITCH_DEPS: Record<string, { keys: string[]; group: Group }> = {
  captcha_enabled: {
    keys: ["captcha_scene_id", "captcha_access_key_id", "captcha_access_key_secret"],
    group: "captcha",
  },
  real_name_enabled: {
    keys: ["real_name_access_key_id", "real_name_access_key_secret"],
    group: "real_name",
  },
};
const SWITCH_REQUIRES: Record<string, string> = {
  real_name_required_for_recharge: "real_name_enabled",
};
// i18n-exempt:关闭安全开关时弹窗复述的风险(与 FIELD_EXTRA 同约定,决策不译)
const RISK_OFF: Record<string, string> = {
  captcha_enabled: "关闭后 /auth/sms-code 不做人机校验,仅剩 IP/手机号限流",
  admin_mfa_enabled: "关闭后管理端仅凭口令即可登录,已绑定的 TOTP 也不再校验",
  real_name_enabled: "关闭后用户无法完成实名;若「充值前强制实名」开着,保存会被拒绝",
  real_name_required_for_recharge: "关闭后未实名用户可以充值与开通实例",
};

/** 导航项状态点:红 = 有 error 告警,琥珀 = warning,绿 = 开关已开,青 = 有覆盖/已配凭据,灰 = 未配置。 */
function groupDotColor(
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

function NavLabel({ color, text }: { color: string; text: string }) {
  return (
    <Space size={8}>
      <span
        style={{ display: "inline-block", width: 8, height: 8, borderRadius: 4, background: color }}
      />
      {text}
    </Space>
  );
}

function SwitchRow({
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

function SecurityPanel({
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
              <Typography.Paragraph style={{ marginBottom: 0 }}>
                {t(GROUP_INTRO_KEYS.security)}
              </Typography.Paragraph>
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

function PlatformConfigPage() {
  const { t } = useTranslation();
  const errText = useApiErrorText();
  const { message } = App.useApp();
  const screens = Grid.useBreakpoint();
  const role = useAdminRole();
  const isAdmin = role === "admin";
  const qc = useQueryClient();
  const { data, queryKey, isLoading, isError, error } = usePlatformConfig();
  const items = data?.items ?? [];
  const warnings: ConfigWarning[] = data?.warnings ?? [];
  const byKey = new Map(items.map((i) => [i.key, i]));
  // 非 secret 字段变更草稿(sessionStorage):页面卸载/刷新后回来能恢复;
  // secret 凭据禁入(会话级存储也是泄露面,见 formDraft 注释约定)
  const configDraft = useFormDraft<Record<string, string>>("platform-config");
  const [draftState, setDraftState] = useState<Record<string, string>>(() => {
    const d = configDraft.load();
    // load() 的 Partial 只是宽限标记:本处草稿值一律为 string,收窄回 Record
    return d
      ? Object.fromEntries(
          Object.entries(d).filter((e): e is [string, string] => typeof e[1] === "string"),
        )
      : {};
  });
  const [reasonOpen, setReasonOpen] = useState(false);
  const [active, setActive] = useState<Group>("security");
  // 安全组开关「前往」跳入的来源分组:目标分组页显示「返回安全组」回链
  const [originGroup, setOriginGroup] = useState<Group | null>(null);
  const [reasonForm] = Form.useForm<{ reason: string }>();
  // 配置项到达后清洗一次恢复出的草稿:剔除 secret 字段与已下线的键(渲染期派生态,不进 effect)
  const [draftSanitized, setDraftSanitized] = useState(false);
  if (!draftSanitized && items.length > 0) {
    setDraftSanitized(true);
    setDraftState((d) =>
      Object.fromEntries(
        Object.entries(d).filter(([k]) => {
          const item = byKey.get(k);
          return item != null && item.kind !== "secret";
        }),
      ),
    );
  }
  // 包装 setState:每次变更同步写草稿(只落非 secret 字段;空草稿直接移除存储键)
  const setDraft: React.Dispatch<React.SetStateAction<Record<string, string>>> = (updater) => {
    setDraftState((prev) => {
      const next = typeof updater === "function" ? updater(prev) : updater;
      if (items.length > 0) {
        const persistable = Object.fromEntries(
          Object.entries(next).filter(([k]) => byKey.get(k)?.kind !== "secret"),
        );
        if (Object.keys(persistable).length === 0) configDraft.clear();
        else configDraft.save(persistable);
      }
      return next;
    });
  };
  const draft = draftState;

  const update = useUpdatePlatformConfig({
    mutation: {
      onSuccess: (d) => {
        message.success(t("platform.savedCount", { count: d.updated.length }));
        setDraft({});
        setReasonOpen(false);
        reasonForm.resetFields();
        void qc.invalidateQueries({ queryKey });
      },
      onError: (e) => message.error(errText(e, t("common.saveFailed"))),
    },
  });

  const changed = Object.entries(draft).filter(([k, v]) => {
    const item = byKey.get(k);
    if (!item) return false;
    if (v === "") return item.kind !== "secret" ? item.source === "override" : false;
    if (item.kind === "secret") return true;
    return v !== (item.value ?? "");
  });
  const riskyOff = changed.filter(([k, v]) => k in RISK_OFF && v === "false");

  if (isError) {
    return (
      <PageContainer title={t("menu.platform")}>
        <Card>
          <Alert
            type="error"
            showIcon
            title={
              isApiError(error) && error.status === 403
                ? t("platform.adminOnly")
                : t("platform.loadFailed", { message: errText(error, t("platform.networkError")) })
            }
          />
        </Card>
      </PageContainer>
    );
  }

  const disabled = !isAdmin;
  const menuItems = NAV.map((n) => ({
    type: "group" as const,
    label: t(n.labelKey),
    children: n.groups.map((g) => ({
      key: g,
      label: (
        <NavLabel color={groupDotColor(g, items, warnings, byKey)} text={t(GROUP_LABEL_KEY[g])} />
      ),
    })),
  }));
  const panel =
    active === "security" ? (
      <SecurityPanel
        items={items.filter((i) => i.group === "security")}
        draft={draft}
        setDraft={setDraft}
        disabled={disabled}
        byKey={byKey}
        warnings={warnings}
        // 开关「前往」带出来源分组:目标分组页显示「返回安全组」回链
        onGoTo={(g) => {
          setOriginGroup("security");
          setActive(g);
        }}
      />
    ) : (
      <GroupPanel
        group={active}
        items={items.filter((i) => i.group === active)}
        draft={draft}
        setDraft={setDraft}
        disabled={disabled}
        origin={
          originGroup
            ? {
                group: originGroup,
                onBack: () => {
                  setActive(originGroup);
                  setOriginGroup(null);
                },
              }
            : undefined
        }
        extraContent={
          active === "sms" ? (
            <SmsTestCard disabled={disabled} />
          ) : active === "registry" ? (
            <RegistryTestCard disabled={disabled} />
          ) : undefined
        }
      />
    );

  return (
    <PageContainer
      title={t("menu.platform")}
      extra={
        <Tooltip title={isAdmin ? "" : t("platform.adminOnlyEdit")}>
          <Button
            type="primary"
            disabled={disabled || changed.length === 0}
            onClick={() => setReasonOpen(true)}
          >
            {t("settings.saveChanges", { count: changed.length })}
          </Button>
        </Tooltip>
      }
    >
    <Card loading={isLoading}>
      {warnings.length > 0 && (
        <Space orientation="vertical" size={8} style={{ width: "100%", marginBottom: 16 }}>
          {warnings.map((w) => (
            <Alert
              key={`${w.key}:${w.message}`}
              type={w.level}
              showIcon
              title={w.message}
              action={
                <Button
                  size="small"
                  onClick={() => {
                    const g = byKey.get(w.key)?.group;
                    if (g) setActive(g);
                  }}
                >
                  {t("platform.goTo")}
                </Button>
              }
            />
          ))}
        </Space>
      )}
      {/* 窄屏(lg 以下)左 Menu 改顶部横排,上下折行;桌面左竖排右表单 */}
      <div
        style={{
          display: "flex",
          gap: 24,
          alignItems: "flex-start",
          flexDirection: screens.lg ? "row" : "column",
        }}
      >
        <Menu
          mode={screens.lg ? "inline" : "horizontal"}
          selectedKeys={[active]}
          items={menuItems}
          // 手动切分组即作废来源回链,避免回链指去过时的入口
          onClick={(e) => {
            setOriginGroup(null);
            setActive(e.key as Group);
          }}
          style={
            screens.lg
              ? { width: 220, flex: "none", background: "transparent" }
              : { width: "100%", flex: "none", background: "transparent" }
          }
        />
        <div style={{ flex: 1, minWidth: 0, width: "100%" }}>{panel}</div>
      </div>
      <Modal
        title={t("platform.confirmTitle")}
        open={reasonOpen}
        onCancel={() => setReasonOpen(false)}
        okButtonProps={{ loading: update.isPending }}
        onOk={async () => {
          try {
            const { reason } = await reasonForm.validateFields();
            update.mutate({ data: { updates: Object.fromEntries(changed), reason } });
          } catch {
            // 校验失败:antd 已在字段下给出红字反馈,静默停留
          }
        }}
      >
        <Space orientation="vertical" size={8} style={{ width: "100%" }}>
          {changed.map(([k, v]) => {
            const item = byKey.get(k);
            const shown =
              item?.kind === "secret" ? t("platform.secretMasked") : v === "" ? t("platform.clearOverride") : v;
            return (
              <div key={k}>
                {FIELD_LABELS[k] ?? k} → <b>{shown}</b>
              </div>
            );
          })}
          {riskyOff.length > 0 && (
            <Alert
              type="error"
              showIcon
              title={t("platform.riskOffTitle")}
              description={riskyOff.map(([k]) => (
                <div key={k}>
                  {FIELD_LABELS[k] ?? k}:{RISK_OFF[k]}
                </div>
              ))}
            />
          )}
          <Alert
            type="warning"
            showIcon
            title={t("platform.instantEffect")}
          />
          <Form form={reasonForm} layout="vertical">
            <Form.Item
              name="reason"
              label={t("platform.reasonLabel")}
              rules={[{ required: true, min: 2, message: t("common.reasonRule") }]}
            >
              <Input.TextArea rows={2} placeholder={t("platform.reasonPlaceholder")} />
            </Form.Item>
          </Form>
        </Space>
      </Modal>
    </Card>
    </PageContainer>
  );
}
