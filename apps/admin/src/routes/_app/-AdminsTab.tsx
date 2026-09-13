/** 管理员账号:建号 / 改角色 / 停用 / 重置密码 + 自助改密。 */

import { adminColors, controlWidth, fontSize, formatDateTime, layout } from "@superdl/ui";
import {
  CopyButton,
  EmptyState,
  GatedButton,
  Mono,
  RowActions,
  RowMoreMenu,
  TableErrorEmpty,
  useConfirm,
} from "@superdl/ui/components";
import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { Alert, App, Button, Card, Form, Input, Modal, Select, Space, Table, Tag } from "antd";
import type { TableColumnsType } from "antd";
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
  const confirm = useConfirm();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { admin: me, logout } = useAuth();
  const isSuperAdmin = me?.role === "admin";
  const { data, queryKey, isLoading, isError, error, refetch } = useAdminAccounts();
  // admin_mfa_enabled 仅超管可读,读不到按「开启」处理
  const mfaEnabled =
    usePlatformConfig({ enabled: isSuperAdmin }).data?.items.find((i) => i.key === "admin_mfa_enabled")?.value !==
    "false";

  const [createOpen, setCreateOpen] = useState(false);
  const [pwdTarget, setPwdTarget] = useState<AdminAccountOut | null>(null);
  const [selfOpen, setSelfOpen] = useState(false);
  const [roleTarget, setRoleTarget] = useState<AdminAccountOut | null>(null);
  const [createForm] = Form.useForm<{ username: string; password: string; role: Role; reason: string }>();
  const [pwdForm] = Form.useForm<{ password: string; reason: string }>();
  const [selfForm] = Form.useForm<{ current_password: string; new_password: string }>();
  const [roleForm] = Form.useForm<{ role: Role; reason: string }>();
  // 弹窗标题回显当前选中的新角色
  // useWatch 在表单挂载前返回 undefined,antd 的类型没标出来
  const nextRole = Form.useWatch("role", roleForm) as Role | undefined;

  const refresh = () => void qc.invalidateQueries({ queryKey });
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

  const columns: TableColumnsType<AdminAccountOut> = [
    {
      title: t("admins.colUsername"),
      dataIndex: "username",
      key: "username",
      width: 160,
      fixed: "left",
      render: (v: string) => <Mono>{v}</Mono>,
    },
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
      // MFA 关闭时列头标「已关闭」,行内仍区分已绑定/待绑定
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
      fixed: "right",
      width: 180,
      render: (_: unknown, row: AdminAccountOut) => {
        const isSelf = row.id === me?.id;
        const noPerm = !isSuperAdmin ? t("admins.superAdminOnly") : undefined;
        const selfNote = isSelf ? t("admins.cannotChangeSelf") : undefined;
        return (
          <RowActions
            primary={
              <GatedButton
                size="small"
                reason={noPerm}
                onClick={() => {
                  pwdForm.resetFields();
                  setPwdTarget(row);
                }}
              >
                {t("admins.resetPassword")}
              </GatedButton>
            }
            more={
              <RowMoreMenu
                items={[
                  {
                    key: "role",
                    label: t("admins.changeRole"),
                    reason: noPerm ?? selfNote,
                    onClick: () => {
                      // 改角色是显式动作:选新角色 + 填原因(入审计)
                      roleForm.resetFields();
                      setRoleTarget(row);
                    },
                  },
                ]}
              >
                <ReasonAction
                  label={row.status === "active" ? t("admins.disable") : t("admins.enable")}
                  type="text"
                  target={row.username}
                  title={t("admins.confirmStatusTitle", { name: row.username })}
                  confirmText={
                    row.status === "active"
                      ? t("admins.disableConfirm", { name: row.username })
                      : t("admins.enableConfirm", { name: row.username })
                  }
                  danger={row.status === "active"}
                  disabled={!isSuperAdmin || isSelf}
                  // 禁用时 noPerm/selfNote 必有一个
                  disabledReason={noPerm ?? selfNote ?? t("admins.superAdminOnly")}
                  onSubmit={async (reason) => {
                    await update.mutateAsync({
                      id: row.id,
                      data: { status: row.status === "active" ? "disabled" : "active", reason },
                    });
                    refresh();
                  }}
                />
                {row.totp_enabled && (
                  <ReasonAction
                    label={t("admins.resetMfa")}
                    type="text"
                    target={row.username}
                    title={t("admins.resetMfaTitle", { name: row.username })}
                    confirmText={t("admins.resetMfaConfirm", { name: row.username })}
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
              </RowMoreMenu>
            }
          />
        );
      },
    },
  ];

  const submitCreate = async () => {
    let v: { username: string; password: string; role: Role; reason: string };
    try {
      v = await createForm.validateFields();
    } catch {
      return; // 校验失败:antd 已就地标红
    }
    try {
      await create.mutateAsync({
        data: { username: v.username, password: v.password, role: v.role, reason: v.reason },
      });
      message.success(t("admins.created"));
      setCreateOpen(false);
      refresh();
    } catch (e) {
      message.error(errText(e));
    }
  };

  const submitPwd = async () => {
    let v: { password: string; reason: string };
    try {
      v = await pwdForm.validateFields();
    } catch {
      return;
    }
    if (!pwdTarget) return;
    const target = pwdTarget;
    // 二次确认带目标:重置会踢掉该管理员全部登录态
    confirm({
      title: t("admins.resetPasswordConfirmTitle", { name: target.username }),
      consequences: [t("admins.resetKicksSessions")],
      okText: t("admins.resetPassword"),
      danger: true,
      onOk: async () => {
        try {
          await resetPwd.mutateAsync({ id: target.id, data: { password: v.password, reason: v.reason } });
          message.success(t("admins.passwordReset"));
          setPwdTarget(null);
          refresh();
        } catch (e) {
          message.error(errText(e));
        }
      },
    });
  };

  const submitSelf = async () => {
    let v: { current_password: string; new_password: string };
    try {
      v = await selfForm.validateFields();
    } catch {
      return;
    }
    try {
      await changeOwn.mutateAsync({ data: v });
      message.success(t("admins.ownPasswordChanged"));
      setSelfOpen(false);
      // 改密撤销全部会话,回登录页
      logout();
      void navigate({ to: "/login" });
    } catch (e) {
      message.error(errText(e));
    }
  };

  return (
    <Card
      variant="borderless"
      styles={{ body: { padding: 0 } }}
      extra={
        <Space>
          {(data ?? []).find((a) => a.id === me?.id)?.totp_enabled && (
            <Button
              onClick={() => {
                // 重新生成:旧恢复码立即失效
                modal.confirm({
                  title: t("admins.regenCodesConfirmTitle"),
                  content: t("admins.regenCodesConfirmDesc"),
                  okText: t("admins.regenCodes"),
                  onOk: () => regenCodes.mutate(),
                });
              }}
              loading={regenCodes.isPending}
            >
              {t("admins.regenCodes")}
            </Button>
          )}
          <Button
            onClick={() => {
              selfForm.resetFields();
              setSelfOpen(true);
            }}
          >
            {t("admins.changeOwnPassword")}
          </Button>
          <Button
            type="primary"
            disabled={!isSuperAdmin}
            onClick={() => {
              createForm.resetFields();
              setCreateOpen(true);
            }}
          >
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
        loading={isLoading}
        scroll={{ x: 1040 }}
        sticky={{ offsetHeader: layout.topBarHeight }}
        locale={{
          emptyText: isError ? (
            <TableErrorEmpty
              isError
              isForbidden={isApiError(error) && error.status === 403}
              onRetry={() => void refetch()}
            />
          ) : (
            <EmptyState scene="list" compact />
          ),
        }}
        dataSource={data ?? []}
        columns={columns}
        pagination={false}
      />

      {roleTarget && (
        <RowActionModal
          title={t("admins.confirmRoleTitle", {
            name: roleTarget.username,
            role: t(ROLE_LABEL_KEY[nextRole ?? (roleTarget.role as Role)]),
          })}
          okText={t("admins.confirmRoleOk")}
          note={t("admins.roleTakesEffectNow")}
          form={roleForm}
          submit={async (values) => {
            await update.mutateAsync({
              id: roleTarget.id,
              data: { role: values.role, reason: values.reason },
            });
          }}
          successText={t("admins.updated")}
          failText={t("admins.updateFailed")}
          onClose={() => setRoleTarget(null)}
          onDone={refresh}
        >
          <Form.Item
            name="role"
            label={t("admins.colRole")}
            initialValue={roleTarget.role}
            rules={[
              { required: true },
              // 同角色提交等于一次没意义的审计写入,就地拦下
              {
                validator: (_rule, value: Role) =>
                  value === roleTarget.role ? Promise.reject(new Error(t("admins.roleUnchanged"))) : Promise.resolve(),
              },
            ]}
          >
            <Select
              style={{ width: controlWidth.sm }}
              options={ALL_ROLES.map((r) => ({ value: r, label: t(ROLE_LABEL_KEY[r]) }))}
            />
          </Form.Item>
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
        <Alert type="warning" showIcon style={{ marginBottom: 12 }} title={t("admins.regenCodesHint")} />
        <Card size="small">
          <CopyButton text={(codes ?? []).join("\n")} label={t("login.recoveryCopy")} />
          <div style={{ marginTop: 8, fontSize: fontSize.caption, lineHeight: 1.8 }}>
            {(codes ?? []).map((c) => (
              <Mono block key={c}>
                {c}
              </Mono>
            ))}
          </div>
        </Card>
      </Modal>

      <Modal
        open={createOpen}
        title={t("admins.create")}
        okText={t("admins.create")}
        onCancel={() => setCreateOpen(false)}
        onOk={() => void submitCreate()}
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
        onOk={() => void submitPwd()}
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
        onOk={() => void submitSelf()}
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
