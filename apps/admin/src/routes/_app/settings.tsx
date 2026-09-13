/** 系统设置:策略参数(env 默认 + DB 覆盖,保存需原因)/ 公告发布(群发 active 租户)/ 法务文档 / 管理员账号。 */

import { adminColors, announcementStatusMap, fontSize, formatDateTime, idemKeyOf, metaOf, space } from "@superdl/ui";
import { DataErrorAlert, GatedButton, HexTag, PageContainer, TableErrorEmpty } from "@superdl/ui/components";
import { useQueryClient } from "@tanstack/react-query";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { Alert, App, Card, Form, Input, InputNumber, Modal, Space, Table, Tabs, Tag, Tooltip } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import {
  type AnnouncementRow,
  isApiError,
  useAdminPolicies,
  useAnnouncements,
  usePublishAnnouncement,
  useRevokeAnnouncement,
  useUpdatePolicies,
} from "../../api";
import { AdminsTab } from "./-AdminsTab";
import { LegalDocsTab } from "./-LegalDocsTab";
import { LIST_CAPS, ListCapNote } from "../../components/ListCapNote";
import { ReasonAction } from "../../components/ReasonAction";
import { useApiErrorText } from "@superdl/ui";
import { useFormDraft } from "@superdl/ui";
import { canWriteOps, useAdminRole } from "../../stores/auth";

const SETTINGS_TABS = ["policies", "announcement", "legal", "admins"] as const;
type SettingsTab = (typeof SETTINGS_TABS)[number];

export const Route = createFileRoute("/_app/settings")({
  // Tab 入 URL,非法值回落默认
  validateSearch: (search: Record<string, unknown>): { tab?: SettingsTab } => ({
    tab: SETTINGS_TABS.includes(search.tab as SettingsTab) ? (search.tab as SettingsTab) : undefined,
  }),
  component: SettingsPage,
});

/** 策略参数:值是 locale 键(settings.policy.*),渲染时 t() 查表。 */
const POLICY_LABELS = {
  disk_price_gb_month: {
    label: "settings.policy.disk_price_gb_month.label",
    unit: "settings.policy.disk_price_gb_month.unit",
    hint: "settings.policy.disk_price_gb_month.hint",
  },
  disk_min_gb: {
    label: "settings.policy.disk_min_gb.label",
    unit: "settings.policy.disk_min_gb.unit",
  },
  disk_max_gb: {
    label: "settings.policy.disk_max_gb.label",
    unit: "settings.policy.disk_max_gb.unit",
  },
  disk_grace_days: {
    label: "settings.policy.disk_grace_days.label",
    unit: "settings.policy.disk_grace_days.unit",
    hint: "settings.policy.disk_grace_days.hint",
  },
  disk_frozen_days: {
    label: "settings.policy.disk_frozen_days.label",
    unit: "settings.policy.disk_frozen_days.unit",
    hint: "settings.policy.disk_frozen_days.hint",
  },
  freeze_grace_hours: {
    label: "settings.policy.freeze_grace_hours.label",
    unit: "settings.policy.freeze_grace_hours.unit",
    hint: "settings.policy.freeze_grace_hours.hint",
  },
  afford_cover_hours: {
    label: "settings.policy.afford_cover_hours.label",
    unit: "settings.policy.afford_cover_hours.unit",
    hint: "settings.policy.afford_cover_hours.hint",
  },
  max_instances_per_user: {
    label: "settings.policy.max_instances_per_user.label",
    unit: "settings.policy.max_instances_per_user.unit",
    hint: "settings.policy.max_instances_per_user.hint",
  },
  max_gpus_per_user: {
    label: "settings.policy.max_gpus_per_user.label",
    unit: "settings.policy.max_gpus_per_user.unit",
    hint: "settings.policy.max_gpus_per_user.hint",
  },
  max_disks_per_user: {
    label: "settings.policy.max_disks_per_user.label",
    unit: "settings.policy.max_disks_per_user.unit",
    hint: "settings.policy.max_disks_per_user.hint",
  },
  prewarm_min_coverage_pct: {
    label: "settings.policy.prewarm_min_coverage_pct.label",
    unit: "settings.policy.prewarm_min_coverage_pct.unit",
    hint: "settings.policy.prewarm_min_coverage_pct.hint",
  },
  prewarm_recheck_hours: {
    label: "settings.policy.prewarm_recheck_hours.label",
    unit: "settings.policy.prewarm_recheck_hours.unit",
    hint: "settings.policy.prewarm_recheck_hours.hint",
  },
  period_discount_day: {
    label: "settings.policy.period_discount_day.label",
    unit: "settings.policy.period_discount_day.unit",
    hint: "settings.policy.period_discount_day.hint",
  },
  period_discount_week: {
    label: "settings.policy.period_discount_week.label",
    unit: "settings.policy.period_discount_week.unit",
  },
  period_discount_month: {
    label: "settings.policy.period_discount_month.label",
    unit: "settings.policy.period_discount_month.unit",
  },
  period_discount_year: {
    label: "settings.policy.period_discount_year.label",
    unit: "settings.policy.period_discount_year.unit",
  },
  period_expire_warn_days: {
    label: "settings.policy.period_expire_warn_days.label",
    unit: "settings.policy.period_expire_warn_days.unit",
    hint: "settings.policy.period_expire_warn_days.hint",
  },
  spot_discount_pct: {
    label: "settings.policy.spot_discount_pct.label",
    unit: "settings.policy.spot_discount_pct.unit",
    hint: "settings.policy.spot_discount_pct.hint",
  },
  spot_grace_seconds: {
    label: "settings.policy.spot_grace_seconds.label",
    unit: "settings.policy.spot_grace_seconds.unit",
    hint: "settings.policy.spot_grace_seconds.hint",
  },
} as const satisfies Record<string, { label: string; unit: string; hint?: string }>;
type PolicyMeta = (typeof POLICY_LABELS)[keyof typeof POLICY_LABELS];
/** 统一形状:联合体上直接取可选 hint 过不了 TS,先补齐再索引。 */
interface PolicyEntry {
  label: PolicyMeta["label"];
  unit: PolicyMeta["unit"];
  hint?: Extract<PolicyMeta, { hint: string }>["hint"];
}
const POLICY_ENTRIES = Object.entries(POLICY_LABELS) as [string, PolicyEntry][];

function PoliciesTab() {
  const { t } = useTranslation(["admin", "shared"]);
  const errText = useApiErrorText();
  const { message } = App.useApp();
  const role = useAdminRole();
  const writable = canWriteOps(role);
  const qc = useQueryClient();
  const { data, queryKey, isLoading, isError, refetch } = useAdminPolicies();
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
      onError: (e) => message.error(errText(e, t("common.saveFailed"))),
    },
  });

  const policyLabel = (key: string) => {
    const meta = (POLICY_LABELS as Record<string, PolicyEntry>)[key];
    return meta ? t(meta.label) : key;
  };
  const rows = POLICY_ENTRIES.map(([key, meta]) => ({
    key,
    label: t(meta.label),
    unit: t(meta.unit),
    hint: meta.hint ? t(meta.hint) : undefined,
    effective: data?.effective[key] ?? "",
    overridden: data?.overrides[key] != null,
    spec: data?.specs[key],
  }));
  const changed = Object.entries(draft).filter(([k, v]) => v !== "" && v !== (data?.effective[k] ?? ""));

  return (
    <>
      {isError && (
        <DataErrorAlert
          style={{ marginBottom: 12 }}
          title={t("common.loadFailed", { ns: "shared" })}
          description={null}
          onRetry={() => void refetch()}
        />
      )}
      <Table
        rowKey="key"
        loading={isLoading}
        pagination={false}
        scroll={{ x: 560 }}
        dataSource={rows}
        columns={[
          {
            title: t("settings.colParam"),
            render: (_, r) => (
              <>
                <div>
                  {r.label}
                  {r.overridden && <Tag style={{ marginLeft: space.sm }}>{t("settings.overridden")}</Tag>}
                </div>
                <div style={{ color: adminColors.textSecondary, fontSize: fontSize.caption }}>
                  {t("settings.paramMeta", {
                    unit: r.unit,
                    range: r.spec ? `${r.spec.min} ~ ${r.spec.max}` : "-",
                  })}
                  {r.hint ? ` · ${r.hint}` : ""}
                </div>
              </>
            ),
          },
          { title: t("settings.colEffective"), dataIndex: "effective", width: 130, align: "right" },
          {
            title: t("settings.colNewValue"),
            width: 160,
            align: "right",
            render: (_, r) => (
              <InputNumber
                size="small"
                style={{ width: "100%" }}
                disabled={!writable}
                stringMode
                min={r.spec?.min}
                max={r.spec?.max}
                placeholder={r.effective}
                value={draft[r.key] ?? null}
                onChange={(v) => setDraft((d) => ({ ...d, [r.key]: v ?? "" }))}
              />
            ),
          },
        ]}
      />
      <GatedButton
        type="primary"
        style={{ marginTop: 12 }}
        reason={writable ? undefined : t("settings.opsOnlyPolicies")}
        disabled={changed.length === 0}
        onClick={() => setReasonOpen(true)}
      >
        {t("settings.saveChanges", { count: changed.length })}
      </GatedButton>
      <Modal
        title={t("settings.confirmPolicyTitle")}
        open={reasonOpen}
        onCancel={() => setReasonOpen(false)}
        okButtonProps={{ loading: update.isPending }}
        onOk={() => {
          void (async () => {
            try {
              const { reason } = await reasonForm.validateFields();
              update.mutate({ data: { updates: Object.fromEntries(changed), reason } });
            } catch {
              /* 校验失败:antd 已给红字 */
            }
          })();
        }}
      >
        <Space orientation="vertical" size={8} style={{ width: "100%" }}>
          {changed.map(([k, v]) => (
            <div key={k}>
              {policyLabel(k)}:{data?.effective[k]} → <b>{v}</b>
            </div>
          ))}
          <Alert type="info" showIcon title={t("settings.instantEffect")} />
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
  const { t } = useTranslation(["admin", "shared"]);
  const errText = useApiErrorText();
  const { message, modal } = App.useApp();
  const role = useAdminRole();
  const writable = canWriteOps(role);
  const qc = useQueryClient();
  const [form] = Form.useForm<{ title: string; content: string }>();
  // 公告草稿(sessionStorage),发布成功清除
  const draft = useFormDraft<{ title: string; content: string }>("announcement-new");
  const { data, queryKey, isLoading, isError, error, refetch } = useAnnouncements();
  const revoke = useRevokeAnnouncement();
  const rows: AnnouncementRow[] = data ?? [];
  // 「上次发布」= 最新一条 published 公告
  const lastPublished = rows.find((r) => r.status === "published");
  const refresh = () => void qc.invalidateQueries({ queryKey });

  const publish = usePublishAnnouncement({
    mutation: {
      onSuccess: (d) => {
        message.success(t("settings.announcementPublished", { count: d.reached }));
        form.resetFields();
        draft.clear();
        refresh();
      },
      onError: (e) => message.error(errText(e, t("settings.publishFailed"))),
    },
  });

  return (
    <Space orientation="vertical" size={12} style={{ width: "100%", maxWidth: 860 }}>
      <Alert type="info" showIcon title={t("settings.announceScope")} />
      <Form
        form={form}
        layout="vertical"
        disabled={!writable}
        style={{ maxWidth: 640 }}
        initialValues={draft.load()}
        onValuesChange={() => draft.save(form.getFieldsValue(true) as Partial<{ title: string; content: string }>)}
      >
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
      <GatedButton
        type="primary"
        loading={publish.isPending}
        reason={writable ? undefined : t("settings.opsOnlyAnnounce")}
        onClick={() => {
          modal.confirm({
            title: t("settings.confirmAnnounce"),
            content: t("settings.confirmAnnounceDetail", {
              title: form.getFieldValue("title") ?? "",
            }),
            okText: t("settings.publish"),
            onOk: async () => {
              try {
                const values = await form.validateFields();
                publish.mutate({
                  data: values,
                  // 幂等键从表单快照派生
                  idempotencyKey: idemKeyOf("ann", [values.title, values.content]),
                });
              } catch {
                /* 校验失败:antd 已给红字 */
              }
            },
          });
        }}
      >
        {t("settings.publish")}
      </GatedButton>
      {lastPublished && (
        <Alert
          type="success"
          showIcon
          title={t("settings.lastPublished", {
            title: lastPublished.title,
            time: formatDateTime(lastPublished.created_at),
            count: lastPublished.reached,
          })}
        />
      )}
      <Table<AnnouncementRow>
        rowKey="id"
        loading={isLoading}
        locale={{
          emptyText: (
            <TableErrorEmpty
              isError={isError}
              isForbidden={isApiError(error) && error.status === 403}
              onRetry={() => void refetch()}
            />
          ),
        }}
        pagination={false}
        scroll={{ x: 720 }}
        dataSource={rows}
        columns={[
          {
            title: t("settings.colAnnTitle"),
            dataIndex: "title",
            ellipsis: true,
            render: (v: string) => (
              <Tooltip title={v}>
                <span>{v}</span>
              </Tooltip>
            ),
          },
          {
            title: t("settings.colAnnPublishedAt"),
            dataIndex: "created_at",
            width: 170,
            render: formatDateTime,
          },
          {
            title: t("settings.colAnnStatus"),
            dataIndex: "status",
            width: 110,
            render: (v: string, r) => {
              const meta = metaOf(announcementStatusMap, v);
              return (
                <Tooltip
                  title={
                    r.status === "revoked" && r.revoked_at
                      ? t("settings.revokedMeta", {
                          time: formatDateTime(r.revoked_at),
                          reason: r.revoke_reason ?? "-",
                        })
                      : undefined
                  }
                >
                  <span>
                    <HexTag color={meta?.color}>{meta ? t(meta.labelKey) : v}</HexTag>
                  </span>
                </Tooltip>
              );
            },
          },
          {
            title: t("settings.colAnnActions"),
            width: 100,
            render: (_, r) =>
              r.status === "published" ? (
                <ReasonAction
                  label={t("settings.revoke")}
                  target={r.title}
                  title={t("settings.revokeTitle")}
                  confirmText={t("settings.revokeConfirm", { title: r.title })}
                  danger
                  disabled={!writable}
                  disabledReason={t("settings.opsOnlyAnnounce")}
                  onSubmit={async (reason) => {
                    await revoke.mutateAsync({
                      announcementId: r.id,
                      data: { reason },
                    });
                    refresh();
                    return t("settings.revokeDone");
                  }}
                />
              ) : null,
          },
        ]}
      />
      <ListCapNote rows={rows.length} cap={LIST_CAPS.announcements} />
    </Space>
  );
}

function SettingsPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const tab = Route.useSearch({ select: (s) => s.tab });
  return (
    <PageContainer title={t("menu.settings")}>
      <Card>
        <Tabs
          activeKey={tab ?? "policies"}
          onChange={(key) =>
            void navigate({
              to: "/settings",
              replace: true,
              search: key === "policies" ? {} : { tab: key as SettingsTab },
            })
          }
          items={[
            { key: "policies", label: t("settings.tabPolicies"), children: <PoliciesTab /> },
            { key: "announcement", label: t("settings.tabAnnouncement"), children: <AnnouncementTab /> },
            { key: "legal", label: t("settings.tabLegal"), children: <LegalDocsTab /> },
            { key: "admins", label: t("settings.tabAdmins"), children: <AdminsTab /> },
          ]}
        />
      </Card>
    </PageContainer>
  );
}
