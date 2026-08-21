/**
 * 市场页与创建页共用的 SKU 表列与「计费方式」卡。
 * 两处展示同一份 SKU 数据,列定义与文案必须同源,避免口径漂移。
 */

import type { SkuMarketOut } from "@superdl/api-client";
import { statusColors, type Formatters } from "@superdl/ui";
import type { TFunction } from "i18next";
import { Card, Space, Table, Tag, Tooltip, Typography } from "antd";
import type { ComponentProps, ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { ChipRow } from "./ChipRow";
import { TierTag } from "./common";

/** GPU / 显存列文案:共享档报算力份额,MIG 档报切分规格,其余为整卡。 */
function formatSkuGpu(s: SkuMarketOut, t: TFunction<readonly ["web", "shared"]>): string {
  if (s.tier.startsWith("shared")) {
    return t("sku.gpuShared", { model: s.gpu_model, vram: s.vram_gb, pct: s.gpu_cores_pct });
  }
  if (s.tier === "mig") {
    return t("sku.gpuMig", { model: s.gpu_model, vram: s.vram_gb, profile: s.mig_profile ?? t("sku.sliceFallback") });
  }
  return t("sku.gpuDedicated", { model: s.gpu_model, vram: s.vram_gb });
}

/**
 * SKU 表列。availability=true 时插入「空闲 GPU」列(市场页选规格用,创建页规格已定不需要);
 * priceFontSize 控制价格字号(市场页放大到 18)。
 */
export function skuColumns(
  opts: { fmt: Formatters; t: TFunction<readonly ["web", "shared"]>; availability?: boolean; priceFontSize?: number },
): NonNullable<ComponentProps<typeof Table<SkuMarketOut>>["columns"]> {
  const { t } = opts;
  const availability = [
    {
      title: t("sku.colFree"),
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
      render: (_: unknown, s: SkuMarketOut) => (
        <Space>
          <Typography.Text strong>{s.name}</Typography.Text>
          <TierTag tier={s.tier} />
        </Space>
      ),
    },
    { title: t("sku.colGpu"), render: (_: unknown, s: SkuMarketOut) => formatSkuGpu(s, t) },
    ...(opts.availability ? availability : []),
    {
      title: t("sku.colHost"),
      render: (_: unknown, s: SkuMarketOut) => t("sku.hostShort", { vcpu: s.vcpu, mem: s.mem_gb }),
    },
    { title: t("sku.colDisk"), render: (_: unknown, s: SkuMarketOut) => t("sku.diskWithBase", { disk: s.disk_gb }) },
    {
      title: (
        <Tooltip title={t("sku.cudaTooltip")}>
          <span>{t("sku.colCuda")}</span>
        </Tooltip>
      ),
      render: (_: unknown, s: SkuMarketOut) => s.cuda_max ?? "-",
    },
    {
      title: t("sku.colPrice"),
      render: (_: unknown, s: SkuMarketOut) => (
        <span style={{ fontSize: opts.priceFontSize, fontWeight: 700 }}>
          {opts.fmt.formatHourlyPrice(s.price_hourly)}
        </span>
      ),
    },
  ];
}

/** 计费方式卡:仅按量可选,包日/包周/包月可见但禁用(铁律 #2)。 */
export function BillingModeCard({ extra }: { extra?: ReactNode }) {
  const { t } = useTranslation(["web", "shared"]);
  return (
    <Card title={t("sku.billingModeTitle")} styles={{ body: { paddingBlock: 16 } }}>
      <ChipRow
        label={t("sku.billingModeTitle")}
        value="hourly"
        onChange={() => undefined}
        options={[
          { value: "hourly", label: t("sku.modeHourly") },
          { value: "daily", label: t("sku.modeDaily"), disabled: true, disabledReason: t("copy.billingModeComingSoon") },
          { value: "weekly", label: t("sku.modeWeekly"), disabled: true, disabledReason: t("copy.billingModeComingSoon") },
          {
            value: "monthly",
            label: t("sku.modeMonthly"),
            disabled: true,
            disabledReason: t("copy.billingModeComingSoon"),
          },
        ]}
        extra={extra}
      />
    </Card>
  );
}
