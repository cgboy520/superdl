/** Organization members (owners and admins): remark edits, role changes (owner only) and removal; removing a member detaches their instances from the organization's gateways. */

import type { MemberOut, MyOrgOut } from "@superdl/api-client";
import { formatDateTime } from "@superdl/ui";
import { GatedButton, RowActions, TableErrorEmpty, useConfirm } from "@superdl/ui/components";
import { App, Card, Input, Modal, Table, Tag, Typography } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useRemoveMember, useUpdateMember } from "../../api/mutations";
import { useOrgMembers } from "../../api/queries";
import { Field } from "../Field";
import { InviteModal } from "./InviteModal";

export function MembersTab({ my }: { my: MyOrgOut }) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const confirm = useConfirm();
  const members = useOrgMembers();
  const [inviting, setInviting] = useState(false);
  const [editing, setEditing] = useState<MemberOut | null>(null);
  const [remark, setRemark] = useState("");
  const update = useUpdateMember({
    onSuccess: () => {
      message.success(t("org.members.saved"));
      setEditing(null);
    },
  });
  const remove = useRemoveMember({ onSuccess: () => message.success(t("org.members.removed")) });
  const owner = my.role === "owner";
  const removeReason = (m: MemberOut) =>
    m.member_id === my.member_id
      ? t("org.members.removeSelf")
      : m.role === "owner"
        ? t("org.members.removeOwner")
        : m.role === "admin" && !owner
          ? t("org.members.adminOnlyOwner")
          : undefined;

  return (
    <Card
      size="small"
      title={t("org.members.title", { n: members.data?.length ?? 0 })}
      extra={
        <GatedButton size="small" type="primary" onClick={() => setInviting(true)}>
          {t("org.invite")}
        </GatedButton>
      }
    >
      {members.isError ? (
        <TableErrorEmpty isError compact onRetry={() => void members.refetch()} />
      ) : (
        <Table<MemberOut>
          rowKey="member_id"
          size="small"
          loading={members.isLoading}
          pagination={false}
          scroll={{ x: 760 }}
          dataSource={members.data ?? []}
          columns={[
            { title: t("org.members.colMember"), render: (_, m) => m.label },
            {
              title: t("org.members.colEmail"),
              render: (_, m) => <Typography.Text type="secondary">{m.email_masked}</Typography.Text>,
            },
            {
              title: t("org.members.colRole"),
              render: (_, m) => <Tag color={m.role === "member" ? undefined : "blue"}>{t(`org.role.${m.role}`)}</Tag>,
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
                      onClick={() => {
                        setRemark(m.remark ?? "");
                        setEditing(m);
                      }}
                    >
                      {t("org.members.editRemark")}
                    </GatedButton>
                  }
                  more={[
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
    </Card>
  );
}
