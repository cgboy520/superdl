/** ECharts wrapper: full option updates, auto resize, accessible label and loading / empty / error overlays. */

import { BarChart, LineChart, PieChart } from "echarts/charts";
import { AriaComponent, GridComponent, LegendComponent, MarkLineComponent, TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { useEffect, useRef } from "react";
import type { CSSProperties, ReactNode } from "react";
import { Spin, Typography } from "antd";
import { useTranslation } from "react-i18next";

import {
  adminColors,
  chartAccentColors,
  chartSeriesColors,
  colorPrimary,
  fontFamily,
  webDarkColors,
  chartAxisColors,
} from "../tokens";

echarts.use([
  BarChart,
  LineChart,
  PieChart,
  AriaComponent,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TooltipComponent,
  CanvasRenderer,
]);

echarts.registerTheme("noc", {
  color: [
    adminColors.dataAccent,
    chartAccentColors.indigo,
    adminColors.positive,
    adminColors.alertAccent,
    chartAccentColors.pink,
    adminColors.chartNeutral,
  ],
  backgroundColor: "transparent",
  textStyle: { color: adminColors.textSecondary, fontFamily },
  title: { textStyle: { color: adminColors.textSecondary } },
  legend: { textStyle: { color: adminColors.textSecondary } },
  categoryAxis: {
    axisLine: { lineStyle: { color: adminColors.gridLine } },
    axisTick: { lineStyle: { color: adminColors.gridLine } },
    axisLabel: { color: adminColors.textSecondary },
    splitLine: { lineStyle: { color: adminColors.gridLine } },
  },
  valueAxis: {
    axisLine: { lineStyle: { color: adminColors.gridLine } },
    axisLabel: { color: adminColors.textSecondary },
    splitLine: { lineStyle: { color: adminColors.gridLine } },
  },
  tooltip: {
    backgroundColor: adminColors.bgElevated,
    borderColor: adminColors.divider,
    textStyle: { color: adminColors.textSecondary },
  },
});

/** Light chart preset for the user console */
echarts.registerTheme("web-light", {
  color: [
    colorPrimary,
    chartAccentColors.indigo,
    chartSeriesColors.light.green,
    chartSeriesColors.light.orange,
    chartAccentColors.pink,
    chartSeriesColors.light.neutral,
  ],
  backgroundColor: "transparent",
  textStyle: { color: chartAxisColors.light.text, fontFamily },
  title: { textStyle: { color: chartAxisColors.light.text } },
  legend: { textStyle: { color: chartAxisColors.light.text } },
  categoryAxis: {
    axisLine: { lineStyle: { color: chartAxisColors.light.axis } },
    axisTick: { lineStyle: { color: chartAxisColors.light.axis } },
    axisLabel: { color: chartAxisColors.light.text },
    splitLine: { lineStyle: { color: chartAxisColors.light.grid } },
  },
  valueAxis: {
    axisLine: { lineStyle: { color: chartAxisColors.light.axis } },
    axisLabel: { color: chartAxisColors.light.text },
    splitLine: { lineStyle: { color: chartAxisColors.light.grid } },
  },
  tooltip: {
    backgroundColor: chartAxisColors.light.tooltipBg,
    borderColor: chartAxisColors.light.axis,
    textStyle: { color: chartAxisColors.light.tooltipText },
  },
});

/** Dark chart preset for the user console (palette = webDarkColors) */
echarts.registerTheme("web-dark", {
  color: [
    webDarkColors.menuSelectedColor,
    chartAccentColors.indigo,
    chartSeriesColors.dark.green,
    chartSeriesColors.dark.orange,
    chartAccentColors.pink,
    chartSeriesColors.dark.neutral,
  ],
  backgroundColor: "transparent",
  textStyle: { color: webDarkColors.textSecondary, fontFamily },
  title: { textStyle: { color: webDarkColors.textSecondary } },
  legend: { textStyle: { color: webDarkColors.textSecondary } },
  categoryAxis: {
    axisLine: { lineStyle: { color: webDarkColors.border } },
    axisTick: { lineStyle: { color: webDarkColors.border } },
    axisLabel: { color: webDarkColors.textSecondary },
    splitLine: { lineStyle: { color: webDarkColors.border } },
  },
  valueAxis: {
    axisLine: { lineStyle: { color: webDarkColors.border } },
    axisLabel: { color: webDarkColors.textSecondary },
    splitLine: { lineStyle: { color: webDarkColors.border } },
  },
  tooltip: {
    backgroundColor: webDarkColors.bgElevated,
    borderColor: webDarkColors.border,
    textStyle: { color: webDarkColors.text },
  },
});

export interface EChartProps {
  option: Record<string, unknown>;
  style?: CSSProperties;
  className?: string;
  /** Registered theme name (e.g. "noc") */
  theme?: string;
  /** Accessible chart name (screen readers) */
  ariaLabel?: string;
  /** Loading: the chart area is covered by a Spin */
  loading?: boolean;
  /** Empty state: true = default copy, ReactNode = custom (boolean is part of ReactNode) */
  empty?: ReactNode;
  /** Source-down degradation: true = default copy, ReactNode = custom */
  degraded?: ReactNode;
  /** Link group name: charts in the same group share axisPointer / tooltip (echarts.connect) */
  group?: string;
}

export default function EChart({
  option,
  style,
  className,
  theme,
  ariaLabel,
  loading,
  empty,
  degraded,
  group,
}: EChartProps) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);
  const { t } = useTranslation("shared");

  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current, theme);
    chartRef.current = chart;
    if (group) {
      chart.group = group;
      echarts.connect(group);
    }
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(ref.current);
    return () => {
      observer.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
  }, [theme, group]);

  useEffect(() => {
    chartRef.current?.setOption({ ...option, aria: { enabled: true, label: { description: ariaLabel ?? "" } } }, true);
  }, [option, ariaLabel]);

  const covered = Boolean(loading || empty || degraded);
  const overlay = loading ? (
    <Spin />
  ) : degraded ? (
    degraded === true ? (
      <Typography.Text type="secondary">{t("chart.degraded")}</Typography.Text>
    ) : (
      degraded
    )
  ) : empty ? (
    empty === true ? (
      <Typography.Text type="secondary">{t("chart.empty")}</Typography.Text>
    ) : (
      empty
    )
  ) : null;

  return (
    <div className={className} style={{ position: "relative", ...style }}>
      <div ref={ref} style={{ width: "100%", height: "100%", visibility: covered ? "hidden" : "visible" }} />
      {covered && (
        <div
          style={{
            position: "absolute",
            inset: 0,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          {overlay}
        </div>
      )}
    </div>
  );
}
