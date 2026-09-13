/** 实例监控面板:GPU 利用率 / 显存 / CPU / 内存 2×2 栅格,四图 axisPointer 联动(同 group);范围选择器 sticky;% 类固定 0~100,MB 按量级换 GB;每图右上给「当前 / 峰值」。
 *  指标只做展示,不参与计费;非 running 仍可查历史(无数据时按 range 给空态);503 = 监控源未接入 / 断源(专用文案),其余错误不渲染成空图。实例详情页与服务详情页共用。 */

import { isApiError } from "@superdl/api-client";
import { fontSize, layout, POLL, space, useAutoRefresh, useChartTheme } from "@superdl/ui";
import { DataErrorAlert, EChart, Freshness } from "@superdl/ui/components";
import { Alert, Card, Radio, Space, Typography, theme } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useInstanceMetrics } from "../../api/queries";

type Unit = "%" | "MB";

const SERIES_META = {
  gpu_util: { nameKey: "instances.seriesGpu", unit: "%" as Unit },
  vram_used_mb: { nameKey: "instances.seriesVram", unit: "MB" as Unit },
  cpu_pct: { nameKey: "instances.seriesCpu", unit: "%" as Unit },
  mem_used_mb: { nameKey: "instances.seriesMem", unit: "MB" as Unit },
} as const;

/** MB 序列超过 1 GB 时整图换 GB 显示(1 位小数) */
function scaleOf(points: [number, number][], unit: Unit): { factor: number; label: string; digits: number } {
  if (unit === "%") return { factor: 1, label: "%", digits: 0 };
  const max = points.reduce((m, [, v]) => Math.max(m, v), 0);
  return max >= 1024 ? { factor: 1 / 1024, label: "GB", digits: 1 } : { factor: 1, label: "MB", digits: 0 };
}

export function MetricsPanel({ uuid, running }: { uuid: string; running: boolean }) {
  const { t } = useTranslation(["web", "shared"]);
  const chartTheme = useChartTheme();
  const { token } = theme.useToken();
  const [range, setRange] = useState<"1h" | "6h" | "24h">("1h");
  // running 时自动刷新;停机后只看历史(不轮询),仍可手动刷新
  const autoRefresh = useAutoRefresh(POLL.daily);
  const { data, error, isLoading, isRefetching, refetch, dataUpdatedAt } = useInstanceMetrics(
    uuid,
    { range },
    { refetchInterval: running ? autoRefresh.refetchInterval : false, retry: 0 },
  );

  if (error && isApiError(error) && error.status === 503) {
    return <Alert type="warning" showIcon title={t("copy.monitoringDown")} />;
  }
  if (error) {
    return <DataErrorAlert onRetry={() => void refetch()} />;
  }
  const series = (data?.series ?? {}) as Record<string, [number, number][]>;
  const group = `metrics-${uuid}`;

  return (
    <Space orientation="vertical" size={space.md} style={{ width: "100%" }}>
      {/* 工具行 sticky:滚到第 4 张图仍看得到当前范围 */}
      <div
        style={{
          position: "sticky",
          top: layout.topBarHeight,
          zIndex: 1,
          background: token.colorBgLayout,
          paddingBlock: space.xs,
          display: "flex",
          alignItems: "center",
          gap: space.md,
          flexWrap: "wrap",
        }}
      >
        <Radio.Group
          value={range}
          onChange={(e) => setRange(e.target.value as typeof range)}
          optionType="button"
          options={[
            { value: "1h", label: t("instances.range1h") },
            { value: "6h", label: t("instances.range6h") },
            { value: "24h", label: t("instances.range24h") },
          ]}
        />
        {!running && (
          <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
            {t("instances.metricsHistoryOnly")}
          </Typography.Text>
        )}
        <Freshness
          updatedAt={dataUpdatedAt}
          intervalMs={running ? autoRefresh.intervalMs : false}
          paused={autoRefresh.paused}
          onTogglePause={running ? autoRefresh.toggle : undefined}
          onRefresh={() => void refetch()}
          refreshing={isRefetching}
        />
      </div>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(min(360px, 100%), 1fr))",
          gap: layout.cardGap,
        }}
      >
        {Object.entries(SERIES_META).map(([key, meta]) => {
          const points = series[key] ?? [];
          const scale = scaleOf(points, meta.unit);
          const scaled = points.map(([ts, v]) => [ts * 1000, v * scale.factor] as [number, number]);
          const last = scaled.at(-1)?.[1];
          const peak = scaled.reduce((m, [, v]) => Math.max(m, v), 0);
          const fmt = (v: number) => `${v.toFixed(scale.digits)}${scale.label}`;
          return (
            <Card
              key={key}
              size="small"
              title={t(meta.nameKey)}
              extra={
                points.length > 0 && last != null ? (
                  <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
                    {t("instances.metricsNowPeak", { now: fmt(last), peak: fmt(peak) })}
                  </Typography.Text>
                ) : undefined
              }
            >
              <EChart
                theme={chartTheme}
                group={group}
                style={{ height: 200 }}
                ariaLabel={t(meta.nameKey)}
                loading={isLoading}
                empty={!isLoading && points.length === 0 ? t("instances.metricsNoData") : false}
                option={{
                  grid: { left: 52, right: 16, top: 12, bottom: 24 },
                  xAxis: { type: "time" },
                  yAxis: {
                    type: "value",
                    // 百分比类固定 0~100,低负载不再被自适应轴放大成「满载」
                    ...(meta.unit === "%" ? { min: 0, max: 100 } : { min: 0 }),
                    axisLabel: { formatter: (v: number) => fmt(v) },
                  },
                  tooltip: {
                    trigger: "axis",
                    axisPointer: { type: "line" },
                    valueFormatter: (v: number) => fmt(v),
                  },
                  series: [{ type: "line", showSymbol: false, areaStyle: { opacity: 0.08 }, data: scaled }],
                }}
              />
            </Card>
          );
        })}
      </div>
    </Space>
  );
}
