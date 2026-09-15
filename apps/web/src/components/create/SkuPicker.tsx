/** 规格选择器:full 提供 GPU/CPU 分栏与工具插槽;compact 可折叠已选规格并选择卡数。 */

import type { SkuMarketOut } from "@superdl/api-client";
import { fontSize, GPU_COUNT_STEPS, skuTierMap, skuVariant, space, useFormat } from "@superdl/ui";
import { CHIP_LABEL_WIDTH, ChipRow, TableErrorEmpty, type ChipOption } from "@superdl/ui/components";
import { Button, Segmented, Space, Table, Tooltip, Typography } from "antd";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { dedupAvailableByModel } from "../../lib/inventory";
import { SKU_ROW_DISABLED_CLASS, skuColumns, skuDisabledReason, skuSelectable } from "../skuTable";
import type { SpotPolicy } from "../spotBilling";

const ALL = "";
export type SkuKind = "gpu" | "cpu";

/** 规格筛选态;0 或空串表示该项不限,不含购买数量。 */
export interface SkuFilters {
  kind: SkuKind;
  model: string;
  tier: string;
  vram: number;
  vcpu: number;
  mem: number;
}

export const DEFAULT_SKU_FILTERS: SkuFilters = {
  kind: "gpu",
  model: ALL,
  tier: ALL,
  vram: 0,
  vcpu: 0,
  mem: 0,
};

/** 按分栏与筛选值匹配规格;GPU 规格同时校验单实例卡数上限。 */
function matches(s: SkuMarketOut, f: SkuFilters, qty: number): boolean {
  const isCpu = f.kind === "cpu";
  if ((s.tier === "cpu") !== isCpu) return false;
  if (isCpu) {
    return (!f.vcpu || s.vcpu === f.vcpu) && (!f.mem || s.mem_gb === f.mem);
  }
  return (
    (!f.model || s.gpu_model === f.model) &&
    (!f.tier || skuVariant(s.tier, s.pool_label) === f.tier) &&
    (!f.vram || s.vram_gb === f.vram) &&
    s.max_gpus_per_instance >= qty
  );
}

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
  variant = "compact",
  filters: controlled,
  onFiltersChange,
  priceFontSize,
  qtyRow,
  toolbarExtra,
}: {
  skus: SkuMarketOut[] | undefined;
  isLoading: boolean;
  isError: boolean;
  onRetry: () => void;
  value: SkuMarketOut | undefined;
  onChange: (sku: SkuMarketOut | undefined) => void;
  /** 购买数量(不是筛选):决定库存口径、价格列总价与不可选行 */
  gpuCount: number;
  onGpuCount: (n: number) => void;
  /** 竞价档选中时传入:价格列改显折后价,未上竞价的行灰置 */
  spot?: SpotPolicy;
  variant?: "full" | "compact";
  /** 受控筛选态(不传则组件内部持有) */
  filters?: SkuFilters;
  onFiltersChange?: (next: SkuFilters) => void;
  priceFontSize?: number;
  /** full:筛选行与工具行之间的独立行(市场页的「GPU 数量」购买数量 chips) */
  qtyRow?: ReactNode;
  /** full:工具行右侧(市场页的「计费方式」chips) */
  toolbarExtra?: ReactNode;
}) {
  const { t } = useTranslation(["web", "shared"]);
  const fmt = useFormat();
  const [expanded, setExpanded] = useState(value == null);
  const [moreOpen, setMoreOpen] = useState(false);
  const [inner, setInner] = useState<SkuFilters>({
    ...DEFAULT_SKU_FILTERS,
    kind: value?.tier === "cpu" ? "cpu" : "gpu",
  });
  const f = controlled ?? inner;
  const setF = (patch: Partial<SkuFilters>) => {
    const next = { ...f, ...patch };
    if (controlled) onFiltersChange?.(next);
    else setInner(next);
  };
  const isCpu = f.kind === "cpu";
  const all = skus ?? [];
  const kindSkus = all.filter((s) => (s.tier === "cpu") === isCpu);
  const freeByModel = dedupAvailableByModel(kindSkus);
  const needed = isCpu ? 1 : gpuCount;
  const selectable = (s: SkuMarketOut) => skuSelectable(s, needed, spot != null);

  /** 仅替换一个筛选值后的匹配规格数。 */
  const facet = <K extends keyof SkuFilters>(key: K, v: SkuFilters[K]) =>
    all.filter((s) => matches(s, { ...f, [key]: v }, needed)).length;
  const gate = <T extends string | number>(key: keyof SkuFilters, v: T): Omit<ChipOption<T>, "label"> => {
    const n = facet(key, v as never);
    return {
      value: v,
      disabled: n === 0 && f[key] !== v,
      disabledReason: n === 0 ? t("market.noResultForChip") : undefined,
    };
  };
  const numOptions = (
    key: "vram" | "vcpu" | "mem",
    values: number[],
    unit: (v: number) => string,
  ): ChipOption<number>[] => [
    { value: 0, label: t("market.all") },
    ...Array.from(new Set(values))
      .sort((a, b) => a - b)
      .map((v) => ({ ...gate(key, v), label: unit(v) })),
  ];
  const modelOptions: ChipOption<string>[] = [
    { value: ALL, label: t("market.all") },
    ...Array.from(freeByModel.entries()).map(([m, free]) => ({
      ...gate("model", m),
      label: (
        <span>
          {m}{" "}
          <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
            {t("market.freeSuffix", { count: free })}
          </Typography.Text>
        </span>
      ),
    })),
  ];
  const tierOptions: ChipOption<string>[] = [
    { value: ALL, label: t("market.all") },
    ...Object.entries(skuTierMap)
      .filter(([v]) => v !== "cpu")
      .map(([v, meta]) => ({ ...gate("tier", v), label: t(meta.labelKey) })),
  ];
  const vramOptions = numOptions(
    "vram",
    kindSkus.map((s) => s.vram_gb),
    (v) => `${v} GB`,
  );
  const vcpuOptions = numOptions(
    "vcpu",
    kindSkus.map((s) => s.vcpu),
    (v) => t("market.vcpuUnit", { count: v }),
  );
  const memOptions = numOptions(
    "mem",
    kindSkus.map((s) => s.mem_gb),
    (v) => `${v} GB`,
  );

  const filtered = all.filter((s) => matches(s, f, needed));
  const rows = [...filtered].sort((a, b) => Number(selectable(b)) - Number(selectable(a)));
  const hasFilter = f.model !== ALL || f.tier !== ALL || f.vram !== 0 || f.vcpu !== 0 || f.mem !== 0;
  const showMore = moreOpen || f.vram !== 0 || f.vcpu !== 0 || f.mem !== 0;

  const columns = skuColumns({
    fmt,
    t,
    availability: true,
    priceFontSize: priceFontSize ?? fontSize.sectionTitle,
    cpu: isCpu,
    units: needed,
    ...(spot ? { spot } : {}),
  });

  const gpuCountRow = variant === "compact" && value && value.tier !== "cpu" && (
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

  if (variant === "compact" && value && !expanded) {
    return (
      <Space orientation="vertical" size={space.md} style={{ width: "100%" }}>
        <Table<SkuMarketOut>
          size="small"
          rowKey="id"
          dataSource={[value]}
          columns={skuColumns({ fmt, t, cpu: value.tier === "cpu", units: gpuCount, ...(spot ? { spot } : {}) })}
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
    <Space orientation="vertical" size={space.md} style={{ width: "100%" }}>
      <Segmented<SkuKind>
        value={f.kind}
        options={[
          { value: "gpu", label: t("market.kindGpu") },
          { value: "cpu", label: t("market.kindCpu") },
        ]}
        onChange={(v) => {
          setF({ ...DEFAULT_SKU_FILTERS, kind: v });
          onChange(undefined);
        }}
      />
      {!isCpu && (
        <>
          <ChipRow
            label={t("market.chipGpuModel")}
            value={f.model}
            onChange={(v) => setF({ model: v })}
            options={modelOptions}
          />
          <ChipRow
            label={t("market.chipTier")}
            value={f.tier}
            onChange={(v) => setF({ tier: v })}
            options={tierOptions}
          />
        </>
      )}
      {variant === "full" && (
        <>
          <div style={{ paddingInlineStart: CHIP_LABEL_WIDTH + 12 }}>
            <Button
              type="link"
              size="small"
              style={{ paddingInline: 0, fontSize: fontSize.caption }}
              onClick={() => setMoreOpen((v) => !v)}
            >
              {showMore ? t("market.lessFilters") : t("market.moreFilters")}
            </Button>
          </div>
          {showMore &&
            (isCpu ? (
              <>
                <ChipRow
                  label={t("market.chipVcpu")}
                  value={f.vcpu}
                  onChange={(v) => setF({ vcpu: v })}
                  options={vcpuOptions}
                />
                <ChipRow
                  label={t("market.chipMem")}
                  value={f.mem}
                  onChange={(v) => setF({ mem: v })}
                  options={memOptions}
                />
              </>
            ) : (
              <ChipRow
                label={t("market.chipVram")}
                value={f.vram}
                onChange={(v) => setF({ vram: v })}
                options={vramOptions}
              />
            ))}
          {qtyRow}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: space.md,
              flexWrap: "wrap",
              paddingInlineStart: CHIP_LABEL_WIDTH + 12,
            }}
          >
            <Space size={space.sm}>
              <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
                {t("market.resultCount", { count: filtered.length })}
              </Typography.Text>
              {hasFilter && (
                <Button
                  type="link"
                  size="small"
                  style={{ paddingInline: 0, fontSize: fontSize.caption }}
                  onClick={() => {
                    setF({ ...DEFAULT_SKU_FILTERS, kind: f.kind });
                  }}
                >
                  {t("market.clearFilters")}
                </Button>
              )}
            </Space>
            {toolbarExtra && <div style={{ flex: 1, minWidth: 280 }}>{toolbarExtra}</div>}
          </div>
        </>
      )}
      <Table<SkuMarketOut>
        size={variant === "compact" ? "small" : "middle"}
        rowKey="id"
        loading={isLoading}
        scroll={{ x: 880 }}
        dataSource={rows}
        columns={columns}
        pagination={false}
        locale={{
          emptyText: isError ? <TableErrorEmpty isError onRetry={onRetry} /> : t("market.noMatch"),
        }}
        rowClassName={(s) => (selectable(s) ? "" : SKU_ROW_DISABLED_CLASS)}
        rowSelection={{
          type: "radio",
          selectedRowKeys: value ? [value.id] : [],
          onChange: (keys) => onChange(rows.find((s) => s.id === keys[0])),
          getCheckboxProps: (s) => ({ disabled: !selectable(s) }),
          renderCell: (_checked, s, _index, node) => {
            const reason = skuDisabledReason(s, needed, spot != null, t);
            return reason ? <Tooltip title={reason}>{node}</Tooltip> : node;
          },
        }}
        onRow={(s) => ({
          style: selectable(s) ? { cursor: "pointer" } : undefined,
          onClick: () => {
            if (selectable(s)) onChange(s);
          },
        })}
      />
      {gpuCountRow}
    </Space>
  );
}
