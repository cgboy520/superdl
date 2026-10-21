/** Organization: outside one, create one or answer invitations; inside, overview (everyone) and members / invitations / finance (owners and admins). `?tab=` and `?invite=<uuid>` (the acceptance drawer) are whitelisted in the URL. */

import type { MemberOut, MyOrgOut, ReceivedInvitationOut } from "@superdl/api-client";
import { formatDateTime, idemKeyOf, space } from "@superdl/ui";
import {
  DangerZone,
  EmptyState,
  GatedButton,
  KeyValue,
  PageContainer,
  TableErrorEmpty,
  TypeConfirmModal,
  useConfirm,
} from "@superdl/ui/components";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { App, Card, Input, Modal, Select, Skeleton, Space, Table, Tabs, Tag, Typography } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import {
  useCreateOrg,
  useDeclineInvitation,
  useDissolveOrg,
  useLeaveOrg,
  useRenameOrg,
  useTransferOrg,
} from "../api/mutations";
import { useMe, useMyOrg, useOrgMembers, usePolicies, useReceivedInvitations, useSiteConfig } from "../api/queries";
import { Field } from "../components/Field";
import { AcceptInvitationDrawer } from "../components/org/AcceptInvitationDrawer";
import { FinanceTab } from "../components/org/FinanceTab";
import { InvitationsTab } from "../components/org/InvitationsTab";
import { MembersTab } from "../components/org/MembersTab";
import { requireAuth } from "../lib/guard";

export const ORG_TABS = ["overview", "members", "invitations", "finance"] as const;
export type OrgTab = (typeof ORG_TABS)[number];

interface OrgSearch {
  tab?: OrgTab;
  invite?: string;
}

/** Whitelist: unknown tabs and malformed invitation ids are dropped. */
export function orgValidateSearch(search: Record<string, unknown>): OrgSearch {
  const out: OrgSearch = {};
  if (typeof search.tab === "string" && (ORG_TABS as readonly string[]).includes(search.tab)) {
    out.tab = search.tab as OrgTab;
  }
  if (typeof search.invite === "string" && /^[0-9a-f]{32}$/.test(search.invite)) out.invite = search.invite;
  return out;
}

export const Route = createFileRoute("/_console/org")({
  beforeLoad: requireAuth,
  validateSearch: orgValidateSearch,
  component: OrgPage,
});

/** Same gate as the server's real-name check (inert without a KYC form). */
function useRealNameReason(): string | undefined {
  const { t } = useTranslation();
  const { data: me } = useMe();
  const { data: policies } = usePolicies();
  const siteQ = useSiteConfig();
  const kycMayBeRequired = siteQ.isError || Boolean(siteQ.data?.kyc_form);
  return kycMayBeRequired && policies?.real_name_required_for_recharge && me != null && me.kyc_status !== "verified"
    ? t("org.realNameReason")
    : undefined;
}

function ReceivedCard({ onOpen }: { onOpen: (uuid: string) => void }) {
  const { t } = useTranslation();
  const confirm = useConfirm();
  const { message } = App.useApp();
  const received = useReceivedInvitations();
  const decline = useDeclineInvitation({ onSuccess: () => message.success(t("org.received.declined")) });
  const rows = received.data ?? [];
  if (!received.isError && rows.length === 0) return null;
  return (
    <Card size="small" title={t("org.received.title")}>
      {received.isError ? (
        <TableErrorEmpty isError compact onRetry={() => void received.refetch()} />
      ) : (
        <Table<ReceivedInvitationOut>
          rowKey="uuid"
          size="small"
          pagination={false}
          scroll={{ x: 640 }}
          dataSource={rows}
          columns={[
            { title: t("org.received.colOrg"), render: (_, r) => r.org_name },
            { title: t("org.received.colRole"), render: (_, r) => t(`org.role.${r.role}`) },
            { title: t("org.received.colInviter"), render: (_, r) => r.invited_by },
            { title: t("org.received.colExpires"), render: (_, r) => formatDateTime(r.expires_at) },
            {
              title: t("org.colActions"),
              render: (_, r) => (
                <Space size={space.xs}>
                  <GatedButton
                    size="small"
                    type="primary"
                    reason={r.blocked ? t("org.received.blocked") : undefined}
                    onClick={() => onOpen(r.uuid)}
                  >
                    {t("org.received.review")}
                  </GatedButton>
                  <GatedButton
                    size="small"
                    onClick={() =>
                      confirm({
                        title: t("org.received.declineTitle", { name: r.org_name }),
                        consequences: [t("org.received.declineConsequence")],
                        okText: t("org.received.decline"),
                        cancelText: t("org.cancel"),
                        onOk: () => decline.mutate(r.uuid),
                      })
                    }
                  >
                    {t("org.received.decline")}
                  </GatedButton>
                </Space>
              ),
            },
          ]}
        />
      )}
    </Card>
  );
}

function CreateOrgModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [name, setName] = useState("");
  const [seq] = useState(() => Date.now());
  const create = useCreateOrg({
    onSuccess: () => {
      message.success(t("org.created"));
      setName("");
      onClose();
    },
  });
  const trimmed = name.trim();
  return (
    <Modal
      title={t("org.createTitle")}
      open={open}
      onCancel={onClose}
      onOk={() => create.mutate({ name: trimmed, idempotencyKey: idemKeyOf("org", [seq, trimmed]) })}
      okText={t("org.createSubmit")}
      okButtonProps={{ disabled: trimmed.length < 2 }}
      confirmLoading={create.isPending}
    >
      <Space orientation="vertical" size={space.md} style={{ width: "100%" }}>
        <Typography.Text type="secondary">{t("org.createHint")}</Typography.Text>
        <Field label={t("org.name")} required hint={t("org.nameHint")}>
          <Input value={name} maxLength={40} onChange={(e) => setName(e.target.value)} aria-label={t("org.name")} />
        </Field>
      </Space>
    </Modal>
  );
}

function NoOrg({ onOpenInvite }: { onOpenInvite: (uuid: string) => void }) {
  const { t } = useTranslation();
  const [creating, setCreating] = useState(false);
  const realName = useRealNameReason();
  const createButton = (
    <GatedButton type="primary" reason={realName} onClick={() => setCreating(true)}>
      {t("org.create")}
    </GatedButton>
  );
  return (
    <Space orientation="vertical" size={space.lg} style={{ width: "100%" }}>
      <ReceivedCard onOpen={onOpenInvite} />
      <Card>
        <EmptyState description={t("org.emptyDescription")} action={createButton} />
      </Card>
      <CreateOrgModal open={creating} onClose={() => setCreating(false)} />
    </Space>
  );
}

function TransferModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const members = useOrgMembers({ enabled: open });
  const [target, setTarget] = useState<number>();
  const transfer = useTransferOrg({
    onSuccess: () => {
      message.success(t("org.transferred"));
      setTarget(undefined);
      onClose();
    },
  });
  const options = (members.data ?? [])
    .filter((m: MemberOut) => m.role !== "owner" && m.status === "active")
    .map((m) => ({ value: m.member_id, label: `${m.label} · ${t(`org.role.${m.role}`)}` }));
  return (
    <Modal
      title={t("org.transferTitle")}
      open={open}
      onCancel={onClose}
      onOk={() => target !== undefined && transfer.mutate(target)}
      okText={t("org.transferSubmit")}
      okButtonProps={{ disabled: target === undefined, danger: true }}
      confirmLoading={transfer.isPending}
    >
      <Space orientation="vertical" size={space.md} style={{ width: "100%" }}>
        <Typography.Text type="secondary">{t("org.transferHint")}</Typography.Text>
        <Field label={t("org.transferTarget")} required>
          <Select
            value={target}
            onChange={setTarget}
            options={options}
            placeholder={t("org.transferPlaceholder")}
            aria-label={t("org.transferTarget")}
          />
        </Field>
      </Space>
    </Modal>
  );
}

function Overview({ my }: { my: MyOrgOut }) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const confirm = useConfirm();
  const org = my.org;
  const [renaming, setRenaming] = useState(false);
  const [name, setName] = useState(org?.name ?? "");
  const [transferring, setTransferring] = useState(false);
  const [dissolving, setDissolving] = useState(false);
  const rename = useRenameOrg({
    onSuccess: () => {
      message.success(t("org.renamed"));
      setRenaming(false);
    },
  });
  const leave = useLeaveOrg({ onSuccess: () => message.success(t("org.left")) });
  const dissolve = useDissolveOrg({
    onSuccess: () => {
      message.success(t("org.dissolved"));
      setDissolving(false);
    },
  });
  if (!org) return null;
  const owner = my.role === "owner";
  return (
    <Space orientation="vertical" size={space.lg} style={{ width: "100%" }}>
      <Card
        size="small"
        title={t("org.overviewTitle")}
        extra={
          owner && (
            <GatedButton
              size="small"
              onClick={() => {
                setName(org.name);
                setRenaming(true);
              }}
            >
              {t("org.rename")}
            </GatedButton>
          )
        }
      >
        <KeyValue
          columns={1}
          items={[
            { label: t("org.name"), value: org.name },
            { label: t("org.myRole"), value: my.role ? t(`org.role.${my.role}`) : null },
            { label: t("org.memberCount"), value: String(my.member_count ?? 0) },
            {
              label: t("org.admins"),
              value: my.admins.map((a) => `${a.label} (${t(`org.role.${a.role}`)})`).join(", "),
            },
            { label: t("org.createdAt"), value: formatDateTime(org.created_at) },
          ]}
        />
        <Typography.Paragraph type="secondary" style={{ marginTop: space.md, marginBottom: 0 }}>
          {t("org.visibilityNote")}
        </Typography.Paragraph>
      </Card>
      {owner ? (
        <DangerZone
          title={t("org.dangerZone")}
          description={t("org.ownerDangerNote")}
          actions={[
            { key: "transfer", label: t("org.transfer"), onClick: () => setTransferring(true) },
            { key: "dissolve", label: t("org.dissolve"), onClick: () => setDissolving(true) },
          ]}
        />
      ) : (
        <DangerZone
          title={t("org.dangerZone")}
          description={t("org.leaveNote")}
          actions={[
            {
              key: "leave",
              label: t("org.leave"),
              loading: leave.isPending,
              onClick: () =>
                confirm({
                  title: t("org.leaveTitle", { name: org.name }),
                  consequences: [t("org.leaveConsequence")],
                  okText: t("org.leave"),
                  cancelText: t("org.cancel"),
                  danger: true,
                  onOk: () => leave.mutate(),
                }),
            },
          ]}
        />
      )}
      <Modal
        title={t("org.renameTitle")}
        open={renaming}
        onCancel={() => setRenaming(false)}
        onOk={() => rename.mutate(name.trim())}
        okText={t("org.save")}
        okButtonProps={{ disabled: name.trim().length < 2 }}
        confirmLoading={rename.isPending}
      >
        <Field label={t("org.name")} required hint={t("org.nameHint")}>
          <Input value={name} maxLength={40} onChange={(e) => setName(e.target.value)} aria-label={t("org.name")} />
        </Field>
      </Modal>
      <TransferModal open={transferring} onClose={() => setTransferring(false)} />
      <TypeConfirmModal
        open={dissolving}
        title={t("org.dissolveTitle")}
        body={t("org.dissolveBody", { name: org.name })}
        targetName={org.name}
        confirmLabel={t("org.dissolve")}
        cancelLabel={t("org.cancel")}
        loading={dissolve.isPending}
        onConfirm={() => dissolve.mutate(org.name)}
        onCancel={() => setDissolving(false)}
      />
    </Space>
  );
}

function OrgPage() {
  const { t } = useTranslation();
  const navigate = useNavigate({ from: "/org" });
  const search = Route.useSearch();
  const myQ = useMyOrg();
  const my = myQ.data;
  const admin = my?.role === "owner" || my?.role === "admin";
  const tab: OrgTab = admin ? (search.tab ?? "overview") : "overview";
  const setSearch = (next: OrgSearch) => void navigate({ search: next, replace: true });
  const openInvite = (uuid: string | undefined) => setSearch({ ...search, invite: uuid });

  const tabs = [
    { key: "overview", label: t("org.tab.overview") },
    ...(admin
      ? [
          { key: "members", label: t("org.tab.members") },
          { key: "invitations", label: t("org.tab.invitations") },
          { key: "finance", label: t("org.tab.finance") },
        ]
      : []),
  ];

  return (
    <PageContainer
      title={t("org.title")}
      description={my?.org ? my.org.name : t("org.description")}
      extra={my?.org && <Tag>{t(`org.role.${my.role ?? "member"}`)}</Tag>}
    >
      {myQ.isPending ? (
        <Skeleton active />
      ) : myQ.isError ? (
        <TableErrorEmpty isError onRetry={() => void myQ.refetch()} />
      ) : !my?.org ? (
        <NoOrg onOpenInvite={openInvite} />
      ) : (
        <Space orientation="vertical" size={space.lg} style={{ width: "100%" }}>
          <ReceivedCard onOpen={openInvite} />
          <Tabs activeKey={tab} onChange={(k) => setSearch({ tab: k as OrgTab })} items={tabs} />
          {tab === "overview" && <Overview my={my} />}
          {tab === "members" && <MembersTab my={my} />}
          {tab === "invitations" && <InvitationsTab my={my} />}
          {tab === "finance" && <FinanceTab />}
        </Space>
      )}
      <AcceptInvitationDrawer uuid={search.invite} onClose={() => openInvite(undefined)} />
    </PageContainer>
  );
}
