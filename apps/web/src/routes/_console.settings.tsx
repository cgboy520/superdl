/** Account settings: four tabs (SSH keys / notifications / identity verification / account), `?tab=`
 *  in the URL (whitelisted + replace); #ssh / #notify deep links land on their tab and scroll-highlight. */

import type { TokenPairOut, UserOut } from "@superdl/api-client";
import { deletionStatusMap, fontSize, formatDateTime, maskHandle, metaOf, space, useFormat } from "@superdl/ui";
import {
  DangerZone,
  DataErrorAlert,
  GatedButton,
  PageContainer,
  PhoneField,
  TableErrorEmpty,
  TypeConfirmModal,
  useConfirm,
} from "@superdl/ui/components";
import { createFileRoute, Link, useNavigate, useRouterState } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { App, Alert, Button, Card, Form, Input, Modal, Skeleton, Space, Table, Tabs, Tag, Typography } from "antd";
import { useState } from "react";

import {
  useAddSshKey,
  useCancelDeletionRequest,
  useConfirmHandle,
  useCreateDeletionRequest,
  useDeleteSshKey,
  useLogout,
  useRemovePhone,
  useResetPassword,
  useSubmitKyc,
} from "../api/mutations";
import { useMe, useMyDeletionRequest, usePolicies, useSiteConfig, useSshKeys } from "../api/queries";
import { WarnThresholdField } from "../components/WarnThresholdField";
import { requireAuth } from "../lib/guard";
import { useHashScroll } from "../lib/useHashScroll";
import { CodeField } from "../components/CodeField";
import { useVerificationCode } from "../lib/useVerificationCode";
import { authStore } from "../stores/auth";

export const SETTINGS_TABS = ["ssh", "notify", "realname", "account"] as const;
export type SettingsTab = (typeof SETTINGS_TABS)[number];

/** Tab whitelist: unknown values are stripped (the page falls back to ssh; e2e goes straight to /settings). */
export function settingsValidateSearch(search: Record<string, unknown>): { tab?: SettingsTab } {
  const tab = search.tab;
  if (typeof tab !== "string") return {};
  return (SETTINGS_TABS as readonly string[]).includes(tab) ? { tab: tab as SettingsTab } : {};
}

/** Legacy #ssh / #notify deep links (billing balance card "change") map to their tab. */
export function tabOfHash(hash: string): SettingsTab | undefined {
  const id = hash.replace(/^#/, "");
  if (id === "ssh") return "ssh";
  if (id === "notify") return "notify";
  return undefined;
}

export const Route = createFileRoute("/_console/settings")({
  beforeLoad: requireAuth,
  validateSearch: settingsValidateSearch,
  component: SettingsPage,
});

function SettingsPage() {
  const { t } = useTranslation();
  const { tab } = Route.useSearch();
  const navigate = useNavigate();
  const hash = useRouterState({ select: (s) => s.location.hash });
  const meQ = useMe();
  const { data: me } = meQ;
  const { data: policies } = usePolicies();
  const siteQ = useSiteConfig();
  const kycAvailable = kycTabAvailable(siteQ.isSuccess, siteQ.data?.kyc_form);
  useHashScroll({ highlight: true });
  const requested: SettingsTab = tab ?? tabOfHash(hash) ?? "ssh";
  const activeTab: SettingsTab = requested === "realname" && !kycAvailable ? "ssh" : requested;

  return (
    <PageContainer width="narrow" title={t("settings.title")}>
      <Tabs
        activeKey={activeTab}
        onChange={(k) => void navigate({ to: "/settings", search: { tab: k as SettingsTab }, replace: true })}
        items={[
          { key: "ssh", label: t("settings.sshCard"), children: <SshTab /> },
          {
            key: "notify",
            label: t("settings.notifyCard"),
            children: (
              <div id="notify">
                <WarnThresholdField />
              </div>
            ),
          },
          ...(kycAvailable
            ? [
                {
                  key: "realname",
                  label: t("settings.realNameCard"),
                  children: (
                    <KycTab
                      me={me}
                      enabled={policies?.real_name_enabled ?? false}
                      loading={meQ.isPending || siteQ.isPending}
                      error={meQ.isError || siteQ.isError}
                      onRetry={() => {
                        void meQ.refetch();
                        void siteQ.refetch();
                      }}
                    />
                  ),
                },
              ]
            : []),
          { key: "account", label: t("settings.accountCard"), children: <AccountTab me={me} /> },
        ]}
      />
    </PageContainer>
  );
}

/** The KYC tab disappears only when /site-config succeeded and reports no KYC form; while pending or
 *  failed it stays and shows the loading / retry state. */
function kycTabAvailable(siteLoaded: boolean, kycForm: string | null | undefined): boolean {
  if (!siteLoaded) return true;
  return Boolean(kycForm);
}

/** SSH keys tab: existing keys table (delete L1) + add form. */
function SshTab() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const { data: keys, isLoading, isError, refetch } = useSshKeys();
  const [form] = Form.useForm();
  const confirm = useConfirm();
  const addKey = useAddSshKey({
    onSuccess: () => {
      message.success(t("create.keyAdded"));
      form.resetFields();
    },
  });
  const delKey = useDeleteSshKey();

  return (
    <Card id="ssh" extra={<Typography.Text type="secondary">{t("copy.sshKeyOnly")}</Typography.Text>}>
      <Space orientation="vertical" size={space.lg} style={{ width: "100%" }}>
        {(keys ?? []).length === 0 && !isLoading && !isError ? (
          <Typography.Text type="secondary">{t("settings.noKeys")}</Typography.Text>
        ) : (
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
        )}
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
  );
}

/** Account tab: email / phone handles (add, change, remove phone) / set or change password /
 *  log out (L0) / log out everywhere (L2) / danger zone deletion. */
function AccountTab({ me }: { me: UserOut | undefined }) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [pwdOpen, setPwdOpen] = useState(false);
  const [handleKind, setHandleKind] = useState<"email" | "phone" | null>(null);
  const logout = useLogout();
  const confirm = useConfirm();
  const { data: site } = useSiteConfig();
  const phoneRequired = site?.phone_required ?? false;
  const removePhone = useRemovePhone({
    onSuccess: () => {
      message.success(t("settings.handleBound"));
    },
  });
  const primary = me?.email ?? me?.phone ?? "";

  return (
    <Space orientation="vertical" size={space.lg} style={{ width: "100%" }}>
      <Card>
        <Space orientation="vertical" size={space.md}>
          {me ? (
            <>
              <Space size={space.sm} wrap>
                <Typography.Text>
                  {me.email ? t("settings.emailLine", { email: maskHandle(me.email) }) : t("settings.noEmail")}
                </Typography.Text>
                <Button size="small" type="link" onClick={() => setHandleKind("email")}>
                  {me.email ? t("settings.changeEmail") : t("settings.addEmail")}
                </Button>
              </Space>
              <Space size={space.sm} wrap>
                <Typography.Text>
                  {me.phone ? t("settings.phoneLine", { phone: maskHandle(me.phone) }) : t("settings.noPhone")}
                </Typography.Text>
                <Button size="small" type="link" onClick={() => setHandleKind("phone")}>
                  {me.phone ? t("settings.changePhone") : t("settings.addPhone")}
                </Button>
                {me.phone && !phoneRequired && (
                  <Button
                    size="small"
                    type="link"
                    danger
                    loading={removePhone.isPending}
                    onClick={() =>
                      confirm({
                        title: t("settings.removePhoneConfirm"),
                        consequences: [t("settings.removePhoneBody")],
                        okText: t("settings.removePhone"),
                        danger: true,
                        onOk: async () => {
                          await removePhone.mutateAsync();
                        },
                      })
                    }
                  >
                    {t("settings.removePhone")}
                  </Button>
                )}
              </Space>
            </>
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
              onClick={() =>
                confirm({
                  title: t("settings.logoutAllConfirm"),
                  consequences: [t("settings.logoutAllBody")],
                  okText: t("settings.logoutAll"),
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
          <Button onClick={() => void logout()}>{t("settings.logout")}</Button>
        </Space>
      </Card>
      <DeletionZone handle={primary} />
      <PasswordModal open={pwdOpen} handle={primary} onClose={() => setPwdOpen(false)} />
      <HandleModal kind={handleKind} dialCodes={site?.phone_dial_codes ?? []} onClose={() => setHandleKind(null)} />
    </Space>
  );
}

/** Add or replace an email / phone: code to the new handle, then confirm. */
function HandleModal({
  kind,
  dialCodes,
  onClose,
}: {
  kind: "email" | "phone" | null;
  dialCodes: readonly string[];
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [form] = Form.useForm<{ handle: string; code: string }>();
  const codes = useVerificationCode("bind_handle", t("settings.codeSent"));
  const confirmHandle = useConfirmHandle({
    onSuccess: () => {
      message.success(t("settings.handleBound"));
      form.resetFields();
      onClose();
    },
  });
  const isEmail = kind === "email";
  return (
    <Modal
      title={isEmail ? t("settings.handleModalTitleEmail") : t("settings.handleModalTitlePhone")}
      open={kind !== null}
      onCancel={onClose}
      okText={t("settings.saveHandle")}
      confirmLoading={confirmHandle.isPending}
      onOk={() => {
        void form.validateFields().then((v) => confirmHandle.mutate({ handle: v.handle, code: v.code }));
      }}
      destroyOnHidden
    >
      <Form form={form} layout="vertical">
        <Form.Item
          name="handle"
          label={isEmail ? t("login.emailLabel") : t("login.phoneLabel")}
          rules={[
            isEmail
              ? { required: true, pattern: /^[^@\s]+@[^@\s]+\.[^@\s]+$/, message: t("login.emailInvalid") }
              : { required: true, message: t("login.phoneInvalid") },
          ]}
        >
          {isEmail ? (
            <Input placeholder={t("login.emailPlaceholder")} maxLength={254} autoComplete="email" inputMode="email" />
          ) : (
            <PhoneField
              dialCodes={dialCodes}
              placeholder={t("login.phonePlaceholder")}
              dialCodeLabel={t("login.dialCodeLabel")}
            />
          )}
        </Form.Item>
        <CodeField
          code={codes}
          placeholder={t("settings.codePlaceholder")}
          requiredMessage={t("settings.codeRequired")}
          getCodeLabel={t("settings.getCode")}
          onSend={() => {
            void form.validateFields(["handle"]).then(({ handle }) => codes.send(handle));
          }}
        />
      </Form>
    </Modal>
  );
}

/** Set / change the password with a code sent to the primary handle; no old password needed. */
function PasswordModal({ open, handle, onClose }: { open: boolean; handle: string; onClose: () => void }) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [form] = Form.useForm<{ code: string; new_password: string }>();
  const codes = useVerificationCode("reset_password", t("settings.codeSent"));
  const reset = useResetPassword({
    onSuccess: (data) => {
      const pair = data as TokenPairOut;
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
        void form.validateFields().then((v) => reset.mutate({ handle, code: v.code, new_password: v.new_password }));
      }}
      destroyOnHidden
    >
      <Form form={form} layout="vertical">
        <Typography.Paragraph type="secondary">
          {t("settings.changePasswordDesc", { handle: maskHandle(handle) })}
        </Typography.Paragraph>
        <CodeField
          code={codes}
          placeholder={t("settings.codePlaceholder")}
          requiredMessage={t("settings.codeRequired")}
          getCodeLabel={t("settings.getCode")}
          onSend={() => codes.send(handle)}
        />
        <Form.Item name="new_password" rules={[{ required: true, min: 12, message: t("settings.passwordMin") }]}>
          <Input.Password placeholder={t("settings.newPasswordPlaceholder")} />
        </Form.Item>
      </Form>
    </Modal>
  );
}

/** Danger zone · account deletion: request (reason + retype the primary handle via the shared
 *  TypeConfirmModal) / cooling-off countdown + withdraw. */
function DeletionZone({ handle }: { handle: string }) {
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
    onSuccess: () => {
      message.success(t("settings.deletion.cancelled"));
    },
  });
  const confirm = useConfirm();

  const pending = req?.status === "pending";
  const statusMeta = req ? metaOf(deletionStatusMap, req.status) : undefined;
  const countdown = pending ? formatDaysUntil(req.cooldown_ends_at) : null;

  return (
    <>
      {pending ? (
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
        <DangerZone
          title={t("settings.deletion.dangerZone")}
          description={
            <Space orientation="vertical" size={space.xs}>
              <span>{t("settings.deletion.dangerNote")}</span>
              {req && statusMeta && (
                <Space size={space.sm}>
                  <Tag color={statusMeta.color}>{t(statusMeta.labelKey)}</Tag>
                  {req.status === "rejected" && req.note && (
                    <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
                      {t("settings.deletion.rejectedLine", { note: req.note })}
                    </Typography.Text>
                  )}
                </Space>
              )}
            </Space>
          }
          actions={[{ key: "delete", label: t("settings.deletion.apply"), onClick: () => setOpen(true) }]}
        />
      )}

      <TypeConfirmModal
        open={open}
        title={t("settings.deletion.modalTitle")}
        targetName={handle}
        maxLength={254}
        body={
          <Space orientation="vertical" size={space.md} style={{ width: "100%" }}>
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
            <Space orientation="vertical" size={space.xs} style={{ width: "100%" }}>
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
        onConfirm={() => create.mutate({ handle, reason: reason.trim() })}
        onCancel={close}
      />
    </>
  );
}

/** Identity verification (shown only when the compliance profile offers a KYC form): skeleton /
 *  error with retry / verified / unverified; with real_name_enabled=false the form is disabled. */
function KycTab({
  me,
  enabled,
  loading,
  error,
  onRetry,
}: {
  me: UserOut | undefined;
  enabled: boolean;
  loading: boolean;
  error: boolean;
  onRetry: () => void;
}) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [form] = Form.useForm<{ full_name: string; identity_number: string }>();
  const submit = useSubmitKyc({
    onSuccess: () => {
      message.success(t("settings.realNameDone"));
    },
  });
  const verified = me?.kyc_status === "verified";
  return (
    <Card
      extra={
        loading || error ? undefined : (
          <Tag color={verified ? "green" : "orange"}>
            {verified ? t("settings.verified") : t("settings.unverified")}
          </Tag>
        )
      }
    >
      {loading ? (
        <Skeleton active paragraph={{ rows: 1 }} title={false} />
      ) : error ? (
        <DataErrorAlert onRetry={onRetry} />
      ) : verified ? (
        <Typography.Text type="secondary">{t("settings.realNameDoneNote")}</Typography.Text>
      ) : (
        <Space orientation="vertical" size={space.sm} style={{ width: "100%" }}>
          <Typography.Text type="secondary">
            {enabled ? t("settings.realNameNote") : t("settings.realNameDisabled")}
          </Typography.Text>
          <Form
            form={form}
            layout="inline"
            disabled={!enabled}
            onFinish={(v) => submit.mutate({ full_name: v.full_name, identity_number: v.identity_number })}
          >
            <Form.Item name="full_name" rules={[{ required: true, min: 2, message: t("settings.realNameNameRule") }]}>
              <Input
                placeholder={t("settings.realNamePlaceholder")}
                aria-label={t("settings.realNamePlaceholder")}
                autoComplete="name"
                style={{ width: 160 }}
              />
            </Form.Item>
            <Form.Item
              name="identity_number"
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
            <GatedButton
              type="primary"
              htmlType="submit"
              loading={submit.isPending}
              reason={enabled ? undefined : t("settings.realNameDisabled")}
            >
              {t("settings.submitVerify")}
            </GatedButton>
          </Form>
        </Space>
      )}
    </Card>
  );
}
