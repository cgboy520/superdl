/**
 * 算力市场:AutoDL 式「筛选链 chips + 表格 radio 单选 + 底部结算条」,数据行 = SKU。
 * 铁律 #1 CTA 即库存 / #2 售罄行灰置不隐藏。未登录可看,结算条 CTA 变「登录后租用」。
 */

import { copy, formatHourlyPrice, mulPrice, skuTierMap } from "@superdl/ui";
import type { SkuMarketOut } from "@superdl/api-client";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { Alert, Button, Card, Modal, Space, Table, Tooltip, Typography } from "antd";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

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

  const freeByModel = useMemo(() => {
    const m = new Map<string, number>();
    for (const s of allSkus ?? []) {
      m.set(s.gpu_model, (m.get(s.gpu_model) ?? 0) + (s.available_count ?? 0));
    }
    return m;
  }, [allSkus]);

  const modelOptions: ChipOption<string>[] = [
    { value: ALL, label: "全部" },
    ...Array.from(freeByModel.entries()).map(([m, free]) => ({
      value: m,
      label: (
        <span>
          {m} <Typography.Text type="secondary">空闲 {free}</Typography.Text>
        </span>
      ),
    })),
  ];
  const tierOptions: ChipOption<string>[] = [
    { value: ALL, label: "全部" },
    ...Object.entries(skuTierMap).map(([value, meta]) => ({ value, label: t(meta.labelKey) })),
  ];
  const vramOptions: ChipOption<number>[] = [
    { value: 0, label: "全部" },
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

  const columns = skuColumns({ availability: true, priceFontSize: 18 });

  return (
    <Space orientation="vertical" size={16} style={{ width: "100%" }}>
      <Typography.Title level={4} style={{ margin: 0 }}>
        算力市场
      </Typography.Title>
      <Alert type="warning" showIcon title={copy.antiMiningNotice} />

      <BillingModeCard
        extra={<Typography.Link onClick={() => setRulesOpen(true)}>计费规则</Typography.Link>}
      />

      <Card title="选择规格" styles={{ body: { paddingBlock: 16 } }}>
        <Space orientation="vertical" size={12} style={{ width: "100%" }}>
          <ChipRow label="GPU 型号" value={gpuModel} onChange={setGpuModel} options={modelOptions} />
          <ChipRow label="档位" value={tier} onChange={setTier} options={tierOptions} />
          <ChipRow label="显存" value={vram} onChange={setVram} options={vramOptions} />
          <ChipRow
            label="GPU 数量"
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
                "没有符合条件的规格,试试放宽筛选"
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
            ? `${selected.gpu_model} × ${gpuCount} · ${selected.vcpu * gpuCount} vCPU · ${
                selected.mem_gb * gpuCount
              }G 内存 · 实例盘 ${selected.disk_gb}G`
            : "选择规格后可下一步配置实例"
        }
        items={[
          {
            label: "配置费用",
            value: selected
              ? formatHourlyPrice(mulPrice(selected.price_hourly, gpuCount))
              : "--",
          },
        ]}
        detail={
          selected ? (
            <Space orientation="vertical" size={4}>
              <span>
                {formatHourlyPrice(selected.price_hourly)} × {gpuCount} 卡
              </span>
              <Typography.Text type="secondary">{copy.eventsAreBilling}</Typography.Text>
            </Space>
          ) : undefined
        }
        actions={
          loggedIn ? (
            <Tooltip title={selected ? undefined : "请先在上方选择一个规格"}>
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
                下一步:配置实例
              </Button>
            </Tooltip>
          ) : (
            <Button
              type="primary"
              size="large"
              onClick={() => void navigate({ to: "/login", search: { redirect: "/market" } })}
            >
              登录后租用
            </Button>
          )
        }
      />

      <Modal
        open={rulesOpen}
        onCancel={() => setRulesOpen(false)}
        footer={null}
        title="计费规则"
      >
        <ul style={{ paddingInlineStart: 20, margin: 0 }}>
          {copy.billingRules.map((r) => (
            <li key={r} style={{ marginBottom: 8 }}>
              {r}
            </li>
          ))}
        </ul>
      </Modal>
    </Space>
  );
}
