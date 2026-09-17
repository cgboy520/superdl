/** Signed amount colour: positive in the positive colour; negative in the negative colour by default (highlightNegative={false} keeps the default colour); colours via useThemeColors. */

import { useFormat, useThemeColors } from "@superdl/ui";

export function SignedAmount({ value, highlightNegative = true }: { value: string; highlightNegative?: boolean }) {
  const { formatMoney } = useFormat();
  const colors = useThemeColors();
  const negative = value.startsWith("-");
  const color = negative ? (highlightNegative ? colors.negative : undefined) : colors.positive;
  return <span style={{ color }}>{formatMoney(value)}</span>;
}
