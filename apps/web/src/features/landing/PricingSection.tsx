/**
 * GPU 价格墙:实时 /skus 数据(公开端点),卡片与市场页同构,CTA 即库存。
 * 接口失败整区降级为「前往算力市场」入口。
 */

import { getGpuSpec, metaOf, skuTierMap } from "@superdl/ui";
import { Link, useNavigate } from "@tanstack/react-router";
import { Button, Card, Col, Row, Skeleton, Tabs, Typography } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useFormat } from "../../lib/format";
import { useSkus } from "../../api/queries";
import { TierTag } from "../../components/common";
import { useIsLoggedIn } from "../../stores/auth";

export function PricingSection() {
  const { t } = useTranslation(["web", "shared"]);
  const { formatHourlyPrice } = useFormat();
  const navigate = useNavigate();
  const loggedIn = useIsLoggedIn();
  const [tab, setTab] = useState<"dedicated" | "shared">("dedicated");
  const { data: skus, isLoading, isError } = useSkus({}, { refetchInterval: 60_000 });

  const filtered = (skus ?? []).filter((s) =>
    tab === "dedicated" ? s.tier === "dedicated" : s.tier !== "dedicated",
  );

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
          <Row gutter={[16, 16]}>
            {isLoading &&
              Array.from({ length: 4 }, (_, i) => (
                <Col key={i} xs={24} sm={12} lg={8} xl={6}>
                  <Card>
                    <Skeleton active paragraph={{ rows: 3 }} />
                  </Card>
                </Col>
              ))}
            {filtered.map((sku) => {
              const spec = getGpuSpec(sku.gpu_model);
              const available = sku.available_count ?? 0;
              const shared = sku.tier.startsWith("shared");
              const meta = metaOf(skuTierMap, sku.tier);
              return (
                <Col key={sku.id} xs={24} sm={12} lg={8} xl={6}>
                  <Card
                    hoverable
                    title={
                      <span>
                        {sku.name} <TierTag tier={sku.tier} />
                      </span>
                    }
                    styles={{ body: { display: "flex", flexDirection: "column", gap: 4 } }}
                    style={{ height: "100%" }}
                  >
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      {spec?.label ?? sku.gpu_model}
                    </Typography.Text>
                    <Typography.Text strong>
                      {shared
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
                      {formatHourlyPrice(sku.price_hourly)}
                    </div>
                    <Button type="primary" block disabled={available <= 0} onClick={() => rent(sku.id)}>
                      {available > 0 ? t("copy.stockAvailable", { count: available }) : t("copy.outOfStock")}
                    </Button>
                    {meta && "hintKey" in meta && (
                      <Typography.Text type="warning" style={{ fontSize: 12 }}>
                        {t(meta.hintKey)}
                      </Typography.Text>
                    )}
                  </Card>
                </Col>
              );
            })}
          </Row>
          <div style={{ textAlign: "center", marginTop: 24 }}>
            <Link to="/market">{t("landing.pricing.moreLink")} →</Link>
          </div>
        </>
      )}
    </section>
  );
}
