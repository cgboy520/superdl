/** 服务端点卡(详情页常驻):URL + 复制 / 打开、就绪位、鉴权方式、容器端口 / 健康检查。就绪为「否」不是故障态,如实显示并引到日志。
 *  停机类四态分开说:stopped / stopping(启动后恢复)、frozen(欠费,去充值)、failed(部署失败,看日志 / 看事件)。 */

import type { ServiceOut } from "@superdl/api-client";
import { fontSize } from "@superdl/ui";
import { Link } from "@tanstack/react-router";
import { Alert, Badge, Button, Card, Space, Tag, Typography } from "antd";
import { useTranslation } from "react-i18next";

import { CopyButton } from "../common";

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
      <Space orientation="vertical" size={8} style={{ width: "100%" }}>
        <Space wrap size={8}>
          <Typography.Text code className="mono" style={{ fontSize: fontSize.sectionTitle }}>
            {service.url}
          </Typography.Text>
          <CopyButton text={service.url} label={t("services.copyEndpoint")} />
          <Button
            size="small"
            disabled={!live}
            onClick={() => window.open(service.url, "_blank", "noopener,noreferrer")}
          >
            {t("services.openEndpoint")}
          </Button>
        </Space>
        <Space wrap size={12}>
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
              <Space size={8}>
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
