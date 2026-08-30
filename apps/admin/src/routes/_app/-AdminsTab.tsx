/** 管理员账号:建号 / 改角色 / 停用 / 重置密码 + 自助改密。 */

import { adminColors, fontSize, formatDateTime } from "@superdl/ui";
import { TableErrorEmpty } from "@superdl/ui/components";
import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { Alert, App, Button, Card, Form, Input, Modal, Select, Space, Table, Tag, Typography } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import type { AdminAccountOut } from "@superdl/api-client";

import {
  isApiError,
  useAdminAccounts,
  useChangeOwnPassword,
  useCreateAdminAccount,
  usePlatformConfig,
  useRegenerateRecoveryCodes,
  useResetAdminMfa,
  useResetAdminPassword,
  useUpdateAdminAccount,
} from "../../api";
import { ReasonAction } from "../../components/ReasonAction";
import { RowActionModal } from "../../components/RowActionModal";
import { useApiErrorText } from "@superdl/ui";
import { ALL_ROLES, ROLE_LABEL_KEY, type Role } from "../../lib/menu";
import { REASON_MAX_LEN } from "../../lib/validators";
import { useAuth } from "../../stores/auth";

const MIN_PASSWORD = 12;

function roleColor(role: string): string {
  if (role === "admin") return adminColors.critical;
  if (role === "readonly") return adminColors.textMuted;
  return adminColors.dataAccent;
}

export function AdminsTab() {
  const { t } = useTranslation();
  const errText = useApiErrorText();
  const { message, modal } = App.useApp();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { admin: me, logout } = useAuth();
  const isSuperAdmin = me?.role === "admin";
  const { data, queryKey, isLoading, isError, error, refetch } = useAdminAccounts();
  // 安全策略 admin_mfa_enabled(仅超管可读;读不到按「开启」处理,不误标)
  // 非超管不发该请求(注定 403)
  const mfaEnabled =
    usePlatformConfig({ enabled: isSuperAdmin }).data?.items.find((i) => i.key === "admin_mfa_enabled")?.value !== "false";

  const [createOpen, setCreateOpen] = useState(false);
  const [pwdTarget, setPwdTarget] = useState<AdminAccountOut | null>(null);
  const [selfOpen, setSelfOpen] = useState(false);
  const [roleTarget, setRoleTarget] = useState<{ row: AdminAccountOut; role: Role } | null>(null);
  const [createForm] = Form.useForm<{ username: string; password: string; role: Role; reason: string }>();
  const [pwdForm] = Form.useForm<{ password: string; reason: string }>();
  const [selfForm] = Form.useForm<{ current_password: string; new_password: string }>();
  const [roleForm] = Form.useForm<{ reason: string }>();

  const refresh = () => qc.invalidateQueries({ queryKey });
  const create = useCreateAdminAccount();
  const update = useUpdateAdminAccount();
  const resetPwd = useResetAdminPassword();
  const changeOwn = useChangeOwnPassword();
  const resetMfa = useResetAdminMfa();
  const [codes, setCodes] = useState<string[] | null>(null);
  const regenCodes = useRegenerateRecoveryCodes({
    mutation: {
      onSuccess: (d) => setCodes(d.recovery_codes),
      onError: (e) => message.error(errText(e)),
    },
  });

  const activeAdmins = (data ?? []).filter((a) => a.role === "admin" && a.status === "active").length;

  const columns = [
    { title: t("admins.colUsername"), dataIndex: "username", key: "username", width: 160 },
    {
      title: t("admins.colRole"),
      dataIndex: "role",
      key: "role",
      width: 110,
      render: (role: Role) => <Tag color={roleColor(role)}>{t(ROLE_LABEL_KEY[role])}</Tag>,
    },
    {
      title: t("admins.colStatus"),
      dataIndex: "status",
      key: "status",
      width: 90,
      render: (s: string) => (
        <Tag color={s === "active" ? adminColors.positive : adminColors.textMuted}>
          {s === "active" ? t("admins.statusActive") : t("admins.statusDisabled")}
        </Tag>
      ),
    },
    {
      title: (
        <Space size={6}>
          {t("admins.colMfa")}
          {!mfaEnabled && <Tag color="orange">{t("admins.mfaDisabledTag")}</Tag>}
        </Space>
      ),
      key: "mfa",
      // 开关开启时四个角色一律强制 TOTP;关闭时列头标「已关闭」,行内仍只区分已绑定/待绑定
      render: (_: unknown, row: AdminAccountOut) =>
        row.totp_enabled ? (
          <Tag color={adminColors.positive}>{t("admins.mfaBound")}</Tag>
        ) : (
          <Tag color="gold">{t("admins.mfaUnbound")}</Tag>
        ),
    },
    {
      title: t("admins.colCreatedAt"),
      dataIndex: "created_at",
      key: "created_at",
      width: 170,
      render: (v: string) => formatDateTime(v),
    },
    {
      title: t("admins.colActions"),
      key: "actions",
      // 操作列 = 角色 Select + 3~4 个按钮,全站最挤的单元格:定宽 + wrap,窄屏经表格横滚保住完整可点
      width: 420,
      render: (_: unknown, row: AdminAccountOut) => {
        const isSelf = row.id === me?.id;
        const noPerm = !isSuperAdmin ? t("admins.superAdminOnly") : undefined;
        const selfNote = isSelf ? t("admins.cannotChangeSelf") : undefined;
        return (
          <Space size={4} wrap>
            <Select
              size="small"
              style={{ width: 110 }}
              value={row.role as Role}
              disabled={!isSuperAdmin || isSelf}
              options={ALL_ROLES.map((r) => ({ value: r, label: t(ROLE_LABEL_KEY[r]) }))}
              onChange={(role: Role) => {
                // 改角色必须手输原因(入审计):弹表单收集
                setRoleTarget({ row, role });
              }}
            />
            <ReasonAction
              label={row.status === "active" ? t("admins.disable") : t("admins.enable")}
              title={t("admins.confirmStatusTitle", { name: row.username })}
              confirmText={
                row.status === "active" ? t("admins.disableConfirm", { name: row.username }) : t("admins.enableConfirm", { name: row.username })
              }
              danger={row.status === "active"}
              disabled={!isSuperAdmin || isSelf}
              // 禁用时 noPerm/selfNote 必有一个(disabled 条件即两者之一);兜底与 noPerm 同文案
              disabledReason={noPerm ?? selfNote ?? t("admins.superAdminOnly")}
              onSubmit={async (reason) => {
                await update.mutateAsync({
                  id: row.id,
                  data: { status: row.status === "active" ? "disabled" : "active", reason },
                });
                refresh();
              }}
            />
            <Button
              size="small"
              disabled={!isSuperAdmin}
              onClick={() => {
                pwdForm.resetFields();
                setPwdTarget(row);
              }}
            >
              {t("admins.resetPassword")}
            </Button>
            {row.totp_enabled && (
              <ReasonAction
                label={t("admins.resetMfa")}
                title={t("admins.resetMfaTitle", { name: row.username })}
                confirmText={t("admins.resetMfaConfirm")}
                danger
                disabled={!isSuperAdmin || isSelf}
                disabledReason={noPerm ?? selfNote ?? t("admins.superAdminOnly")}
                onSubmit={async (reason) => {
                  await resetMfa.mutateAsync({ id: row.id, reason });
                  message.success(t("admins.resetMfaDone"));
                  refresh();
                }}
              />
            )}
          </Space>
        );
      },
    },
  ];

  return (
    <Card
      variant="borderless"
      styles={{ body: { padding: 0 } }}
      extra={
        <Space>
          {(data ?? []).find((a) => a.id === me?.id)?.totp_enabled && (
            <Button
              onClick={() =>
                // 重新生成后旧恢复码立即失效,先复述后果再执行
                modal.confirm({
                  title: t("admins.regenCodesConfirmTitle"),
                  content: t("admins.regenCodesConfirmDesc"),
                  okText: t("admins.regenCodes"),
                  onOk: () => regenCodes.mutate(),
                })
              }
              loading={regenCodes.isPending}
            >
              {t("admins.regenCodes")}
            </Button>
          )}
          <Button onClick={() => { selfForm.resetFields(); setSelfOpen(true); }}>
            {t("admins.changeOwnPassword")}
          </Button>
          <Button type="primary" disabled={!isSuperAdmin} onClick={() => { createForm.resetFields(); setCreateOpen(true); }}>
            {t("admins.create")}
          </Button>
        </Space>
      }
    >
      {activeAdmins < 2 && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          title={t("admins.needSecondAdmin")}
          description={t("admins.needSecondAdminDetail")}
        />
      )}
      {!isSuperAdmin && <Alert type="info" showIcon style={{ marginBottom: 12 }} title={t("admins.superAdminOnly")} />}
      <Table<AdminAccountOut>
        rowKey="id"
        size="small"
        loading={isLoading}
        scroll={{ x: 1040 }}
        locale={{
          emptyText: (
            <TableErrorEmpty
              isError={isError}
              isForbidden={isApiError(error) && error.status === 403}
              onRetry={() => void refetch()}
            />
          ),
        }}
        dataSource={data ?? []}
        columns={columns}
        pagination={false}
      />

      {roleTarget && (
        <RowActionModal
          title={t("admins.confirmRoleTitle", {
            name: roleTarget.row.username,
            role: t(ROLE_LABEL_KEY[roleTarget.role]),
          })}
          okText={t("admins.confirmRoleOk")}
          note={t("admins.roleTakesEffectNow")}
          form={roleForm}
          submit={async (values) => {
            await update.mutateAsync({
              id: roleTarget.row.id,
              data: { role: roleTarget.role, reason: values.reason },
            });
          }}
          successText={t("admins.updated")}
          failText={t("admins.updateFailed")}
          onClose={() => setRoleTarget(null)}
          onDone={() => void refresh()}
        >
          <Form.Item
            name="reason"
            label={t("admins.reason")}
            rules={[{ required: true, min: 2, max: REASON_MAX_LEN, message: t("common.reasonRule") }]}
          >
            <Input.TextArea rows={2} maxLength={REASON_MAX_LEN} showCount placeholder={t("admins.reasonPlaceholder")} />
          </Form.Item>
        </RowActionModal>
      )}

      <Modal
        open={codes != null}
        title={t("admins.regenCodesTitle")}
        footer={
          <Button type="primary" onClick={() => setCodes(null)}>
            {t("admins.regenCodesClose")}
          </Button>
        }
        onCancel={() => setCodes(null)}
      >
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          title={t("admins.regenCodesHint")}
        />
        <Card size="small">
          <Typography.Text code copyable={{ text: (codes ?? []).join("\n") }}>
            {t("login.recoveryCopy")}
          </Typography.Text>
          <pre style={{ margin: "8px 0 0", fontSize: fontSize.caption, lineHeight: 1.8 }}>
            {(codes ?? []).join("\n")}
          </pre>
        </Card>
      </Modal>

      <Modal
        open={createOpen}
        title={t("admins.create")}
        okText={t("admins.create")}
        onCancel={() => setCreateOpen(false)}
        onOk={async () => {
          const v = await createForm.validateFields();
          try {
            await create.mutateAsync({ data: { username: v.username, password: v.password, role: v.role, reason: v.reason } });
            message.success(t("admins.created"));
            setCreateOpen(false);
            refresh();
          } catch (e) {
            message.error(errText(e));
          }
        }}
      >
        <Form form={createForm} layout="vertical">
          <Form.Item
            name="username"
            label={t("admins.colUsername")}
            rules={[{ required: true, pattern: /^[a-zA-Z0-9._-]{3,64}$/, message: t("admins.usernameRule") }]}
          >
            <Input autoComplete="off" />
          </Form.Item>
          <Form.Item
            name="password"
            label={t("admins.password")}
            rules={[{ required: true, min: MIN_PASSWORD, message: t("admins.passwordRule", { min: MIN_PASSWORD }) }]}
          >
            <Input.Password autoComplete="new-password" />
          </Form.Item>
          <Form.Item name="role" label={t("admins.colRole")} initialValue="ops" rules={[{ required: true }]}>
            <Select options={ALL_ROLES.map((r) => ({ value: r, label: t(ROLE_LABEL_KEY[r]) }))} />
          </Form.Item>
          <Form.Item name="reason" label={t("admins.reason")} rules={[{ required: true, min: 2, max: REASON_MAX_LEN }]}>
            <Input.TextArea rows={2} placeholder={t("admins.reasonPlaceholder")} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        open={pwdTarget != null}
        title={t("admins.resetPasswordFor", { name: pwdTarget?.username ?? "" })}
        okText={t("admins.resetPassword")}
        okButtonProps={{ danger: true }}
        onCancel={() => setPwdTarget(null)}
        onOk={async () => {
          const v = await pwdForm.validateFields();
          if (!pwdTarget) return;
          try {
            await resetPwd.mutateAsync({ id: pwdTarget.id, data: { password: v.password, reason: v.reason } });
            message.success(t("admins.passwordReset"));
            setPwdTarget(null);
            refresh();
          } catch (e) {
            message.error(errText(e));
          }
        }}
      >
        <Alert type="warning" showIcon style={{ marginBottom: 12 }} title={t("admins.resetKicksSessions")} />
        <Form form={pwdForm} layout="vertical">
          <Form.Item
            name="password"
            label={t("admins.newPassword")}
            rules={[{ required: true, min: MIN_PASSWORD, message: t("admins.passwordRule", { min: MIN_PASSWORD }) }]}
          >
            <Input.Password autoComplete="new-password" />
          </Form.Item>
          <Form.Item name="reason" label={t("admins.reason")} rules={[{ required: true, min: 2, max: REASON_MAX_LEN }]}>
            <Input.TextArea rows={2} placeholder={t("admins.reasonPlaceholder")} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        open={selfOpen}
        title={t("admins.changeOwnPassword")}
        okText={t("admins.changeOwnPassword")}
        onCancel={() => setSelfOpen(false)}
        onOk={async () => {
          const v = await selfForm.validateFields();
          try {
            await changeOwn.mutateAsync({ data: v });
            message.success(t("admins.ownPasswordChanged"));
            setSelfOpen(false);
            // 改密会撤销全部在外会话(含当前这个),登出后回登录页重登
            logout();
            void navigate({ to: "/login" });
          } catch (e) {
            message.error(errText(e));
          }
        }}
      >
        <Alert type="info" showIcon style={{ marginBottom: 12 }} title={t("admins.selfChangeKicksSessions")} />
        <Form form={selfForm} layout="vertical">
          <Form.Item name="current_password" label={t("admins.currentPassword")} rules={[{ required: true }]}>
            <Input.Password autoComplete="current-password" />
          </Form.Item>
          <Form.Item
            name="new_password"
            label={t("admins.newPassword")}
            rules={[{ required: true, min: MIN_PASSWORD, message: t("admins.passwordRule", { min: MIN_PASSWORD }) }]}
          >
            <Input.Password autoComplete="new-password" />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}
