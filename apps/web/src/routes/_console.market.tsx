/** 算力市场:规格选择(SkuPicker full,筛选入 URL)在上 → 计费方式在下(可选项依赖已选规格)→ 底部结算条。CTA 即库存,不可选行灰置排末不隐藏;
 *  未登录可看,CTA「登录后租用」带回完整筛选态。规格不支持所选计费方式时用 message 明示并切回按量,不让 chip 静默跳动。 */

import type { SkuMarketOut } from "@superdl/api-client";
import {
  billingUnits,
  fontSize,
  isBillingPeriod,
  MAX_PERIOD_COUNT,
  mulPrice,
  periodMap,
  POLL,
  skuTierMap,
  skuVariant,
  space,
} from "@superdl/ui";
import { PageHeader } from "@superdl/ui/components";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { App, Button, Card, Modal, Space, Tooltip, Typography } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useFormat } from "@superdl/ui";
import { usePolicies, useSkus } from "../api/queries";
import { CheckoutBar } from "../components/CheckoutBar";
import { DEFAULT_SKU_FILTERS, SkuPicker, type SkuFilters } from "../components/create/SkuPicker";
import { PeriodQuoteRows, periodQuoteOf, usePeriodDiscounts } from "../components/periodBilling";
import { BillingModeCard, type BillingMode } from "../components/skuTable";
import { SpotOffLabel, SpotPriceInline, spotPriceOf, useSpotPolicy } from "../components/spotBilling";
import { useIsLoggedIn } from "../stores/auth";

type Kind = "gpu" | "cpu";

/** 市场页 URL 状态:筛选 / 选中 / 计费 10 个参数,默认值一律剥离(kind=gpu / mode=on_demand / chips 空档 / gpus=1 / count=1);非法值回默认。 */
export interface MarketSearch {
  kind?: Kind;
  mode?: BillingMode;
  model?: string;
  tier?: string;
  vram?: number;
  gpus?: number;
  vcpu?: number;
  mem?: number;
  sku?: number;
  /** 购买时长(周期份数,1~36;仅包周期模式有意义) */
  count?: number;
}

function posInt(v: unknown): number | undefined {
  const n = Number(v);
  return Number.isInteger(n) && n > 0 ? n : undefined;
}

/** 独立导出供单测往返验证(与 Route.validateSearch 同一函数)。 */
export function marketValidateSearch(search: Record<string, unknown>): MarketSearch {
  const out: MarketSearch = {};
  if (search.kind === "cpu") out.kind = "cpu"; // gpu 为默认栏,剥离
  const mode = search.mode;
  if (mode === "spot") out.mode = "spot";
  else if (typeof mode === "string" && isBillingPeriod(mode)) out.mode = mode;
  // on_demand 为默认,剥离
  if (typeof search.model === "string" && search.model !== "") out.model = search.model;
  if (
    typeof search.tier === "string" &&
    search.tier !== "" &&
    search.tier !== "cpu" &&
    search.tier in skuTierMap
  ) {
    out.tier = search.tier;
  }
  const vram = posInt(search.vram);
  if (vram != null) out.vram = vram;
  const gpus = posInt(search.gpus);
  if (gpus != null && gpus > 1) out.gpus = gpus; // 1 卡为默认,剥离
  const vcpu = posInt(search.vcpu);
  if (vcpu != null) out.vcpu = vcpu;
  const mem = posInt(search.mem);
  if (mem != null) out.mem = mem;
  const sku = posInt(search.sku);
  if (sku != null) out.sku = sku;
  const count = posInt(search.count);
  // 1 份为默认,剥离;超上限丢弃
  if (count != null && count > 1 && count <= MAX_PERIOD_COUNT) out.count = count;
  return out;
}

export const Route = createFileRoute("/_console/market")({
  validateSearch: marketValidateSearch,
  component: MarketPage,
});

/** URL 参数 → SkuPicker 筛选态(默认值补齐) */
function filtersOf(s: MarketSearch): SkuFilters {
  return {
    kind: s.kind ?? "gpu",
    model: s.model ?? DEFAULT_SKU_FILTERS.model,
    tier: s.tier ?? DEFAULT_SKU_FILTERS.tier,
    vram: s.vram ?? 0,
    gpus: s.gpus ?? 1,
    vcpu: s.vcpu ?? 0,
    mem: s.mem ?? 0,
  };
}

/** SkuPicker 筛选态 → URL 参数(默认值剥离) */
function searchOfFilters(f: SkuFilters, prev: MarketSearch): MarketSearch {
  return {
    ...prev,
    kind: f.kind === "cpu" ? "cpu" : undefined,
    model: f.model || undefined,
    tier: f.tier || undefined,
    vram: f.vram || undefined,
    gpus: f.gpus > 1 ? f.gpus : undefined,
    vcpu: f.vcpu || undefined,
    mem: f.mem || undefined,
  };
}

function MarketPage() {
  const { t } = useTranslation(["web", "shared"]);
  const fmt = useFormat();
  const { formatHourlyPrice } = fmt;
  const navigate = useNavigate();
  const { message } = App.useApp();
  const loggedIn = useIsLoggedIn();
  const [rulesOpen, setRulesOpen] = useState(false);
  // 筛选/选中全部入 URL(replace)
  const search = Route.useSearch();
  const filters = filtersOf(search);
  const isCpu = filters.kind === "cpu";
  const gpuCount = filters.gpus;
  const billingMode: BillingMode = search.mode ?? "on_demand";
  const selectedId = search.sku;
  // 购买时长(1~36 个周期):入 URL 并透传创建页
  const periodCount = search.count ?? 1;
  const update = (next: MarketSearch) => void navigate({ to: "/market", search: next, replace: true });

  const { data: allSkus, isLoading, isError, refetch } = useSkus({ refetchInterval: POLL.steady });
  // 冻结宽限小时数读 /policies;未就绪用无数字兜底句
  const { data: policies } = usePolicies();
  const discounts = usePeriodDiscounts();
  const spotPolicy = useSpotPolicy();

  const selected = (allSkus ?? []).find((s) => s.id === selectedId);
  const needed = isCpu ? 1 : gpuCount;

  // 选中规格不接受包周期 / 未上竞价:URL 里已是按量(选中时即切回并提示),这里只做兜底
  const periodBlocked = selected != null && !selected.period_enabled;
  const spotUnavailable = selected != null && !selected.spot_enabled;
  const spotBlocked = spotUnavailable || spotPolicy == null;
  const mode: BillingMode =
    (periodBlocked && isBillingPeriod(billingMode)) || (spotBlocked && billingMode === "spot")
      ? "on_demand"
      : billingMode;
  const isSpot = mode === "spot";
  const period = isBillingPeriod(mode) ? mode : null;

  /** 选中规格:若当前计费方式对它不可用,明示并切回按量(不让 chip 静默跳动) */
  const onSelectSku = (s: SkuMarketOut | undefined) => {
    let nextMode: BillingMode | undefined = search.mode;
    if (s) {
      if (isBillingPeriod(billingMode) && !s.period_enabled) {
        message.info(t("market.modeFallback", { mode: t(periodMap[billingMode].labelKey) }));
        nextMode = undefined;
      } else if (billingMode === "spot" && !s.spot_enabled) {
        message.info(t("market.modeFallback", { mode: t("shared:status.market.spot") }));
        nextMode = undefined;
      }
    }
    update({ ...search, sku: s?.id, mode: nextMode });
  };

  // 明细区单价:竞价档报折后价,其余报 SKU 原价
  const unitPrice =
    selected && isSpot
      ? (spotPriceOf(selected.price_hourly, spotPolicy) ?? selected.price_hourly)
      : selected?.price_hourly;

  // 市场页无报价端点,按 policies 折扣本地估算(展示值,创建页报价为准)
  const quote =
    selected && period
      ? periodQuoteOf(selected.price_hourly, { units: billingUnits(isCpu ? 0 : gpuCount), period, periodCount }, discounts)
      : undefined;

  const selectedVariant = selected ? skuVariant(selected.tier, selected.pool_label) : null;
  const createSearch = {
    ...(isCpu ? {} : { gpus: gpuCount }),
    ...(period ? { period } : {}),
    ...(period && periodCount > 1 ? { count: periodCount } : {}),
    ...(isSpot ? { market: "spot" as const } : {}),
  };
  /** 未登录去登录:带回完整市场筛选态 */
  const marketHref = () => {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(search)) if (v !== undefined) qs.set(k, String(v));
    const s = qs.toString();
    return s ? `/market?${s}` : "/market";
  };

  return (
    // 不用 Space(ant-space-item 包装会破坏 sticky 结算条的包含块)
    <div style={{ display: "flex", flexDirection: "column", gap: space.lg, width: "100%" }}>
      <PageHeader
        title={t("market.title")}
        extra={
          <Button type="link" size="small" onClick={() => setRulesOpen(true)}>
            {t("market.billingRulesLink")}
          </Button>
        }
      />

      <Card title={t("market.selectSpec")} styles={{ body: { paddingBlock: 16 } }}>
        <SkuPicker
          variant="full"
          skus={allSkus}
          isLoading={isLoading}
          isError={isError}
          onRetry={() => void refetch()}
          value={selected}
          onChange={onSelectSku}
          gpuCount={gpuCount}
          onGpuCount={(n) => update({ ...search, gpus: n > 1 ? n : undefined })}
          filters={filters}
          onFiltersChange={(f) => update({ ...searchOfFilters(f, search), sku: undefined })}
          priceFontSize={fontSize.pageTitle}
          {...(isSpot && spotPolicy ? { spot: spotPolicy } : {})}
        />
        {/* 选中共享·经济时,在选择处就地给风险摘要(完整条款在创建页提交前的知情同意里) */}
        {selectedVariant === "shared_hami" && (
          <Typography.Text type="warning" style={{ display: "block", marginTop: space.md, fontSize: fontSize.caption }}>
            {t("market.ecoRiskSummary")}
          </Typography.Text>
        )}
      </Card>

      <BillingModeCard
        value={mode}
        onChange={(v) => update({ ...search, mode: v === "on_demand" ? undefined : v })}
        periodEnabled={!periodBlocked}
        spotEnabled={!spotUnavailable}
        count={periodCount}
        onCountChange={(n) => update({ ...search, count: n > 1 ? n : undefined })}
      />
      {/* 合规声明只在市场页脚(ui-ux-spec §1 规则 1) */}
      <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
        {t("copy.antiMiningNotice")}
      </Typography.Text>

      <CheckoutBar
        // 选中规格/计费方式/时长变化时汇总数字淡入
        changeKey={selected ? `${selected.id}-${mode}-${periodCount}` : "none"}
        summary={
          selected
            ? isCpu
              ? t("market.summaryCpu", { vcpu: selected.vcpu, mem: selected.mem_gb, disk: selected.disk_gb })
              : t("market.summary", {
                  model: selected.gpu_model,
                  count: gpuCount,
                  vcpu: selected.vcpu * gpuCount,
                  mem: selected.mem_gb * gpuCount,
                  disk: selected.disk_gb,
                })
            : t("market.selectHint")
        }
        items={[
          period && quote
            ? {
                label: t("period.costLabel", { period: t(periodMap[period].labelKey) }),
                value: (
                  <Space size={8} align="baseline">
                    <Typography.Text type="secondary" delete style={{ fontSize: fontSize.body }}>
                      {fmt.formatMoney(quote.listAmount)}
                    </Typography.Text>
                    <span>{fmt.formatPeriodPrice(quote.amount, period, periodCount)}</span>
                  </Space>
                ),
              }
            : {
                label: t("create.configCostLabel"),
                // 大字带「× N 卡」/「整机」后缀(价格口径显性化);CPU 规格 price_hourly 已是整机时价
                suffix: !selected ? undefined : isCpu ? t("sku.wholeMachine") : t("sku.timesCards", { count: gpuCount }),
                value: !selected ? (
                  "--"
                ) : isSpot ? (
                  <Space size={8} align="baseline">
                    <SpotPriceInline baseHourly={selected.price_hourly} units={needed} policy={spotPolicy} />
                    <SpotOffLabel policy={spotPolicy} />
                  </Space>
                ) : (
                  formatHourlyPrice(mulPrice(selected.price_hourly, needed))
                ),
              },
        ]}
        detail={
          selected ? (
            period && quote ? (
              <PeriodQuoteRows quote={quote} gpuCount={gpuCount} cpu={isCpu} hint={t("period.hintFinalOnCreate")} />
            ) : (
              <Space orientation="vertical" size={4}>
                <span>
                  {isCpu
                    ? t("instances.pricePerInstance", { price: formatHourlyPrice(unitPrice) })
                    : t("instances.pricePerCard", { price: formatHourlyPrice(unitPrice), count: gpuCount })}
                </span>
                <Typography.Text type="secondary">{isCpu ? t("copy.billingBasisCpu") : t("copy.billingBasis")}</Typography.Text>
                {isSpot && <Typography.Text type="secondary">{t("copy.spotBillingBasis")}</Typography.Text>}
              </Space>
            )
          ) : undefined
        }
        actions={
          <Tooltip title={selected ? undefined : t("market.selectFirst")}>
            <Button
              type="primary"
              size="large"
              disabled={!selected}
              onClick={() => {
                if (!selected) return;
                if (!loggedIn) {
                  void navigate({ to: "/login", search: { redirect: marketHref() } });
                  return;
                }
                void navigate({
                  to: "/market/create/$skuId",
                  params: { skuId: String(selected.id) },
                  search: createSearch,
                });
              }}
            >
              {loggedIn ? t("market.next") : t("market.loginToRent")}
            </Button>
          </Tooltip>
        }
      />

      <Modal open={rulesOpen} onCancel={() => setRulesOpen(false)} footer={null} title={t("market.billingRulesLink")}>
        <ul style={{ paddingInlineStart: 20, margin: 0 }}>
          {[
            t("copy.billingRules.r1"),
            t("copy.billingRules.r2"),
            t("copy.billingRules.r3"),
            policies ? t("copy.billingRules.r4", { hours: policies.freeze_grace_hours }) : t("copy.billingRules.r4Fallback"),
            t("copy.billingRules.r5"),
          ].map((r) => (
            <li key={r} style={{ marginBottom: 8 }}>
              {r}
            </li>
          ))}
        </ul>
      </Modal>
    </div>
  );
}
