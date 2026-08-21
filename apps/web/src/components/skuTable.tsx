/**
 * 市场页与创建页共用的 SKU 表列与「计费方式」卡。
 * 两处展示同一份 SKU 数据,列定义与文案必须同源,避免口径漂移。
 */

import type { SkuMarketOut } from "@superdl/api-client";
import { copy, statusColors, type Formatters } from "@superdl/ui";
import { Card, Space, Table, Tag, Tooltip, Typography } from "antd";
import type { ComponentProps, ReactNode } from "react";

import { ChipRow } from "./ChipRow";
import { TierTag } from "./common";

/** GPU / 显存列文案:共享档报算力份额,MIG 档报切分规格,其余为整卡。 */
function formatSkuGpu(s: SkuMarketOut): string {
  if (s.tier.startsWith("shared")) {
    return `${s.gpu_model} · ${s.vram_gb}G · ${s.gpu_cores_pct}% 算力(均值)`;
  }
  if (s.tier === "mig") {
    return `${s.gpu_model} · ${s.vram_gb}G · MIG ${s.mig_profile ?? "切分"}`;
  }
  return `${s.gpu_model} · ${s.vram_gb}G · 整卡`;
}

/**
 * SKU 表列。availability=true 时插入「空闲 GPU」列(市场页选规格用,创建页规格已定不需要);
 * priceFontSize 控制价格字号(市场页放大到 18)。
 */
export function skuColumns(
  opts: { fmt: Formatters; availability?: boolean; priceFontSize?: number },
): NonNullable<ComponentProps<typeof Table<SkuMarketOut>>["columns"]> {
  const availability = [
    {
      title: "空闲 GPU",
      render: (_: unknown, s: SkuMarketOut) => {
        const n = s.available_count ?? 0;
        return n > 0 ? (
          <span style={{ color: statusColors.green, fontWeight: 600 }}>{n}</span>
        ) : (
          <Tag>{copy.outOfStock}</Tag>
        );
      },
    },
  ];
  return [
    {
      title: "规格",
      render: (_: unknown, s: SkuMarketOut) => (
        <Space>
          <Typography.Text strong>{s.name}</Typography.Text>
          <TierTag tier={s.tier} />
        </Space>
      ),
    },
    { title: "GPU / 显存", render: (_: unknown, s: SkuMarketOut) => formatSkuGpu(s) },
    ...(opts.availability ? availability : []),
    {
      title: "实例配置",
      render: (_: unknown, s: SkuMarketOut) => `${s.vcpu} vCPU / ${s.mem_gb}G 内存`,
    },
    { title: "实例盘", render: (_: unknown, s: SkuMarketOut) => `${s.disk_gb}G(含 100G)` },
    {
      title: (
        <Tooltip title="镜像可用的最高 CUDA 版本,取决于节点驱动">
          <span>最高 CUDA</span>
        </Tooltip>
      ),
      render: (_: unknown, s: SkuMarketOut) => s.cuda_max ?? "-",
    },
    {
      title: "价格(单卡)",
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
  return (
    <Card title="计费方式" styles={{ body: { paddingBlock: 16 } }}>
      <ChipRow
        label="计费方式"
        value="hourly"
        onChange={() => undefined}
        options={[
          { value: "hourly", label: "按量计费" },
          { value: "daily", label: "包日", disabled: true, disabledReason: copy.billingModeComingSoon },
          { value: "weekly", label: "包周", disabled: true, disabledReason: copy.billingModeComingSoon },
          {
            value: "monthly",
            label: "包月",
            disabled: true,
            disabledReason: copy.billingModeComingSoon,
          },
        ]}
        extra={extra}
      />
    </Card>
  );
}
