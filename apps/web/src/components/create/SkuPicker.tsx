/** 紧凑版规格选择器(部署页内嵌):已选时折叠成一行回显 + GPU 数量,点「更换规格」展开
 *  GPU / CPU 分栏 + 型号 / 档位 chips + SKU 表 radio(与市场页同一套列与库存口径)。
 *  库存不足所选卡数的行灰置不隐藏。 */

import type { SkuMarketOut } from "@superdl/api-client";
import { fontSize, GPU_COUNT_STEPS, skuTierMap, skuVariant, useFormat } from "@superdl/ui";
import { TableErrorEmpty } from "@superdl/ui/components";
import { Button, Segmented, Space, Table, Typography } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { dedupAvailableByModel } from "../../lib/inventory";
import { ChipRow, type ChipOption } from "../ChipRow";
import { skuColumns } from "../skuTable";
import type { SpotPolicy } from "../spotBilling";

const ALL = "";
type Kind = "gpu" | "cpu";

export function SkuPicker({
  skus,
  isLoading,
  isError,
  onRetry,
  value,
  onChange,
  gpuCount,
  onGpuCount,
  spot,
}: {
  skus: SkuMarketOut[] | undefined;
  isLoading: boolean;
  isError: boolean;
  onRetry: () => void;
  value: SkuMarketOut | undefined;
  onChange: (sku: SkuMarketOut | undefined) => void;
  gpuCount: number;
  onGpuCount: (n: number) => void;
  /** 竞价档选中时传入:表格价格列改显折后价 */
  spot?: SpotPolicy;
}) {
  const { t } = useTranslation(["web", "shared"]);
  const fmt = useFormat();
  const [expanded, setExpanded] = useState(value == null);
  const [kind, setKind] = useState<Kind>(value?.tier === "cpu" ? "cpu" : "gpu");
  const [model, setModel] = useState(ALL);
  const [tier, setTier] = useState(ALL);
  const isCpu = kind === "cpu";
  const kindSkus = (skus ?? []).filter((s) => (s.tier === "cpu") === isCpu);
  const freeByModel = dedupAvailableByModel(kindSkus);
  const modelOptions: ChipOption<string>[] = [
    { value: ALL, label: t("market.all") },
    ...Array.from(freeByModel.entries()).map(([m, free]) => ({
      value: m,
      label: (
        <span>
          {m} <Typography.Text type="secondary">{t("market.freeSuffix", { count: free })}</Typography.Text>
        </span>
      ),
    })),
  ];
  const tierOptions: ChipOption<string>[] = [
    { value: ALL, label: t("market.all") },
    ...Object.entries(skuTierMap)
      .filter(([v]) => v !== "cpu")
      .map(([v, meta]) => ({ value: v, label: t(meta.labelKey) })),
  ];
  const rows = kindSkus.filter(
    (s) =>
      isCpu ||
      ((!model || s.gpu_model === model) && (!tier || skuVariant(s.tier, s.pool_label) === tier)),
  );
  const needed = isCpu ? 1 : gpuCount;
  const selectable = (s: SkuMarketOut) =>
    (s.available_count ?? 0) >= needed && (!spot || s.spot_enabled);
  const columns = skuColumns({
    fmt,
    t,
    availability: true,
    priceFontSize: fontSize.sectionTitle,
    cpu: isCpu,
    ...(spot ? { spot } : {}),
  });

  const gpuCountRow = value && value.tier !== "cpu" && (
    <ChipRow
      label={t("market.chipGpuCount")}
      value={gpuCount}
      onChange={onGpuCount}
      options={Array.from({ length: value.max_gpus_per_instance }, (_, i) => i + 1)
        .filter((n) => GPU_COUNT_STEPS.includes(n) || n === value.max_gpus_per_instance)
        .map((n) => ({
          value: n,
          label: t("market.cardsUnit", { count: n }),
          disabled: n > (value.available_count ?? 0),
          disabledReason: t("copy.noStockForGpuCount"),
        }))}
    />
  );

  if (value && !expanded) {
    return (
      <Space orientation="vertical" size={12} style={{ width: "100%" }}>
        <Table<SkuMarketOut>
          size="small"
          rowKey="id"
          dataSource={[value]}
          columns={skuColumns({ fmt, t, cpu: value.tier === "cpu", ...(spot ? { spot } : {}) })}
          pagination={false}
        />
        {gpuCountRow}
        <Button type="link" size="small" style={{ paddingInline: 0 }} onClick={() => setExpanded(true)}>
          {t("services.form.specChange")}
        </Button>
      </Space>
    );
  }
  return (
    <Space orientation="vertical" size={12} style={{ width: "100%" }}>
      <Segmented<Kind>
        value={kind}
        options={[
          { value: "gpu", label: t("market.kindGpu") },
          { value: "cpu", label: t("market.kindCpu") },
        ]}
        onChange={(v) => {
          // 换栏必须清选中:上一栏的行不在本栏表里
          setKind(v);
          onChange(undefined);
        }}
      />
      {!isCpu && (
        <>
          <ChipRow label={t("market.chipGpuModel")} value={model} onChange={setModel} options={modelOptions} />
          <ChipRow label={t("market.chipTier")} value={tier} onChange={setTier} options={tierOptions} />
        </>
      )}
      <Table<SkuMarketOut>
        size="small"
        rowKey="id"
        loading={isLoading}
        scroll={{ x: 880 }}
        dataSource={rows}
        columns={columns}
        pagination={false}
        locale={{
          emptyText: isError ? <TableErrorEmpty isError onRetry={onRetry} /> : t("market.noMatch"),
        }}
        rowSelection={{
          type: "radio",
          selectedRowKeys: value ? [value.id] : [],
          onChange: (keys) => onChange(rows.find((s) => s.id === keys[0])),
          getCheckboxProps: (s) => ({ disabled: !selectable(s) }),
        }}
        onRow={(s) => ({
          style: selectable(s) ? { cursor: "pointer" } : { opacity: 0.5 },
          onClick: () => {
            if (selectable(s)) onChange(s);
          },
        })}
      />
      {gpuCountRow}
    </Space>
  );
}
