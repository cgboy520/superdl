/**
 * GPU 价格墙:实时 /skus 数据(公开端点);按型号分组取代表 SKU(组内最低价;库存口径见
 * lib/inventory:同 (池, 型号) 互斥档位取 max、跨池求和),CTA 即库存。卡片列数随宽度自适应。
 * 接口失败整区降级为「前往算力市场」入口。
 */

import { getGpuSpec, metaOf, skuTierMap, skuVariant } from "@superdl/ui";
import { Link, useNavigate } from "@tanstack/react-router";
import { Button, Card, Skeleton, Tabs, Typography } from "antd";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { useFormat } from "../../lib/format";
import { dedupAvailableByModel } from "../../lib/inventory";
import { useSkus } from "../../api/queries";
import { TierTag } from "../../components/common";
import { useIsLoggedIn } from "../../stores/auth";
import type { SkuMarketOut } from "@superdl/api-client";

/** 型号分组代表:价格最低的为展示卡;CTA 优先指向「有货且最便宜」的 SKU。 */
interface ModelGroup {
  model: string;
  representative: SkuMarketOut; // 展示用:组内最低价
  rentTarget: SkuMarketOut; // CTA 用:有货最低价,全组无货回落 representative
  available: number; // 组内可售数(同 (池, 型号) 取 max,跨池求和)
  tiers: string[]; // 组内覆盖的展示档位(tier × pool 的变体,型号下多档展示)
}

export function PricingSection() {
  const { t } = useTranslation(["web", "shared"]);
  const { formatHourlyPrice } = useFormat();
  const navigate = useNavigate();
  const loggedIn = useIsLoggedIn();
  const [tab, setTab] = useState<"dedicated" | "shared">("dedicated");
  const { data: skus, isLoading, isError } = useSkus({ refetchInterval: 60_000 });

  const groups = useMemo<ModelGroup[]>(() => {
    const inTab = (skus ?? []).filter((s) =>
      tab === "dedicated" ? s.tier === "dedicated" : s.tier !== "dedicated",
    );
    const freeByModel = dedupAvailableByModel(inTab);
    const byModel = new Map<string, SkuMarketOut[]>();
    for (const s of inTab) {
      const list = byModel.get(s.gpu_model) ?? [];
      list.push(s);
      byModel.set(s.gpu_model, list);
    }
    const out: ModelGroup[] = [];
    for (const [model, list] of byModel) {
      const byPrice = [...list].sort((a, b) => Number(a.price_hourly) - Number(b.price_hourly));
      const representative = byPrice[0]!;
      const rentTarget = byPrice.find((s) => (s.available_count ?? 0) > 0) ?? representative;
      out.push({
        model,
        representative,
        rentTarget,
        available: freeByModel.get(model) ?? 0,
        tiers: [...new Set(list.map((s) => skuVariant(s.tier, s.pool_label)))],
      });
    }
    // 型号按代表价升序:低价在前
    return out.sort((a, b) => Number(a.representative.price_hourly) - Number(b.representative.price_hourly));
  }, [skus, tab]);

  const rent = (skuId: number) => {
    const target = `/market/create/${skuId}`;
    if (loggedIn) {
      void navigate({ to: "/market/create/$skuId", params: { skuId: String(skuId) } });
    } else {
      void navigate({ to: "/login", search: { redirect: target } });
    }
  };

  return (
    <section id="pricing" style={{ maxWidth: 1200, margin: "0 auto", padding: "48px 24px" }}>
      <Typography.Title level={2} style={{ textAlign: "center", marginBottom: 4 }}>
        {t("landing.pricing.title")}
      </Typography.Title>
      <Typography.Paragraph type="secondary" style={{ textAlign: "center", marginBottom: 24 }}>
        {t("landing.pricing.subtitle")}
      </Typography.Paragraph>
      {isError ? (
        <div style={{ textAlign: "center", padding: 32 }}>
          <Link to="/market">
            <Button type="primary" size="large">
              {t("landing.pricing.fallbackCta")}
            </Button>
          </Link>
        </div>
      ) : (
        <>
          <Tabs
            centered
            activeKey={tab}
            onChange={(k) => setTab(k as "dedicated" | "shared")}
            items={[
              { key: "dedicated", label: t("landing.pricing.tabDedicated") },
              { key: "shared", label: t("landing.pricing.tabShared") },
            ]}
          />
          {/* 自适应列数:卡片 ≥260px,行内个数随容器宽度伸缩(不写死断点列数) */}
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))",
              gap: 16,
            }}
          >
            {isLoading &&
              Array.from({ length: 4 }, (_, i) => (
                <Card key={i}>
                  <Skeleton active paragraph={{ rows: 3 }} />
                </Card>
              ))}
            {groups.map((g) => {
              const sku = g.representative;
              const spec = getGpuSpec(sku.gpu_model);
              const variant = skuVariant(sku.tier, sku.pool_label);
              const meta = metaOf(skuTierMap, variant);
              return (
                <Card
                  key={g.model}
                  hoverable
                  title={
                    <span>
                      {spec?.label ?? g.model} <TierTag tier={sku.tier} pool={sku.pool_label} />
                    </span>
                  }
                  styles={{ body: { display: "flex", flexDirection: "column", gap: 4 } }}
                >
                  {g.tiers.length > 1 && (
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      {g.tiers.map((tier) => {
                        const m = metaOf(skuTierMap, tier);
                        return m ? t(m.labelKey) : tier;
                      }).join(" / ")}
                    </Typography.Text>
                  )}
                  <Typography.Text strong>
                    {variant === "shared_mig"
                      ? t("landing.pricing.migSpec", {
                          profile: sku.mig_profile ?? t("sku.sliceFallback"),
                          vram: sku.vram_gb,
                        })
                      : variant === "shared_hami"
                        ? t("landing.pricing.sharedSpec", { pct: sku.gpu_cores_pct, vram: sku.vram_gb })
                        : t("landing.pricing.dedicatedSpec", { vram: sku.vram_gb })}
                  </Typography.Text>
                  {spec && (
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      {t("landing.pricing.tflops", { fp32: spec.fp32Tflops, fp16: spec.fp16Tflops })}
                    </Typography.Text>
                  )}
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    {t("landing.pricing.hostSpec", { vcpu: sku.vcpu, mem: sku.mem_gb, disk: sku.disk_gb })}
                  </Typography.Text>
                  <div style={{ margin: "8px 0", fontSize: 26, fontWeight: 700 }}>
                    {g.tiers.length > 1
                      ? t("landing.pricing.priceFrom", { price: formatHourlyPrice(sku.price_hourly) })
                      : formatHourlyPrice(sku.price_hourly)}
                  </div>
                  <Button
                    type="primary"
                    block
                    disabled={g.available <= 0}
                    onClick={() => rent(g.rentTarget.id)}
                  >
                    {g.available > 0 ? t("copy.stockAvailable", { count: g.available }) : t("copy.outOfStock")}
                  </Button>
                  {meta && "hintKey" in meta && (
                    <Typography.Text type="warning" style={{ fontSize: 12 }}>
                      {t(meta.hintKey)}
                    </Typography.Text>
                  )}
                </Card>
              );
            })}
          </div>
          <div style={{ textAlign: "center", marginTop: 24 }}>
            <Link to="/market">{t("landing.pricing.moreLink")} →</Link>
          </div>
        </>
      )}
    </section>
  );
}
