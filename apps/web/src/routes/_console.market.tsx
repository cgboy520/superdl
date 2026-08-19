/** 算力市场:SKU 制卡片网格。铁律 #1「CTA 即库存」。未登录可看。 */

import { copy, formatHourlyPrice, skuTierMap, tabularNums, type SkuTier } from "@superdl/ui";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { Button, Card, Col, Empty, Row, Select, Space, Tooltip, Typography } from "antd";
import { useMemo, useState } from "react";

import { useSkus } from "../api/queries";
import { TierTag } from "../components/common";
import { useIsLoggedIn } from "../stores/auth";

export const Route = createFileRoute("/_console/market")({
  component: MarketPage,
});

function MarketPage() {
  const navigate = useNavigate();
  const loggedIn = useIsLoggedIn();
  const [tier, setTier] = useState<string>();
  const [gpuModel, setGpuModel] = useState<string>();
  const [vram, setVram] = useState<number>();

  const { data: allSkus, isLoading } = useSkus({}, { refetchInterval: 30_000 });

  const gpuModels = useMemo(
    () => Array.from(new Set((allSkus ?? []).map((s) => s.gpu_model))),
    [allSkus],
  );
  const vrams = useMemo(
    () => Array.from(new Set((allSkus ?? []).map((s) => s.vram_gb))).sort((a, b) => a - b),
    [allSkus],
  );
  const skus = (allSkus ?? []).filter(
    (s) =>
      (!tier || s.tier === tier) &&
      (!gpuModel || s.gpu_model === gpuModel) &&
      (!vram || s.vram_gb === vram),
  );

  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Typography.Title level={4} style={{ margin: 0 }}>
        算力市场
      </Typography.Title>
      <Space wrap>
        <Select
          allowClear
          placeholder="GPU 型号"
          style={{ width: 160 }}
          value={gpuModel}
          onChange={setGpuModel}
          options={gpuModels.map((m) => ({ value: m, label: m }))}
        />
        <Select
          allowClear
          placeholder="档位"
          style={{ width: 180 }}
          value={tier}
          onChange={setTier}
          options={Object.entries(skuTierMap).map(([value, meta]) => ({
            value,
            label: meta.label,
          }))}
        />
        <Select
          allowClear
          placeholder="显存"
          style={{ width: 140 }}
          value={vram}
          onChange={setVram}
          options={vrams.map((v) => ({ value: v, label: `${v} GB` }))}
        />
      </Space>
      {!isLoading && skus.length === 0 ? (
        <Empty description="没有符合条件的规格,试试放宽筛选" />
      ) : (
        <Row gutter={[16, 16]}>
          {skus.map((sku) => {
            const meta = skuTierMap[sku.tier as SkuTier];
            const available = sku.available_count ?? 0;
            const shared = sku.tier.startsWith("shared");
            return (
              <Col key={sku.id} xs={24} sm={12} lg={8} xl={6}>
                <Card
                  title={
                    <Space>
                      <span>{sku.gpu_model}</span>
                      <TierTag tier={sku.tier} />
                    </Space>
                  }
                  loading={isLoading}
                >
                  <Space direction="vertical" size={4} style={{ width: "100%" }}>
                    <Typography.Text strong>
                      {shared
                        ? `${sku.gpu_cores_pct}% 算力(均值) · ${sku.vram_gb}G 显存`
                        : `整卡 · ${sku.vram_gb}G 显存`}
                    </Typography.Text>
                    <Typography.Text type="secondary">
                      {sku.vcpu} vCPU / {sku.mem_gb}G 内存 / 实例盘 {sku.disk_gb}G(含 100G)
                    </Typography.Text>
                    <Tooltip title="镜像可用的最高 CUDA 版本,取决于节点驱动">
                      <Typography.Text type="secondary">
                        CUDA ≤ {sku.cuda_max ?? "-"}
                      </Typography.Text>
                    </Tooltip>
                    <div style={{ margin: "8px 0" }}>
                      <span style={{ fontSize: 24, fontWeight: 700, ...tabularNums }}>
                        {formatHourlyPrice(sku.price_hourly)}
                      </span>
                    </div>
                    <Button
                      type="primary"
                      block
                      disabled={available <= 0}
                      onClick={() => {
                        if (!loggedIn) {
                          void navigate({ to: "/login" });
                          return;
                        }
                        void navigate({
                          to: "/market/create/$skuId",
                          params: { skuId: String(sku.id) },
                        });
                      }}
                    >
                      {available > 0 ? copy.stockAvailable(available) : copy.outOfStock}
                    </Button>
                    {meta?.hint && (
                      <Typography.Text type="warning" style={{ fontSize: 12 }}>
                        {meta.hint};详情见下单前的服务说明
                      </Typography.Text>
                    )}
                  </Space>
                </Card>
              </Col>
            );
          })}
        </Row>
      )}
    </Space>
  );
}
