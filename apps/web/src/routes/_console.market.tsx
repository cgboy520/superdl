/** Market: spec filters, purchase quantity, billing mode and checkout preview; the selection state lives in the URL. */

import { QuestionCircleOutlined } from "@ant-design/icons";
import type { SkuMarketOut } from "@superdl/api-client";
import {
  billingUnits,
  fontSize,
  GPU_COUNT_STEPS,
  isBillingPeriod,
  MAX_PERIOD_COUNT,
  mulPrice,
  periodMap,
  POLL,
  skuTierMap,
  skuVariant,
  space,
  useAutoRefresh,
} from "@superdl/ui";
import { ChipRow, GatedButton, PageContainer } from "@superdl/ui/components";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { App, Button, Card, Modal, Space, Typography } from "antd";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { useFormat } from "@superdl/ui";
import { usePolicies, useSkus } from "../api/queries";
import { CheckoutBar } from "../components/CheckoutBar";
import { DEFAULT_SKU_FILTERS, SkuPicker, type SkuFilters } from "../components/create/SkuPicker";
import { PeriodQuoteRows, periodQuoteOf, usePeriodDiscounts } from "../components/periodBilling";
import { BillingModeChips, type BillingMode } from "../components/skuTable";
import { SpotOffLabel, SpotPriceInline, spotPriceOf, useSpotPolicy } from "../components/spotBilling";
import { useIsLoggedIn } from "../stores/auth";
import { listSearchStore } from "../stores/listSearch";

type Kind = "gpu" | "cpu";

/** Market page URL state: 10 parameters for filters / selection / quantity / billing, defaults always stripped (kind=gpu / mode=on_demand / empty chips / qty=1 / count=1); invalid values fall back to defaults. */
export interface MarketSearch {
  kind?: Kind;
  mode?: BillingMode;
  model?: string;
  tier?: string;
  vram?: number;
  /** Purchase quantity (cards, 1–8): not a filter, only decides the stock basis and price */
  qty?: number;
  vcpu?: number;
  mem?: number;
  sku?: number;
  /** Purchase length (period count, 1–36; meaningful in subscription modes only) */
  count?: number;
}

function posInt(v: unknown): number | undefined {
  const n = Number(v);
  return Number.isInteger(n) && n > 0 ? n : undefined;
}

/** Parse the market page URL state, dropping invalid values and defaults. */
export function marketValidateSearch(search: Record<string, unknown>): MarketSearch {
  const out: MarketSearch = {};
  if (search.kind === "cpu") out.kind = "cpu";
  const mode = search.mode;
  if (mode === "spot") out.mode = "spot";
  else if (typeof mode === "string" && isBillingPeriod(mode)) out.mode = mode;
  if (typeof search.model === "string" && search.model !== "") out.model = search.model;
  if (typeof search.tier === "string" && search.tier !== "" && search.tier !== "cpu" && search.tier in skuTierMap) {
    out.tier = search.tier;
  }
  const vram = posInt(search.vram);
  if (vram != null) out.vram = vram;
  const qty = posInt(search.qty);
  if (qty != null && qty > 1) out.qty = qty;
  const vcpu = posInt(search.vcpu);
  if (vcpu != null) out.vcpu = vcpu;
  const mem = posInt(search.mem);
  if (mem != null) out.mem = mem;
  const sku = posInt(search.sku);
  if (sku != null) out.sku = sku;
  const count = posInt(search.count);
  if (count != null && count > 1 && count <= MAX_PERIOD_COUNT) out.count = count;
  return out;
}

export const Route = createFileRoute("/_console/market")({
  validateSearch: marketValidateSearch,
  component: MarketPage,
});

/** URL parameters → SkuPicker filter state (defaults filled in) */
function filtersOf(s: MarketSearch): SkuFilters {
  return {
    kind: s.kind ?? "gpu",
    model: s.model ?? DEFAULT_SKU_FILTERS.model,
    tier: s.tier ?? DEFAULT_SKU_FILTERS.tier,
    vram: s.vram ?? 0,
    vcpu: s.vcpu ?? 0,
    mem: s.mem ?? 0,
  };
}

/** SkuPicker filter state → URL parameters (defaults stripped) */
function searchOfFilters(f: SkuFilters, prev: MarketSearch): MarketSearch {
  return {
    ...prev,
    kind: f.kind === "cpu" ? "cpu" : undefined,
    model: f.model || undefined,
    tier: f.tier || undefined,
    vram: f.vram || undefined,
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
  const search = Route.useSearch();
  const filters = filtersOf(search);
  const isCpu = filters.kind === "cpu";
  const qty = search.qty ?? 1;
  const billingMode: BillingMode = search.mode ?? "on_demand";
  const selectedId = search.sku;
  const periodCount = search.count ?? 1;
  const update = (next: MarketSearch) => void navigate({ to: "/market", search: next, replace: true });
  const setQty = (n: number) => update({ ...search, qty: n > 1 ? n : undefined });

  useEffect(() => {
    listSearchStore.getState().remember("/market", search);
  }, [search]);

  const auto = useAutoRefresh(POLL.steady);
  const {
    data: allSkus,
    isLoading,
    isError,
    refetch,
    isRefetching,
    dataUpdatedAt,
  } = useSkus({
    refetchInterval: auto.refetchInterval,
  });
  const { data: policies } = usePolicies();
  const discounts = usePeriodDiscounts();
  const spotPolicy = useSpotPolicy();

  const selected = (allSkus ?? []).find((s) => s.id === selectedId);
  const needed = isCpu ? 1 : qty;
  const shortOfStock = selected != null && (selected.available_count ?? 0) < needed;

  const periodBlocked = selected != null && !selected.period_enabled;
  const spotUnavailable = selected != null && !selected.spot_enabled;
  const spotBlocked = spotUnavailable || spotPolicy == null;
  const mode: BillingMode =
    (periodBlocked && isBillingPeriod(billingMode)) || (spotBlocked && billingMode === "spot")
      ? "on_demand"
      : billingMode;
  const isSpot = mode === "spot";
  const period = isBillingPeriod(mode) ? mode : null;

  /** Select a spec; when it does not support the current billing mode, notify and switch back to on-demand. */
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

  const unitPrice =
    selected && isSpot
      ? (spotPriceOf(selected.price_hourly, spotPolicy) ?? selected.price_hourly)
      : selected?.price_hourly;

  const quote =
    selected && period
      ? periodQuoteOf(selected.price_hourly, { units: billingUnits(isCpu ? 0 : qty), period, periodCount }, discounts)
      : undefined;

  const selectedVariant = selected ? skuVariant(selected.tier, selected.pool_label) : null;
  const createSearch = {
    ...(isCpu ? {} : { gpus: qty }),
    ...(period ? { period } : {}),
    ...(period && periodCount > 1 ? { count: periodCount } : {}),
    ...(isSpot ? { market: "spot" as const } : {}),
  };
  /** Signed out → login, carrying the full market filter state back */
  const marketHref = () => {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(search)) qs.set(k, String(v));
    const s = qs.toString();
    return s ? `/market?${s}` : "/market";
  };

  return (
    <PageContainer
      title={t("market.title")}
      extra={
        <Button icon={<QuestionCircleOutlined />} onClick={() => setRulesOpen(true)}>
          {t("market.billingRulesLink")}
        </Button>
      }
      freshness={{
        updatedAt: dataUpdatedAt,
        intervalMs: auto.intervalMs,
        paused: auto.paused,
        onTogglePause: auto.toggle,
        onRefresh: () => void refetch(),
        refreshing: isRefetching,
      }}
    >
      <div style={{ display: "flex", flexDirection: "column", gap: space.lg, width: "100%" }}>
        <Card title={t("market.selectSpec")} styles={{ body: { paddingBlock: 16 } }}>
          <SkuPicker
            variant="full"
            skus={allSkus}
            isLoading={isLoading}
            isError={isError}
            onRetry={() => void refetch()}
            value={selected}
            onChange={onSelectSku}
            gpuCount={qty}
            onGpuCount={setQty}
            filters={filters}
            onFiltersChange={(f) => update({ ...searchOfFilters(f, search), sku: undefined })}
            priceFontSize={fontSize.pageTitle}
            {...(isCpu
              ? {}
              : {
                  qtyRow: (
                    <ChipRow
                      label={t("market.chipGpuCount")}
                      value={qty}
                      onChange={setQty}
                      options={GPU_COUNT_STEPS.map((n) => ({
                        value: n,
                        label: t("market.cardsUnit", { count: n }),
                      }))}
                    />
                  ),
                })}
            toolbarExtra={
              <BillingModeChips
                value={mode}
                onChange={(v) => update({ ...search, mode: v === "on_demand" ? undefined : v })}
                periodEnabled={!periodBlocked}
                spotEnabled={!spotUnavailable}
                count={periodCount}
                onCountChange={(n) => update({ ...search, count: n > 1 ? n : undefined })}
              />
            }
            {...(isSpot && spotPolicy ? { spot: spotPolicy } : {})}
          />
          {selectedVariant === "shared_hami" && (
            <Typography.Text
              type="warning"
              style={{ display: "block", marginTop: space.md, fontSize: fontSize.caption }}
            >
              {t("market.ecoRiskSummary")}
            </Typography.Text>
          )}
        </Card>

        <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
          {t("copy.antiMiningNotice")}
        </Typography.Text>

        <CheckoutBar
          changeKey={selected ? `${selected.id}-${mode}-${periodCount}-${qty}` : "none"}
          summary={
            selected
              ? isCpu
                ? t("market.summaryCpu", { vcpu: selected.vcpu, mem: selected.mem_gb, disk: selected.disk_gb })
                : t("market.summary", {
                    model: selected.gpu_model,
                    count: qty,
                    vcpu: selected.vcpu * qty,
                    mem: selected.mem_gb * qty,
                    disk: selected.disk_gb,
                  })
              : t("market.selectHint")
          }
          items={[
            period && quote
              ? {
                  label: t("period.costLabel", { period: t(periodMap[period].labelKey) }),
                  value: (
                    <Space size={space.sm} align="baseline">
                      <Typography.Text type="secondary" delete style={{ fontSize: fontSize.body }}>
                        {fmt.formatMoney(quote.listAmount)}
                      </Typography.Text>
                      <span>{fmt.formatPeriodPrice(quote.amount, period, periodCount)}</span>
                    </Space>
                  ),
                }
              : {
                  label: t("create.configCostLabel"),
                  suffix: !selected ? undefined : isCpu ? t("sku.wholeMachine") : t("sku.timesCards", { count: qty }),
                  value: !selected ? (
                    "--"
                  ) : isSpot ? (
                    <Space size={space.sm} align="baseline">
                      <SpotPriceInline baseHourly={selected.price_hourly} units={needed} policy={spotPolicy} />
                      <SpotOffLabel policy={spotPolicy} />
                    </Space>
                  ) : (
                    formatHourlyPrice(mulPrice(selected.price_hourly, needed))
                  ),
                },
          ]}
          detail={
            unitPrice != null ? (
              period && quote ? (
                <PeriodQuoteRows quote={quote} gpuCount={qty} cpu={isCpu} hint={t("period.hintFinalOnCreate")} />
              ) : (
                <Space orientation="vertical" size={space.xs}>
                  <span>
                    {isCpu
                      ? t("instances.pricePerInstance", { price: formatHourlyPrice(unitPrice) })
                      : t("instances.pricePerCard", { price: formatHourlyPrice(unitPrice), count: qty })}
                  </span>
                  <Typography.Text type="secondary">
                    {isCpu ? t("copy.billingBasisCpu") : t("copy.billingBasis")}
                  </Typography.Text>
                  {isSpot && <Typography.Text type="secondary">{t("copy.spotBillingBasis")}</Typography.Text>}
                </Space>
              )
            ) : undefined
          }
          actions={
            <GatedButton
              type="primary"
              size="large"
              reason={!selected ? t("market.selectFirst") : shortOfStock ? t("copy.noStockForGpuCount") : undefined}
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
            </GatedButton>
          }
        />

        <Modal open={rulesOpen} onCancel={() => setRulesOpen(false)} footer={null} title={t("market.billingRulesLink")}>
          <ul style={{ paddingInlineStart: 20, margin: 0 }}>
            {[
              t("copy.billingRules.r1"),
              t("copy.billingRules.r2"),
              t("copy.billingRules.r3"),
              policies
                ? t("copy.billingRules.r4", { hours: policies.freeze_grace_hours })
                : t("copy.billingRules.r4Fallback"),
              t("copy.billingRules.r5"),
            ].map((r) => (
              <li key={r} style={{ marginBottom: 8 }}>
                {r}
              </li>
            ))}
          </ul>
        </Modal>
      </div>
    </PageContainer>
  );
}
