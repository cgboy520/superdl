/** 带正负色的金额:正数绿;负数默认红(流水表传 highlightNegative={false} 保持默认色)。 */

import { adminColors } from "@superdl/ui";

import { useFormat } from "../lib/format";

export function SignedAmount({ value, highlightNegative = true }: { value: string; highlightNegative?: boolean }) {
  const { formatMoney } = useFormat();
  const negative = value.startsWith("-");
  const color = negative ? (highlightNegative ? adminColors.negative : undefined) : adminColors.positive;
  return <span style={{ color }}>{formatMoney(value)}</span>;
}
