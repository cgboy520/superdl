/** 小时账单 Tab(按月)。 */

import { HourlyBillsTable } from "../components/HourlyBillsTable";
import { useHourlyBillPages } from "../api/queries";

/** 按月查询小时账单并显示实例列。 */
export function MonthlyBillsTab({ month, tzOffsetMinutes }: { month: string; tzOffsetMinutes: number }) {
  return <HourlyBillsTable query={useHourlyBillPages({ month, tz_offset_minutes: tzOffsetMinutes })} showInstance />;
}
