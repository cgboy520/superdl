/** Organization drawer (?org=<uuid>): members and invitations with masked handles, the organization's gateways; dissolve and remove a member (ops, reason), transfer ownership (admin only, reason). */

import { drawerWidth, formatDateTime, space } from "@superdl/ui";
import { EntityHeader, Mono, TableErrorEmpty, type KeyValueItem } from "@superdl/ui/components";
import { Link } from "@tanstack/react-router";
import { Drawer, Skeleton, Space, Table, Tag, Typography } from "antd";
import { useTranslation } from "react-i18next";

import {
  type AdminOrgDetailOut,
  isApiError,
  useAdminDissolveOrg,
  useAdminOrg,
  useAdminRemoveOrgMember,
  useAdminTransferOrg,
} from "../../api";
import { ReasonAction } from "../../components/ReasonAction";
import { TenantLink } from "../../components/TenantLink";
import { canWriteOps, useAdminRole } from "../../stores/auth";

type Member = AdminOrgDetailOut["member_list"][number];
type Invitation = AdminOrgDetailOut["invitations"][number];

export function OrgDrawer({
  uuid,
  onClose,
  onChanged,
}: {
  uuid: string | null;
  onClose: () => void;
  onChanged: () => void;
}) {
  const { t } = useTranslation(["admin", "shared"]);
  const role = useAdminRole();
  const ops = canWriteOps(role);
  const superAdmin = role === "admin";
  const q = useAdminOrg(uuid);
  const dissolve = useAdminDissolveOrg();
  const remove = useAdminRemoveOrgMember();
  const transfer = useAdminTransferOrg();
  const org = q.data;
  const active = org?.status === "active";
  const done = () => {
    void q.refetch();
    onChanged();
  };

  const meta: KeyValueItem[] = org
    ? [
        {
          label: t("orgs.colOwner"),
          value: org.owner_user_id ? (
            <Space size={space.xs}>
              <TenantLink id={org.owner_user_id} />
              {org.owner_email_masked}
            </Space>
          ) : null,
        },
        { label: t("orgs.colMembers"), value: org.members },
        { label: t("orgs.colPending"), value: org.pending_invitations },
        { label: t("orgs.colCreated"), value: formatDateTime(org.created_at) },
        ...(org.dissolved_at ? [{ label: t("orgs.colDissolved"), value: formatDateTime(org.dissolved_at) }] : []),
      ]
    : [];

  return (
    <Drawer
      open={uuid !== null}
      onClose={onClose}
      size={drawerWidth.lg}
      title={t("orgs.drawerTitle")}
      extra={
        org && (
          <Link to="/netgw" search={{ org: org.uuid }}>
            {t("orgs.viewGateways")}
          </Link>
        )
      }
    >
      {q.isError ? (
        <TableErrorEmpty
          isError
          compact
          isForbidden={isApiError(q.error) && q.error.status === 403}
          onRetry={() => void q.refetch()}
        />
      ) : !org ? (
        <Skeleton active />
      ) : (
        <Space orientation="vertical" size={space.lg} style={{ width: "100%" }}>
          <EntityHeader
            size="drawer"
            name={org.name}
            subtitle={<Mono>{org.uuid}</Mono>}
            status={<Tag color={active ? "green" : undefined}>{t(`orgs.status.${org.status}`)}</Tag>}
            meta={meta}
            actions={
              <ReasonAction
                label={t("orgs.dissolve")}
                title={t("orgs.dissolveTitle")}
                confirmText={t("orgs.dissolveConfirm", { name: org.name })}
                target={`${org.name} · ${org.uuid}`}
                danger
                size="middle"
                disabled={!ops || !active}
                disabledReason={!ops ? t("tenants.noPermission") : t("orgs.alreadyDissolved")}
                onSubmit={async (reason) => {
                  await dissolve.mutateAsync({ uuid: org.uuid, data: { reason } });
                  done();
                }}
              />
            }
          />
          <div>
            <Typography.Title level={5}>{t("orgs.membersTitle")}</Typography.Title>
            <Table<Member>
              rowKey="user_id"
              size="small"
              pagination={false}
              scroll={{ x: 720 }}
              dataSource={org.member_list}
              locale={{ emptyText: t("orgs.noMembers") }}
              columns={[
                { title: "ID", width: 80, render: (_, m) => <TenantLink id={m.user_id} /> },
                { title: t("orgs.colEmail"), render: (_, m) => m.email_masked },
                { title: t("orgs.colRole"), render: (_, m) => t(`orgs.role.${m.role}`) },
                { title: t("orgs.colAccount"), render: (_, m) => t(`orgs.account.${m.status}`) },
                { title: t("orgs.colJoined"), render: (_, m) => formatDateTime(m.joined_at) },
                {
                  title: t("orgs.colActions"),
                  render: (_, m) => (
                    <Space size={space.xs}>
                      <ReasonAction
                        label={t("orgs.makeOwner")}
                        title={t("orgs.transferTitle")}
                        confirmText={t("orgs.transferConfirm", { id: m.user_id, name: org.name })}
                        target={`#${String(m.user_id)} · ${m.email_masked}`}
                        triggerDanger={false}
                        disabled={!superAdmin || !active || m.role === "owner"}
                        disabledReason={!superAdmin ? t("orgs.adminOnly") : t("orgs.alreadyOwner")}
                        onSubmit={async (reason) => {
                          await transfer.mutateAsync({ uuid: org.uuid, data: { user_id: m.user_id, reason } });
                          done();
                        }}
                      />
                      <ReasonAction
                        label={t("orgs.remove")}
                        title={t("orgs.removeTitle")}
                        confirmText={t("orgs.removeConfirm", { id: m.user_id, name: org.name })}
                        target={`#${String(m.user_id)} · ${m.email_masked}`}
                        danger
                        disabled={!ops || !active || m.role === "owner"}
                        disabledReason={!ops ? t("tenants.noPermission") : t("orgs.ownerNotRemovable")}
                        onSubmit={async (reason) => {
                          await remove.mutateAsync({ uuid: org.uuid, data: { user_id: m.user_id, reason } });
                          done();
                        }}
                      />
                    </Space>
                  ),
                },
              ]}
            />
          </div>
          <div>
            <Typography.Title level={5}>{t("orgs.invitationsTitle")}</Typography.Title>
            <Table<Invitation>
              rowKey="uuid"
              size="small"
              pagination={false}
              scroll={{ x: 640 }}
              dataSource={org.invitations}
              locale={{ emptyText: t("orgs.noInvitations") }}
              columns={[
                { title: t("orgs.colEmail"), render: (_, i) => i.email_masked },
                { title: t("orgs.colRole"), render: (_, i) => t(`orgs.role.${i.role}`) },
                { title: t("orgs.colStatus"), render: (_, i) => t(`orgs.invitation.${i.status}`) },
                { title: t("orgs.colCreated"), render: (_, i) => formatDateTime(i.created_at) },
                { title: t("orgs.colExpires"), render: (_, i) => formatDateTime(i.expires_at) },
              ]}
            />
          </div>
        </Space>
      )}
    </Drawer>
  );
}
