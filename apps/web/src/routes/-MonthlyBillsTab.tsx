/** Hourly bills tab (per month). */

import { HourlyBillsTable } from "../components/HourlyBillsTable";
import { useHourlyBillPages } from "../api/queries";

/** Query hourly bills by month and show the instance column. */
export function MonthlyBillsTab({ month, tzOffsetMinutes }: { month: string; tzOffsetMinutes: number }) {
  return <HourlyBillsTable query={useHourlyBillPages({ month, tz_offset_minutes: tzOffsetMinutes })} showInstance />;
}
