/** Sent invitations (owners and admins); pending ones can be revoked. */

import type { InvitationOut, MyOrgOut } from "@superdl/api-client";
import { formatDateTime } from "@superdl/ui";
import { GatedButton, TableErrorEmpty, useConfirm } from "@superdl/ui/components";
import { App, Card, Table, Tag, Typography } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useRevokeInvitation } from "../../api/mutations";
import { useOrgInvitations } from "../../api/queries";
import { InviteModal } from "./InviteModal";

const STATUS_COLOR: Record<InvitationOut["status"], string | undefined> = {
  pending: "processing",
  accepted: "success",
  declined: undefined,
  revoked: undefined,
  expired: undefined,
};

export function InvitationsTab({ my }: { my: MyOrgOut }) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const confirm = useConfirm();
  const list = useOrgInvitations();
  const [inviting, setInviting] = useState(false);
  const revoke = useRevokeInvitation({ onSuccess: () => message.success(t("org.invitations.revoked")) });
  return (
    <Card
      size="small"
      title={t("org.invitations.title")}
      extra={
        <GatedButton size="small" type="primary" onClick={() => setInviting(true)}>
          {t("org.invite")}
        </GatedButton>
      }
    >
      {list.isError ? (
        <TableErrorEmpty isError compact onRetry={() => void list.refetch()} />
      ) : (
        <Table<InvitationOut>
          rowKey="uuid"
          size="small"
          loading={list.isLoading}
          pagination={false}
          scroll={{ x: 760 }}
          dataSource={list.data ?? []}
          locale={{ emptyText: t("org.invitations.empty") }}
          columns={[
            { title: t("org.invitations.email"), render: (_, i) => i.email },
            { title: t("org.invitations.role"), render: (_, i) => t(`org.role.${i.role}`) },
            {
              title: t("org.members.remark"),
              render: (_, i) => i.remark ?? <Typography.Text type="secondary">—</Typography.Text>,
            },
            {
              title: t("org.invitations.colStatus"),
              render: (_, i) => <Tag color={STATUS_COLOR[i.status]}>{t(`org.invitations.status.${i.status}`)}</Tag>,
            },
            { title: t("org.invitations.colInviter"), render: (_, i) => i.invited_by },
            { title: t("org.invitations.colExpires"), render: (_, i) => formatDateTime(i.expires_at) },
            {
              title: t("org.colActions"),
              render: (_, i) => (
                <GatedButton
                  size="small"
                  danger
                  reason={i.status === "pending" ? undefined : t("org.invitations.notPending")}
                  onClick={() =>
                    confirm({
                      title: t("org.invitations.revokeTitle", { email: i.email }),
                      consequences: [t("org.invitations.revokeConsequence")],
                      okText: t("org.invitations.revoke"),
                      cancelText: t("org.cancel"),
                      danger: true,
                      onOk: () => revoke.mutate(i.uuid),
                    })
                  }
                >
                  {t("org.invitations.revoke")}
                </GatedButton>
              ),
            },
          ]}
        />
      )}
      <InviteModal open={inviting} owner={my.role === "owner"} onClose={() => setInviting(false)} />
    </Card>
  );
}
