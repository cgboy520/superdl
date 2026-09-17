/** Data-disk size (shared by both consoles): slider + number box bound to one value, min / max passed by the caller from /policies; optional daily estimate and expansion baseline difference. */

import { Flex, InputNumber, Slider, Space, Typography } from "antd";
import { useTranslation } from "react-i18next";

import { diskDailyEstimate, formatSizeGb } from "../format";
import { useFormat } from "../hooks/useFormat";
import { controlWidth, fontSize, space } from "../tokens";

export function DiskSizeField({
  value,
  onChange,
  min,
  max,
  step = 10,
  priceGbMonth,
  baseline,
  disabled,
  ariaLabel,
  maxWidth = 480,
}: {
  value: number;
  onChange: (gb: number) => void;
  min: number | undefined;
  max: number | undefined;
  step?: number;
  /** Price per GB·month; when given, an "about X/day" estimate row appears */
  priceGbMonth?: string;
  /** Expansion: current size, the estimate covers only the difference */
  baseline?: number;
  disabled?: boolean;
  ariaLabel: string;
  maxWidth?: number;
}) {
  const { t } = useTranslation("shared");
  const { formatMoney, minorUnits } = useFormat();
  const ready = min !== undefined && max !== undefined && !disabled;
  const delta = baseline === undefined ? value : Math.max(0, value - baseline);
  const daily =
    priceGbMonth === undefined
      ? undefined
      : formatMoney(diskDailyEstimate(priceGbMonth, delta, minorUnits === 0 ? 0 : 2));
  return (
    <Space orientation="vertical" size={space.xs} style={{ width: "100%" }}>
      <Flex gap={space.md} align="center">
        <Slider
          style={{ flex: 1, maxWidth }}
          min={baseline ?? min}
          max={max}
          step={step}
          value={value}
          onChange={onChange}
          disabled={!ready}
        />
        <Space.Compact style={{ width: controlWidth.xs + 14 }}>
          <InputNumber
            min={baseline ?? min}
            max={max}
            step={step}
            value={value}
            onChange={(v) => {
              if (typeof v === "number") onChange(v);
            }}
            disabled={!ready}
            style={{ width: "100%" }}
            aria-label={ariaLabel}
          />
          <Space.Addon>GB</Space.Addon>
        </Space.Compact>
      </Flex>
      {daily !== undefined && (
        <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
          {baseline === undefined
            ? t("disk.sizeEstimate", { size: formatSizeGb(value), daily })
            : t("disk.expandEstimate", { size: formatSizeGb(value), extra: formatSizeGb(delta), daily })}
        </Typography.Text>
      )}
    </Space>
  );
}
