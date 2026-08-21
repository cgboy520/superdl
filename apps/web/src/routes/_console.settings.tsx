/** 账户设置:SSH 公钥管理 / 通知阈值(保存按钮) / 账号(实名、登录密码、登出)。 */

import { TableErrorEmpty } from "../components/QueryState";
import type { TokenPair } from "@superdl/api-client";
import { formatDateTime } from "@superdl/ui";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import {
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
  Tag,
  Typography,
} from "antd";
import { useState } from "react";

import {
  useAddSshKey,
  useDeleteSshKey,
  useResetPassword,
  useSendSmsCode,
  useSetWarnThreshold,
  useSubmitRealName,
} from "../api/mutations";
import { useMe, useSshKeys } from "../api/queries";
import { requireAuth } from "../lib/guard";
import { authStore } from "../stores/auth";

export const Route = createFileRoute("/_console/settings")({
  beforeLoad: requireAuth,
  component: SettingsPage,
});

function SettingsPage() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const navigate = useNavigate();
  const { data: me } = useMe();
  const { data: keys, isLoading, isError, refetch } = useSshKeys();
  const [form] = Form.useForm();
  const [warnHours, setWarnHours] = useState<number>();
  const [pwdOpen, setPwdOpen] = useState(false);

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

      <RealNameCard verified={me?.verification_status === "verified"} />

      <Card title={t("settings.accountCard")}>
        <Space orientation="vertical" size={12}>
          <Typography.Text>{t("settings.phoneLine", { phone: me?.phone ?? "" })}</Typography.Text>
          <Space>
            <Button onClick={() => setPwdOpen(true)}>{t("settings.changePassword")}</Button>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {t("settings.changePasswordHint")}
            </Typography.Text>
          </Space>
          <Button
            danger
            onClick={() => {
              authStore.getState().logout();
              void navigate({ to: "/login" });
            }}
          >
            {t("settings.logout")}
          </Button>
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
  const sendCode = useSendSmsCode({
    onSuccess: () => {
      message.success(t("settings.codeSent"));
      setCountdown(60);
      const timer = setInterval(
        () => setCountdown((c) => (c <= 1 ? (clearInterval(timer), 0) : c - 1)),
        1000,
      );
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
            <Input placeholder={t("settings.codePlaceholder")} maxLength={6} />
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
          rules={[{ required: true, min: 8, message: t("settings.passwordMin") }]}
        >
          <Input.Password placeholder={t("settings.newPasswordPlaceholder")} />
        </Form.Item>
      </Form>
    </Modal>
  );
}

function RealNameCard({ verified }: { verified: boolean }) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [form] = Form.useForm<{ name: string; id_number: string }>();
  const submit = useSubmitRealName({
    onSuccess: () => message.success(t("settings.realNameDone")),
  });
  return (
    <Card
      title={
        <Space size={8}>
          {t("settings.realNameCard")}
          <Tag color={verified ? "green" : "orange"}>{verified ? t("settings.verified") : t("settings.unverified")}</Tag>
        </Space>
      }
    >
      {verified ? (
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
              <Input placeholder={t("settings.realNamePlaceholder")} style={{ width: 160 }} />
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
              <Input placeholder={t("settings.idNumberPlaceholder")} style={{ width: 220 }} maxLength={18} />
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
