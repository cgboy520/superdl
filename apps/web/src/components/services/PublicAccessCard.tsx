/** 服务配置段(对外访问):服务端口 / 协议(TCP、gRPC 即将上线,灰置不隐藏)/ 健康检查 / 访问鉴权 / 端点占位。 */

import { fontSize } from "@superdl/ui";
import { Input, InputNumber, Radio, Space, Typography } from "antd";
import { useTranslation } from "react-i18next";

import { RESERVED_SERVICE_PORTS } from "../../lib/serviceSpec";

export function PublicAccessFields({
  port,
  onPort,
  healthPath,
  onHealthPath,
  requireApiKey,
  onRequireApiKey,
}: {
  port: number | null;
  onPort: (v: number | null) => void;
  healthPath: string;
  onHealthPath: (v: string) => void;
  requireApiKey: boolean;
  onRequireApiKey: (v: boolean) => void;
}) {
  const { t } = useTranslation();
  return (
    <Space orientation="vertical" size={12} style={{ width: "100%" }}>
      <Space size={24} wrap align="start">
        <Space orientation="vertical" size={4}>
          <Typography.Text type="secondary">{t("services.form.portLabel")}</Typography.Text>
          <InputNumber
            min={1}
            max={65535}
            style={{ width: "100%", maxWidth: 160 }}
            placeholder="8000"
            aria-label={t("services.form.portLabel")}
            status={port != null && RESERVED_SERVICE_PORTS.includes(port) ? "error" : undefined}
            value={port}
            onChange={(v) => onPort(typeof v === "number" ? v : null)}
          />
        </Space>
        <Space orientation="vertical" size={4}>
          <Typography.Text type="secondary">{t("services.form.protocolLabel")}</Typography.Text>
          {/* TCP / gRPC 未上线:灰置并写明,不隐藏 */}
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
      <Space orientation="vertical" size={4} style={{ width: "100%" }}>
        <Typography.Text type="secondary">{t("services.form.healthLabel")}</Typography.Text>
        <Input
          style={{ width: "100%", maxWidth: 320 }}
          placeholder="/healthz"
          aria-label={t("services.form.healthLabel")}
          status={healthPath.trim() !== "" && !healthPath.trim().startsWith("/") ? "error" : undefined}
          value={healthPath}
          onChange={(e) => onHealthPath(e.target.value)}
        />
        <Typography.Text type="secondary">{t("services.form.healthHint")}</Typography.Text>
      </Space>
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
        {!requireApiKey && (
          <Typography.Text type="warning">{t("services.form.authPublicHint")}</Typography.Text>
        )}
        <Typography.Text type="secondary">{t("copy.serviceGatewayAuth")}</Typography.Text>
      </Space>
      <Space orientation="vertical" size={4}>
        <Typography.Text type="secondary">{t("services.form.endpointLabel")}</Typography.Text>
        <Typography.Text type="secondary">{t("services.form.endpointPending")}</Typography.Text>
      </Space>
    </Space>
  );
}
