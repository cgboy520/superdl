/** Tenant members (owners and admins): personal accounts that joined by invitation and accounts the tenant created (<code>.<name>). Remarks, roles (owner only), balance moves for everyone; password reset, sign-in switch and deletion for created accounts (owner: any, admin: members); removal for joined accounts, which detaches their instances from the tenant's gateways. */

import type { MemberOut, MyOrgOut } from "@superdl/api-client";
import { formatDateTime, space } from "@superdl/ui";
import { GatedButton, RowActions, TableErrorEmpty, useConfirm } from "@superdl/ui/components";
import { App, Card, Input, Modal, Segmented, Space, Table, Tag, Typography } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import {
  useDeleteMemberAccount,
  useRemoveMember,
  useResetMemberPassword,
  useSetMemberLogin,
  useUpdateMember,
} from "../../api/mutations";
import { useOrgFinance, useOrgMembers } from "../../api/queries";
import { Field } from "../Field";
import { BalanceMoveModal } from "./BalanceMoveModal";
import { CreateAccountsModal, CredentialsResult } from "./CreateAccountsModal";
import { InviteModal } from "./InviteModal";

const MIN_PASSWORD = 12;

function ResetPasswordModal({ member, onClose }: { member: MemberOut | null; onClose: () => void }) {
  const { t } = useTranslation();
  const [mode, setMode] = useState<"random" | "set">("random");
  const [password, setPassword] = useState("");
  const [result, setResult] = useState<{ username: string; password: string } | null>(null);
  const reset = useResetMemberPassword({ onSuccess: (out) => setResult(out) });
  const close = () => {
    setResult(null);
    setPassword("");
    onClose();
  };
  if (result) {
    return (
      <Modal
        title={t("org.accounts.resetDone", { name: result.username })}
        open={member != null}
        onCancel={close}
        footer={null}
        width={640}
        mask={{ closable: false }}
      >
        <CredentialsResult rows={[result]} fileName={`${result.username}.csv`} />
      </Modal>
    );
  }
  const short = mode === "set" && password.length < MIN_PASSWORD;
  return (
    <Modal
      title={t("org.accounts.resetTitle", { name: member?.label ?? "" })}
      open={member != null}
      onCancel={close}
      onOk={() =>
        member && reset.mutate({ memberId: member.member_id, body: { password: mode === "set" ? password : null } })
      }
      okText={t("org.accounts.resetSubmit")}
      okButtonProps={{ disabled: short, danger: true }}
      confirmLoading={reset.isPending}
      destroyOnHidden
    >
      <Space orientation="vertical" size={space.md} style={{ width: "100%" }}>
        <Typography.Text type="secondary">{t("org.accounts.resetHint")}</Typography.Text>
        <Segmented
          value={mode}
          onChange={(v) => setMode(v as "random" | "set")}
          options={[
            { value: "random", label: t("org.accounts.passwordRandom") },
            { value: "set", label: t("org.accounts.passwordSet") },
          ]}
        />
        {mode === "set" && (
          <Field
            label={t("org.accounts.passwordLabel")}
            error={password && short ? t("org.accounts.passwordShort", { min: MIN_PASSWORD }) : undefined}
          >
            <Input.Password
              value={password}
              maxLength={128}
              onChange={(e) => setPassword(e.target.value)}
              aria-label={t("org.accounts.passwordLabel")}
              autoComplete="new-password"
            />
          </Field>
        )}
      </Space>
    </Modal>
  );
}

export function MembersTab({ my }: { my: MyOrgOut }) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const confirm = useConfirm();
  const members = useOrgMembers();
  const [inviting, setInviting] = useState(false);
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<MemberOut | null>(null);
  const [resetting, setResetting] = useState<MemberOut | null>(null);
  const [moving, setMoving] = useState<MemberOut | null>(null);
  const finance = useOrgFinance({ enabled: moving?.managed === true });
  const [remark, setRemark] = useState("");
  const update = useUpdateMember({
    onSuccess: () => {
      message.success(t("org.members.saved"));
      setEditing(null);
    },
  });
  const remove = useRemoveMember({ onSuccess: () => message.success(t("org.members.removed")) });
  const setLogin = useSetMemberLogin({
    onSuccess: (m) => message.success(m.login_disabled ? t("org.accounts.loginOff") : t("org.accounts.loginOn")),
  });
  const deleteAccount = useDeleteMemberAccount({ onSuccess: () => message.success(t("org.accounts.deleted")) });
  const owner = my.role === "owner";
  const code = my.org?.code ?? "";
  const balanceOf = (m: MemberOut) =>
    finance.data?.members.find((f) => f.member_id === m.member_id)?.available_balance ?? null;
  const removeReason = (m: MemberOut) =>
    m.member_id === my.member_id
      ? t("org.members.removeSelf")
      : m.role === "owner"
        ? t("org.members.removeOwner")
        : m.role === "admin" && !owner
          ? t("org.members.adminOnlyOwner")
          : undefined;
  /** Created accounts: the owner manages any, an admin only plain members. */
  const manageReason = (m: MemberOut) =>
    m.member_id === my.member_id
      ? t("org.accounts.selfUseSettings")
      : !owner && m.role !== "member"
        ? t("org.members.adminOnlyOwner")
        : undefined;

  const accountItems = (m: MemberOut) => [
    {
      key: "password",
      label: t("org.accounts.resetPassword"),
      reason: manageReason(m),
      onClick: () => setResetting(m),
    },
    {
      key: "login",
      label: m.login_disabled ? t("org.accounts.enableLogin") : t("org.accounts.disableLogin"),
      reason: manageReason(m),
      onClick: () =>
        m.login_disabled
          ? setLogin.mutate({ memberId: m.member_id, disabled: false })
          : confirm({
              title: t("org.accounts.disableTitle", { name: m.label }),
              consequences: [t("org.accounts.disableConsequence")],
              okText: t("org.accounts.disableLogin"),
              cancelText: t("org.cancel"),
              danger: true,
              onOk: () => setLogin.mutate({ memberId: m.member_id, disabled: true }),
            }),
    },
    {
      key: "delete",
      label: t("org.accounts.delete"),
      danger: true,
      reason: manageReason(m),
      onClick: () =>
        confirm({
          title: t("org.accounts.deleteTitle", { name: m.label }),
          consequences: [t("org.accounts.deleteConsequence"), t("org.accounts.deleteNeedsEmpty")],
          okText: t("org.accounts.delete"),
          cancelText: t("org.cancel"),
          danger: true,
          onOk: () => deleteAccount.mutate(m.member_id),
        }),
    },
  ];

  return (
    <Card
      size="small"
      title={t("org.members.title", { n: members.data?.length ?? 0 })}
      extra={
        <Space size={space.xs}>
          <GatedButton size="small" type="primary" onClick={() => setCreating(true)}>
            {t("org.accounts.create")}
          </GatedButton>
          <GatedButton size="small" onClick={() => setInviting(true)}>
            {t("org.invite")}
          </GatedButton>
        </Space>
      }
    >
      {members.isError ? (
        <TableErrorEmpty isError compact onRetry={() => void members.refetch()} />
      ) : (
        <Table<MemberOut>
          rowKey="member_id"
          size="small"
          loading={members.isLoading}
          pagination={members.data && members.data.length > 50 ? { pageSize: 50 } : false}
          scroll={{ x: 900 }}
          dataSource={members.data ?? []}
          columns={[
            {
              title: t("org.members.colMember"),
              render: (_, m) => (
                <Space size={space.xs} wrap>
                  <Typography.Text className={m.managed ? "mono" : undefined}>{m.label}</Typography.Text>
                  {m.managed && (
                    <Tag variant="filled" color="purple">
                      {t("org.accounts.tagManaged")}
                    </Tag>
                  )}
                  {m.login_disabled && (
                    <Tag variant="filled" color="red">
                      {t("org.accounts.tagLoginOff")}
                    </Tag>
                  )}
                </Space>
              ),
            },
            {
              title: t("org.members.colEmail"),
              render: (_, m) =>
                m.managed ? (
                  <Typography.Text type="secondary">{t("org.accounts.noContact")}</Typography.Text>
                ) : (
                  <Typography.Text type="secondary">{m.email_masked}</Typography.Text>
                ),
            },
            {
              title: t("org.members.colRole"),
              render: (_, m) => <Tag color={m.role === "member" ? undefined : "blue"}>{t(`org.role.${m.role}`)}</Tag>,
            },
            {
              title: t("org.members.remark"),
              render: (_, m) => m.remark || <Typography.Text type="secondary">—</Typography.Text>,
            },
            {
              title: t("org.members.colStatus"),
              render: (_, m) =>
                m.status === "active" ? (
                  t("org.members.statusActive")
                ) : (
                  <Typography.Text type="warning">{t(`org.members.status.${m.status}`)}</Typography.Text>
                ),
            },
            { title: t("org.members.colJoined"), render: (_, m) => formatDateTime(m.joined_at) },
            {
              title: t("org.colActions"),
              render: (_, m) => (
                <RowActions
                  primary={
                    <GatedButton
                      size="small"
                      reason={m.member_id === my.member_id ? t("org.balance.notSelf") : undefined}
                      onClick={() => setMoving(m)}
                    >
                      {t("org.balance.move")}
                    </GatedButton>
                  }
                  more={[
                    {
                      key: "remark",
                      label: t("org.members.editRemark"),
                      onClick: () => {
                        setRemark(m.remark ?? "");
                        setEditing(m);
                      },
                    },
                    {
                      key: "role",
                      label: m.role === "admin" ? t("org.members.makeMember") : t("org.members.makeAdmin"),
                      reason: !owner
                        ? t("org.members.ownerOnly")
                        : m.role === "owner"
                          ? t("org.members.ownerViaTransfer")
                          : undefined,
                      onClick: () =>
                        update.mutate({
                          memberId: m.member_id,
                          body: { role: m.role === "admin" ? "member" : "admin" },
                        }),
                    },
                    ...(m.managed
                      ? accountItems(m)
                      : [
                          {
                            key: "remove",
                            label: t("org.members.remove"),
                            danger: true,
                            reason: removeReason(m),
                            onClick: () =>
                              confirm({
                                title: t("org.members.removeTitle", { name: m.label }),
                                consequences: [t("org.members.removeConsequence")],
                                okText: t("org.members.remove"),
                                cancelText: t("org.cancel"),
                                danger: true,
                                onOk: () => remove.mutate(m.member_id),
                              }),
                          },
                        ]),
                  ]}
                />
              ),
            },
          ]}
        />
      )}
      <Modal
        title={t("org.members.remarkTitle", { name: editing?.label ?? "" })}
        open={editing !== null}
        onCancel={() => setEditing(null)}
        onOk={() => editing && update.mutate({ memberId: editing.member_id, body: { remark: remark.trim() } })}
        okText={t("org.save")}
        confirmLoading={update.isPending}
      >
        <Field label={t("org.members.remark")} hint={t("org.members.remarkHint")}>
          <Input
            value={remark}
            maxLength={64}
            onChange={(e) => setRemark(e.target.value)}
            aria-label={t("org.members.remark")}
          />
        </Field>
      </Modal>
      <InviteModal open={inviting} owner={owner} onClose={() => setInviting(false)} />
      <CreateAccountsModal open={creating} code={code} onClose={() => setCreating(false)} />
      <ResetPasswordModal member={resetting} onClose={() => setResetting(null)} />
      <BalanceMoveModal
        member={moving}
        memberBalance={moving ? balanceOf(moving) : null}
        onClose={() => setMoving(null)}
      />
    </Card>
  );
}
