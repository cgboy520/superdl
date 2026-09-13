/** 带正负色的金额:正数正向色;负数默认负向色(highlightNegative={false} 保持默认色);色值经 useThemeColors 取。 */

import { useFormat, useThemeColors } from "@superdl/ui";

export function SignedAmount({ value, highlightNegative = true }: { value: string; highlightNegative?: boolean }) {
  const { formatMoney } = useFormat();
  const colors = useThemeColors();
  const negative = value.startsWith("-");
  const color = negative ? (highlightNegative ? colors.negative : undefined) : colors.positive;
  return <span style={{ color }}>{formatMoney(value)}</span>;
}
