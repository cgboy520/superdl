/** echarts 按需注册封装(两端共用,收口原 apps/web 与 apps/admin 两份重复实现)。
 *  新增图型在此登记,禁止在页面里直接 import echarts。
 *
 *  theme="noc":管理端深色图表预设(轴/网格/提示框默认色取自 adminColors),
 *  调用方 option 里的显式色仍优先(theme 只作默认值兜底,不改变既有手写配色)。
 */

import { BarChart, LineChart, PieChart } from "echarts/charts";
import {
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TooltipComponent,
} from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import EChartsReactCore from "echarts-for-react/esm/core";
import type { EChartsReactProps } from "echarts-for-react/esm/types";

import { adminColors, fontFamily } from "../tokens";

echarts.use([
  BarChart,
  LineChart,
  PieChart,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TooltipComponent,
  CanvasRenderer,
]);

echarts.registerTheme("noc", {
  color: [
    adminColors.dataAccent,
    "#818CF8",
    adminColors.positive,
    adminColors.alertAccent,
    "#F472B6",
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

export default function EChart(props: Omit<EChartsReactProps, "echarts">) {
  return <EChartsReactCore echarts={echarts} {...props} />;
}
