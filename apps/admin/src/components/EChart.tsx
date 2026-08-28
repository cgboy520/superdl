/** echarts 按需注册封装:只打包本端用到的图型/组件。新增图型在此登记,禁止在页面里直接 import echarts。 */
import { BarChart, LineChart } from "echarts/charts";
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
