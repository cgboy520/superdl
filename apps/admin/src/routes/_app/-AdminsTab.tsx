/** 管理员账号:建号 / 改角色 / 停用 / 重置密码 + 自助改密。 */

import { adminColors, formatDateTime } from "@superdl/ui";
import { useQueryClient } from "@tanstack/react-query";
import { Alert, App, Button, Card, Form, Input, Modal, Select, Space, Table, Tag, Typography } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import type { AdminAccountOut } from "@superdl/api-client";

import {
  useAdminAccounts,
  useChangeOwnPassword,
  useCreateAdminAccount,
  useRegenerateRecoveryCodes,
  useResetAdminMfa,
  useResetAdminPassword,
  useUpdateAdminAccount,
} from "../../api";
import { ReasonAction } from "../../components/ReasonAction";
import { useApiErrorText } from "../../lib/apiError";
import { useAuth } from "../../stores/auth";

const ROLES = ["admin", "ops", "finance", "readonly"] as const;
type AdminRole = (typeof ROLES)[number];
const MIN_PASSWORD = 12;

// 角色文案复用 roles.* 目录(与顶栏角色 Tag 同源)
const ROLE_LABEL_KEY = {
  admin: "roles.admin",
  ops: "roles.ops",
  finance: "roles.finance",
  readonly: "roles.readonly",
} as const satisfies Record<AdminRole, string>;

function roleColor(role: string): string {
  if (role === "admin") return adminColors.critical;
  if (role === "readonly") return adminColors.textMuted;
  return adminColors.dataAccent;
}

export function AdminsTab() {
  const { t } = useTranslation();
  const errText = useApiErrorText();
  const { message, modal } = App.useApp();
  const qc = useQueryClient();
  const { admin: me, logout } = useAuth();
  const isSuperAdmin = me?.role === "admin";
  const { data, queryKey, isLoading } = useAdminAccounts();

  const [createOpen, setCreateOpen] = useState(false);
  const [pwdTarget, setPwdTarget] = useState<AdminAccountOut | null>(null);
  const [selfOpen, setSelfOpen] = useState(false);
  const [createForm] = Form.useForm<{ username: string; password: string; role: AdminRole; reason: string }>();
  const [pwdForm] = Form.useForm<{ password: string; reason: string }>();
  const [selfForm] = Form.useForm<{ current_password: string; new_password: string }>();

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
    { title: t("admins.colUsername"), dataIndex: "username", key: "username" },
    {
      title: t("admins.colRole"),
      dataIndex: "role",
      key: "role",
      render: (role: AdminRole) => <Tag color={roleColor(role)}>{t(ROLE_LABEL_KEY[role])}</Tag>,
    },
    {
      title: t("admins.colStatus"),
      dataIndex: "status",
      key: "status",
      render: (s: string) => (
        <Tag color={s === "active" ? adminColors.positive : adminColors.textMuted}>
          {s === "active" ? t("admins.statusActive") : t("admins.statusDisabled")}
        </Tag>
      ),
    },
    {
      title: t("admins.colMfa"),
      key: "mfa",
      render: (_: unknown, row: AdminAccountOut) => {
        // admin/finance 强制 TOTP;其余角色不启用,列显灰
        const required = row.role === "admin" || row.role === "finance";
        if (!required) return <Tag>{t("admins.mfaNotRequired")}</Tag>;
        return row.totp_enabled ? (
          <Tag color={adminColors.positive}>{t("admins.mfaBound")}</Tag>
        ) : (
          <Tag color="gold">{t("admins.mfaUnbound")}</Tag>
        );
      },
    },
    {
      title: t("admins.colCreatedAt"),
      dataIndex: "created_at",
      key: "created_at",
      render: (v: string) => formatDateTime(v),
    },
    {
      title: t("admins.colActions"),
      key: "actions",
      render: (_: unknown, row: AdminAccountOut) => {
        const isSelf = row.id === me?.id;
        const noPerm = !isSuperAdmin ? t("admins.superAdminOnly") : undefined;
        const selfNote = isSelf ? t("admins.cannotChangeSelf") : undefined;
        return (
          <Space size={4} wrap>
            <Select
              size="small"
              style={{ width: 110 }}
              value={row.role as AdminRole}
              disabled={!isSuperAdmin || isSelf}
              options={ROLES.map((r) => ({ value: r, label: t(ROLE_LABEL_KEY[r]) }))}
              onChange={(role: AdminRole) => {
                // 改角色必须带 reason:审计只记新值,不带原因就答不出「从什么改成什么」
                // 用 App.useApp() 的 modal 实例:静态 Modal.confirm 拿不到深色主题 token
                modal.confirm({
                  title: t("admins.confirmRoleTitle", { name: row.username, role: t(ROLE_LABEL_KEY[role]) }),
                  content: t("admins.roleTakesEffectNow"),
                  onOk: async () => {
                    try {
                      await update.mutateAsync({ id: row.id, data: { role, reason: t("admins.reasonRoleChange") } });
                      message.success(t("admins.updated"));
                      refresh();
                    } catch (e) {
                      message.error(errText(e));
                    }
                  },
                });
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
              disabledReason={noPerm ?? selfNote}
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
                disabledReason={noPerm ?? selfNote}
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
            <Button onClick={() => regenCodes.mutate()} loading={regenCodes.isPending}>
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
        dataSource={data ?? []}
        columns={columns}
        pagination={false}
      />

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
          <pre style={{ margin: "8px 0 0", fontSize: 13, lineHeight: 1.8 }}>
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
            <Select options={ROLES.map((r) => ({ value: r, label: t(ROLE_LABEL_KEY[r]) }))} />
          </Form.Item>
          <Form.Item name="reason" label={t("admins.reason")} rules={[{ required: true, min: 2, max: 200 }]}>
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
          <Form.Item name="reason" label={t("admins.reason")} rules={[{ required: true, min: 2, max: 200 }]}>
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
            // 改密会撤销全部在外会话(含当前这个),因此必须登出重登
            logout();
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
