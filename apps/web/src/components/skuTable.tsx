/** SKU table columns and "billing mode" selection shared by the market / create / deploy pages (the market page puts the chips into the table toolbar, create and deploy use a card). */

import type { SkuMarketOut } from "@superdl/api-client";
import {
  BILLING_PERIODS,
  compareAmounts,
  fontSize,
  isBillingPeriod,
  marketMap,
  MAX_PERIOD_COUNT,
  mulPrice,
  periodMap,
  space,
  statusColors,
  type BillingPeriod,
  type Formatters,
} from "@superdl/ui";
import type { TFunction } from "i18next";
import { Card, InputNumber, Space, Table, Tag, Tooltip, Typography } from "antd";
import type { ComponentProps, ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { ChipRow, CHIP_LABEL_WIDTH } from "@superdl/ui/components";
import { skuVariant } from "@superdl/ui";

import { TierTag } from "./common";
import { discountOff, PeriodCountUnit, usePeriodDiscounts } from "./periodBilling";
import { SpotOffLabel, SpotPriceInline, spotPriceOf, useSpotPolicy, type SpotPolicy } from "./spotBilling";

/** GPU / VRAM column copy: the shared tier reports the compute share, MIG the slice spec, the rest whole cards; the CPU tier reports "no GPU". */
function formatSkuGpu(s: SkuMarketOut, t: TFunction<readonly ["web", "shared"]>): string {
  const variant = skuVariant(s.tier, s.pool_label);
  if (variant === "cpu") {
    return t("sku.gpuCpuNone", { vcpu: s.vcpu, mem: s.mem_gb });
  }
  if (variant === "shared_mig") {
    return t("sku.gpuMig", { model: s.gpu_model, vram: s.vram_gb, profile: s.mig_profile ?? t("sku.sliceFallback") });
  }
  if (variant === "shared_hami") {
    return t("sku.gpuShared", { model: s.gpu_model, vram: s.vram_gb, pct: s.gpu_cores_pct });
  }
  return t("sku.gpuDedicated", { model: s.gpu_model, vram: s.vram_gb });
}

/** SKU table columns: spec, stock and price; the total is shown when several cards are selected. */
export function skuColumns(opts: {
  fmt: Formatters;
  t: TFunction<readonly ["web", "shared"]>;
  availability?: boolean;
  priceFontSize?: number;
  /** CPU spec table: the price is the whole-machine hourly price, the header says "whole machine" */
  cpu?: boolean;
  /** Passed while the spot tier is selected: specs with spot show "list price struck + discounted", the rest show the list price with a marker. */
  spot?: SpotPolicy;
  /** Selected card count (GPU column); >1 adds a total sub-row to the price column and the available column judges "short" by it */
  units?: number;
}): NonNullable<ComponentProps<typeof Table<SkuMarketOut>>["columns"]> {
  const { t } = opts;
  const units = opts.cpu ? 1 : Math.max(1, opts.units ?? 1);
  const availability = [
    {
      title: t("sku.colFree"),
      width: 120,
      sorter: (a: SkuMarketOut, b: SkuMarketOut) => (a.available_count ?? 0) - (b.available_count ?? 0),
      render: (_: unknown, s: SkuMarketOut) => {
        const n = s.available_count ?? 0;
        if (n === 0) return <Tag>{opts.t("copy.outOfStock")}</Tag>;
        if (n < units) {
          return (
            <Tooltip title={t("copy.noStockForGpuCount")}>
              <Tag color="orange">{t("sku.shortOfCards", { count: units })}</Tag>
            </Tooltip>
          );
        }
        return <span style={{ color: statusColors.green, fontWeight: 600 }}>{n}</span>;
      },
    },
  ];
  const priceCell = (s: SkuMarketOut) => {
    const unit =
      opts.spot && s.spot_enabled ? (
        <SpotPriceInline baseHourly={s.price_hourly} units={1} policy={opts.spot} />
      ) : (
        opts.fmt.formatHourlyPrice(s.price_hourly)
      );
    return (
      <Space orientation="vertical" size={0} align="end">
        <Space size={space.sm} align="baseline">
          <span style={{ fontSize: opts.priceFontSize, fontWeight: 700 }}>{unit}</span>
          {opts.spot && !s.spot_enabled && <Tag style={{ marginInlineEnd: 0 }}>{t("sku.spotUnavailable")}</Tag>}
        </Space>
        {units > 1 && (
          <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
            {t("sku.priceTimesUnits", {
              count: units,
              total: opts.fmt.formatHourlyPrice(
                mulPrice(
                  opts.spot && s.spot_enabled
                    ? (spotPriceOf(s.price_hourly, opts.spot) ?? s.price_hourly)
                    : s.price_hourly,
                  units,
                ),
              ),
            })}
          </Typography.Text>
        )}
      </Space>
    );
  };
  return [
    {
      title: t("sku.colSpec"),
      width: 220,
      render: (_: unknown, s: SkuMarketOut) => (
        <Space>
          <Typography.Text strong>{s.name}</Typography.Text>
          <TierTag tier={s.tier} pool={s.pool_label} />
        </Space>
      ),
    },
    {
      title: opts.cpu ? t("sku.colGpuCpu") : t("sku.colGpu"),
      render: (_: unknown, s: SkuMarketOut) => formatSkuGpu(s, t),
    },
    ...(opts.availability ? availability : []),
    {
      title: t("sku.colHost"),
      render: (_: unknown, s: SkuMarketOut) => t("sku.hostShort", { vcpu: s.vcpu, mem: s.mem_gb }),
    },
    { title: t("sku.colDisk"), render: (_: unknown, s: SkuMarketOut) => t("sku.diskWithBase", { disk: s.disk_gb }) },
    ...(opts.cpu
      ? []
      : [
          {
            title: (
              <Tooltip title={t("sku.cudaTooltip")}>
                <span>{t("sku.colCuda")}</span>
              </Tooltip>
            ),
            render: (_: unknown, s: SkuMarketOut) => s.cuda_max ?? "-",
          },
        ]),
    {
      title: opts.cpu ? t("sku.colPriceCpu") : t("sku.colPrice"),
      fixed: "right" as const,
      align: "right" as const,
      width: opts.spot ? 210 : 160,
      sorter: (a: SkuMarketOut, b: SkuMarketOut) => compareAmounts(a.price_hourly, b.price_hourly),
      render: (_: unknown, s: SkuMarketOut) => priceCell(s),
    },
  ];
}

/** Row selectability: enough stock for the selected cards and (in the spot tier) spot enabled on the spec. */
export function skuSelectable(s: SkuMarketOut, needed: number, spot: boolean): boolean {
  return (s.available_count ?? 0) >= needed && (!spot || s.spot_enabled);
}

/** Reason copy of an unselectable row (tooltip / empty state). */
export function skuDisabledReason(
  s: SkuMarketOut,
  needed: number,
  spot: boolean,
  t: TFunction<readonly ["web", "shared"]>,
): string | undefined {
  if ((s.available_count ?? 0) === 0) return t("copy.outOfStock");
  if ((s.available_count ?? 0) < needed) return t("copy.noStockForGpuCount");
  if (spot && !s.spot_enabled) return t("sku.spotUnavailable");
  return undefined;
}

/** Muted style class of unselectable spec rows. */
export const SKU_ROW_DISABLED_CLASS = "sku-row--disabled";

/** Billing mode: on-demand + spot + the four periods, orthogonal to the tier; spot and periods exclude each other (market is single-valued), single select in one row. */
export type BillingMode = "on_demand" | "spot" | BillingPeriod;

interface BillingModeProps {
  value: BillingMode;
  onChange: (v: BillingMode) => void;
  periodEnabled?: boolean;
  spotEnabled?: boolean;
  count?: number;
  onCountChange?: (n: number) => void;
  extra?: ReactNode;
}

/** Billing-mode chips: policy discounts, spec gating and the optional period count selector. */
export function BillingModeChips({
  value,
  onChange,
  periodEnabled = true,
  spotEnabled = true,
  count,
  onCountChange,
  extra,
}: BillingModeProps) {
  const { t } = useTranslation(["web", "shared"]);
  const { t: tErr } = useTranslation("errors");
  const discounts = usePeriodDiscounts();
  const spotPolicy = useSpotPolicy();
  const showCount = count != null && onCountChange != null && isBillingPeriod(value);
  return (
    <Space orientation="vertical" size={space.md} style={{ width: "100%" }}>
      <ChipRow<BillingMode>
        label={t("sku.billingModeTitle")}
        value={value}
        onChange={onChange}
        options={[
          { value: "on_demand", label: t("sku.modeHourly") },
          {
            value: "spot",
            label: (
              <span>
                {t(marketMap.spot.labelKey)}
                <SpotOffLabel policy={spotPolicy} />
              </span>
            ),
            disabled: !spotEnabled || spotPolicy == null,
            disabledReason: !spotEnabled
              ? tErr("orchestrator.spotNotEnabled")
              : spotPolicy == null
                ? t("period.quotePending")
                : undefined,
          },
          ...BILLING_PERIODS.map((p) => {
            const off = discounts ? discountOff(discounts[p]) : 0;
            return {
              value: p,
              label: (
                <span>
                  {t(periodMap[p].labelKey)}
                  {off > 0 && (
                    <Typography.Text type="secondary" style={{ marginInlineStart: 4 }}>
                      {t("period.offPct", { off })}
                    </Typography.Text>
                  )}
                </span>
              ),
              disabled: !periodEnabled,
              disabledReason: periodEnabled ? undefined : tErr("orchestrator.periodNotEnabled"),
            };
          }),
        ]}
        extra={extra}
      />
      {showCount && (
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <Typography.Text
            type="secondary"
            style={{ flexShrink: 0, width: CHIP_LABEL_WIDTH, lineHeight: "32px", textAlign: "right" }}
          >
            {t("period.countLabel")}
          </Typography.Text>
          <Space size={space.sm}>
            <InputNumber
              min={1}
              max={MAX_PERIOD_COUNT}
              value={count}
              aria-label={t("period.countLabel")}
              onChange={(v) => onCountChange(typeof v === "number" ? v : 1)}
              style={{ width: 96 }}
            />
            <PeriodCountUnit period={value} />
          </Space>
        </div>
      )}
      {isBillingPeriod(value) && (
        <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
          {t("copy.periodPrepaid")}
        </Typography.Text>
      )}
      {value === "spot" && spotPolicy && (
        <Typography.Text type="warning" style={{ fontSize: fontSize.caption }}>
          {t("copy.spotReclaimNotice", { seconds: spotPolicy.graceSeconds })}
        </Typography.Text>
      )}
    </Space>
  );
}

/** Billing-mode card (create and deploy pages): BillingModeChips in a card. The market page uses no card, the chips sit in the table toolbar. */
export function BillingModeCard(props: BillingModeProps) {
  return (
    <Card styles={{ body: { paddingBlock: 16 } }}>
      <BillingModeChips {...props} />
    </Card>
  );
}
