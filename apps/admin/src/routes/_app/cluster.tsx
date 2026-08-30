import { CheckCircleFilled, CloseCircleFilled } from "@ant-design/icons";
import { adminColors, formatDateTime, metaOf } from "@superdl/ui";
import { DataErrorAlert, PageContainer } from "@superdl/ui/components";
import { useQueryClient } from "@tanstack/react-query";
import { createFileRoute, Link } from "@tanstack/react-router";
import { Alert, App, Badge, Button, Card, Col, Row, Space, Tag, Tooltip, Typography } from "antd";
import { useTranslation } from "react-i18next";

import {
  type ClusterComponent,
  useClusterStatus,
  useTestClusterConnection,
} from "../../api";

import { useApiErrorText } from "@superdl/ui";
import { POOL_LABEL_KEY } from "../../lib/pools";
import { canWriteOps, useAdminRole } from "../../stores/auth";

export const Route = createFileRoute("/_app/cluster")({
  component: ClusterPage,
});

const COMPONENT_LABEL = {
  nodes: "cluster.comp.nodes",
  hami: "cluster.comp.hami",
  gpu_operator: "cluster.comp.gpuOperator",
  dcgm: "cluster.comp.dcgm",
  nvidia_runtimeclass: "cluster.comp.nvidiaRuntimeclass",
  kata_runtimeclass: "cluster.comp.kataRuntimeclass",
  storage: "cluster.comp.storage",
  gateway: "cluster.comp.gateway",
  cert_manager: "cluster.comp.certManager",
  monitoring: "cluster.comp.monitoring",
} as const satisfies Record<ClusterComponent["key"], string>;

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
        void qc.invalidateQueries({ queryKey }); // 失败也要刷新:error 已落缓存,刷新后红牌才可见
      },
    },
  });

  const unlabeled = data?.pools["unlabeled"] ?? 0;
  const isK3s = data?.distro === "k3s";

  return (
    <PageContainer title={t("menu.cluster")}>
    <Space orientation="vertical" size={16} style={{ width: "100%" }}>
      {/* 查询失败必须明示:四卡静默全空会被值班读成「接口就没数据」,全站「失败必明示」纪律 */}
      {isError && <DataErrorAlert onRetry={() => void refetch()} />}
      {isK3s && <Alert type="warning" showIcon title={t("cluster.lightWarning")} />}
      {data && !data.api_reachable && data.error && (
        <Alert type="error" showIcon title={t("cluster.unreachable")} description={data.error} />
      )}
      {data && !data.config.prometheus_url_set && (
        <Alert type="info" showIcon title={t("cluster.promHint")} />
      )}
      <Row gutter={[16, 16]}>
        <Col xs={24} lg={12}>
          <Card
            title={t("cluster.connCard")}
            extra={
              <Tooltip title={writable ? "" : t("cluster.readonlyNoTest")}>
                <Button
                  type="primary"
                  size="small"
                  disabled={!writable}
                  loading={test.isPending}
                  onClick={() => test.mutate()}
                >
                  {t("cluster.testBtn")}
                </Button>
              </Tooltip>
            }
          >
            <Space orientation="vertical" size={8}>
              <Space size={8}>
                {/* 首响未到不闪「不可达」(假阴性):加载期渲染探测中,数据到达后按真实值 */}
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
            <Space orientation="vertical" size={8} style={{ width: "100%" }}>
              {(
                [
                  ["cluster.cfgServer", data?.config.server_url_set],
                  ["cluster.cfgToken", data?.config.join_token_set],
                  ["cluster.cfgProm", data?.config.prometheus_url_set],
                ] as const
              ).map(([key, ok]) => (
                <Space key={key} size={8}>
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
              <Space size={12}>
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
        <Col xs={24} lg={12}>
          <Card title={t("cluster.healthCard")}>
            <Space orientation="vertical" size={10} style={{ width: "100%" }}>
              {/* 空数组渲染空白会被读成「探测缺失/加载失败」:数据已到但无组件时给一句话空态 */}
              {data && (data.components ?? []).length === 0 && (
                <Typography.Text type="secondary">{t("cluster.noComponents")}</Typography.Text>
              )}
              {(data?.components ?? []).map((c) => (
                <div key={c.key}>
                  <Space size={8}>
                    {c.ok ? (
                      <CheckCircleFilled style={{ color: adminColors.positive }} />
                    ) : (
                      <CloseCircleFilled style={{ color: adminColors.negative }} />
                    )}
                    <Typography.Text strong={!c.ok}>{t(COMPONENT_LABEL[c.key])}</Typography.Text>
                    {c.detail && <Typography.Text type="secondary">{c.detail}</Typography.Text>}
                  </Space>
                  {!c.ok && c.fix_hint && (
                    <div style={{ marginLeft: 24, marginTop: 4 }}>
                      <Typography.Text type="secondary">{t("cluster.fixHint")}:</Typography.Text>{" "}
                      <Typography.Text code copyable>
                        {c.fix_hint}
                      </Typography.Text>
                    </div>
                  )}
                </div>
              ))}
            </Space>
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card title={t("cluster.poolCard")}>
            <Space orientation="vertical" size={10}>
              <Space size={8} wrap>
                {Object.entries(data?.pools ?? {})
                  .filter(([k]) => k !== "unlabeled")
                  .map(([pool, count]) => {
                    const labelKey = metaOf(POOL_LABEL_KEY, pool);
                    return (
                      <Tag key={pool} color="cyan">
                        {labelKey ? t(labelKey) : pool} · {count}
                      </Tag>
                    );
                  })}
              </Space>
              {unlabeled > 0 && (
                <Alert
                  type="warning"
                  showIcon
                  title={t("cluster.unlabeledWarn", { count: unlabeled })}
                  action={<Link to="/nodes">{t("cluster.viewNodes")}</Link>}
                />
              )}
            </Space>
          </Card>
        </Col>
      </Row>
    </Space>
    </PageContainer>
  );
}
