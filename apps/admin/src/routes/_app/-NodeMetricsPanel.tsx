/** Node metrics panel: GPU utilisation / VRAM time series of the selected node; the time range is given by the drawer. */

import { Button, Card, Space, Tag, Typography } from "antd";
import { useTranslation } from "react-i18next";

import { fontSize, space, useChartTheme } from "@superdl/ui";
import { EChart } from "@superdl/ui/components";

import { type NodeMetricsOut, type NodeRow } from "../../api";

/** Node-level history curves (per-GPU util / VRAM) + XID badge + optional Grafana link. */
export function NodeMetricsPanel({ node, metrics }: { node: NodeRow; metrics: NodeMetricsOut | undefined }) {
  const { t } = useTranslation();
  const chartTheme = useChartTheme();
  const gpus = metrics?.gpus ?? [];
  const chart = (key: "util" | "mem_used_mb", title: string, unit: string) => (
    <Card size="small" title={title}>
      <EChart
        style={{ height: 200 }}
        theme={chartTheme}
        ariaLabel={title}
        option={{
          grid: { left: 48, right: 16, top: 28, bottom: 24 },
          legend: { top: 0, textStyle: { fontSize: fontSize.caption } },
          xAxis: { type: "time" },
          yAxis: { type: "value", axisLabel: { formatter: `{value}${unit}` } },
          tooltip: { trigger: "axis" },
          series: gpus.map((g) => ({
            name: `GPU ${g.index}`,
            type: "line",
            showSymbol: false,
            data: (g[key] ?? []).map(([ts, v]) => [ts * 1000, v]),
          })),
        }}
      />
    </Card>
  );
  const xid = metrics?.xid_count_24h ?? 0;
  const grafanaUrl = metrics?.grafana_url;
  return (
    <Card
      title={t("nodes.historyTitle")}
      extra={
        <Space size={space.md}>
          {xid > 0 && <Tag color="red">{t("nodes.xidBadge", { count: xid })}</Tag>}
          {grafanaUrl && (
            <Button size="small" onClick={() => window.open(grafanaUrl, "_blank", "noopener,noreferrer")}>
              {t("nodes.openGrafana")}
            </Button>
          )}
        </Space>
      }
    >
      {metrics?.available && gpus.length > 0 ? (
        <Space orientation="vertical" size={space.md} style={{ width: "100%" }}>
          {chart("util", t("nodes.utilChart"), "%")}
          {chart("mem_used_mb", t("nodes.vramChart"), "MB")}
        </Space>
      ) : (
        <Typography.Text type="secondary">
          {t("nodes.historyPending")} · {node.name}
        </Typography.Text>
      )}
    </Card>
  );
}
