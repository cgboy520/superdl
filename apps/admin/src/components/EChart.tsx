/**
 * echarts 按需注册封装:只打包本端实际用到的图型/组件,
 * 替代 echarts-for-react 默认入口的全量 echarts(minified 1.1MB chunk 的主因)。
 * 新增图型时在此登记,勿在页面里直接 import echarts。
 */
import { BarChart, LineChart } from "echarts/charts";
import {
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TooltipComponent,
} from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import EChartsReactCore from "echarts-for-react/lib/core";
import type { EChartsReactProps } from "echarts-for-react/lib/types";

echarts.use([
  BarChart,
  LineChart,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TooltipComponent,
  CanvasRenderer,
]);

export default function EChart(props: Omit<EChartsReactProps, "echarts">) {
  return <EChartsReactCore echarts={echarts} {...props} />;
}
