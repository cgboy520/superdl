/** echarts 按需注册封装(两端共用,收口原 apps/web 与 apps/admin 两份重复实现)。
 *  新增图型在此登记,禁止在页面里直接 import echarts。
 *
 *  基于 echarts 原生实例的薄封装(echarts-for-react 已停更于 echarts 5 时代):
 *  - option 变更整量 setOption(notMerge),避免旧 series 残留;
 *  - 容器尺寸变化经 ResizeObserver 自动 resize;
 *  - aria.enabled 常开,ariaLabel 作为图表的可访问名称(读屏替代文本)。
 *
 *  theme="noc":管理端深色图表预设(轴/网格/提示框默认色取自 adminColors),
 *  调用方 option 里的显式色仍优先(theme 只作默认值兜底,不改变既有手写配色)。
 */

import { BarChart, LineChart, PieChart } from "echarts/charts";
import {
  AriaComponent,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TooltipComponent,
} from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { useEffect, useRef } from "react";
import type { CSSProperties, ReactNode } from "react";
import { Spin, Typography } from "antd";
import { useTranslation } from "react-i18next";

import { adminColors, chartAccentColors, chartSeriesColors, colorPrimary, fontFamily, webDarkColors } from "../tokens";

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

/** 用户端浅色图表预设(实例监控/费用环图):轴/网格/提示框色取自浅色板 */
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
  textStyle: { color: "rgba(0,0,0,0.60)", fontFamily },
  title: { textStyle: { color: "rgba(0,0,0,0.60)" } },
  legend: { textStyle: { color: "rgba(0,0,0,0.60)" } },
  categoryAxis: {
    axisLine: { lineStyle: { color: "#E5E7EB" } },
    axisTick: { lineStyle: { color: "#E5E7EB" } },
    axisLabel: { color: "rgba(0,0,0,0.60)" },
    splitLine: { lineStyle: { color: "#F0F1F5" } },
  },
  valueAxis: {
    axisLine: { lineStyle: { color: "#E5E7EB" } },
    axisLabel: { color: "rgba(0,0,0,0.60)" },
    splitLine: { lineStyle: { color: "#F0F1F5" } },
  },
  tooltip: {
    backgroundColor: "#FFFFFF",
    borderColor: "#E5E7EB",
    textStyle: { color: "rgba(0,0,0,0.88)" },
  },
});

/** 用户端暗色图表预设(「开发者夜间工作台」):色板与 webDarkColors 同源 */
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
  /** 已注册主题名(如 "noc") */
  theme?: string;
  /** 图表的可访问名称:读屏时作为整图替代描述(aria.enabled 常开) */
  ariaLabel?: string;
  /** 加载中:图表区盖 Spin(首载白屏与图表跳动都出自「没盖这层」) */
  loading?: boolean;
  /** 无数据空态:true 渲染默认空态文案,ReactNode 自定义(图表保留挂载但不可见) */
  empty?: boolean | ReactNode;
  /** 断源降级(如监控 503「监控暂不可用」):ReactNode 自定义内容;true 用默认文案 */
  degraded?: boolean | ReactNode;
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
}: EChartProps) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);
  const { t } = useTranslation("shared");

  // 实例生命周期与主题绑定(theme 运行期不变;变化则整体重建)
  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current, theme);
    chartRef.current = chart;
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(ref.current);
    return () => {
      observer.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
  }, [theme]);

  useEffect(() => {
    chartRef.current?.setOption(
      { ...option, aria: { enabled: true, label: { description: ariaLabel ?? "" } } },
      true
    );
  }, [option, ariaLabel]);

  // 三态盖层:图表 div 始终挂载(visibility 切换),避免空态→有数据时图表不重建、布局不跳高
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
      <div
        ref={ref}
        style={{ width: "100%", height: "100%", visibility: covered ? "hidden" : "visible" }}
      />
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
