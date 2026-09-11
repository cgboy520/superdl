/** 市场页与创建页共用的 SKU 表列与「计费方式」卡。 */

import type { SkuMarketOut } from "@superdl/api-client";
import {
  BILLING_PERIODS,
  compareAmounts,
  isBillingPeriod,
  marketMap,
  MAX_PERIOD_COUNT,
  periodMap,
  statusColors,
  type BillingPeriod,
  type Formatters,
} from "@superdl/ui";
import type { TFunction } from "i18next";
import { Card, InputNumber, Space, Table, Tag, Tooltip, Typography } from "antd";
import type { ComponentProps, ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { ChipRow, CHIP_LABEL_WIDTH } from "./ChipRow";
import { skuVariant } from "@superdl/ui";

import { TierTag } from "./common";
import { discountOff, PeriodCountUnit, usePeriodDiscounts } from "./periodBilling";
import { SpotOffLabel, SpotPriceInline, useSpotPolicy, type SpotPolicy } from "./spotBilling";

/** GPU / 显存列文案:共享档报算力份额,MIG 档报切分规格,其余整卡;CPU 档报「不带 GPU」。 */
function formatSkuGpu(s: SkuMarketOut, t: TFunction<readonly ["web", "shared"]>): string {
  // 按展示档位派发:「共享」可能是 MIG 硬切分或 HAMi 份额
  const variant = skuVariant(s.tier, s.pool_label);
  if (variant === "cpu") {
    // CPU 规格型号与显存恒为空,改报整机 CPU/内存
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

/** SKU 表列。availability=true 时插入「空闲 GPU」列(市场页用);priceFontSize 控制价格字号。 */
export function skuColumns(
  opts: {
    fmt: Formatters;
    t: TFunction<readonly ["web", "shared"]>;
    availability?: boolean;
    priceFontSize?: number;
    /** CPU 规格表:价格是整机时价,表头不写「单卡」 */
    cpu?: boolean;
    /** 竞价档选中时传入:上了竞价的规格价格列显「原价划线 + 折后价」,没上的显原价并挂标。 */
    spot?: SpotPolicy;
  },
): NonNullable<ComponentProps<typeof Table<SkuMarketOut>>["columns"]> {
  const { t } = opts;
  const availability = [
    {
      title: t("sku.colFree"),
      width: 110,
      sorter: (a: SkuMarketOut, b: SkuMarketOut) => (a.available_count ?? 0) - (b.available_count ?? 0),
      render: (_: unknown, s: SkuMarketOut) => {
        const n = s.available_count ?? 0;
        return n > 0 ? (
          <span style={{ color: statusColors.green, fontWeight: 600 }}>{n}</span>
        ) : (
          <Tag>{opts.t("copy.outOfStock")}</Tag>
        );
      },
    },
  ];
  return [
    {
      title: t("sku.colSpec"),
      // fixed 价格列使整表 table-layout:fixed;规格列须声明宽度
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
    // 最高 CUDA 只对带卡规格有意义,CPU 分栏恒为「-」
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
      // 钉右:窄屏(≤1024)表格横滚时价格不滚出视口
      fixed: "right" as const,
      align: "right" as const,
      // 竞价档一格放两个价,150 放不下
      width: opts.spot ? 210 : 150,
      // 金额比较用 BigInt 万分位,不过 Number
      sorter: (a: SkuMarketOut, b: SkuMarketOut) => compareAmounts(a.price_hourly, b.price_hourly),
      render: (_: unknown, s: SkuMarketOut) =>
        opts.spot && !s.spot_enabled ? (
          <Space size={6} align="baseline">
            <span style={{ fontSize: opts.priceFontSize, fontWeight: 700 }}>
              {opts.fmt.formatHourlyPrice(s.price_hourly)}
            </span>
            <Tag style={{ marginInlineEnd: 0 }}>{t("sku.spotUnavailable")}</Tag>
          </Space>
        ) : (
          <span style={{ fontSize: opts.priceFontSize, fontWeight: 700 }}>
            {opts.spot ? (
              <SpotPriceInline baseHourly={s.price_hourly} units={1} policy={opts.spot} />
            ) : (
              opts.fmt.formatHourlyPrice(s.price_hourly)
            )}
          </span>
        ),
    },
  ];
}

/** 计费方式:按量 + 竞价 + 四个包周期,与档位正交;竞价与包周期互斥(market 单值),同行单选。 */
export type BillingMode = "on_demand" | "spot" | BillingPeriod;

/** 计费方式卡(市场页与创建页共用)。折扣角标与竞价折扣从 `/policies` 读,禁止硬编码;规格不接受包周期 / 未上竞价时对应项灰置 + tooltip,不隐藏;数量选择器只在传 count + onCountChange 时出。 */
export function BillingModeCard({
  value,
  onChange,
  periodEnabled = true,
  spotEnabled = true,
  count,
  onCountChange,
  extra,
}: {
  value: BillingMode;
  onChange: (v: BillingMode) => void;
  periodEnabled?: boolean;
  spotEnabled?: boolean;
  count?: number;
  onCountChange?: (n: number) => void;
  extra?: ReactNode;
}) {
  const { t } = useTranslation(["web", "shared"]);
  // 「该规格暂不支持包周期 / 暂未上竞价档」文案事实源在后端 messages.py
  const { t: tErr } = useTranslation("errors");
  const discounts = usePeriodDiscounts();
  const spotPolicy = useSpotPolicy();
  const showCount = count != null && onCountChange != null && isBillingPeriod(value);
  return (
    <Card styles={{ body: { paddingBlock: 16 } }}>
      <Space orientation="vertical" size={12} style={{ width: "100%" }}>
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
              // 策略没回来不放行
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
          // 与 ChipRow 同款标签栏(CHIP_LABEL_WIDTH 右对齐)
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <Typography.Text
              type="secondary"
              style={{ flexShrink: 0, width: CHIP_LABEL_WIDTH, lineHeight: "32px", textAlign: "right" }}
            >
              {t("period.countLabel")}
            </Typography.Text>
            <Space size={8}>
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
        <Typography.Text type="secondary">{t("copy.periodPrepaid")}</Typography.Text>
      </Space>
    </Card>
  );
}
