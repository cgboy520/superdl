/** 账户设置:SSH 公钥管理 / 通知阈值(保存按钮) / 账号(实名、登录密码、登出、注销)。 */

import type { TokenPairOut } from "@superdl/api-client";
import { deletionStatusMap, fontSize, formatDateTime, maskPhone, metaOf } from "@superdl/ui";
import { DataErrorAlert, TableErrorEmpty, TypeConfirmModal, useConfirm } from "@superdl/ui/components";
import { createFileRoute, Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { App, Alert, Button, Card, Form, Input, Modal, Skeleton, Space, Table, Tag, Tooltip, Typography } from "antd";
import { useState } from "react";

import {
  useAddSshKey,
  useCancelDeletionRequest,
  useCreateDeletionRequest,
  useDeleteSshKey,
  useLogout,
  useResetPassword,
  useSubmitRealName,
} from "../api/mutations";
import { useMe, useMyDeletionRequest, usePolicies, useSshKeys } from "../api/queries";
import { WarnThresholdField } from "../components/WarnThresholdField";
import { useFormat } from "@superdl/ui";
import { requireAuth } from "../lib/guard";
import { useHashScroll } from "../lib/useHashScroll";
import { SmsCodeField } from "../components/SmsCodeField";
import { useSmsCode } from "../lib/useSmsCode";
import { authStore } from "../stores/auth";

export const Route = createFileRoute("/_console/settings")({
  beforeLoad: requireAuth,
  component: SettingsPage,
});

function SettingsPage() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const meQ = useMe();
  const { data: me } = meQ;
  const { data: policies } = usePolicies();
  const { data: keys, isLoading, isError, refetch } = useSshKeys();
  const [form] = Form.useForm();
  const [pwdOpen, setPwdOpen] = useState(false);
  const logout = useLogout();
  const confirm = useConfirm();

  const addKey = useAddSshKey({
    onSuccess: () => {
      message.success(t("create.keyAdded"));
      form.resetFields();
    },
  });
  const delKey = useDeleteSshKey();
  // /settings#ssh 深链:滚动到 SSH 卡并高亮 2s
  useHashScroll({ highlight: true });

  return (
    <Space orientation="vertical" size={16} style={{ width: "100%" }}>
      <Typography.Title level={4} style={{ margin: 0 }}>
        {t("settings.title")}
      </Typography.Title>

      <Card
        id="ssh"
        title={t("settings.sshCard")}
        extra={<Typography.Text type="secondary">{t("copy.sshKeyOnly")}</Typography.Text>}
      >
        <Space orientation="vertical" size={16} style={{ width: "100%" }}>
          <Table
            rowKey="id"
            size="small"
            loading={isLoading}
            pagination={false}
            scroll={{ x: 640 }}
            dataSource={keys ?? []}
            locale={{
              emptyText: isError ? <TableErrorEmpty isError onRetry={() => void refetch()} /> : t("settings.noKeys"),
            }}
            columns={[
              { title: t("storage.nameLabel"), dataIndex: "name" },
              {
                title: t("settings.colFingerprint"),
                render: (_, r) => <Typography.Text code>{r.fingerprint}</Typography.Text>,
              },
              { title: t("settings.colAddedAt"), render: (_, r) => formatDateTime(r.created_at) },
              {
                title: t("storage.colActions"),
                render: (_, r) => (
                  // L1 确认(可逆、影响面 = 1),危险按钮配红色确认
                  <Button
                    size="small"
                    danger
                    onClick={() =>
                      confirm({
                        title: t("settings.deleteKeyConfirm"),
                        consequences: [t("settings.deleteKeyBody", { name: r.name })],
                        okText: t("storage.delete"),
                        danger: true,
                        onOk: async () => {
                          await delKey.mutateAsync(r.id);
                        },
                      })
                    }
                  >
                    {t("storage.delete")}
                  </Button>
                ),
              },
            ]}
          />
          <Form form={form} layout="vertical" onFinish={(v: { name: string; public_key: string }) => addKey.mutate(v)}>
            <Form.Item
              name="name"
              label={t("storage.nameLabel")}
              rules={[{ required: true, message: t("settings.keyNameHint") }]}
            >
              <Input style={{ width: 240 }} maxLength={64} />
            </Form.Item>
            <Form.Item
              name="public_key"
              label={t("settings.keyContentLabel")}
              rules={[
                { required: true, message: t("settings.keyContentRequired") },
                {
                  pattern: /^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(256|384|521))\s+\S+/,
                  message: t("settings.keyFormatHint"),
                },
              ]}
            >
              <Input.TextArea rows={3} placeholder="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5… you@host" />
            </Form.Item>
            <Button type="primary" htmlType="submit" loading={addKey.isPending}>
              {t("create.addKey")}
            </Button>
          </Form>
        </Space>
      </Card>

      <Card title={t("settings.notifyCard")}>
        <WarnThresholdField />
      </Card>

      <RealNameCard
        me={me}
        enabled={policies?.real_name_enabled ?? false}
        loading={meQ.isPending}
        error={meQ.isError}
        onRetry={() => void meQ.refetch()}
      />

      <Card title={t("settings.accountCard")}>
        <Space orientation="vertical" size={12}>
          {me ? (
            <Typography.Text>{t("settings.phoneLine", { phone: maskPhone(me.phone) })}</Typography.Text>
          ) : (
            <Skeleton.Input active size="small" style={{ width: 200 }} />
          )}
          <Space>
            <Button onClick={() => setPwdOpen(true)}>{t("settings.changePassword")}</Button>
            <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
              {t("settings.changePasswordHint")}
            </Typography.Text>
          </Space>
          <Space>
            <Button
              danger
              onClick={() =>
                confirm({
                  title: t("settings.logoutAllConfirm"),
                  consequences: [t("settings.logoutAllBody")],
                  okText: t("settings.logoutAll"),
                  danger: true,
                  onOk: () => logout("all"),
                })
              }
            >
              {t("settings.logoutAll")}
            </Button>
            <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
              {t("settings.logoutAllHint")}
            </Typography.Text>
          </Space>
          <Button
            danger
            onClick={() =>
              confirm({
                title: t("settings.logoutConfirm"),
                consequences: [t("settings.logoutBody")],
                okText: t("settings.logout"),
                danger: true,
                onOk: () => logout(),
              })
            }
          >
            {t("settings.logout")}
          </Button>
          <DeletionZone phone={me?.phone ?? ""} />
        </Space>
      </Card>
      <PasswordModal open={pwdOpen} phone={me?.phone ?? ""} onClose={() => setPwdOpen(false)} />
    </Space>
  );
}

/** 设置/修改密码:凭手机号 + 验证码,不要求旧密码。 */
function PasswordModal({ open, phone, onClose }: { open: boolean; phone: string; onClose: () => void }) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [form] = Form.useForm<{ sms_code: string; new_password: string }>();
  const sms = useSmsCode("reset_password", t("settings.codeSent"));
  const reset = useResetPassword({
    onSuccess: (data) => {
      const pair = data as TokenPairOut;
      // 改密撤销全部在外会话,本设备用返回的新 token 继续
      authStore.getState().login(pair.access_token);
      message.success(t("settings.passwordChanged"));
      form.resetFields();
      onClose();
    },
  });

  return (
    <Modal
      title={t("settings.changePassword")}
      open={open}
      onCancel={onClose}
      okText={t("settings.savePassword")}
      confirmLoading={reset.isPending}
      onOk={() => {
        void form
          .validateFields()
          .then((v) => reset.mutate({ phone, sms_code: v.sms_code, new_password: v.new_password }));
      }}
      destroyOnHidden
    >
      <Form form={form} layout="vertical">
        <Typography.Paragraph type="secondary">{t("settings.changePasswordDesc", { phone })}</Typography.Paragraph>
        <SmsCodeField
          sms={sms}
          placeholder={t("settings.codePlaceholder")}
          requiredMessage={t("settings.codeRequired")}
          getCodeLabel={t("settings.getCode")}
          onSend={() => sms.send(phone)}
        />
        <Form.Item name="new_password" rules={[{ required: true, min: 12, message: t("settings.passwordMin") }]}>
          <Input.Password placeholder={t("settings.newPasswordPlaceholder")} />
        </Form.Item>
      </Form>
    </Modal>
  );
}

/** 危险区·账号注销:申请(原因必填 + 键入手机号,走共享 TypeConfirmModal)/ 冷静期倒计时 + 撤销。 */
function DeletionZone({ phone }: { phone: string }) {
  const { t } = useTranslation(["web", "shared"]);
  const { message } = App.useApp();
  const { formatDaysUntil } = useFormat();
  const reqQ = useMyDeletionRequest();
  const req = reqQ.data;
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const close = () => {
    setOpen(false);
    setReason("");
  };
  const create = useCreateDeletionRequest({
    onSuccess: () => {
      message.success(t("settings.deletion.submitted"));
      close();
    },
  });
  const cancel = useCancelDeletionRequest({
    onSuccess: () => message.success(t("settings.deletion.cancelled")),
  });
  const confirm = useConfirm();

  const pending = req?.status === "pending";
  const statusMeta = req ? metaOf(deletionStatusMap, req.status) : undefined;
  // 冷静期截止由服务端给出(cooldown_ends_at)
  const countdown = pending ? formatDaysUntil(req.cooldown_ends_at) : null;

  return (
    <Space orientation="vertical" size={8} style={{ width: "100%" }}>
      <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
        {t("settings.deletion.dangerZone")}
      </Typography.Text>
      {pending && req ? (
        <Alert
          type="warning"
          showIcon
          title={t("settings.deletion.pendingLine", {
            countdown: countdown ?? "",
            date: formatDateTime(req.cooldown_ends_at),
          })}
          action={
            <Button
              size="small"
              loading={cancel.isPending}
              onClick={() =>
                confirm({
                  title: t("settings.deletion.cancelConfirm"),
                  consequences: [t("settings.deletion.cancelBody")],
                  okText: t("settings.deletion.cancel"),
                  onOk: async () => {
                    await cancel.mutateAsync();
                  },
                })
              }
            >
              {t("settings.deletion.cancel")}
            </Button>
          }
        />
      ) : (
        <Space orientation="vertical" size={4}>
          {req && statusMeta && (
            <Space size={8}>
              <Tag color={statusMeta.color}>{t(statusMeta.labelKey)}</Tag>
              {req.status === "rejected" && req.note && (
                <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
                  {t("settings.deletion.rejectedLine", { note: req.note })}
                </Typography.Text>
              )}
            </Space>
          )}
          <Button danger onClick={() => setOpen(true)}>
            {t("settings.deletion.apply")}
          </Button>
        </Space>
      )}

      <TypeConfirmModal
        open={open}
        title={t("settings.deletion.modalTitle")}
        targetName={phone}
        maxLength={11}
        body={
          <Space orientation="vertical" size={12} style={{ width: "100%" }}>
            <Alert
              type="error"
              showIcon
              title={t("settings.deletion.notesTitle")}
              description={
                <>
                  <ul style={{ margin: 0, paddingInlineStart: 20 }}>
                    <li>{t("settings.deletion.noteResources")}</li>
                    <li>{t("settings.deletion.noteBalance")}</li>
                    <li>{t("settings.deletion.noteAnonymize")}</li>
                    <li>{t("settings.deletion.noteLedger")}</li>
                    <li>{t("settings.deletion.noteCooldown")}</li>
                  </ul>
                  <Link to="/legal/deletion-notice" target="_blank" style={{ fontSize: fontSize.caption }}>
                    {t("settings.deletion.viewFullNotice")}
                  </Link>
                </>
              }
            />
            <Space orientation="vertical" size={4} style={{ width: "100%" }}>
              <Typography.Text>{t("settings.deletion.reasonLabel")}</Typography.Text>
              <Input.TextArea
                rows={2}
                maxLength={256}
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                aria-label={t("settings.deletion.reasonLabel")}
              />
              {reason !== "" && reason.trim().length < 2 && (
                <Typography.Text type="danger" style={{ fontSize: fontSize.caption }}>
                  {t("settings.deletion.reasonRequired")}
                </Typography.Text>
              )}
            </Space>
          </Space>
        }
        extraDisabled={reason.trim().length < 2}
        confirmLabel={t("settings.deletion.confirmText")}
        cancelLabel={t("create.cancel")}
        loading={create.isPending}
        onConfirm={() => create.mutate({ phone, reason: reason.trim() })}
        onCancel={close}
      />
    </Space>
  );
}

/** 实名卡四态:未就绪骨架 / 错误可重试 / 已认证 / 未认证;平台未开通实名(real_name_enabled=false)时表单可见但禁用 + 说明。 */
function RealNameCard({
  me,
  enabled,
  loading,
  error,
  onRetry,
}: {
  me: { verification_status?: string } | undefined;
  enabled: boolean;
  loading: boolean;
  error: boolean;
  onRetry: () => void;
}) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [form] = Form.useForm<{ name: string; id_number: string }>();
  const submit = useSubmitRealName({
    onSuccess: () => message.success(t("settings.realNameDone")),
  });
  const verified = me?.verification_status === "verified";
  return (
    <Card
      title={
        <Space size={8}>
          {t("settings.realNameCard")}
          {!loading && !error && (
            <Tag color={verified ? "green" : "orange"}>
              {verified ? t("settings.verified") : t("settings.unverified")}
            </Tag>
          )}
        </Space>
      }
    >
      {loading ? (
        <Skeleton active paragraph={{ rows: 1 }} title={false} />
      ) : error ? (
        <DataErrorAlert onRetry={onRetry} />
      ) : verified ? (
        <Typography.Text type="secondary">{t("settings.realNameDoneNote")}</Typography.Text>
      ) : (
        <Space orientation="vertical" size={8} style={{ width: "100%" }}>
          <Typography.Text type="secondary">
            {enabled ? t("settings.realNameNote") : t("settings.realNameDisabled")}
          </Typography.Text>
          <Form
            form={form}
            layout="inline"
            disabled={!enabled}
            onFinish={(v) => submit.mutate({ name: v.name, id_number: v.id_number })}
          >
            <Form.Item name="name" rules={[{ required: true, min: 2, message: t("settings.realNameNameRule") }]}>
              <Input
                placeholder={t("settings.realNamePlaceholder")}
                aria-label={t("settings.realNamePlaceholder")}
                autoComplete="name"
                style={{ width: 160 }}
              />
            </Form.Item>
            <Form.Item
              name="id_number"
              rules={[
                {
                  required: true,
                  pattern: /^\d{17}[\dXx]$/,
                  message: t("settings.idNumberRule"),
                },
              ]}
            >
              <Input
                placeholder={t("settings.idNumberPlaceholder")}
                aria-label={t("settings.idNumberPlaceholder")}
                style={{ width: 220 }}
                maxLength={18}
              />
            </Form.Item>
            <Tooltip title={enabled ? "" : t("settings.realNameDisabled")}>
              <Button type="primary" htmlType="submit" loading={submit.isPending} disabled={!enabled}>
                {t("settings.submitVerify")}
              </Button>
            </Tooltip>
          </Form>
        </Space>
      )}
    </Card>
  );
}
