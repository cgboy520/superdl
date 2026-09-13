/** GPU 价格墙:一个型号一张卡,卡内按档位(专用整卡 / 共享·标准 / 共享·经济)分行,每行自带价格与可开台数。
 *  行 CTA 进市场并带上型号与规格(不直落创建页:档位、卡数、计费方式还没选);售罄行给同型号其它档位入口。
 *  接口失败整区降级为「前往算力市场」,不渲染假数字。 */

import type { SkuMarketOut } from "@superdl/api-client";
import {
  compareAmounts,
  fontFamilyMono,
  fontSize,
  fontWeight,
  getGpuSpec,
  layout,
  POLL,
  skuTierMap,
  skuVariant,
  space,
  useFormat,
} from "@superdl/ui";
import { StatusTag } from "@superdl/ui/components";
import { Link } from "@tanstack/react-router";
import { Button, Card, Skeleton, theme, Typography } from "antd";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";

import { useSkus } from "../../api/queries";
import { LandingSection } from "./LandingSection";

interface TierRow {
  sku: SkuMarketOut;
  variant: string;
  available: number;
}

interface ModelCard {
  model: string;
  rows: TierRow[];
  minPrice: string;
}

/** 按型号分组,组内按档位取最低价的一条,卡片按最低价排序。 */
function groupByModel(skus: readonly SkuMarketOut[]): ModelCard[] {
  const byModel = new Map<string, Map<string, TierRow>>();
  for (const sku of skus) {
    if (!sku.gpu_model) continue; // CPU 规格不进 GPU 价格墙
    const variant = skuVariant(sku.tier, sku.pool_label);
    const tiers = byModel.get(sku.gpu_model) ?? new Map<string, TierRow>();
    const cur = tiers.get(variant);
    if (cur === undefined || compareAmounts(sku.price_hourly, cur.sku.price_hourly) < 0) {
      tiers.set(variant, { sku, variant, available: sku.available_count ?? 0 });
    } else if (cur.available === 0 && (sku.available_count ?? 0) > 0) {
      // 同档位里有货的那条更适合摆出来
      tiers.set(variant, { sku, variant, available: sku.available_count ?? 0 });
    }
    byModel.set(sku.gpu_model, tiers);
  }
  const cards: ModelCard[] = [];
  for (const [model, tiers] of byModel) {
    const rows = [...tiers.values()].sort((a, b) => compareAmounts(b.sku.price_hourly, a.sku.price_hourly));
    const minRow = rows[rows.length - 1];
    if (!minRow) continue;
    cards.push({ model, rows, minPrice: minRow.sku.price_hourly });
  }
  return cards.sort((a, b) => compareAmounts(a.minPrice, b.minPrice));
}

/** 档位规格副行。t 走宽签名:嵌套插值下泛型 t 会把类型实例化撑爆(TS2589),键的完整性由 locales.test 兜底。 */
type LooseT = (key: string, opts?: Record<string, unknown>) => string;

function tierSpecText(row: TierRow, t: LooseT): string {
  const { sku, variant } = row;
  if (variant === "shared_mig") {
    return t("landing.pricing.migSpec", { profile: sku.mig_profile ?? t("sku.sliceFallback"), vram: sku.vram_gb });
  }
  if (variant === "shared_hami") {
    return t("landing.pricing.sharedSpec", { pct: sku.gpu_cores_pct, vram: sku.vram_gb });
  }
  return t("landing.pricing.dedicatedSpec", { vram: sku.vram_gb });
}

function TierRowView({ row }: { row: TierRow }) {
  const { t } = useTranslation(["web", "shared"]);
  const { formatHourlyPrice } = useFormat();
  const { token } = theme.useToken();
  const { sku, variant, available } = row;
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: space.md,
        flexWrap: "wrap",
        padding: `${space.md}px 0`,
        borderTop: `1px solid ${token.colorBorderSecondary}`,
      }}
    >
      <div style={{ flex: "1 1 180px", minWidth: 0, display: "flex", flexDirection: "column", gap: 2 }}>
        <StatusTag map={skuTierMap} value={variant} />
        <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
          {tierSpecText(row, t as unknown as LooseT)}
        </Typography.Text>
      </div>
      <div style={{ flex: "0 0 auto", textAlign: "right" }}>
        <div style={{ fontFamily: fontFamilyMono, fontSize: fontSize.sectionTitle, fontWeight: fontWeight.semibold }}>
          {formatHourlyPrice(sku.price_hourly)}
        </div>
        <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
          {t("landing.board.perCard")}
        </Typography.Text>
      </div>
      {available > 0 ? (
        <Link to="/market" search={{ model: sku.gpu_model, sku: sku.id }} style={{ flex: "0 0 auto" }}>
          <Button type="primary">{t("copy.stockAvailable", { count: available })}</Button>
        </Link>
      ) : (
        <div style={{ flex: "0 0 auto", display: "flex", flexDirection: "column", alignItems: "flex-end" }}>
          <Typography.Text type="secondary">{t("copy.outOfStock")}</Typography.Text>
          <Link to="/market" search={{ model: sku.gpu_model }} style={{ fontSize: fontSize.caption }}>
            {t("landing.board.otherTiers")}
          </Link>
        </div>
      )}
    </div>
  );
}

export function PricingSection() {
  const { t } = useTranslation(["web", "shared"]);
  const { data: skus, isLoading, isError } = useSkus({ refetchInterval: POLL.publicBoard });
  const cards = useMemo(() => groupByModel(skus ?? []), [skus]);

  return (
    <LandingSection id="pricing" title={t("landing.pricing.title")} subtitle={t("landing.pricing.subtitle")}>
      {isError ? (
        <div style={{ textAlign: "center", padding: space.xxl }}>
          <Link to="/market">
            <Button type="primary" size="large">
              {t("landing.pricing.fallbackCta")}
            </Button>
          </Link>
        </div>
      ) : (
        <>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))",
              gap: layout.cardGap,
            }}
          >
            {isLoading &&
              [0, 1, 2, 3].map((i) => (
                <Card key={i}>
                  <Skeleton active paragraph={{ rows: 3 }} />
                </Card>
              ))}
            {cards.map((card) => {
              const spec = getGpuSpec(card.model);
              return (
                <Card
                  key={card.model}
                  title={
                    <span style={{ fontFamily: fontFamilyMono, fontSize: fontSize.sectionTitle }}>
                      {spec?.label ?? card.model}
                    </span>
                  }
                  extra={
                    spec ? (
                      <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
                        {t("landing.ranking.modelVram", { label: spec.arch, vram: spec.vramGb })}
                      </Typography.Text>
                    ) : undefined
                  }
                  styles={{ body: { paddingBlock: 0 } }}
                >
                  {card.rows.map((row) => (
                    <TierRowView key={row.sku.id} row={row} />
                  ))}
                  {spec && (
                    <div style={{ paddingBlock: space.md }}>
                      <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
                        {t("landing.pricing.tflops", { fp32: spec.fp32Tflops, fp16: spec.fp16Tflops })}
                      </Typography.Text>
                    </div>
                  )}
                </Card>
              );
            })}
          </div>
          <div style={{ textAlign: "center", marginTop: space.xl }}>
            <Link to="/market">{t("landing.pricing.moreLink")}</Link>
          </div>
          <Typography.Paragraph
            type="secondary"
            style={{ textAlign: "center", marginTop: space.sm, fontSize: fontSize.caption }}
          >
            {t("landing.pricing.stockNote")}
          </Typography.Paragraph>
        </>
      )}
    </LandingSection>
  );
}
