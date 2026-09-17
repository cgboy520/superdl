/** Service configuration section (public access): service port / protocol tiles (TCP, gRPC coming soon, greyed not hidden) / health check / access-auth tiles / endpoint placeholder. Field-level errors show in place after blur (not only in the submit button tooltip). */

import { controlWidth, space } from "@superdl/ui";
import { OptionTileGroup } from "@superdl/ui/components";
import { Input, InputNumber, Space, Typography } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { RESERVED_SERVICE_PORTS } from "../../lib/serviceSpec";
import { Field } from "../Field";

type Protocol = "http" | "tcp" | "grpc";

export function PublicAccessFields({
  port,
  onPort,
  healthPath,
  onHealthPath,
  requireApiKey,
  onRequireApiKey,
  hideAuth,
}: {
  port: number | null;
  onPort: (v: number | null) => void;
  healthPath: string;
  onHealthPath: (v: string) => void;
  requireApiKey: boolean;
  onRequireApiKey: (v: boolean) => void;
  /** Revision drawer: auth does not follow the revision, change it under Settings */
  hideAuth?: boolean;
}) {
  const { t } = useTranslation();
  const [portTouched, setPortTouched] = useState(false);
  const [healthTouched, setHealthTouched] = useState(false);
  const portError =
    port != null && RESERVED_SERVICE_PORTS.includes(port)
      ? t("services.form.portReserved")
      : portTouched && port == null
        ? t("services.form.portRequired")
        : null;
  const healthError =
    healthTouched && healthPath.trim() !== "" && !healthPath.trim().startsWith("/")
      ? t("services.form.healthPathSlash")
      : null;
  return (
    <Space orientation="vertical" size={space.md} style={{ width: "100%" }}>
      <Field label={t("services.form.portLabel")} required error={portError} hint={t("services.form.portHint")}>
        <InputNumber
          min={1}
          max={65535}
          style={{ width: "100%", maxWidth: controlWidth.sm }}
          placeholder="8000"
          aria-label={t("services.form.portLabel")}
          status={portError ? "error" : undefined}
          value={port}
          onChange={(v) => onPort(typeof v === "number" ? v : null)}
          onBlur={() => setPortTouched(true)}
        />
      </Field>
      <OptionTileGroup<Protocol>
        label={t("services.form.protocolLabel")}
        columns={3}
        size="sm"
        value="http"
        onChange={() => undefined}
        options={[
          { value: "http", title: "HTTP" },
          { value: "tcp", title: "TCP", reason: t("services.form.protocolSoon") },
          { value: "grpc", title: "gRPC", reason: t("services.form.protocolSoon") },
        ]}
      />
      <Field label={t("services.form.healthLabel")} error={healthError} hint={t("services.form.healthHint")}>
        <Input
          style={{ width: "100%", maxWidth: controlWidth.lg }}
          placeholder="/healthz"
          aria-label={t("services.form.healthLabel")}
          status={healthError ? "error" : undefined}
          value={healthPath}
          onChange={(e) => onHealthPath(e.target.value)}
          onBlur={() => setHealthTouched(true)}
          className="mono"
        />
      </Field>
      {!hideAuth && (
        <Space orientation="vertical" size={space.xs} style={{ width: "100%" }}>
          <OptionTileGroup
            label={t("services.form.authLabel")}
            columns={2}
            size="sm"
            value={requireApiKey ? "key" : "public"}
            onChange={(v) => onRequireApiKey(v === "key")}
            options={[
              { value: "key", title: t("services.form.authRequire"), description: t("services.form.authRequireDesc") },
              { value: "public", title: t("services.form.authPublic"), description: t("services.form.authPublicDesc") },
            ]}
          />
          {!requireApiKey && <Typography.Text type="warning">{t("services.form.authPublicHint")}</Typography.Text>}
          <Typography.Text type="secondary">{t("copy.serviceGatewayAuth")}</Typography.Text>
        </Space>
      )}
      {!hideAuth && (
        <Space orientation="vertical" size={space.xs}>
          <Typography.Text type="secondary">{t("services.form.endpointLabel")}</Typography.Text>
          <Typography.Text type="secondary">{t("services.form.endpointPending")}</Typography.Text>
        </Space>
      )}
    </Space>
  );
}
