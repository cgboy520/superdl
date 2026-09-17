/** Revision update drawer: prefilled with the current configuration, keep / override / delete secret variables, dirty-form leave confirmation. */

import { isApiError, type ServiceOut, type ServiceRevisionCreate } from "@superdl/api-client";
import { drawerWidth, idemKeyOf, marketLabelKey, space, useApiErrorText } from "@superdl/ui";
import { GatedButton, useConfirm } from "@superdl/ui/components";
import { Alert, App, Button, Card, Checkbox, Drawer, Space, Tag, Typography } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useCreateRevision } from "../../api/mutations";
import {
  buildRevisionEnv,
  commandToList,
  envRowIssue,
  isPinnedImageRef,
  newRowId,
  RESERVED_SERVICE_PORTS,
  type ArgRow,
  type EnvRow,
} from "../../lib/serviceSpec";
import { useLeaveGuard } from "../../lib/useLeaveGuard";
import { SshKeyPicker } from "../create/SshKeyPicker";
import { ContainerFields } from "./ContainerCard";
import { PublicAccessFields } from "./PublicAccessCard";

export function RevisionDrawer({
  service,
  open,
  onClose,
}: {
  service: ServiceOut;
  open: boolean;
  onClose: () => void;
}) {
  if (!open) return null;
  return <RevisionForm key={service.revision} service={service} onClose={onClose} />;
}

function RevisionForm({ service, onClose }: { service: ServiceOut; onClose: () => void }) {
  const { t } = useTranslation(["web", "shared"]);
  const { t: tErr } = useTranslation("errors");
  const { message } = App.useApp();
  const confirm = useConfirm();
  const errText = useApiErrorText();
  const c = service.container;
  const inst = service.current_instance;
  const [image, setImage] = useState(c?.image_ref ?? "");
  const [command, setCommand] = useState(c?.container_command?.join(" ") ?? "");
  const [argRows, setArgRows] = useState<ArgRow[]>(
    (c?.container_args ?? []).map((value) => ({ id: newRowId(), value })),
  );
  const [envRows, setEnvRows] = useState<EnvRow[]>(
    Object.entries(c?.env ?? {}).map(([name, value]) => ({ id: newRowId(), name, value, secret: false })),
  );
  const [keepKeys, setKeepKeys] = useState<string[]>(c?.env_secret_keys ?? []);
  const [port, setPort] = useState<number | null>(c?.service_port ?? null);
  const [healthPath, setHealthPath] = useState(c?.health_path ?? "");
  const [withSsh, setWithSsh] = useState(c?.with_ssh ?? false);
  const [keyIds, setKeyIds] = useState<number[]>([]);
  const [nonce] = useState(() => crypto.randomUUID());
  const create = useCreateRevision(service.slug, { silentError: true });
  const dirty =
    image !== (c?.image_ref ?? "") ||
    command !== (c?.container_command?.join(" ") ?? "") ||
    argRows.map((r) => r.value).join("\n") !== (c?.container_args ?? []).join("\n") ||
    envRows.length !== Object.keys(c?.env ?? {}).length ||
    envRows.some((r) => r.secret || c?.env[r.name] !== r.value) ||
    keepKeys.length !== (c?.env_secret_keys ?? []).length ||
    port !== (c?.service_port ?? null) ||
    healthPath !== (c?.health_path ?? "") ||
    withSsh !== (c?.with_ssh ?? false);
  const leave = useLeaveGuard(dirty);
  const requestClose = () =>
    leave.confirmLeave(() => {
      leave.bypass();
      onClose();
    });

  const imageRef = image.trim();
  const issue = ((): string | null => {
    if (!imageRef) return t("services.form.needsImage");
    if (!isPinnedImageRef(imageRef)) return tErr("orchestrator.imageRefNotPinned");
    const bad = envRows.map((r) => envRowIssue(r, envRows)).find((e) => e != null);
    if (bad === "invalid") return t("services.form.envNameInvalid");
    if (bad === "reserved") return t("services.form.envNameReserved");
    if (bad === "duplicate") return t("services.form.envNameDuplicate");
    if (port == null) return t("services.form.portRequired");
    if (RESERVED_SERVICE_PORTS.includes(port)) return t("services.form.portReserved");
    if (healthPath.trim() !== "" && !healthPath.trim().startsWith("/")) {
      return t("services.form.healthPathSlash");
    }
    if (withSsh && keyIds.length === 0) return t("services.form.needsKey");
    return null;
  })();

  const overrideSecret = (key: string) => {
    setKeepKeys((ks) => ks.filter((k) => k !== key));
    setEnvRows((rows) => [...rows, { id: newRowId(), name: key, value: "", secret: true }]);
  };

  const nextNo = service.revision + 1;
  const buildBody = (): ServiceRevisionCreate | null => {
    if (!inst || port == null) return null;
    const commandList = commandToList(command);
    const argList = argRows.map((r) => r.value.trim()).filter((v) => v !== "");
    const env = buildRevisionEnv(envRows, keepKeys);
    return {
      sku_id: inst.sku_id,
      gpu_count: inst.gpu_count,
      image_ref: imageRef,
      ssh_key_ids: withSsh ? keyIds : [],
      data_disk_id: null,
      with_ssh: withSsh,
      container_command: commandList.length > 0 ? commandList : null,
      container_args: argList.length > 0 ? argList : null,
      ...env,
      service_port: port,
      health_path: healthPath.trim() || null,
      market: inst.market === "spot" ? "spot" : "on_demand",
    };
  };

  const submit = () => {
    const body = buildBody();
    if (!body) return;
    confirm({
      title: t("services.revision.confirmTitle", { no: nextNo }),
      consequences: [t("services.revision.confirmBody"), t("services.revision.confirmKeep")],
      onOk: async () => {
        const idempotencyKey = idemKeyOf("svc-rev", [nonce, service.slug, service.revision, JSON.stringify(body)]);
        try {
          await create.mutateAsync({ body, idempotencyKey });
          message.success(t("services.revision.started", { no: nextNo }));
          leave.bypass();
          onClose();
        } catch (err) {
          if (isApiError(err) && err.code === "NO_CAPACITY") {
            message.warning(t("copy.noCapacityGuide"), 6);
          } else {
            message.error(errText(err));
          }
        }
      },
    });
  };

  const specLine = inst
    ? inst.gpu_count === 0
      ? t("create.summaryCpu", { vcpu: inst.spec.vcpu as number, mem: inst.spec.mem_gb as number })
      : t("instances.specLine", { model: inst.spec.gpu_model as string, count: inst.gpu_count })
    : "—";
  const billingKey = inst ? marketLabelKey(inst.market, inst.subscription?.period) : null;

  return (
    <Drawer
      size={drawerWidth.lg}
      open
      onClose={requestClose}
      destroyOnHidden
      title={t("services.revision.title", { no: service.revision })}
      footer={
        <Space style={{ width: "100%", justifyContent: "flex-end" }}>
          <Button onClick={requestClose}>{t("services.revision.cancel")}</Button>
          <GatedButton type="primary" reason={issue ?? undefined} loading={create.isPending} onClick={submit}>
            {t("services.revision.submit")}
          </GatedButton>
        </Space>
      }
    >
      <Space orientation="vertical" size={space.lg} style={{ width: "100%" }}>
        <Alert type="warning" showIcon title={t("services.revision.notice")} />
        <Card size="small" title={t("services.revision.sectionContainer")}>
          <Space orientation="vertical" size={space.md} style={{ width: "100%" }}>
            <ContainerFields
              image={image}
              onImage={setImage}
              command={command}
              onCommand={setCommand}
              argRows={argRows}
              onArgRows={setArgRows}
              envRows={envRows}
              onEnvRows={setEnvRows}
              collapsibleEnv
            />
            {keepKeys.length > 0 && (
              <Space orientation="vertical" size={space.xs} style={{ width: "100%" }}>
                <Typography.Text type="secondary">{t("services.revision.keptSecrets")}</Typography.Text>
                {keepKeys.map((key) => (
                  <Space key={key} wrap>
                    <Typography.Text code>{key}</Typography.Text>
                    <Tag>{t("services.revision.keepCurrent")}</Tag>
                    <Button size="small" onClick={() => overrideSecret(key)}>
                      {t("services.revision.override")}
                    </Button>
                    <Button size="small" onClick={() => setKeepKeys((ks) => ks.filter((k) => k !== key))}>
                      {t("services.revision.remove")}
                    </Button>
                  </Space>
                ))}
                <Typography.Text type="secondary">{t("services.revision.keptHint")}</Typography.Text>
              </Space>
            )}
            {inst?.data_disk_id != null && <Alert type="info" showIcon title={t("services.revision.diskNote")} />}
          </Space>
        </Card>
        <Card size="small" title={t("services.revision.sectionAccess")}>
          <Space orientation="vertical" size={space.sm} style={{ width: "100%" }}>
            <PublicAccessFields
              port={port}
              onPort={setPort}
              healthPath={healthPath}
              onHealthPath={setHealthPath}
              requireApiKey={service.require_api_key}
              onRequireApiKey={() => undefined}
              hideAuth
            />
            <Typography.Text type="secondary">{t("services.revision.authNote")}</Typography.Text>
          </Space>
        </Card>
        <Card size="small" title={t("services.revision.sectionAdvanced")}>
          <Space orientation="vertical" size={space.sm} style={{ width: "100%" }}>
            <Checkbox checked={withSsh} onChange={(e) => setWithSsh(e.target.checked)}>
              {t("services.form.withSsh")}
            </Checkbox>
            <Typography.Text type="secondary">{t("services.form.withSshHint")}</Typography.Text>
            {withSsh && <SshKeyPicker value={keyIds} onChange={setKeyIds} />}
          </Space>
        </Card>
        <Card size="small" title={t("services.revision.sectionSpec")}>
          <Typography.Text type="secondary">
            {t("services.revision.specFixed", {
              no: service.revision,
              spec: specLine,
              billing: billingKey ? t(billingKey) : (inst?.market ?? "—"),
            })}
          </Typography.Text>
        </Card>
      </Space>
      {leave.modal}
    </Drawer>
  );
}
