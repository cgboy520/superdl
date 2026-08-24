/** 账户设置:SSH 公钥管理 / 通知阈值(保存按钮) / 账号(实名、登录密码、登出、注销)。 */

import type { TokenPair } from "@superdl/api-client";
import { deletionStatusMap, formatDateTime, maskPhone, metaOf } from "@superdl/ui";
import { createFileRoute, Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import {
  App,
  Alert,
  Button,
  Card,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Skeleton,
  Space,
  Table,
  Tag,
  Typography,
} from "antd";
import { useEffect, useRef, useState } from "react";

import {
  useAddSshKey,
  useCancelDeletionRequest,
  useCreateDeletionRequest,
  useDeleteSshKey,
  useLogout,
  useLogoutAll,
  useResetPassword,
  useSendSmsCode,
  useSetWarnThreshold,
  useSubmitRealName,
} from "../api/mutations";
import { useMe, useMyDeletionRequest, useSshKeys } from "../api/queries";
import { DataErrorAlert, TableErrorEmpty } from "../components/QueryState";
import { useFormat } from "../lib/format";
import { requireAuth } from "../lib/guard";
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
  const { data: keys, isLoading, isError, refetch } = useSshKeys();
  const [form] = Form.useForm();
  const [warnHours, setWarnHours] = useState<number>();
  const [pwdOpen, setPwdOpen] = useState(false);
  const logout = useLogout();
  const logoutAll = useLogoutAll();

  const addKey = useAddSshKey({
    onSuccess: () => {
      message.success(t("create.keyAdded"));
      form.resetFields();
    },
  });
  const delKey = useDeleteSshKey();
  const setThreshold = useSetWarnThreshold({ onSuccess: () => message.success(t("settings.saved")) });

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
              emptyText: isError ? (
                <TableErrorEmpty onRetry={() => void refetch()} />
              ) : (
                t("settings.noKeys")
              ),
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
                  <Popconfirm
                    title={t("settings.deleteKeyConfirm")}
                    onConfirm={() => delKey.mutate(r.id)}
                  >
                    <Button size="small" danger>
                      {t("storage.delete")}
                    </Button>
                  </Popconfirm>
                ),
              },
            ]}
          />
          <Form
            form={form}
            layout="vertical"
            onFinish={(v: { name: string; public_key: string }) => addKey.mutate(v)}
          >
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
              <Input.TextArea
                rows={3}
                placeholder="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5… you@host"
              />
            </Form.Item>
            <Button type="primary" htmlType="submit" loading={addKey.isPending}>
              {t("create.addKey")}
            </Button>
          </Form>
        </Space>
      </Card>

      <Card title={t("settings.notifyCard")}>
        <Space>
          <Typography.Text>{t("settings.warnThresholdLabel")}</Typography.Text>
          <InputNumber
            min={1}
            max={168}
            aria-label={t("settings.warnThresholdLabel")}
            value={warnHours ?? me?.low_balance_warn_hours}
            onChange={(v) => setWarnHours(v ?? undefined)}
            onPressEnter={() => {
              const v = warnHours ?? me?.low_balance_warn_hours;
              if (v != null) setThreshold.mutate(v);
            }}
          />
          <Button
            loading={setThreshold.isPending}
            onClick={() => {
              const v = warnHours ?? me?.low_balance_warn_hours;
              if (v != null) setThreshold.mutate(v);
            }}
          >
            {t("billing.save")}
          </Button>
          <Typography.Text type="secondary">{t("settings.warnThresholdHint")}</Typography.Text>
        </Space>
      </Card>

      <RealNameCard
        me={me}
        loading={meQ.isPending}
        error={meQ.isError}
        onRetry={() => void meQ.refetch()}
      />

      <Card title={t("settings.accountCard")}>
        <Space orientation="vertical" size={12}>
          <Typography.Text>{t("settings.phoneLine", { phone: me ? maskPhone(me.phone) : "" })}</Typography.Text>
          <Space>
            <Button onClick={() => setPwdOpen(true)}>{t("settings.changePassword")}</Button>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {t("settings.changePasswordHint")}
            </Typography.Text>
          </Space>
          <Space>
            <Popconfirm
              title={t("settings.logoutAllConfirm")}
              okText={t("settings.logoutAll")}
              onConfirm={() => void logoutAll()}
            >
              <Button danger>{t("settings.logoutAll")}</Button>
            </Popconfirm>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {t("settings.logoutAllHint")}
            </Typography.Text>
          </Space>
          <Button danger onClick={() => void logout()}>
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
function PasswordModal({
  open,
  phone,
  onClose,
}: {
  open: boolean;
  phone: string;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [form] = Form.useForm<{ sms_code: string; new_password: string }>();
  const [countdown, setCountdown] = useState(0);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  // 倒计时递减只走 updater 纯函数;清零与卸载的清 timer 都在 effect 里(StrictMode 双调安全)
  useEffect(() => {
    if (countdown <= 0 && timer.current) {
      clearInterval(timer.current);
      timer.current = null;
    }
  }, [countdown]);
  useEffect(
    () => () => {
      if (timer.current) clearInterval(timer.current);
    },
    [],
  );
  const sendCode = useSendSmsCode({
    onSuccess: () => {
      message.success(t("settings.codeSent"));
      if (timer.current) clearInterval(timer.current);
      setCountdown(60);
      timer.current = setInterval(() => setCountdown((c) => (c > 0 ? c - 1 : 0)), 1000);
    },
  });
  const reset = useResetPassword({
    onSuccess: (data) => {
      const pair = data as TokenPair;
      // 改密会撤销全部在外会话,本设备用返回的新 token 继续
      authStore.getState().login(pair.access_token, pair.refresh_token);
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
        void form.validateFields().then((v) =>
          reset.mutate({ phone, sms_code: v.sms_code, new_password: v.new_password }),
        );
      }}
      destroyOnHidden
    >
      <Form form={form} layout="vertical">
        <Typography.Paragraph type="secondary">
          {t("settings.changePasswordDesc", { phone })}
        </Typography.Paragraph>
        <Form.Item name="sms_code" rules={[{ required: true, message: t("settings.codeRequired") }]}>
          <Space.Compact style={{ width: "100%" }}>
            <Input
              placeholder={t("settings.codePlaceholder")}
              maxLength={6}
              autoComplete="one-time-code"
              aria-label={t("settings.codePlaceholder")}
            />
            <Button
              disabled={countdown > 0}
              loading={sendCode.isPending}
              onClick={() => sendCode.mutate({ phone, purpose: "reset_password" })}
            >
              {countdown > 0 ? `${countdown}s` : t("settings.getCode")}
            </Button>
          </Space.Compact>
        </Form.Item>
        <Form.Item
          name="new_password"
          rules={[{ required: true, min: 12, message: t("settings.passwordMin") }]}
        >
          <Input.Password placeholder={t("settings.newPasswordPlaceholder")} />
        </Form.Item>
      </Form>
    </Modal>
  );
}

/** 危险区·账号注销:申请(弹窗内联说明 + 键入手机号二次确认)/ 冷静期倒计时 + 撤销。 */
function DeletionZone({ phone }: { phone: string }) {
  const { t } = useTranslation(["web", "shared"]);
  const { message } = App.useApp();
  const { formatDaysLeft } = useFormat();
  const reqQ = useMyDeletionRequest();
  const req = reqQ.data;
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm<{ phone: string; reason: string }>();
  const create = useCreateDeletionRequest({
    onSuccess: () => {
      message.success(t("settings.deletion.submitted"));
      setOpen(false);
      form.resetFields();
    },
  });
  const cancel = useCancelDeletionRequest({
    onSuccess: () => message.success(t("settings.deletion.cancelled")),
  });

  const pending = req?.status === "pending";
  const statusMeta = req ? metaOf(deletionStatusMap, req.status) : undefined;
  const countdown = pending ? formatDaysLeft(req.requested_at, 7) : null;

  return (
    <Space orientation="vertical" size={8} style={{ width: "100%" }}>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
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
            <Popconfirm
              title={t("settings.deletion.cancelConfirm")}
              okText={t("settings.deletion.cancel")}
              onConfirm={() => cancel.mutate()}
            >
              <Button size="small" loading={cancel.isPending}>
                {t("settings.deletion.cancel")}
              </Button>
            </Popconfirm>
          }
        />
      ) : (
        <Space orientation="vertical" size={4}>
          {req && statusMeta && (
            <Space size={8}>
              <Tag color={statusMeta.color}>{t(statusMeta.labelKey)}</Tag>
              {req.status === "rejected" && req.note && (
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
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

      <Modal
        title={t("settings.deletion.modalTitle")}
        open={open}
        onCancel={() => setOpen(false)}
        okText={t("settings.deletion.confirmText")}
        okButtonProps={{ danger: true }}
        confirmLoading={create.isPending}
        onOk={() => {
          void form.validateFields().then((v) =>
            create.mutate({ phone: v.phone.trim(), reason: v.reason.trim() }),
          );
        }}
        destroyOnHidden
      >
        <Form form={form} layout="vertical">
          <Alert
            type="error"
            showIcon
            title={t("settings.deletion.notesTitle")}
            style={{ marginBottom: 16 }}
            description={
              <>
                <ul style={{ margin: 0, paddingInlineStart: 20 }}>
                  <li>{t("settings.deletion.noteResources")}</li>
                  <li>{t("settings.deletion.noteBalance")}</li>
                  <li>{t("settings.deletion.noteAnonymize")}</li>
                  <li>{t("settings.deletion.noteLedger")}</li>
                  <li>{t("settings.deletion.noteCooldown")}</li>
                </ul>
                <Link to="/legal/deletion-notice" target="_blank" style={{ fontSize: 12 }}>
                  {t("settings.deletion.viewFullNotice")}
                </Link>
              </>
            }
          />
          <Form.Item
            name="reason"
            label={t("settings.deletion.reasonLabel")}
            rules={[{ required: true, min: 2, message: t("settings.deletion.reasonRequired") }]}
          >
            <Input.TextArea rows={2} maxLength={256} />
          </Form.Item>
          <Form.Item
            name="phone"
            label={t("settings.deletion.phoneLabel")}
            rules={[
              { required: true, message: t("settings.deletion.phoneRequired") },
              {
                validator: (_, v: string) =>
                  v?.trim() === phone
                    ? Promise.resolve()
                    : Promise.reject(new Error(t("settings.deletion.phoneMismatch"))),
              },
            ]}
          >
            <Input placeholder={phone} maxLength={11} autoComplete="off" />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  );
}

/** 实名卡四态:未就绪骨架 / 错误可重试(绝不把「没查到」渲染成「未认证」) / 已认证 / 未认证。 */
function RealNameCard({
  me,
  loading,
  error,
  onRetry,
}: {
  me: { verification_status?: string } | undefined;
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
            {t("settings.realNameNote")}
          </Typography.Text>
          <Form
            form={form}
            layout="inline"
            onFinish={(v) => submit.mutate({ name: v.name, id_number: v.id_number })}
          >
            <Form.Item
              name="name"
              rules={[{ required: true, min: 2, message: t("settings.realNameNameRule") }]}
            >
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
            <Button type="primary" htmlType="submit" loading={submit.isPending}>
              {t("settings.submitVerify")}
            </Button>
          </Form>
        </Space>
      )}
    </Card>
  );
}
