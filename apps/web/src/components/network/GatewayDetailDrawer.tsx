/** Gateway detail drawer: connection facts (what to configure on the tenant side), how instances use it, forwards (add / delete) and the gateway log. */

import type { ForwardOut, GatewayOut } from "@superdl/api-client";
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

import { useAddForward, useDeleteForward } from "../../api/mutations";
import { useGatewayLogs, useInstances } from "../../api/queries";
import { Field } from "../Field";

type Kind = ForwardOut["kind"];

function peerOf(g: GatewayOut): string {
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

function LogsModal({ gateway, open, onClose }: { gateway: GatewayOut; open: boolean; onClose: () => void }) {
  const { t } = useTranslation();
  const { data, isFetching, refetch } = useGatewayLogs(gateway.uuid, { tail_lines: 300 }, { enabled: open });
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
  egressIps,
  socksPort,
  maxForwards,
  onClose,
  onEdit,
}: {
  gateway: GatewayOut | null;
  egressIps: string[];
  socksPort: number;
  maxForwards: number;
  onClose: () => void;
  onEdit: (g: GatewayOut) => void;
}) {
  const { t } = useTranslation();
  const confirm = useConfirm();
  const { message } = App.useApp();
  const [adding, setAdding] = useState(false);
  const [logsOpen, setLogsOpen] = useState(false);
  const del = useDeleteForward({ onSuccess: () => message.success(t("network.forward.deleted")) });
  if (!gateway) return <Drawer open={false} />;
  const g = gateway;
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
        <Card size="small" title={t("network.detail.usage")}>
          <Space orientation="vertical" size={space.sm} style={{ width: "100%" }}>
            <Typography.Text>{t("network.detail.socksLine")}</Typography.Text>
            <CopyField value={`export ALL_PROXY=${g.socks_url}`} mono />
            <Typography.Text type="secondary">{t("network.detail.socksNote", { port: socksPort })}</Typography.Text>
            <Typography.Text>{t("network.detail.forwardLine")}</Typography.Text>
          </Space>
        </Card>
        <Card
          size="small"
          title={t("network.detail.forwards", { n: g.forwards.length, max: maxForwards })}
          extra={
            <GatedButton
              size="small"
              type="primary"
              reason={
                deleting
                  ? t("network.deletingReason")
                  : g.forwards.length >= maxForwards
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
            dataSource={g.forwards}
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
                        onOk: () => del.mutate({ uuid: g.uuid, forwardId: f.id }),
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
      </Space>
      <AddForwardModal gateway={g} open={adding} onClose={() => setAdding(false)} />
      <LogsModal gateway={g} open={logsOpen} onClose={() => setLogsOpen(false)} />
    </Drawer>
  );
}
