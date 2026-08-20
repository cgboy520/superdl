/**
 * GPU 价格墙:实时 /skus 数据(公开端点),卡片与市场页同构(铁律 #1「CTA 即库存」)。
 * 接口失败整区降级为「前往算力市场」入口。
 */

import {
  copy,
  formatHourlyPrice,
  getGpuSpec,
  marketing,
  skuTierMap,
  type SkuTier,
} from "@superdl/ui";
import { Link, useNavigate } from "@tanstack/react-router";
import { Button, Card, Col, Row, Skeleton, Tabs, Typography } from "antd";
import { useState } from "react";

import { useSkus } from "../../api/queries";
import { TierTag } from "../../components/common";
import { useIsLoggedIn } from "../../stores/auth";

export function PricingSection() {
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
        {marketing.pricing.title}
      </Typography.Title>
      <Typography.Paragraph type="secondary" style={{ textAlign: "center", marginBottom: 24 }}>
        {marketing.pricing.subtitle}
      </Typography.Paragraph>
      {isError ? (
        <div style={{ textAlign: "center", padding: 32 }}>
          <Link to="/market">
            <Button type="primary" size="large">
              {marketing.pricing.fallbackCta}
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
              { key: "dedicated", label: marketing.pricing.tabDedicated },
              { key: "shared", label: marketing.pricing.tabShared },
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
              const meta = skuTierMap[sku.tier as SkuTier];
              return (
                <Col key={sku.id} xs={24} sm={12} lg={8} xl={6}>
                  <Card
                    hoverable
                    title={
                      <span>
                        {spec?.label ?? sku.gpu_model}{" "}
                        <TierTag tier={sku.tier} />
                      </span>
                    }
                    styles={{ body: { display: "flex", flexDirection: "column", gap: 4 } }}
                    style={{ height: "100%" }}
                  >
                    <Typography.Text strong>
                      {shared
                        ? `${sku.gpu_cores_pct}% 算力(均值) · ${sku.vram_gb}G 显存`
                        : `整卡 · ${sku.vram_gb}G 显存`}
                    </Typography.Text>
                    {spec && (
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        单精 {spec.fp32Tflops} TFLOPS / 半精 {spec.fp16Tflops} Tensor TFLOPS
                      </Typography.Text>
                    )}
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      {sku.vcpu} vCPU / {sku.mem_gb}G 内存 / 实例盘 {sku.disk_gb}G
                    </Typography.Text>
                    <div style={{ margin: "8px 0", fontSize: 26, fontWeight: 700 }}>
                      {formatHourlyPrice(sku.price_hourly)}
                    </div>
                    <Button type="primary" block disabled={available <= 0} onClick={() => rent(sku.id)}>
                      {available > 0 ? copy.stockAvailable(available) : copy.outOfStock}
                    </Button>
                    {meta?.hint && (
                      <Typography.Text type="warning" style={{ fontSize: 12 }}>
                        {meta.hint}
                      </Typography.Text>
                    )}
                  </Card>
                </Col>
              );
            })}
          </Row>
          <div style={{ textAlign: "center", marginTop: 24 }}>
            <Link to="/market">{marketing.pricing.moreLink} →</Link>
          </div>
        </>
      )}
    </section>
  );
}
