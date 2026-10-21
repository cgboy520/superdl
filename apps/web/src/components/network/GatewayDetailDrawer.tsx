/** Gateway detail drawer: connection facts (what to configure on the tenant side), how instances use it, forwards (add / delete), whole-subnet attachments (L3) and the gateway log. scope="org": an organization gateway, whole-subnet access only; owners and admins attach members' instances. */

import type { AttachmentOut, ForwardOut, GatewayOut, OrgInstanceOut } from "@superdl/api-client";
import { drawerWidth, formatDateTime, netgwStateMap, space } from "@superdl/ui";
import { CopyField, GatedButton, KeyValue, Mono, StatusTag, useConfirm } from "@superdl/ui/components";
import {
  App,
  Button,
  Card,
  Drawer,
  Input,
  InputNumber,
  Modal,
  Segmented,
  Select,
  Space,
  Table,
  Typography,
} from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import {
  useAddForward,
  useAttachInstance,
  useAttachOrgInstance,
  useDeleteForward,
  useDetachInstance,
  useDetachOrgInstance,
} from "../../api/mutations";
import { useGatewayLogs, useInstances, useOrgGatewayLogs, useOrgInstances } from "../../api/queries";
import { Field } from "../Field";
import type { AnyGateway, GatewayScope } from "./GatewayFormDrawer";

type Kind = ForwardOut["kind"];

function peerOf(g: AnyGateway): string {
  const tn = g.tunnel;
  switch (tn.protocol) {
    case "wireguard":
      return tn.endpoint;
    case "ipsec":
      return tn.peer;
    case "openvpn":
      return tn.remotes.join(", ");
    case "openconnect":
      return tn.server;
  }
}

function AddForwardModal({ gateway, open, onClose }: { gateway: GatewayOut; open: boolean; onClose: () => void }) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const { data: instances } = useInstances({ enabled: open });
  const [kind, setKind] = useState<Kind>("intranet");
  const [name, setName] = useState("");
  const [port, setPort] = useState<number | null>(null);
  const [host, setHost] = useState("");
  const [targetPort, setTargetPort] = useState<number | null>(null);
  const [instanceUuid, setInstanceUuid] = useState<string>();
  const add = useAddForward({
    onSuccess: () => {
      message.success(t("network.forward.added"));
      setName("");
      setPort(null);
      setHost("");
      setTargetPort(null);
      setInstanceUuid(undefined);
      onClose();
    },
  });
  const ready =
    name.trim() !== "" &&
    port != null &&
    targetPort != null &&
    (kind === "intranet" ? host.trim() !== "" : instanceUuid !== undefined);
  const submit = () => {
    if (!ready) return;
    add.mutate({
      uuid: gateway.uuid,
      body:
        kind === "intranet"
          ? { kind, name: name.trim(), port, target_host: host.trim(), target_port: targetPort }
          : { kind, name: name.trim(), port, instance_uuid: instanceUuid ?? "", target_port: targetPort },
    });
  };
  const usable = (instances ?? []).filter((i) => i.status !== "released");
  return (
    <Modal
      title={t("network.forward.addTitle")}
      open={open}
      onCancel={onClose}
      onOk={submit}
      okText={t("network.forward.addSubmit")}
      okButtonProps={{ disabled: !ready }}
      confirmLoading={add.isPending}
    >
      <Space orientation="vertical" size={space.md} style={{ width: "100%" }}>
        <Segmented<Kind>
          block
          value={kind}
          onChange={setKind}
          options={[
            { value: "intranet", label: t("network.forward.kindIntranet") },
            { value: "instance", label: t("network.forward.kindInstance") },
          ]}
        />
        <Typography.Text type="secondary">
          {kind === "intranet" ? t("network.forward.intranetHint") : t("network.forward.instanceHint")}
        </Typography.Text>
        <Field label={t("network.forward.name")} required hint={t("network.forward.nameHint")}>
          <Input
            value={name}
            maxLength={40}
            onChange={(e) => setName(e.target.value.toLowerCase())}
            placeholder={kind === "intranet" ? "nas" : "box-ssh"}
            aria-label={t("network.forward.name")}
          />
        </Field>
        <Field
          label={kind === "intranet" ? t("network.forward.port") : t("network.forward.listenPort")}
          required
          inline
        >
          <InputNumber min={1} max={65535} value={port} onChange={setPort} aria-label={t("network.forward.port")} />
        </Field>
        {kind === "intranet" ? (
          <Field label={t("network.forward.targetHost")} required hint={t("network.forward.targetHostHint")}>
            <Input
              value={host}
              onChange={(e) => setHost(e.target.value)}
              placeholder="10.1.0.5"
              aria-label={t("network.forward.targetHost")}
            />
          </Field>
        ) : (
          <Field label={t("network.forward.instance")} required>
            <Select
              value={instanceUuid}
              onChange={setInstanceUuid}
              placeholder={t("network.forward.instancePlaceholder")}
              options={usable.map((i) => ({ value: i.uuid, label: i.name }))}
              aria-label={t("network.forward.instance")}
            />
          </Field>
        )}
        <Field label={t("network.forward.targetPort")} required inline>
          <InputNumber
            min={1}
            max={65535}
            value={targetPort}
            onChange={setTargetPort}
            aria-label={t("network.forward.targetPort")}
          />
        </Field>
      </Space>
    </Modal>
  );
}

/** Kata (VM-isolated) instances have no attach path; the server refuses them too. */
function attachable(i: { status: string; workload_type: string; gpu_count: number; spec: object }): boolean {
  const pool = (i.spec as { pool_label?: string }).pool_label;
  return (
    i.workload_type === "dev" &&
    i.status !== "released" &&
    i.status !== "releasing" &&
    !(pool === "kata" && i.gpu_count > 0)
  );
}

function AttachModal({ gateway, open, onClose }: { gateway: AnyGateway; open: boolean; onClose: () => void }) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const { data: instances } = useInstances({ enabled: open });
  const [instanceUuid, setInstanceUuid] = useState<string>();
  const attach = useAttachInstance({
    onSuccess: () => {
      message.success(t("network.l3.attached"));
      setInstanceUuid(undefined);
      onClose();
    },
  });
  const taken = new Set(gateway.l3?.attachments.map((a) => a.instance_uuid));
  const options = (instances ?? [])
    .filter((i) => i.workload_type === "dev" && i.status !== "released" && !taken.has(i.uuid))
    .map((i) => ({
      value: i.uuid,
      label: attachable(i) ? i.name : `${i.name} · ${t("network.l3.kataUnsupported")}`,
      disabled: !attachable(i),
    }));
  return (
    <Modal
      title={t("network.l3.attachTitle")}
      open={open}
      onCancel={onClose}
      onOk={() => instanceUuid && attach.mutate({ uuid: gateway.uuid, instanceUuid })}
      okText={t("network.l3.attachSubmit")}
      okButtonProps={{ disabled: instanceUuid === undefined }}
      confirmLoading={attach.isPending}
    >
      <Space orientation="vertical" size={space.md} style={{ width: "100%" }}>
        <Typography.Text type="secondary">{t("network.l3.attachHint")}</Typography.Text>
        <Field label={t("network.l3.instance")} required>
          <Select
            value={instanceUuid}
            onChange={setInstanceUuid}
            placeholder={t("network.forward.instancePlaceholder")}
            options={options}
            aria-label={t("network.l3.instance")}
          />
        </Field>
      </Space>
    </Modal>
  );
}

/** Members' instances grouped by member; ineligible ones stay listed with the reason. */
function OrgAttachModal({ gateway, open, onClose }: { gateway: AnyGateway; open: boolean; onClose: () => void }) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const { data: candidates } = useOrgInstances({ enabled: open });
  const [instanceUuid, setInstanceUuid] = useState<string>();
  const attach = useAttachOrgInstance({
    onSuccess: () => {
      message.success(t("network.l3.attached"));
      setInstanceUuid(undefined);
      onClose();
    },
  });
  const groups = new Map<string, OrgInstanceOut[]>();
  for (const c of candidates ?? []) groups.set(c.member_label, [...(groups.get(c.member_label) ?? []), c]);
  const suffix = (c: OrgInstanceOut) =>
    c.reason === "kata_tier"
      ? ` · ${t("network.l3.kataUnsupported")}`
      : c.reason === "attached"
        ? ` · ${c.attached_to === gateway.uuid ? t("network.org.attachedHere") : t("network.org.attachedElsewhere")}`
        : "";
  const options = [...groups.entries()].map(([label, items]) => ({
    label,
    title: label,
    options: items.map((c) => ({
      value: c.instance_uuid,
      label: `${c.instance_name}${suffix(c)}`,
      disabled: !c.attachable,
    })),
  }));
  return (
    <Modal
      title={t("network.l3.attachTitle")}
      open={open}
      onCancel={onClose}
      onOk={() => instanceUuid && attach.mutate({ uuid: gateway.uuid, instanceUuid })}
      okText={t("network.l3.attachSubmit")}
      okButtonProps={{ disabled: instanceUuid === undefined }}
      confirmLoading={attach.isPending}
    >
      <Space orientation="vertical" size={space.md} style={{ width: "100%" }}>
        <Typography.Text type="secondary">{t("network.org.attachHint")}</Typography.Text>
        <Field label={t("network.l3.instance")} required>
          <Select
            showSearch={{ optionFilterProp: "label" }}
            value={instanceUuid}
            onChange={setInstanceUuid}
            placeholder={t("network.forward.instancePlaceholder")}
            options={options}
            aria-label={t("network.l3.instance")}
          />
        </Field>
      </Space>
    </Modal>
  );
}

function LogsModal({
  gateway,
  org,
  open,
  onClose,
}: {
  gateway: AnyGateway;
  org: boolean;
  open: boolean;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const personalLogs = useGatewayLogs(gateway.uuid, { tail_lines: 300 }, { enabled: open && !org });
  const orgLogs = useOrgGatewayLogs(gateway.uuid, { tail_lines: 300 }, { enabled: open && org });
  const { data, isFetching, refetch } = org ? orgLogs : personalLogs;
  return (
    <Modal
      title={t("network.logsTitle", { name: gateway.name })}
      open={open}
      onCancel={onClose}
      width={900}
      footer={
        <Space>
          <Button loading={isFetching} onClick={() => void refetch()}>
            {t("network.logsRefresh")}
          </Button>
          <Button type="primary" onClick={onClose}>
            {t("network.close")}
          </Button>
        </Space>
      }
    >
      {data?.lines.length === 0 ? (
        <Typography.Text type="secondary">{t("network.logsEmpty")}</Typography.Text>
      ) : (
        <Mono block style={{ maxHeight: 480, overflow: "auto", whiteSpace: "pre-wrap" }}>
          {(data?.lines ?? []).join("\n")}
        </Mono>
      )}
    </Modal>
  );
}

export function GatewayDetailDrawer({
  gateway,
  scope = "personal",
  egressIps,
  socksPort,
  maxForwards,
  onClose,
  onEdit,
}: {
  gateway: AnyGateway | null;
  scope?: GatewayScope;
  egressIps: string[];
  socksPort: number;
  maxForwards: number;
  onClose: () => void;
  onEdit: (g: AnyGateway) => void;
}) {
  const { t } = useTranslation();
  const confirm = useConfirm();
  const { message } = App.useApp();
  const [adding, setAdding] = useState(false);
  const [attaching, setAttaching] = useState(false);
  const [logsOpen, setLogsOpen] = useState(false);
  const del = useDeleteForward({ onSuccess: () => message.success(t("network.forward.deleted")) });
  const detachPersonal = useDetachInstance({ onSuccess: () => message.success(t("network.l3.detached")) });
  const detachOrg = useDetachOrgInstance({ onSuccess: () => message.success(t("network.l3.detached")) });
  if (!gateway) return <Drawer open={false} />;
  const g = gateway;
  const org = scope === "org";
  const personal: GatewayOut | null = !org && "socks_url" in g ? g : null;
  const detach = org ? detachOrg : detachPersonal;
  const deleting = g.state === "deleting";
  const tunnelAddr = g.local_address ?? t("network.assignedByServer");
  const target = (f: ForwardOut) =>
    f.kind === "intranet" ? `${f.target_host ?? ""}:${f.target_port}` : `${f.instance_name ?? "—"}:${f.target_port}`;
  const entry = (f: ForwardOut) => (f.kind === "intranet" ? `${f.name}:${f.port}` : `${tunnelAddr}:${f.port}`);

  return (
    <Drawer
      title={
        <Space size={space.sm}>
          <span>{g.name}</span>
          <StatusTag map={netgwStateMap} value={g.state} variant="badge" />
        </Space>
      }
      open
      onClose={onClose}
      size={drawerWidth.lg}
      extra={
        <Space>
          <Button onClick={() => setLogsOpen(true)}>{t("network.viewLogs")}</Button>
          <GatedButton reason={deleting ? t("network.deletingReason") : undefined} onClick={() => onEdit(g)}>
            {t("network.edit")}
          </GatedButton>
        </Space>
      }
    >
      <Space orientation="vertical" size={space.lg} style={{ width: "100%" }}>
        <Card size="small" title={t("network.detail.connection")}>
          <KeyValue
            columns={1}
            items={[
              { label: t("network.col.protocol"), value: t(`network.protocol.${g.protocol}`) },
              { label: t("network.detail.peer"), value: peerOf(g), mono: true },
              { label: t("network.detail.remote"), value: g.remote_cidrs.join(", "), mono: true },
              { label: t("network.detail.tunnelAddress"), value: tunnelAddr, mono: Boolean(g.local_address) },
              ...(g.tunnel.protocol === "wireguard"
                ? [
                    {
                      label: t("network.detail.publicKey"),
                      value: g.tunnel.public_key,
                      copy: g.tunnel.public_key,
                      mono: true,
                      hint: t("network.detail.publicKeyHint"),
                    },
                  ]
                : []),
              {
                label: t("network.detail.egress"),
                value: egressIps.length ? egressIps.join(", ") : t("network.egressUnknown"),
                hint: t("network.detail.egressHint"),
              },
              {
                label: t("network.detail.dns"),
                value: g.dns ? `${g.dns.domains.join(", ")} → ${g.dns.servers.join(", ")}` : null,
              },
              {
                label: t("network.detail.tunnelChanged"),
                value: g.tunnel_changed_at ? formatDateTime(g.tunnel_changed_at) : null,
              },
            ]}
          />
        </Card>
        {personal && (
          <>
            <Card size="small" title={t("network.detail.usage")}>
              <Space orientation="vertical" size={space.sm} style={{ width: "100%" }}>
                <Typography.Text>{t("network.detail.socksLine")}</Typography.Text>
                <CopyField value={`export ALL_PROXY=${personal.socks_url}`} mono />
                <Typography.Text type="secondary">{t("network.detail.socksNote", { port: socksPort })}</Typography.Text>
                <Typography.Text>{t("network.detail.forwardLine")}</Typography.Text>
              </Space>
            </Card>
            <Card
              size="small"
              title={t("network.detail.forwards", { n: personal.forwards.length, max: maxForwards })}
              extra={
                <GatedButton
                  size="small"
                  type="primary"
                  reason={
                    deleting
                      ? t("network.deletingReason")
                      : personal.forwards.length >= maxForwards
                        ? t("network.forward.quotaReached", { max: maxForwards })
                        : undefined
                  }
                  onClick={() => setAdding(true)}
                >
                  {t("network.forward.add")}
                </GatedButton>
              }
            >
              <Table<ForwardOut>
                rowKey="id"
                size="small"
                pagination={false}
                dataSource={personal.forwards}
                locale={{ emptyText: t("network.forward.empty") }}
                columns={[
                  { title: t("network.forward.colKind"), render: (_, f) => t(`network.forward.kind.${f.kind}`) },
                  { title: t("network.forward.colEntry"), render: (_, f) => <CopyField value={entry(f)} mono /> },
                  {
                    title: t("network.forward.colTarget"),
                    render: (_, f) =>
                      f.available ? (
                        <Mono>{target(f)}</Mono>
                      ) : (
                        <Typography.Text type="secondary">{t("network.forward.unavailable")}</Typography.Text>
                      ),
                  },
                  {
                    title: t("network.col.actions"),
                    render: (_, f) => (
                      <GatedButton
                        size="small"
                        danger
                        reason={deleting ? t("network.deletingReason") : undefined}
                        onClick={() =>
                          confirm({
                            title: t("network.forward.deleteTitle", { name: f.name }),
                            consequences: [t("network.forward.deleteConsequence", { entry: entry(f) })],
                            okText: t("network.forward.deleteOk"),
                            cancelText: t("network.cancel"),
                            danger: true,
                            onOk: () => del.mutate({ uuid: personal.uuid, forwardId: f.id }),
                          })
                        }
                      >
                        {t("network.forward.delete")}
                      </GatedButton>
                    ),
                  },
                ]}
              />
            </Card>
          </>
        )}
        {g.l3 && (
          <Card
            size="small"
            title={t("network.l3.title", { n: g.l3.attachments.length, max: g.l3.max_attachments })}
            extra={
              <GatedButton
                size="small"
                type="primary"
                reason={
                  deleting
                    ? t("network.deletingReason")
                    : g.l3.attachments.length >= g.l3.max_attachments
                      ? t("network.l3.full")
                      : undefined
                }
                onClick={() => setAttaching(true)}
              >
                {t("network.l3.attach")}
              </GatedButton>
            }
          >
            <Space orientation="vertical" size={space.md} style={{ width: "100%" }}>
              <KeyValue
                columns={1}
                items={[
                  { label: t("network.l3.cidr"), value: g.l3.cidr, mono: true },
                  { label: t("network.l3.mode"), value: g.l3.nat ? t("network.l3.nat") : t("network.l3.routed") },
                  { label: t("network.l3.hub"), value: g.l3.address, copy: g.l3.address, mono: true },
                ]}
              />
              <Typography.Text type="secondary">
                {g.l3.nat ? t("network.l3.natHint") : t(`network.l3.routedPeer.${g.protocol}`, { cidr: g.l3.cidr })}
                {" · "}
                {t("network.l3.hubHint")}
              </Typography.Text>
              <Table<AttachmentOut>
                rowKey="instance_uuid"
                size="small"
                pagination={false}
                scroll={{ x: 520 }}
                dataSource={g.l3.attachments}
                locale={{ emptyText: t("network.l3.empty") }}
                columns={[
                  ...(org
                    ? [
                        {
                          title: t("network.org.colMember"),
                          render: (_: unknown, a: AttachmentOut) => a.member_label ?? "—",
                        },
                      ]
                    : []),
                  { title: t("network.l3.colInstance"), render: (_, a) => a.instance_name },
                  { title: t("network.l3.colAddress"), render: (_, a) => <CopyField value={a.address} mono /> },
                  {
                    title: t("network.l3.colState"),
                    render: (_, a) =>
                      a.pending_restart ? (
                        <Typography.Text type="warning">{t("network.l3.pendingRestart")}</Typography.Text>
                      ) : (
                        <Typography.Text type="secondary">{t("network.l3.applied")}</Typography.Text>
                      ),
                  },
                  {
                    title: t("network.col.actions"),
                    render: (_, a) => (
                      <GatedButton
                        size="small"
                        danger
                        reason={deleting ? t("network.deletingReason") : undefined}
                        onClick={() =>
                          confirm({
                            title: t("network.l3.detachTitle", { name: a.instance_name }),
                            consequences: [t("network.l3.detachConsequence", { address: a.address })],
                            okText: t("network.l3.detachOk"),
                            cancelText: t("network.cancel"),
                            danger: true,
                            onOk: () => detach.mutate({ uuid: g.uuid, instanceUuid: a.instance_uuid }),
                          })
                        }
                      >
                        {t("network.l3.detach")}
                      </GatedButton>
                    ),
                  },
                ]}
              />
            </Space>
          </Card>
        )}
      </Space>
      {personal && <AddForwardModal gateway={personal} open={adding} onClose={() => setAdding(false)} />}
      {org ? (
        <OrgAttachModal gateway={g} open={attaching} onClose={() => setAttaching(false)} />
      ) : (
        <AttachModal gateway={g} open={attaching} onClose={() => setAttaching(false)} />
      )}
      <LogsModal gateway={g} org={org} open={logsOpen} onClose={() => setLogsOpen(false)} />
    </Drawer>
  );
}
