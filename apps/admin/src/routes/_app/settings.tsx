/**
 * 系统设置:
 * - 策略参数:env 默认 + DB 覆盖,保存需原因,即时生效并同步 GET /policies
 * - 公告发布:announcement 站内信群发全体 active 租户
 * - 管理员账号:建号/改角色/停用/重置密码 + 自助改密
 */

import { adminColors, announcementStatusMap, fontSize, formatDateTime, idemKeyOf, metaOf } from "@superdl/ui";
import { HexTag, PageContainer, TableErrorEmpty } from "@superdl/ui/components";
import { useQueryClient } from "@tanstack/react-query";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import {
  Alert,
  App,
  Button,
  Card,
  Form,
  Input,
  InputNumber,
  Modal,
  Space,
  Table,
  Tabs,
  Tag,
  Tooltip,
} from "antd";
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
  // Tab 入 URL:白名单校验,非法值回落默认 Tab
  validateSearch: (search: Record<string, unknown>): { tab?: SettingsTab } => ({
    tab: SETTINGS_TABS.includes(search.tab as SettingsTab)
      ? (search.tab as SettingsTab)
      : undefined,
  }),
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
  afford_cover_hours: { label: "开户前余额须覆盖小时数", unit: "小时", hint: "余额须覆盖在途+新增实例的消耗,护栏非预占" },
  max_instances_per_user: { label: "每用户实例数上限", unit: "台", hint: "用户级覆盖优先于本项" },
  max_gpus_per_user: { label: "每用户 GPU 总数上限", unit: "卡", hint: "用户级覆盖优先于本项" },
  max_disks_per_user: { label: "每用户数据盘数上限", unit: "块", hint: "用户级覆盖优先于本项" },
  prewarm_min_coverage_pct: { label: "镜像预热覆盖率门槛", unit: "%", hint: "节点覆盖率达标才标记已预热" },
  prewarm_recheck_hours: { label: "预热复检窗口", unit: "小时", hint: "cached 节点多久复检一次" },
  period_discount_day: { label: "包日折扣", unit: "%", hint: "百分数:80 = 8 折,100 = 不打折;下单与续费同源" },
  period_discount_week: { label: "包周折扣", unit: "%" },
  period_discount_month: { label: "包月折扣", unit: "%" },
  period_discount_year: { label: "包年折扣", unit: "%" },
  period_expire_warn_days: { label: "包周期到期预警", unit: "天", hint: "到期前几天开始推送预警,每天至多一条" },
  spot_discount_pct: { label: "竞价折扣", unit: "%", hint: "百分数:40 = 按量价的 4 折;调价只影响新建的竞价实例" },
  spot_grace_seconds: { label: "抢占宽限窗", unit: "秒", hint: "回收通知发出到真删 Pod 的时间;实际上限还受实例创建超时(env 配置,不在本表)约束,越界时保存被驳回并给出具体上限" },
};

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
      {/* 静态行 + 查询填值:查询失败时「生效值」列全空,必须明示错误而非伪装成无覆盖 */}
      {isError && (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 12 }}
          title={t("common.loadFailed", { ns: "shared" })}
          action={<Button size="small" onClick={() => void refetch()}>{t("common.retry", { ns: "shared" })}</Button>}
        />
      )}
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
                  <div style={{ color: adminColors.textSecondary, fontSize: fontSize.caption }}>{r.hint}</div>
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
                // 范围列展示的 min~max 必须落到输入约束上,否则越界只能等服务端驳回
                min={r.spec?.min}
                max={r.spec?.max}
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
          try {
            const { reason } = await reasonForm.validateFields();
            update.mutate({ data: { updates: Object.fromEntries(changed), reason } });
          } catch {
            /* 校验失败:antd 已在字段下给出红字 */
          }
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
  const { t } = useTranslation(["admin", "shared"]);
  const errText = useApiErrorText();
  const { message, modal } = App.useApp();
  const role = useAdminRole();
  const writable = canWriteOps(role);
  const qc = useQueryClient();
  const [form] = Form.useForm<{ title: string; content: string }>();
  // 公告草稿(sessionStorage):刷新/误关不丢;发布成功清除
  const draft = useFormDraft<{ title: string; content: string }>("announcement-new");
  const { data, queryKey, isLoading, isError, error, refetch } = useAnnouncements();
  const revoke = useRevokeAnnouncement();
  const rows: AnnouncementRow[] = data ?? [];
  // 「上次发布」读接口而非本地缓存:最新一条仍处 published 的公告
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
      <Alert
        type="info"
        showIcon
        title={t("settings.announceScope")}
      />
      <Form
        form={form}
        layout="vertical"
        disabled={!writable}
        style={{ maxWidth: 640 }}
        initialValues={draft.load()}
        onValuesChange={() => draft.save(form.getFieldsValue(true))}
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
      <Tooltip title={writable ? "" : t("settings.opsOnlyAnnounce")}>
        <Button
          type="primary"
          loading={publish.isPending}
          disabled={!writable}
          onClick={() => {
            // 复述影响面 + 公告标题,确认后才真正群发
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
                    // 幂等键从表单快照派生:重试/网络丢响应不会给全体租户重复推送
                    idempotencyKey: idemKeyOf("ann", [values.title, values.content]),
                  });
                } catch {
                  /* 校验失败:antd 已在字段下给出红字 */
                }
              },
            });
          }}
        >
          {t("settings.publish")}
        </Button>
      </Tooltip>
      {lastPublished && (
        <Alert
          type="success"
          showIcon
          title={t("settings.lastPublished", { title: lastPublished.title, time: formatDateTime(lastPublished.created_at), count: lastPublished.reached })}
        />
      )}
      <Table<AnnouncementRow>
        rowKey="id"
        size="small"
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
