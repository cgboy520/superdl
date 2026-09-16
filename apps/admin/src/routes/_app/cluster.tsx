/** Cluster page: connection state, configuration readiness, component health and pool distribution. */

import { CheckCircleFilled, CloseCircleFilled } from "@ant-design/icons";
import { COMPONENT_HEALTH_ORDER, adminColors, formatDateTime, isComponentAttention, metaOf, space } from "@superdl/ui";
import { AttentionBar, type AttentionItem, DataErrorAlert, GatedButton, PageContainer } from "@superdl/ui/components";
import { useQueryClient } from "@tanstack/react-query";
import { createFileRoute, Link, Outlet } from "@tanstack/react-router";
import { App, Badge, Card, Col, Row, Space, Tag, Typography } from "antd";
import { useTranslation } from "react-i18next";

import { type ClusterComponent, useClusterStatus, useTestClusterConnection } from "../../api";

import { useApiErrorText } from "@superdl/ui";
import { POOL_LABEL_KEY } from "../../lib/pools";
import { canWriteOps, useAdminRole } from "../../stores/auth";
import { ComponentPanel } from "./-ComponentPanel";

export const Route = createFileRoute("/_app/cluster")({
  component: ClusterPage,
});

/** failed → degraded → disabled → unknown → healthy; within a state keep the chain order given by the backend. */
function byAttention(a: ClusterComponent, b: ClusterComponent): number {
  const rank = (c: ClusterComponent) => COMPONENT_HEALTH_ORDER.indexOf(c.state);
  return rank(a) - rank(b);
}

function ClusterPage() {
  const { t } = useTranslation(["admin", "shared"]);
  const errText = useApiErrorText();
  const { message } = App.useApp();
  const role = useAdminRole();
  const writable = canWriteOps(role);
  const qc = useQueryClient();
  const { data, queryKey, isLoading, isError, refetch } = useClusterStatus();

  const test = useTestClusterConnection({
    mutation: {
      onSuccess: () => {
        message.success(t("cluster.testOk"));
        void qc.invalidateQueries({ queryKey });
      },
      onError: (e) => {
        message.error(errText(e, t("cluster.testFailed")));
        void qc.invalidateQueries({ queryKey });
      },
    },
  });

  const unlabeled = data?.pools.unlabeled ?? 0;
  const isK3s = data?.distro === "k3s";
  const components = [...(data?.components ?? [])].sort(byAttention);
  const attentionCount = components.filter((c) => isComponentAttention(c.state)).length;

  const attention: AttentionItem[] = [];
  if (isError) attention.push({ key: "fetch", severity: "error", title: t("shared:common.loadFailed") });
  if (data && !data.api_reachable && data.error)
    attention.push({
      key: "unreachable",
      severity: "error",
      title: t("cluster.unreachable"),
      description: data.error,
    });
  if (isK3s) attention.push({ key: "light", severity: "warning", title: t("cluster.lightWarning") });
  if (unlabeled > 0)
    attention.push({
      key: "unlabeled",
      severity: "warning",
      title: t("cluster.unlabeledWarn", { count: unlabeled }),
      action: <Link to="/nodes">{t("cluster.viewNodes")}</Link>,
    });
  if (data && !data.config.prometheus_url_set)
    attention.push({ key: "prom", severity: "info", title: t("cluster.promHint") });

  return (
    <PageContainer title={t("menu.cluster")} width="full">
      <Space orientation="vertical" size={space.lg} style={{ width: "100%" }}>
        {isError && <DataErrorAlert onRetry={() => void refetch()} />}
        {attention.length > 0 && <AttentionBar items={attention} />}
        <Row gutter={[16, 16]}>
          <Col xs={24} lg={12}>
            <Card
              title={t("cluster.connCard")}
              extra={
                <GatedButton
                  type="primary"
                  size="small"
                  reason={writable ? undefined : t("cluster.readonlyNoTest")}
                  loading={test.isPending}
                  onClick={() => test.mutate()}
                >
                  {t("cluster.testBtn")}
                </GatedButton>
              }
            >
              <Space orientation="vertical" size={space.sm}>
                <Space size={space.sm}>
                  <Badge status={isLoading ? "processing" : data?.api_reachable ? "success" : "error"} />
                  <Typography.Text strong>
                    {isLoading
                      ? t("cluster.probing")
                      : data?.api_reachable
                        ? t("cluster.connected")
                        : t("cluster.unreachable")}
                  </Typography.Text>
                  {data?.k8s_version && <Tag>{data.k8s_version}</Tag>}
                  {data?.distro && (
                    <Tag color={isK3s ? "gold" : "geekblue"}>
                      {data.distro}
                      {isK3s ? ` · ${t("cluster.lightBadge")}` : ""}
                    </Tag>
                  )}
                </Space>
                <Typography.Text type="secondary">
                  {data?.probed_at
                    ? t("cluster.probedAt", { time: formatDateTime(data.probed_at) })
                    : t("cluster.neverProbed")}
                </Typography.Text>
              </Space>
            </Card>
          </Col>
          <Col xs={24} lg={12}>
            <Card title={t("cluster.configCard")}>
              <Space orientation="vertical" size={space.sm} style={{ width: "100%" }}>
                {(
                  [
                    ["cluster.cfgServer", data?.config.server_url_set],
                    ["cluster.cfgToken", data?.config.join_token_set],
                    ["cluster.cfgProm", data?.config.prometheus_url_set],
                  ] as const
                ).map(([key, ok]) => (
                  <Space key={key} size={space.sm}>
                    {ok ? (
                      <CheckCircleFilled style={{ color: adminColors.positive }} />
                    ) : (
                      <CloseCircleFilled style={{ color: adminColors.negative }} />
                    )}
                    <Typography.Text>{t(key)}</Typography.Text>
                    <Typography.Text type="secondary">
                      {ok ? t("cluster.cfgSet") : t("cluster.cfgUnset")}
                    </Typography.Text>
                  </Space>
                ))}
                <Space size={space.md}>
                  <Link to="/platform">{t("cluster.goPlatform")}</Link>
                  {data?.config.grafana_url && (
                    <Typography.Link href={data.config.grafana_url} target="_blank">
                      {t("cluster.grafanaLink")}
                    </Typography.Link>
                  )}
                </Space>
              </Space>
            </Card>
          </Col>
          <Col span={24}>
            <Card
              title={
                attentionCount > 0 ? t("cluster.healthCardCount", { count: attentionCount }) : t("cluster.healthCard")
              }
            >
              {components.length === 0 ? (
                <Typography.Text type="secondary">{t("cluster.noComponents")}</Typography.Text>
              ) : (
                <Row gutter={[16, 16]}>
                  {components.map((c) => (
                    <Col key={c.key} xs={24} sm={12} lg={8} xxl={6}>
                      <ComponentPanel component={c} />
                    </Col>
                  ))}
                </Row>
              )}
            </Card>
          </Col>
          <Col xs={24} lg={12}>
            <Card title={t("cluster.poolCard")}>
              <Space orientation="vertical" size={space.md} style={{ width: "100%" }}>
                <Space size={space.sm} wrap>
                  {Object.entries(data?.pools ?? {})
                    .filter(([k]) => k !== "unlabeled")
                    .map(([pool, count]) => {
                      const labelKey = metaOf(POOL_LABEL_KEY, pool);
                      const ready = data?.pools_ready[pool] ?? 0;
                      return (
                        <Tag key={pool} color={ready > 0 ? "cyan" : "default"}>
                          {labelKey ? t(labelKey) : pool} · {ready}/{count}
                        </Tag>
                      );
                    })}
                </Space>
                <Typography.Text type="secondary">{t("cluster.poolReadyHint")}</Typography.Text>
              </Space>
            </Card>
          </Col>
        </Row>
      </Space>
      <Outlet />
    </PageContainer>
  );
}
