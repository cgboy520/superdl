/**
 * 系统设置:
 * - 策略参数:env 默认 + DB 覆盖,保存需原因,即时生效并同步 GET /policies
 * - 公告发布:announcement 站内信群发全体 active 租户
 * - 管理员账号:建号/改角色/停用/重置密码 + 自助改密
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
  InputNumber,
  Modal,
  Popconfirm,
  Space,
  Table,
  Tabs,
  Tag,
  Tooltip,
} from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import {
  useAdminPolicies,
  usePublishAnnouncement,
  useUpdatePolicies,
} from "../../api";
import { AdminsTab } from "./-AdminsTab";
import { useApiErrorText } from "../../lib/apiError";
import { canWriteOps, useAdminRole } from "../../stores/auth";

export const Route = createFileRoute("/_app/settings")({
  component: SettingsPage,
});

// i18n-exempt: 策略参数名与单位为运营域术语,随渠道字段表一并豁免(admin CJK 闸门白名单)
const POLICY_LABELS: Record<string, { label: string; unit: string; hint?: string }> = {
  disk_price_gb_month: { label: "数据盘单价", unit: "元/GB·月", hint: "建盘时快照,调价只影响新盘" },
  disk_min_gb: { label: "数据盘最小容量", unit: "GB" },
  disk_max_gb: { label: "数据盘最大容量", unit: "GB" },
  disk_grace_days: { label: "欠费宽限(数据盘)", unit: "天", hint: "宽限到期转冻结" },
  disk_frozen_days: { label: "冻结保留(数据盘)", unit: "天", hint: "冻结到期回收擦除" },
  freeze_grace_hours: { label: "欠费冻结时长(实例)", unit: "小时", hint: "冻结到期回收实例盘" },
  low_balance_warn_hours: { label: "默认余额预警阈值", unit: "小时" },
};

function PoliciesTab() {
  const { t } = useTranslation();
  const errText = useApiErrorText();
  const { message } = App.useApp();
  const role = useAdminRole();
  const writable = canWriteOps(role);
  const qc = useQueryClient();
  const { data, queryKey, isLoading } = useAdminPolicies();
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [reasonOpen, setReasonOpen] = useState(false);
  const [reasonForm] = Form.useForm<{ reason: string }>();

  const update = useUpdatePolicies({
    mutation: {
      onSuccess: () => {
        message.success(t("settings.policySaved"));
        setDraft({});
        setReasonOpen(false);
        reasonForm.resetFields();
        void qc.invalidateQueries({ queryKey });
      },
      onError: (e) => message.error(errText(e, t("skus.saveFailed"))),
    },
  });

  const rows = Object.entries(POLICY_LABELS).map(([key, meta]) => ({
    key,
    ...meta,
    effective: data?.effective[key] ?? "",
    overridden: data?.overrides[key] != null,
    spec: data?.specs[key],
  }));
  const changed = Object.entries(draft).filter(
    ([k, v]) => v !== "" && v !== (data?.effective[k] ?? ""),
  );

  return (
    <>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 12 }}
        title={t("settings.instantEffect")}
      />
      <Table
        rowKey="key"
        size="small"
        loading={isLoading}
        pagination={false}
        scroll={{ x: 760 }}
        dataSource={rows}
        columns={[
          {
            title: t("settings.colParam"),
            render: (_, r) => (
              <>
                {r.label}
                {r.overridden && <Tag style={{ marginLeft: 8 }}>{t("settings.overridden")}</Tag>}
                {r.hint && (
                  <div style={{ color: adminColors.textSecondary, fontSize: 12 }}>{r.hint}</div>
                )}
              </>
            ),
          },
          { title: t("settings.colEffective"), dataIndex: "effective", width: 130 },
          { title: t("settings.colUnit"), dataIndex: "unit", width: 110 },
          {
            title: t("settings.colRange"),
            width: 140,
            render: (_, r) => (r.spec ? `${r.spec.min} ~ ${r.spec.max}` : "-"),
          },
          {
            title: t("settings.colNewValue"),
            width: 160,
            render: (_, r) => (
              <InputNumber
                size="small"
                style={{ width: 140 }}
                disabled={!writable}
                stringMode
                placeholder={r.effective}
                value={draft[r.key] ?? null}
                onChange={(v) =>
                  setDraft((d) => ({ ...d, [r.key]: v == null ? "" : String(v) }))
                }
              />
            ),
          },
        ]}
      />
      <Tooltip title={writable ? "" : t("settings.opsOnlyPolicies")}>
        <Button
          type="primary"
          style={{ marginTop: 12 }}
          disabled={!writable || changed.length === 0}
          onClick={() => setReasonOpen(true)}
        >
          {t("settings.saveChanges", { count: changed.length })}
        </Button>
      </Tooltip>
      <Modal
        title={t("settings.confirmPolicyTitle")}
        open={reasonOpen}
        onCancel={() => setReasonOpen(false)}
        okButtonProps={{ loading: update.isPending }}
        onOk={async () => {
          const { reason } = await reasonForm.validateFields();
          update.mutate({ data: { updates: Object.fromEntries(changed), reason } });
        }}
      >
        <Space orientation="vertical" size={8} style={{ width: "100%" }}>
          {changed.map(([k, v]) => (
            <div key={k}>
              {POLICY_LABELS[k]?.label ?? k}:{data?.effective[k]} → <b>{v}</b>
            </div>
          ))}
          <Form form={reasonForm} layout="vertical">
            <Form.Item
              name="reason"
              label={t("common.reasonLabel")}
              rules={[{ required: true, min: 2, message: t("common.reasonRule") }]}
            >
              <Input.TextArea rows={2} placeholder={t("settings.policyReasonPlaceholder")} />
            </Form.Item>
          </Form>
        </Space>
      </Modal>
    </>
  );
}

function AnnouncementTab() {
  const { t } = useTranslation();
  const errText = useApiErrorText();
  const { message } = App.useApp();
  const role = useAdminRole();
  const writable = canWriteOps(role);
  const [form] = Form.useForm<{ title: string; content: string }>();
  const [lastPublished, setLastPublished] = useState<{ title: string; at: string; reached: number } | null>(null);

  const publish = usePublishAnnouncement({
    mutation: {
      onSuccess: (d) => {
        const reached = (d as { reached: number }).reached;
        message.success(t("settings.announcementPublished", { count: reached }));
        setLastPublished({
          title: form.getFieldValue("title") as string,
          at: new Date().toISOString(),
          reached,
        });
        form.resetFields();
      },
      onError: (e) => message.error(errText(e, t("settings.publishFailed"))),
    },
  });

  return (
    <Space orientation="vertical" size={12} style={{ width: "100%", maxWidth: 640 }}>
      <Alert
        type="info"
        showIcon
        title={t("settings.announceScope")}
      />
      <Form form={form} layout="vertical" disabled={!writable}>
        <Form.Item
          name="title"
          label={t("settings.announceTitleLabel")}
          rules={[{ required: true, min: 2, max: 128, message: t("settings.titleLenRule") }]}
        >
          <Input placeholder={t("settings.announceTitlePlaceholder")} />
        </Form.Item>
        <Form.Item
          name="content"
          label={t("settings.announceContentLabel")}
          rules={[{ required: true, min: 2, max: 2000, message: t("settings.contentLenRule") }]}
        >
          <Input.TextArea rows={4} placeholder={t("settings.announceContentPlaceholder")} />
        </Form.Item>
      </Form>
      <Popconfirm
        title={t("settings.confirmAnnounce")}
        onConfirm={async () => {
          const values = await form.validateFields();
          publish.mutate({ data: values });
        }}
        disabled={!writable}
      >
        <Tooltip title={writable ? "" : t("settings.opsOnlyAnnounce")}>
          <Button type="primary" loading={publish.isPending} disabled={!writable}>
            {t("settings.publish")}
          </Button>
        </Tooltip>
      </Popconfirm>
      {lastPublished && (
        <Alert
          type="success"
          showIcon
          title={t("settings.lastPublished", { title: lastPublished.title, time: formatDateTime(lastPublished.at), count: lastPublished.reached })}
        />
      )}
    </Space>
  );
}

function SettingsPage() {
  const { t } = useTranslation();
  return (
    <Card>
      <Tabs
        items={[
          { key: "policies", label: t("settings.tabPolicies"), children: <PoliciesTab /> },
          { key: "announcement", label: t("settings.tabAnnouncement"), children: <AnnouncementTab /> },
          { key: "admins", label: t("settings.tabAdmins"), children: <AdminsTab /> },
        ]}
      />
    </Card>
  );
}
