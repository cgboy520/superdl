/**
 * echarts 按需注册封装:只打包本端实际用到的图型/组件。
 * 新增图型时在此登记,勿在页面里直接 import echarts。
 */
import { LineChart, PieChart } from "echarts/charts";
import {
  GridComponent,
  LegendComponent,
  TooltipComponent,
} from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import EChartsReactCore from "echarts-for-react/esm/core";
import type { EChartsReactProps } from "echarts-for-react/esm/types";

echarts.use([LineChart, PieChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer]);

export default function EChart(props: Omit<EChartsReactProps, "echarts">) {
  return <EChartsReactCore echarts={echarts} {...props} />;
}
