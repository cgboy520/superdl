/** 小时账单 Tab(按月)。 */

import { HourlyBillsTable } from "../components/HourlyBillsTable";
import { useHourlyBillPages } from "../api/queries";

/** 支付倒计时:到期时刻 → 剩余时长。 */
/** 按月小时账单(带实例列);hook 须在组件里调,不塞进 Tabs 的 items 数组。 */
export function MonthlyBillsTab({ month, tzOffsetMinutes }: { month: string; tzOffsetMinutes: number }) {
  return <HourlyBillsTable query={useHourlyBillPages({ month, tz_offset_minutes: tzOffsetMinutes })} showInstance />;
}
