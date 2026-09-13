/** 规格选择器(市场页 full / 部署页 compact 共用一份表格、库存口径与灰置规则)。
 *  full:GPU / CPU 分栏 + GPU 两行 chips(型号 / 档位)或 CPU 无一级 chips,显存 / vCPU / 内存折进「更多筛选」二级行;
 *        chip 只在 0 结果时灰置,facet 数字不进 chip(结果数只在「共 N 个规格」行);
 *        购买数量与计费方式由调用方经 qtyRow / toolbarExtra 插槽给(它们不是筛选)。
 *  compact:型号 / 档位两行 + 已选时折叠成一行回显 +「更换规格」+ 自持 GPU 数量 chips。
 *  不可选行(库存不足所选卡数 / 竞价档未上)弱化底色 + 原因,排到末尾,不隐藏。 */

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

/** 筛选态(0 / 空串 = 全部);受控时由调用方持有(市场页入 URL)。购买数量不在其中:它不是筛选。 */
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

/** 行是否进结果集:分栏 + 筛选 chips,外加「单实例卡数上限装得下所选数量」(装不下的规格买不到,不是筛选)。 */
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

  /** 某个 chip 选项在「其余筛选不变」下的剩余结果数(facet 计数);0 结果灰置但不隐藏,数字不进 chip */
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
  // 型号 chip 只带一个数字:该型号当前可开实例数(结果数在「共 N 个规格」行)
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

  // 结果:可选行在前,不可选行(库存不足 / 未上竞价)排到末尾
  const filtered = all.filter((s) => matches(s, f, needed));
  const rows = [...filtered].sort((a, b) => Number(selectable(b)) - Number(selectable(a)));
  const hasFilter = f.model !== ALL || f.tier !== ALL || f.vram !== 0 || f.vcpu !== 0 || f.mem !== 0;
  // 二级筛选有值时自动展开(不让筛选态藏在收起的行里)
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

  // compact 变体:已选后出「GPU 数量」行(受 SKU 上限与库存约束);full 的购买数量由调用方经 qtyRow 给
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
      <Space orientation="vertical" size={12} style={{ width: "100%" }}>
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
    <Space orientation="vertical" size={12} style={{ width: "100%" }}>
      <Segmented<SkuKind>
        value={f.kind}
        options={[
          { value: "gpu", label: t("market.kindGpu") },
          { value: "cpu", label: t("market.kindCpu") },
        ]}
        onChange={(v) => {
          // 换栏清选中,筛选回默认
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
          {/* 工具行:结果数 + 清除筛选(左)|计费方式 chips(右,窄屏换行) */}
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
                    // 购买数量不是筛选,不在清除范围内
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
