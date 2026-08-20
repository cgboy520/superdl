/**
 * 平台配置(WP20):微信支付 / 支付宝 / 阿里云短信 / 实名认证 / 合规备案。
 * 仅超级管理员可读写;env 为默认值层,DB 覆盖即时生效(免重启发版)。
 * secret 类永不回显明文:只显示"已配置 + 尾 4 位",输入留空 = 保持不变。
 */

import { adminColors, formatDateTime } from "@superdl/ui";
import { useQueryClient } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import {
  Alert,
  App,
  Button,
  Card,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Switch,
  Tabs,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import { useState } from "react";

import {
  isApiError,
  type PlatformConfigItem,
  usePlatformConfig,
  useTestSms,
  useUpdatePlatformConfig,
} from "../../api";
import { useAdminRole } from "../../stores/auth";

export const Route = createFileRoute("/_app/platform")({
  component: PlatformConfigPage,
});

const FIELD_LABELS: Record<string, string> = {
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
  real_name_provider: "实名核验 Provider",
  real_name_required_for_recharge: "充值前强制实名认证",
  real_name_access_key_id: "AccessKey ID",
  real_name_access_key_secret: "AccessKey Secret",
  icp_number: "ICP 备案号",
  police_record_number: "公安联网备案号",
  k8s_distro: "K8s 发行版",
  rke2_server_url: "Server 地址",
  rke2_join_token: "Join Token",
  rke2_version: "版本(装机脚本钉死)",
  node_driver_version: "NVIDIA 驱动主版本",
  node_registries_yaml: "registries.yaml(镜像缓存 mirror)",
};

const FIELD_EXTRA: Record<string, string> = {
  payment_wechat_enabled: "凭据配置完成并联调通过后再开启;开启后用户端充值弹窗即出现微信入口",
  payment_alipay_enabled: "凭据配置完成并联调通过后再开启;开启后用户端充值弹窗即出现支付宝入口",
  wechat_public_key_id: "公钥模式(2024-10 后新注册商户仅支持该模式);与公钥同时填写,留空则走平台证书模式",
  real_name_required_for_recharge: "《网络安全法》要求;开启后未实名用户无法充值,用户端费用中心出现引导横幅",
  sms_template_verify: "模板需含变量 ${code}",
  sms_template_notice: "模板需含变量 ${title}",
  icp_number: "展示于用户端页脚,链接工信部备案系统(beian.miit.gov.cn)",
  police_record_number: "展示于用户端页脚,链接公安备案系统(beian.mps.gov.cn);未取得可留空",
};

const PROVIDER_LABELS: Record<string, string> = {
  mock: "mock(仅开发环境)",
  aliyun: "阿里云",
  rke2: "RKE2(生产)",
  k3s: "k3s(轻量/本地验证)",
};

const GROUP_INTRO: Record<string, string> = {
  payment_wechat:
    "微信支付 APIv3(Native 扫码):在商户平台(pay.weixin.qq.com)→ 账户中心 → API 安全中下载商户 API 证书/私钥并设置 APIv3 密钥。" +
    "推荐「微信支付公钥」验签模式:申请公钥后同时填入公钥 ID 与公钥;两者留空则回退平台证书模式(存量商户,SDK 自动拉取轮换)。" +
    "支付回调地址为 {public_base_url}/api/v1/webhooks/wechatpay,由下单请求携带,无需在商户平台单独配置。",
  payment_alipay:
    "支付宝当面付(precreate 扫码,RSA2):在开放平台(open.alipay.com)创建应用并签约「当面付」," +
    "开发设置 → 接口加签方式选「公钥模式」:用密钥工具生成应用私钥(填入下方)、上传应用公钥后回填平台生成的「支付宝公钥」。" +
    "异步通知地址 {public_base_url}/api/v1/webhooks/alipay 由下单请求携带。",
  sms:
    "阿里云短信服务(dysmsapi):完成企业资质、签名与模板报备后填入凭据。" +
    "建议使用独立 RAM 子账号并仅授权 AliyunDysmsFullAccess。切换 Provider 为「阿里云」后即时生效,可先用下方测试发送验证。",
  real_name:
    "阿里云实人认证 · 手机号三要素核验(简版,Mobile3MetaSimpleVerify):开通「要素核验」服务并授权 RAM 子账号。" +
    "核验通过即标记已实名;身份证号仅存脱敏串,原文即用即弃。",
  compliance:
    "备案信息展示于用户端页脚。ICP 备案通过接入商(云厂商)提交,下发后填入完整备案号(含 -1 等后缀);" +
    "公安联网备案在网站上线后 30 日内于 beian.mps.gov.cn 申请。",
  cluster:
    "GPU 节点一键加入(WP23)的集群接入参数:Server 地址与 join token 来自 server 节点" +
    "(token 执行 cat /var/lib/rancher/<rke2|k3s>/server/node-token 获取,轮换用 rke2 token rotate 后在此更新)。" +
    "生产一律 RKE2,k3s 仅供轻量/本地验证环境。" +
    "配置完成后,运维在「节点与 GPU → 添加节点」生成一次性注册命令;registries.yaml 为镜像缓存 mirror(WP22),可留空。",
};

const SOURCE_TAG: Record<PlatformConfigItem["source"], { color?: string; text: string }> = {
  override: { color: "cyan", text: "DB 覆盖" },
  env: { text: "env 默认" },
  unset: { color: "warning", text: "未配置" },
};

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
            ? `已配置${item.preview ? `(${item.preview})` : ""},留空保持不变`
            : "未配置"
        }
        autoComplete="new-password"
        onChange={(e) => onChange(e.target.value)}
      />
    );
  }
  if (item.kind === "text") {
    return (
      <Input.TextArea
        style={{ maxWidth: 640, fontFamily: "monospace", fontSize: 12 }}
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
}: {
  group: string;
  items: PlatformConfigItem[];
  draft: Record<string, string>;
  setDraft: React.Dispatch<React.SetStateAction<Record<string, string>>>;
  disabled: boolean;
  extraContent?: React.ReactNode;
}) {
  return (
    <Space orientation="vertical" size={16} style={{ width: "100%", maxWidth: 760 }}>
      <Alert type="info" showIcon title={GROUP_INTRO[group]} />
      <Form layout="vertical">
        {items.map((item) => (
          <Form.Item
            key={item.key}
            label={
              <Space size={8}>
                {FIELD_LABELS[item.key] ?? item.key}
                <Tag color={SOURCE_TAG[item.source].color}>{SOURCE_TAG[item.source].text}</Tag>
                {item.updated_at && (
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    更新于 {formatDateTime(item.updated_at)}
                  </Typography.Text>
                )}
              </Space>
            }
            extra={
              <span style={{ fontSize: 12 }}>
                {[FIELD_EXTRA[item.key], item.hint].filter(Boolean).join(";")}
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
                  清除覆盖
                </Button>
              )}
              {draft[item.key] === "" && item.source === "override" && (
                <Tag color="orange">保存后清除覆盖,回退 env 默认</Tag>
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
  const { message } = App.useApp();
  const [phone, setPhone] = useState("");
  const testSms = useTestSms({
    mutation: {
      onSuccess: (d) => {
        const provider = (d as { provider: string }).provider;
        message.success(`测试短信已提交(渠道:${PROVIDER_LABELS[provider] ?? provider})`);
      },
      onError: (e) => message.error(isApiError(e) ? e.message : "发送失败"),
    },
  });
  return (
    <Card size="small" title="测试发送(走当前生效配置,真实计费)">
      <Space.Compact style={{ width: 360 }}>
        <Input
          placeholder="接收手机号"
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
          发送验证码短信
        </Button>
      </Space.Compact>
      <div style={{ color: adminColors.textSecondary, fontSize: 12, marginTop: 8 }}>
        使用「验证码模板」发送随机 6 位码;先保存配置再测试。Provider 为 mock 时仅落日志。
      </div>
    </Card>
  );
}

function PlatformConfigPage() {
  const { message } = App.useApp();
  const role = useAdminRole();
  const isAdmin = role === "admin";
  const qc = useQueryClient();
  const { data, queryKey, isLoading, isError, error } = usePlatformConfig();
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [reasonOpen, setReasonOpen] = useState(false);
  const [reasonForm] = Form.useForm<{ reason: string }>();

  const update = useUpdatePlatformConfig({
    mutation: {
      onSuccess: (d) => {
        const updated = (d as { updated: string[] }).updated;
        message.success(`已保存 ${updated.length} 项,即时生效`);
        setDraft({});
        setReasonOpen(false);
        reasonForm.resetFields();
        void qc.invalidateQueries({ queryKey });
      },
      onError: (e) => message.error(isApiError(e) ? e.message : "保存失败"),
    },
  });

  const items = data?.items ?? [];
  const byKey = new Map(items.map((i) => [i.key, i]));
  const changed = Object.entries(draft).filter(([k, v]) => {
    const item = byKey.get(k);
    if (!item) return false;
    if (v === "") return item.kind !== "secret" ? item.source === "override" : false;
    if (item.kind === "secret") return true;
    return v !== (item.value ?? "");
  });

  if (isError) {
    return (
      <Card>
        <Alert
          type="error"
          showIcon
          title={
            isApiError(error) && error.status === 403
              ? "平台配置涉及支付/短信凭据,仅超级管理员可访问。"
              : `加载失败:${isApiError(error) ? error.message : "网络错误"}`
          }
        />
      </Card>
    );
  }

  const groupItems = (g: PlatformConfigItem["group"]) => items.filter((i) => i.group === g);
  const disabled = !isAdmin;

  return (
    <Card
      loading={isLoading}
      title="平台配置(渠道凭据与合规)"
      extra={
        <Tooltip title={isAdmin ? "" : "仅超级管理员可修改"}>
          <Button
            type="primary"
            disabled={disabled || changed.length === 0}
            onClick={() => setReasonOpen(true)}
          >
            保存 {changed.length > 0 ? `${changed.length} 项变更` : ""}(需原因)
          </Button>
        </Tooltip>
      }
    >
      <Tabs
        items={[
          {
            key: "payment_wechat",
            label: "微信支付",
            children: (
              <GroupPanel
                group="payment_wechat"
                items={groupItems("payment_wechat")}
                draft={draft}
                setDraft={setDraft}
                disabled={disabled}
              />
            ),
          },
          {
            key: "payment_alipay",
            label: "支付宝",
            children: (
              <GroupPanel
                group="payment_alipay"
                items={groupItems("payment_alipay")}
                draft={draft}
                setDraft={setDraft}
                disabled={disabled}
              />
            ),
          },
          {
            key: "sms",
            label: "短信(阿里云)",
            children: (
              <GroupPanel
                group="sms"
                items={groupItems("sms")}
                draft={draft}
                setDraft={setDraft}
                disabled={disabled}
                extraContent={<SmsTestCard disabled={disabled} />}
              />
            ),
          },
          {
            key: "real_name",
            label: "实名认证",
            children: (
              <GroupPanel
                group="real_name"
                items={groupItems("real_name")}
                draft={draft}
                setDraft={setDraft}
                disabled={disabled}
              />
            ),
          },
          {
            key: "compliance",
            label: "合规备案",
            children: (
              <GroupPanel
                group="compliance"
                items={groupItems("compliance")}
                draft={draft}
                setDraft={setDraft}
                disabled={disabled}
              />
            ),
          },
          {
            key: "cluster",
            label: "集群接入",
            children: (
              <GroupPanel
                group="cluster"
                items={groupItems("cluster")}
                draft={draft}
                setDraft={setDraft}
                disabled={disabled}
              />
            ),
          },
        ]}
      />
      <Modal
        title="确认变更平台配置"
        open={reasonOpen}
        onCancel={() => setReasonOpen(false)}
        okButtonProps={{ loading: update.isPending }}
        onOk={async () => {
          const { reason } = await reasonForm.validateFields();
          update.mutate({ data: { updates: Object.fromEntries(changed), reason } });
        }}
      >
        <Space orientation="vertical" size={8} style={{ width: "100%" }}>
          {changed.map(([k, v]) => {
            const item = byKey.get(k);
            const shown =
              item?.kind === "secret" ? "••••••(新值不回显)" : v === "" ? "清除覆盖" : v;
            return (
              <div key={k}>
                {FIELD_LABELS[k] ?? k} → <b>{shown}</b>
              </div>
            );
          })}
          <Alert
            type="warning"
            showIcon
            title="保存后即时生效:支付下单、短信发送、实名核验与用户端页脚将直接使用新配置。"
          />
          <Form form={reasonForm} layout="vertical">
            <Form.Item
              name="reason"
              label="原因(必填,入审计;审计只记录键名不记录值)"
              rules={[{ required: true, min: 2, message: "请填写变更原因(至少 2 字)" }]}
            >
              <Input.TextArea rows={2} placeholder="如:商户资质下发,录入生产凭据" />
            </Form.Item>
          </Form>
        </Space>
      </Modal>
    </Card>
  );
}
