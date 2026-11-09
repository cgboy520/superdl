/** Tenant storage (owners and admins): directories on the platform's Ceph cluster, each reached with the tenant's own cephx key, mounted at /mnt/org/<name> in every member's instances from their next start. The platform checks each path with the key before mounting it; the key never comes back. */

import type { OrgMountOut } from "@superdl/api-client";
import { fontSize, formatDateTime, orgMountStatusMap, space } from "@superdl/ui";
import { GatedButton, RowActions, StatusTag, TableErrorEmpty, useConfirm } from "@superdl/ui/components";
import { Alert, App, Card, Checkbox, Input, Modal, Space, Switch, Table, Typography } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useCreateOrgMount, useDeleteOrgMount, useRecheckOrgMount, useUpdateOrgMount } from "../../api/mutations";
import { useOrgMounts } from "../../api/queries";
import { ORG_MOUNT_ROOT } from "../../lib/disks";
import { Field } from "../Field";

const NAME_RE = /^[a-z0-9][a-z0-9._-]{0,39}$/;
const PATH_RE = /^\/(?:[A-Za-z0-9._@+=-]+\/?)*$/;
const USER_RE = /^(?:client\.)?[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;
const KEY_RE = /^[A-Za-z0-9+/]{20,120}={0,2}$/;

const pathOk = (p: string) => PATH_RE.test(p) && !p.split("/").some((seg) => seg === "." || seg === "..");

function MountModal({ mount, open, onClose }: { mount: OrgMountOut | null; open: boolean; onClose: () => void }) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const editing = mount != null;
  const [name, setName] = useState(mount?.name ?? "");
  const [path, setPath] = useState(mount?.ceph_path ?? "");
  const [user, setUser] = useState(mount?.ceph_user ?? "");
  const [key, setKey] = useState("");
  const [readOnly, setReadOnly] = useState(mount?.read_only ?? true);
  const done = () => {
    message.success(editing ? t("org.storage.saved") : t("org.storage.added"));
    onClose();
  };
  const create = useCreateOrgMount({ onSuccess: done });
  const update = useUpdateOrgMount({ onSuccess: done });
  const errors = {
    name: !editing && name && !NAME_RE.test(name) ? t("org.storage.nameInvalid") : null,
    path: path && !pathOk(path.trim()) ? t("org.storage.pathInvalid") : null,
    user: user && !USER_RE.test(user.trim()) ? t("org.storage.userInvalid") : null,
    key: key && !KEY_RE.test(key.trim()) ? t("org.storage.keyInvalid") : null,
  };
  const ready = editing
    ? Object.values(errors).every((e) => e == null) && path.trim() !== "" && user.trim() !== ""
    : name !== "" &&
      path.trim() !== "" &&
      user.trim() !== "" &&
      key.trim() !== "" &&
      Object.values(errors).every((e) => e == null);
  const submit = () => {
    if (!ready) return;
    if (mount) {
      update.mutate({
        uuid: mount.uuid,
        body: {
          ...(path.trim() !== mount.ceph_path ? { ceph_path: path.trim() } : {}),
          ...(user.trim() !== mount.ceph_user ? { ceph_user: user.trim() } : {}),
          ...(key.trim() ? { ceph_key: key.trim() } : {}),
          ...(readOnly !== mount.read_only ? { read_only: readOnly } : {}),
        },
      });
      return;
    }
    create.mutate({ name, ceph_path: path.trim(), ceph_user: user.trim(), ceph_key: key.trim(), read_only: readOnly });
  };
  return (
    <Modal
      title={mount ? t("org.storage.editTitle", { name: mount.name }) : t("org.storage.addTitle")}
      open={open}
      onCancel={onClose}
      onOk={submit}
      okButtonProps={{ disabled: !ready }}
      confirmLoading={create.isPending || update.isPending}
      width={640}
      destroyOnHidden
    >
      <Space orientation="vertical" size={space.md} style={{ width: "100%" }}>
        <Typography.Text type="secondary">{t("org.storage.formHint")}</Typography.Text>
        {!editing && (
          <Field
            label={t("org.storage.name")}
            required
            error={errors.name}
            hint={t("org.storage.nameHint", { path: `${ORG_MOUNT_ROOT}/${name || "<name>"}` })}
          >
            <Input
              value={name}
              maxLength={40}
              onChange={(e) => setName(e.target.value.toLowerCase())}
              aria-label={t("org.storage.name")}
              className="mono"
            />
          </Field>
        )}
        <Field label={t("org.storage.cephPath")} required error={errors.path} hint={t("org.storage.cephPathHint")}>
          <Input
            value={path}
            maxLength={255}
            onChange={(e) => setPath(e.target.value)}
            aria-label={t("org.storage.cephPath")}
            className="mono"
            placeholder="/volumes/team-a/datasets"
          />
        </Field>
        <Field label={t("org.storage.cephUser")} required error={errors.user} hint={t("org.storage.cephUserHint")}>
          <Input
            value={user}
            maxLength={71}
            onChange={(e) => setUser(e.target.value)}
            aria-label={t("org.storage.cephUser")}
            className="mono"
            placeholder="client.team-a"
            autoComplete="off"
          />
        </Field>
        <Field
          label={t("org.storage.cephKey")}
          required={!editing}
          error={errors.key}
          hint={editing ? t("org.storage.cephKeyKeep") : t("org.storage.cephKeyHint")}
        >
          <Input.Password
            value={key}
            maxLength={128}
            onChange={(e) => setKey(e.target.value)}
            aria-label={t("org.storage.cephKey")}
            className="mono"
            autoComplete="new-password"
          />
        </Field>
        <Checkbox checked={readOnly} onChange={(e) => setReadOnly(e.target.checked)}>
          {t("org.storage.readOnly")}
        </Checkbox>
        <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
          {t("org.storage.nextStart")}
        </Typography.Text>
      </Space>
    </Modal>
  );
}

export function StorageTab() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const confirm = useConfirm();
  const q = useOrgMounts();
  const [editing, setEditing] = useState<OrgMountOut | null>(null);
  const [adding, setAdding] = useState(false);
  const update = useUpdateOrgMount();
  const recheck = useRecheckOrgMount({ onSuccess: () => message.success(t("org.storage.rechecking")) });
  const remove = useDeleteOrgMount({ onSuccess: () => message.success(t("org.storage.removed")) });
  return (
    <Space orientation="vertical" size={space.lg} style={{ width: "100%" }}>
      <Alert type="info" showIcon title={t("org.storage.intro", { root: ORG_MOUNT_ROOT })} />
      <Card
        size="small"
        title={t("org.storage.title")}
        extra={
          <GatedButton size="small" type="primary" onClick={() => setAdding(true)}>
            {t("org.storage.add")}
          </GatedButton>
        }
      >
        {q.isError ? (
          <TableErrorEmpty isError compact onRetry={() => void q.refetch()} />
        ) : (
          <Table<OrgMountOut>
            rowKey="uuid"
            size="small"
            loading={q.isLoading}
            pagination={false}
            scroll={{ x: 900 }}
            dataSource={q.data ?? []}
            locale={{ emptyText: t("org.storage.empty") }}
            columns={[
              {
                title: t("org.storage.colMount"),
                render: (_, m) => (
                  <Space orientation="vertical" size={0}>
                    <Typography.Text code>{m.mount_path}</Typography.Text>
                    <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
                      {m.read_only ? t("org.storage.readOnlyShort") : t("org.storage.readWriteShort")}
                    </Typography.Text>
                  </Space>
                ),
              },
              {
                title: t("org.storage.colSource"),
                render: (_, m) => (
                  <Space orientation="vertical" size={0}>
                    <Typography.Text className="mono">{m.ceph_path}</Typography.Text>
                    <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }} className="mono">
                      client.{m.ceph_user}
                    </Typography.Text>
                  </Space>
                ),
              },
              {
                title: t("org.storage.colStatus"),
                render: (_, m) => (
                  <Space orientation="vertical" size={0} style={{ maxWidth: 280 }}>
                    <StatusTag map={orgMountStatusMap} value={m.status} variant="badge" />
                    {m.status === "failed" && m.last_error && (
                      <Typography.Text type="danger" style={{ fontSize: fontSize.caption }}>
                        {m.last_error}
                      </Typography.Text>
                    )}
                    {m.checked_at && (
                      <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
                        {t("org.storage.checkedAt", { time: formatDateTime(m.checked_at) })}
                      </Typography.Text>
                    )}
                  </Space>
                ),
              },
              {
                title: t("org.storage.colEnabled"),
                render: (_, m) => (
                  <Switch
                    size="small"
                    checked={m.enabled}
                    aria-label={t("org.storage.colEnabled")}
                    loading={update.isPending && update.variables.uuid === m.uuid}
                    onChange={(v) => update.mutate({ uuid: m.uuid, body: { enabled: v } })}
                  />
                ),
              },
              {
                title: t("org.colActions"),
                render: (_, m) => (
                  <RowActions
                    primary={
                      <GatedButton size="small" onClick={() => setEditing(m)}>
                        {t("org.storage.edit")}
                      </GatedButton>
                    }
                    more={[
                      {
                        key: "check",
                        label: t("org.storage.recheck"),
                        reason: m.status === "checking" ? t("org.storage.checkingNow") : undefined,
                        onClick: () => recheck.mutate(m.uuid),
                      },
                      {
                        key: "remove",
                        label: t("org.storage.remove"),
                        danger: true,
                        onClick: () =>
                          confirm({
                            title: t("org.storage.removeTitle", { path: m.mount_path }),
                            consequences: [t("org.storage.removeConsequence")],
                            okText: t("org.storage.remove"),
                            cancelText: t("org.cancel"),
                            danger: true,
                            onOk: () => remove.mutate(m.uuid),
                          }),
                      },
                    ]}
                  />
                ),
              },
            ]}
          />
        )}
      </Card>
      {(adding || editing) && (
        <MountModal
          mount={editing}
          open
          onClose={() => {
            setAdding(false);
            setEditing(null);
          }}
        />
      )}
    </Space>
  );
}
