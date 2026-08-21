/**
 * 算力市场:筛选链 chips + 表格 radio 单选 + 底部结算条,数据行 = SKU。
 * CTA 即库存,售罄行灰置不隐藏。未登录可看,结算条 CTA 变「登录后租用」。
 */

import { mulPrice, skuTierMap } from "@superdl/ui";
import type { SkuMarketOut } from "@superdl/api-client";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { Alert, Button, Card, Modal, Space, Table, Tooltip, Typography } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useFormat } from "../lib/format";
import { TableErrorEmpty } from "../components/QueryState";
import { useSkus } from "../api/queries";
import { ChipRow, type ChipOption } from "../components/ChipRow";
import { CheckoutBar } from "../components/CheckoutBar";
import { BillingModeCard, skuColumns } from "../components/skuTable";
import { useIsLoggedIn } from "../stores/auth";

export const Route = createFileRoute("/_console/market")({
  component: MarketPage,
});

const ALL = "";
const GPU_COUNTS = [1, 2, 4, 8];

function MarketPage() {
  const { t } = useTranslation(["web", "shared"]);
  const fmt = useFormat();
  const { formatHourlyPrice } = fmt;
  const navigate = useNavigate();
  const loggedIn = useIsLoggedIn();
  const [rulesOpen, setRulesOpen] = useState(false);
  const [gpuModel, setGpuModel] = useState<string>(ALL);
  const [tier, setTier] = useState<string>(ALL);
  const [vram, setVram] = useState<number>(0);
  const [gpuCount, setGpuCount] = useState(1);
  const [selectedId, setSelectedId] = useState<number>();

  const {
    data: allSkus,
    isLoading,
    isError,
    refetch,
  } = useSkus({}, { refetchInterval: 30_000 });

  const freeByModel = new Map<string, number>();
  for (const s of allSkus ?? []) {
    freeByModel.set(s.gpu_model, (freeByModel.get(s.gpu_model) ?? 0) + (s.available_count ?? 0));
  }

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
    ...Object.entries(skuTierMap).map(([value, meta]) => ({ value, label: t(meta.labelKey) })),
  ];
  const vramOptions: ChipOption<number>[] = [
    { value: 0, label: t("market.all") },
    ...Array.from(new Set((allSkus ?? []).map((s) => s.vram_gb)))
      .sort((a, b) => a - b)
      .map((v) => ({ value: v, label: `${v} GB` })),
  ];

  const skus = (allSkus ?? []).filter(
    (s) =>
      (!gpuModel || s.gpu_model === gpuModel) &&
      (!tier || s.tier === tier) &&
      (!vram || s.vram_gb === vram) &&
      s.max_gpus_per_instance >= gpuCount,
  );

  const selected = skus.find((s) => s.id === selectedId);
  const rentable = (s: SkuMarketOut) => (s.available_count ?? 0) >= gpuCount;

  const columns = skuColumns({ fmt, t, availability: true, priceFontSize: 18 });

  return (
    // 刻意不用 Space:Space 会给每个子项包一层等高的 ant-space-item,
    // 底部 sticky 结算条的包含块只有自身高度 → 粘滞行程为 0(等于没粘)
    <div style={{ display: "flex", flexDirection: "column", gap: 16, width: "100%" }}>
      <Typography.Title level={4} style={{ margin: 0 }}>
        {t("market.title")}
      </Typography.Title>
      <Alert type="warning" showIcon title={t("copy.antiMiningNotice")} />

      <BillingModeCard
        extra={<Typography.Link onClick={() => setRulesOpen(true)}>{t("market.billingRulesLink")}</Typography.Link>}
      />

      <Card title={t("market.selectSpec")} styles={{ body: { paddingBlock: 16 } }}>
        <Space orientation="vertical" size={12} style={{ width: "100%" }}>
          <ChipRow label={t("market.chipGpuModel")} value={gpuModel} onChange={setGpuModel} options={modelOptions} />
          <ChipRow label={t("market.chipTier")} value={tier} onChange={setTier} options={tierOptions} />
          <ChipRow label={t("market.chipVram")} value={vram} onChange={setVram} options={vramOptions} />
          <ChipRow
            label={t("market.chipGpuCount")}
            value={gpuCount}
            onChange={setGpuCount}
            options={GPU_COUNTS.map((n) => ({ value: n, label: String(n) }))}
          />
          <Table<SkuMarketOut>
            size="middle"
            rowKey="id"
            loading={isLoading}
            scroll={{ x: 880 }}
            dataSource={skus}
            columns={columns}
            pagination={false}
            locale={{
              emptyText: isError ? (
                <TableErrorEmpty onRetry={() => void refetch()} />
              ) : (
                t("market.noMatch")
              ),
            }}
            rowSelection={{
              type: "radio",
              selectedRowKeys: selected ? [selected.id] : [],
              onChange: (keys) => setSelectedId(keys[0] as number),
              getCheckboxProps: (s) => ({ disabled: !rentable(s) }),
            }}
            onRow={(s) => ({
              style: rentable(s) ? { cursor: "pointer" } : { opacity: 0.5 },
              onClick: () => {
                if (rentable(s)) setSelectedId(s.id);
              },
            })}
          />
        </Space>
      </Card>

      <CheckoutBar
        summary={
          selected
            ? t("market.summary", {
                model: selected.gpu_model,
                count: gpuCount,
                vcpu: selected.vcpu * gpuCount,
                mem: selected.mem_gb * gpuCount,
                disk: selected.disk_gb,
              })
            : t("market.selectHint")
        }
        items={[
          {
            label: t("create.configCostLabel"),
            value: selected
              ? formatHourlyPrice(mulPrice(selected.price_hourly, gpuCount))
              : "--",
          },
        ]}
        detail={
          selected ? (
            <Space orientation="vertical" size={4}>
              <span>
                {t("instances.pricePerCard", { price: formatHourlyPrice(selected.price_hourly), count: gpuCount })}
              </span>
              <Typography.Text type="secondary">{t("copy.eventsAreBilling")}</Typography.Text>
            </Space>
          ) : undefined
        }
        actions={
          loggedIn ? (
            <Tooltip title={selected ? undefined : t("market.selectFirst")}>
              <Button
                type="primary"
                size="large"
                disabled={!selected}
                onClick={() => {
                  if (!selected) return;
                  void navigate({
                    to: "/market/create/$skuId",
                    params: { skuId: String(selected.id) },
                    search: { gpus: gpuCount },
                  });
                }}
              >
                {t("market.next")}
              </Button>
            </Tooltip>
          ) : (
            <Button
              type="primary"
              size="large"
              onClick={() => void navigate({ to: "/login", search: { redirect: "/market" } })}
            >
              {t("market.loginToRent")}
            </Button>
          )
        }
      />

      <Modal
        open={rulesOpen}
        onCancel={() => setRulesOpen(false)}
        footer={null}
        title={t("market.billingRulesLink")}
      >
        <ul style={{ paddingInlineStart: 20, margin: 0 }}>
          {[t("copy.billingRules.r1"), t("copy.billingRules.r2"), t("copy.billingRules.r3"), t("copy.billingRules.r4")].map((r) => (
            <li key={r} style={{ marginBottom: 8 }}>
              {r}
            </li>
          ))}
        </ul>
      </Modal>
    </div>
  );
}
