/** Service endpoint card: access URL, readiness, auth mode, port and health check. */

import type { ServiceOut } from "@superdl/api-client";
import { CopyField, GatedButton } from "@superdl/ui/components";
import { Link } from "@tanstack/react-router";
import { Alert, Badge, Button, Card, Space, Tag, Typography } from "antd";
import { useTranslation } from "react-i18next";
import { space, TEST_IDS } from "@superdl/ui";

export function EndpointCard({
  service,
  onShowLogs,
  onShowEvents,
}: {
  service: ServiceOut;
  onShowLogs: () => void;
  onShowEvents: () => void;
}) {
  const { t } = useTranslation();
  const c = service.container;
  const live = service.status === "running" || service.status === "unready";
  return (
    <Card size="small" title={t("services.detail.endpointCard")}>
      <Space orientation="vertical" size={space.sm} style={{ width: "100%" }}>
        <Space wrap size={space.sm}>
          <CopyField value={service.url} code label={t("services.copyEndpoint")} testId={TEST_IDS.endpointUrl} />
          <GatedButton
            size="small"
            reason={live ? undefined : t("services.endpointUnreachable")}
            onClick={() => window.open(service.url, "_blank", "noopener,noreferrer")}
          >
            {t("services.openEndpoint")}
          </GatedButton>
        </Space>
        <Space wrap size={space.md}>
          <Badge
            status={service.ready ? "success" : "default"}
            text={service.ready ? t("services.ready") : t("services.notReady")}
          />
          <Tag>{service.require_api_key ? t("services.authRequired") : t("services.authPublic")}</Tag>
          {c && (
            <Typography.Text type="secondary">
              {t("services.detail.endpointLine", {
                port: c.service_port ?? "—",
                health: c.health_path ?? t("services.detail.healthNone"),
              })}
            </Typography.Text>
          )}
        </Space>
        {service.status === "deploying" && (
          <Alert type="info" showIcon title={t("services.detail.deployingHint", { no: service.revision })} />
        )}
        {service.status === "unready" && (
          <Alert
            type="info"
            showIcon
            title={t("copy.serviceNotReadyHint")}
            action={
              <Button size="small" onClick={onShowLogs}>
                {t("services.detail.checkLogs")}
              </Button>
            }
          />
        )}
        {(service.status === "stopped" || service.status === "stopping") && (
          <Alert type="info" showIcon title={t("services.detail.stoppedHint")} />
        )}
        {service.status === "frozen" && (
          <Alert
            type="error"
            showIcon
            title={t("services.detail.frozenHint")}
            action={
              <Link to="/billing">
                <Button size="small" type="primary" danger>
                  {t("attention.goRecharge")}
                </Button>
              </Link>
            }
          />
        )}
        {service.status === "failed" && (
          <Alert
            type="error"
            showIcon
            title={t("services.detail.failedHint")}
            action={
              <Space size={space.sm}>
                <Button size="small" onClick={onShowLogs}>
                  {t("services.detail.checkLogs")}
                </Button>
                <Button size="small" onClick={onShowEvents}>
                  {t("attention.viewEvents")}
                </Button>
              </Space>
            }
          />
        )}
      </Space>
    </Card>
  );
}
