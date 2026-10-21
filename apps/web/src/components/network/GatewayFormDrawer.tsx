/** Create / edit drawer of a private-network gateway: name, tunnelled networks, optional split DNS, the protocol card, then whole-subnet access (L3). Secrets are write-only: on edit an empty secret keeps the stored one. scope="org" edits an organization gateway: whole-subnet access is always on. */

import type { GatewayIn, GatewayOut, OrgGatewayOut } from "@superdl/api-client";
import { drawerWidth, idemKeyOf, space } from "@superdl/ui";
import { OptionTileGroup } from "@superdl/ui/components";
import { App, Button, Card, Drawer, Input, InputNumber, Segmented, Select, Space, Switch, Typography } from "antd";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { useCreateGateway, useCreateOrgGateway, useUpdateGateway, useUpdateOrgGateway } from "../../api/mutations";
import { useLeaveGuard } from "../../lib/useLeaveGuard";
import { Field } from "../Field";

export type GatewayProtocol = GatewayOut["protocol"];
export type AnyGateway = GatewayOut | OrgGatewayOut;
export type GatewayScope = "personal" | "org";
const PROTOCOLS: GatewayProtocol[] = ["wireguard", "ipsec", "openvpn", "openconnect"];
const FLAVORS = ["anyconnect", "fortinet", "gp", "pulse", "f5", "array"] as const;
const PROPOSALS = ["compatible", "modern", "default"] as const;

export interface FormState {
  name: string;
  protocol: GatewayProtocol;
  remote: string[];
  local: string;
  dnsServers: string[];
  dnsDomains: string[];
  endpoint: string;
  peerKey: string;
  psk: string;
  mtu: number;
  ipsecVersion: 1 | 2;
  peer: string;
  localId: string;
  remoteId: string;
  proposals: (typeof PROPOSALS)[number];
  profile: string;
  username: string;
  password: string;
  flavor: (typeof FLAVORS)[number];
  server: string;
  servercert: string;
  authgroup: string;
  l3: boolean;
  l3Cidr: string;
  l3Nat: boolean;
}

export const EMPTY: FormState = {
  name: "",
  protocol: "wireguard",
  remote: [],
  local: "",
  dnsServers: [],
  dnsDomains: [],
  endpoint: "",
  peerKey: "",
  psk: "",
  mtu: 1420,
  ipsecVersion: 2,
  peer: "",
  localId: "",
  remoteId: "",
  proposals: "compatible",
  profile: "",
  username: "",
  password: "",
  flavor: "anyconnect",
  server: "",
  servercert: "",
  authgroup: "",
  l3: false,
  l3Cidr: "",
  l3Nat: false,
};

export function fromGateway(g: AnyGateway): FormState {
  const base: FormState = {
    ...EMPTY,
    name: g.name,
    protocol: g.protocol,
    remote: g.remote_cidrs,
    local: g.local_address ?? "",
    dnsServers: g.dns?.servers ?? [],
    dnsDomains: g.dns?.domains ?? [],
    l3: g.l3 != null,
    l3Cidr: g.l3?.cidr ?? "",
    l3Nat: g.l3?.nat ?? false,
  };
  const tn = g.tunnel;
  switch (tn.protocol) {
    case "wireguard":
      return { ...base, endpoint: tn.endpoint, peerKey: tn.peer_public_key, mtu: tn.mtu };
    case "ipsec":
      return {
        ...base,
        ipsecVersion: tn.version === 1 ? 1 : 2,
        peer: tn.peer,
        localId: tn.local_id ?? "",
        remoteId: tn.remote_id ?? "",
        proposals: tn.proposals,
      };
    case "openvpn":
      return { ...base, username: tn.username ?? "" };
    case "openconnect":
      return {
        ...base,
        flavor: tn.flavor,
        server: tn.server,
        username: tn.username,
        servercert: tn.servercert ?? "",
        authgroup: tn.authgroup ?? "",
      };
  }
}

const opt = (s: string) => (s.trim() ? s.trim() : null);

export function toBody(f: FormState): GatewayIn {
  const dns = f.dnsServers.length || f.dnsDomains.length ? { servers: f.dnsServers, domains: f.dnsDomains } : null;
  const common = {
    name: f.name.trim(),
    remote_cidrs: f.remote,
    local_address: f.protocol === "wireguard" || f.protocol === "ipsec" ? opt(f.local) : null,
    dns,
    l3: f.l3 ? { cidr: f.l3Cidr.trim(), nat: f.protocol === "openconnect" || f.l3Nat } : null,
  };
  switch (f.protocol) {
    case "wireguard":
      return {
        ...common,
        tunnel: {
          protocol: "wireguard",
          endpoint: f.endpoint.trim(),
          peer_public_key: f.peerKey.trim(),
          preshared_key: opt(f.psk),
          mtu: f.mtu,
        },
      };
    case "ipsec":
      return {
        ...common,
        tunnel: {
          protocol: "ipsec",
          version: f.ipsecVersion,
          peer: f.peer.trim(),
          psk: f.psk === "" ? null : f.psk,
          local_id: opt(f.localId),
          remote_id: opt(f.remoteId),
          proposals: f.proposals,
        },
      };
    case "openvpn":
      return {
        ...common,
        tunnel: {
          protocol: "openvpn",
          config: f.profile.trim() ? f.profile : null,
          username: opt(f.username),
          password: f.password === "" ? null : f.password,
        },
      };
    case "openconnect":
      return {
        ...common,
        tunnel: {
          protocol: "openconnect",
          flavor: f.flavor,
          server: f.server.trim(),
          username: f.username.trim(),
          password: f.password === "" ? null : f.password,
          servercert: opt(f.servercert),
          authgroup: opt(f.authgroup),
        },
      };
  }
}

/** Tag input for lists (networks / DNS); no options, Enter or comma adds an item. */
function TagsInput({
  value,
  onChange,
  placeholder,
  ariaLabel,
}: {
  value: string[];
  onChange: (v: string[]) => void;
  placeholder: string;
  ariaLabel: string;
}) {
  return (
    <Select
      mode="tags"
      open={false}
      tokenSeparators={[",", " "]}
      value={value}
      onChange={(v: string[]) => onChange(v.map((x) => x.trim()).filter(Boolean))}
      placeholder={placeholder}
      aria-label={ariaLabel}
      style={{ width: "100%" }}
    />
  );
}

export function GatewayFormDrawer({
  open,
  gateway,
  scope = "personal",
  onClose,
  onSaved,
}: {
  open: boolean;
  /** null = create */
  gateway: AnyGateway | null;
  scope?: GatewayScope;
  onClose: () => void;
  onSaved: (g: AnyGateway, created: boolean) => void;
}) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const org = scope === "org";
  const initial = useMemo(
    () => (gateway ? fromGateway(gateway) : org ? { ...EMPTY, l3: true } : EMPTY),
    [gateway, org],
  );
  const [form, setForm] = useState<FormState>(initial);
  const [seq, setSeq] = useState(() => Date.now());
  const [lastInitial, setLastInitial] = useState(initial);
  if (lastInitial !== initial) {
    setLastInitial(initial);
    setForm(initial);
  }
  const set = <K extends keyof FormState>(k: K, v: FormState[K]) => setForm((f) => ({ ...f, [k]: v }));
  const dirty = open && JSON.stringify(form) !== JSON.stringify(initial);
  const leave = useLeaveGuard(dirty);
  const editing = gateway !== null;

  const created = (g: AnyGateway) => {
    message.success(t("network.created"));
    setSeq((s) => s + 1);
    leave.bypass();
    onSaved(g, true);
  };
  const saved = (g: AnyGateway) => {
    message.success(t("network.saved"));
    leave.bypass();
    onSaved(g, false);
  };
  const create = useCreateGateway({ onSuccess: created });
  const update = useUpdateGateway({ onSuccess: saved });
  const createOrg = useCreateOrgGateway({ onSuccess: created });
  const updateOrg = useUpdateOrgGateway({ onSuccess: saved });
  const pending = create.isPending || update.isPending || createOrg.isPending || updateOrg.isPending;
  const submit = () => {
    const body = toBody(form);
    const idempotencyKey = idemKeyOf(org ? "netgw-org" : "netgw", [seq, body.name]);
    if (gateway) (org ? updateOrg : update).mutate({ uuid: gateway.uuid, body });
    else (org ? createOrg : create).mutate({ body, idempotencyKey });
  };
  const close = () => (dirty ? leave.confirmLeave(onClose) : onClose());
  const secretHint = editing ? t("network.form.secretKeep") : undefined;
  const needsLocal = form.protocol === "wireguard" || form.protocol === "ipsec";
  const natOnly = form.protocol === "openconnect";
  const l3Locked = (gateway?.l3?.attachments.length ?? 0) > 0;

  const protocolCard = () => {
    switch (form.protocol) {
      case "wireguard":
        return (
          <>
            <Field label={t("network.form.endpoint")} required hint={t("network.form.endpointHint")}>
              <Input
                value={form.endpoint}
                onChange={(e) => set("endpoint", e.target.value)}
                placeholder="vpn.example.com:51820"
                aria-label={t("network.form.endpoint")}
              />
            </Field>
            <Field label={t("network.form.peerKey")} required>
              <Input
                value={form.peerKey}
                onChange={(e) => set("peerKey", e.target.value)}
                aria-label={t("network.form.peerKey")}
              />
            </Field>
            <Field label={t("network.form.wgPsk")} hint={secretHint}>
              <Input.Password
                value={form.psk}
                onChange={(e) => set("psk", e.target.value)}
                autoComplete="new-password"
                aria-label={t("network.form.wgPsk")}
              />
            </Field>
            <Field label={t("network.form.mtu")} inline>
              <InputNumber
                min={1280}
                max={1500}
                value={form.mtu}
                onChange={(v) => set("mtu", v ?? 1420)}
                aria-label={t("network.form.mtu")}
              />
            </Field>
          </>
        );
      case "ipsec":
        return (
          <>
            <Field label={t("network.form.ikeVersion")} inline>
              <Segmented
                value={form.ipsecVersion}
                onChange={(v) => set("ipsecVersion", v === 1 ? 1 : 2)}
                options={[
                  { value: 2, label: "IKEv2" },
                  { value: 1, label: "IKEv1" },
                ]}
              />
            </Field>
            <Field label={t("network.form.peer")} required hint={t("network.form.peerHint")}>
              <Input
                value={form.peer}
                onChange={(e) => set("peer", e.target.value)}
                aria-label={t("network.form.peer")}
              />
            </Field>
            <Field label={t("network.form.psk")} required={!editing} hint={secretHint ?? t("network.form.pskHint")}>
              <Input.Password
                value={form.psk}
                onChange={(e) => set("psk", e.target.value)}
                autoComplete="new-password"
                aria-label={t("network.form.psk")}
              />
            </Field>
            <Field label={t("network.form.localId")} hint={t("network.form.localIdHint")}>
              <Input
                value={form.localId}
                onChange={(e) => set("localId", e.target.value)}
                aria-label={t("network.form.localId")}
              />
            </Field>
            <Field label={t("network.form.remoteId")}>
              <Input
                value={form.remoteId}
                onChange={(e) => set("remoteId", e.target.value)}
                aria-label={t("network.form.remoteId")}
              />
            </Field>
            <Field label={t("network.form.proposals")} hint={t(`network.proposalHint.${form.proposals}`)}>
              <Select
                value={form.proposals}
                onChange={(v) => set("proposals", v)}
                options={PROPOSALS.map((p) => ({ value: p, label: t(`network.proposal.${p}`) }))}
                aria-label={t("network.form.proposals")}
              />
            </Field>
          </>
        );
      case "openvpn":
        return (
          <>
            <Field
              label={t("network.form.profile")}
              required={!editing}
              hint={editing ? t("network.form.profileKeep") : t("network.form.profileHint")}
            >
              <Space orientation="vertical" size={space.xs} style={{ width: "100%" }}>
                <Input.TextArea
                  rows={8}
                  value={form.profile}
                  onChange={(e) => set("profile", e.target.value)}
                  spellCheck={false}
                  style={{ fontFamily: "var(--sdl-font-mono)" }}
                  aria-label={t("network.form.profile")}
                />
                <input
                  type="file"
                  accept=".ovpn,.conf,text/plain"
                  aria-label={t("network.form.profileFile")}
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (file) void file.text().then((text) => set("profile", text));
                  }}
                />
              </Space>
            </Field>
            <Field label={t("network.form.username")} hint={t("network.form.usernameOptional")}>
              <Input
                value={form.username}
                onChange={(e) => set("username", e.target.value)}
                autoComplete="off"
                aria-label={t("network.form.username")}
              />
            </Field>
            <Field label={t("network.form.password")} hint={secretHint}>
              <Input.Password
                value={form.password}
                onChange={(e) => set("password", e.target.value)}
                autoComplete="new-password"
                aria-label={t("network.form.password")}
              />
            </Field>
          </>
        );
      case "openconnect":
        return (
          <>
            <Field label={t("network.form.flavor")}>
              <Select
                value={form.flavor}
                onChange={(v) => set("flavor", v)}
                options={FLAVORS.map((f) => ({ value: f, label: t(`network.flavor.${f}`) }))}
                aria-label={t("network.form.flavor")}
              />
            </Field>
            <Field label={t("network.form.server")} required hint={t("network.form.serverHint")}>
              <Input
                value={form.server}
                onChange={(e) => set("server", e.target.value)}
                placeholder="https://vpn.example.com"
                aria-label={t("network.form.server")}
              />
            </Field>
            <Field label={t("network.form.username")} required>
              <Input
                value={form.username}
                onChange={(e) => set("username", e.target.value)}
                autoComplete="off"
                aria-label={t("network.form.username")}
              />
            </Field>
            <Field
              label={t("network.form.password")}
              required={!editing}
              hint={secretHint ?? t("network.form.mfaNote")}
            >
              <Input.Password
                value={form.password}
                onChange={(e) => set("password", e.target.value)}
                autoComplete="new-password"
                aria-label={t("network.form.password")}
              />
            </Field>
            <Field label={t("network.form.servercert")} hint={t("network.form.servercertHint")}>
              <Input
                value={form.servercert}
                onChange={(e) => set("servercert", e.target.value)}
                placeholder="pin-sha256:…"
                aria-label={t("network.form.servercert")}
              />
            </Field>
            <Field label={t("network.form.authgroup")}>
              <Input
                value={form.authgroup}
                onChange={(e) => set("authgroup", e.target.value)}
                aria-label={t("network.form.authgroup")}
              />
            </Field>
          </>
        );
    }
  };

  return (
    <Drawer
      title={
        editing
          ? t("network.editTitle", { name: gateway.name })
          : org
            ? t("network.createOrgTitle")
            : t("network.createTitle")
      }
      open={open}
      onClose={close}
      size={drawerWidth.lg}
      destroyOnHidden
      footer={
        <Space style={{ width: "100%", justifyContent: "flex-end" }}>
          <Button onClick={close}>{t("network.cancel")}</Button>
          <Button type="primary" loading={pending} onClick={submit}>
            {editing ? t("network.saveChanges") : t("network.createSubmit")}
          </Button>
        </Space>
      }
    >
      <Space orientation="vertical" size={space.lg} style={{ width: "100%" }}>
        <Card size="small" title={t("network.form.sectionNetwork")}>
          <Space orientation="vertical" size={space.md} style={{ width: "100%" }}>
            <Field label={t("network.form.name")} required hint={t("network.form.nameHint")}>
              <Input
                value={form.name}
                maxLength={40}
                onChange={(e) => set("name", e.target.value.toLowerCase())}
                placeholder="corp"
                aria-label={t("network.form.name")}
              />
            </Field>
            <Field label={t("network.form.remote")} required hint={t("network.form.remoteHint")}>
              <TagsInput
                value={form.remote}
                onChange={(v) => set("remote", v)}
                placeholder="10.1.0.0/16"
                ariaLabel={t("network.form.remote")}
              />
            </Field>
            {needsLocal && (
              <Field label={t("network.form.local")} required hint={t("network.form.localHint")}>
                <Input
                  value={form.local}
                  onChange={(e) => set("local", e.target.value)}
                  placeholder="10.250.0.2"
                  aria-label={t("network.form.local")}
                />
              </Field>
            )}
            <Field label={t("network.form.dnsServers")} hint={t("network.form.dnsHint")}>
              <TagsInput
                value={form.dnsServers}
                onChange={(v) => set("dnsServers", v)}
                placeholder="10.1.0.53"
                ariaLabel={t("network.form.dnsServers")}
              />
            </Field>
            <Field label={t("network.form.dnsDomains")}>
              <TagsInput
                value={form.dnsDomains}
                onChange={(v) => set("dnsDomains", v)}
                placeholder="corp.example.com"
                ariaLabel={t("network.form.dnsDomains")}
              />
            </Field>
          </Space>
        </Card>
        <Card size="small" title={t("network.form.sectionTunnel")}>
          <Space orientation="vertical" size={space.md} style={{ width: "100%" }}>
            {editing ? (
              <Typography.Text>{t(`network.protocol.${form.protocol}`)}</Typography.Text>
            ) : (
              <OptionTileGroup<GatewayProtocol>
                label={t("network.form.protocol")}
                value={form.protocol}
                onChange={(v) => set("protocol", v)}
                columns={2}
                size="sm"
                options={PROTOCOLS.map((p) => ({
                  value: p,
                  title: t(`network.protocol.${p}`),
                  description: t(`network.protocolHint.${p}`),
                }))}
              />
            )}
            {protocolCard()}
          </Space>
        </Card>
        <Card
          size="small"
          title={t("network.form.sectionL3")}
          extra={
            <Switch
              checked={form.l3}
              disabled={org || l3Locked}
              onChange={(v) => set("l3", v)}
              aria-label={t("network.form.l3Enable")}
            />
          }
        >
          <Space orientation="vertical" size={space.md} style={{ width: "100%" }}>
            <Typography.Text type="secondary">
              {org ? t("network.form.l3OrgRequired") : l3Locked ? t("network.form.l3Locked") : t("network.form.l3Hint")}
            </Typography.Text>
            {form.l3 && (
              <>
                <Field label={t("network.form.l3Cidr")} required hint={t("network.form.l3CidrHint")}>
                  <Input
                    value={form.l3Cidr}
                    disabled={l3Locked}
                    onChange={(e) => set("l3Cidr", e.target.value)}
                    placeholder="192.168.250.0/24"
                    aria-label={t("network.form.l3Cidr")}
                  />
                </Field>
                <Field
                  label={t("network.form.l3Mode")}
                  hint={
                    natOnly
                      ? t("network.form.l3NatOnly")
                      : form.l3Nat
                        ? t("network.form.l3NatHint")
                        : t("network.form.l3RoutedHint")
                  }
                >
                  <div>
                    <Segmented<"routed" | "nat">
                      value={natOnly || form.l3Nat ? "nat" : "routed"}
                      onChange={(v) => set("l3Nat", v === "nat")}
                      options={[
                        { value: "routed", label: t("network.l3.routed"), disabled: natOnly },
                        { value: "nat", label: t("network.l3.nat") },
                      ]}
                    />
                  </div>
                </Field>
              </>
            )}
          </Space>
        </Card>
      </Space>
      {leave.modal}
    </Drawer>
  );
}
