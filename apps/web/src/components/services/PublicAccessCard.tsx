/** 服务配置段(对外访问):服务端口 / 协议(TCP、gRPC 即将上线,灰置不隐藏)/ 健康检查 / 访问鉴权 / 端点占位。字段级错误在 blur 后就地显示(不只喂给提交钮 tooltip)。 */

import { controlWidth, fontSize } from "@superdl/ui";
import { Input, InputNumber, Radio, Space, Typography } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { RESERVED_SERVICE_PORTS } from "../../lib/serviceSpec";
import { Field } from "../Field";

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
  /** 更新版本抽屉:鉴权不随版本,在「设置」改 */
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
    <Space orientation="vertical" size={12} style={{ width: "100%" }}>
      <Space size={24} wrap align="start">
        <Field label={t("services.form.portLabel")} required error={portError}>
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
        <Space orientation="vertical" size={4}>
          <Typography.Text type="secondary">{t("services.form.protocolLabel")}</Typography.Text>
          {/* TCP / gRPC 未上线:灰置不隐藏 */}
          <Radio.Group
            value="http"
            options={[
              { value: "http", label: "HTTP" },
              { value: "tcp", label: "TCP", disabled: true },
              { value: "grpc", label: "gRPC", disabled: true },
            ]}
          />
          <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
            {t("services.form.protocolSoon")}
          </Typography.Text>
        </Space>
      </Space>
      <Typography.Text type="secondary">{t("services.form.portHint")}</Typography.Text>
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
        <Space orientation="vertical" size={4} style={{ width: "100%" }}>
          <Typography.Text type="secondary">{t("services.form.authLabel")}</Typography.Text>
          <Radio.Group
            value={requireApiKey ? "key" : "public"}
            onChange={(e) => onRequireApiKey(e.target.value === "key")}
            options={[
              { value: "key", label: t("services.form.authRequire") },
              { value: "public", label: t("services.form.authPublic") },
            ]}
          />
          {!requireApiKey && <Typography.Text type="warning">{t("services.form.authPublicHint")}</Typography.Text>}
          <Typography.Text type="secondary">{t("copy.serviceGatewayAuth")}</Typography.Text>
        </Space>
      )}
      {!hideAuth && (
        <Space orientation="vertical" size={4}>
          <Typography.Text type="secondary">{t("services.form.endpointLabel")}</Typography.Text>
          <Typography.Text type="secondary">{t("services.form.endpointPending")}</Typography.Text>
        </Space>
      )}
    </Space>
  );
}
