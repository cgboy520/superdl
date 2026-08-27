/**
 * 算力市场:GPU / CPU 分栏 + 筛选链 chips + 表格 radio 单选 + 底部结算条,数据行 = SKU。
 * CTA 即库存,售罄行灰置不隐藏。未登录可看,结算条 CTA 变「登录后租用」。
 *
 * 两栏的筛选维度不同,不是同一条链的子集:GPU 按「型号 / 档位 / 显存 / 卡数」选,
 * CPU 不带卡,只按「vCPU / 内存」选,价格也是整机时价而非单卡价。混在一栏里,
 * 型号与显存两行对 CPU 恒为空,卡数行还会算出「× 0 卡 = ¥0」。
 */

import { GPU_COUNT_STEPS, mulPrice, skuTierMap, skuVariant } from "@superdl/ui";
import type { SkuMarketOut } from "@superdl/api-client";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { Alert, Button, Card, Modal, Segmented, Space, Table, Tooltip, Typography } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useFormat } from "../lib/format";
import { dedupAvailableByModel } from "../lib/inventory";
import { TableErrorEmpty } from "../components/QueryState";
import { usePolicies, useSkus } from "../api/queries";
import { ChipRow, type ChipOption } from "../components/ChipRow";
import { CheckoutBar } from "../components/CheckoutBar";
import { BillingModeCard, skuColumns } from "../components/skuTable";
import { useIsLoggedIn } from "../stores/auth";

export const Route = createFileRoute("/_console/market")({
  component: MarketPage,
});

const ALL = "";

type Kind = "gpu" | "cpu";

function MarketPage() {
  const { t } = useTranslation(["web", "shared"]);
  const fmt = useFormat();
  const { formatHourlyPrice } = fmt;
  const navigate = useNavigate();
  const loggedIn = useIsLoggedIn();
  const [rulesOpen, setRulesOpen] = useState(false);
  const [kind, setKind] = useState<Kind>("gpu");
  const [gpuModel, setGpuModel] = useState<string>(ALL);
  const [tier, setTier] = useState<string>(ALL);
  const [vram, setVram] = useState<number>(0);
  const [gpuCount, setGpuCount] = useState(1);
  const [vcpu, setVcpu] = useState<number>(0);
  const [memGb, setMemGb] = useState<number>(0);
  const [selectedId, setSelectedId] = useState<number>();

  const {
    data: allSkus,
    isLoading,
    isError,
    refetch,
  } = useSkus({ refetchInterval: 30_000 });
  // 计费规则的冻结宽限小时数读 /policies;未就绪用无数字兜底句
  const { data: policies } = usePolicies();

  const isCpu = kind === "cpu";
  // 分栏先切分数据源:两栏的 chip 取值域各自从本栏 SKU 聚合,不会互相带出空选项
  const kindSkus = (allSkus ?? []).filter((s) => (s.tier === "cpu") === isCpu);
  const freeByModel = dedupAvailableByModel(kindSkus);

  const kindOptions = [
    { value: "gpu" as const, label: t("market.kindGpu") },
    { value: "cpu" as const, label: t("market.kindCpu") },
  ];
  const numOptions = (values: number[], unit: (v: number) => string): ChipOption<number>[] => [
    { value: 0, label: t("market.all") },
    ...Array.from(new Set(values))
      .sort((a, b) => a - b)
      .map((v) => ({ value: v, label: unit(v) })),
  ];

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
    ...Object.entries(skuTierMap)
      .filter(([value]) => value !== "cpu")
      .map(([value, meta]) => ({ value, label: t(meta.labelKey) })),
  ];
  const vramOptions = numOptions(kindSkus.map((s) => s.vram_gb), (v) => `${v} GB`);
  const vcpuOptions = numOptions(kindSkus.map((s) => s.vcpu), (v) => t("market.vcpuUnit", { count: v }));
  const memOptions = numOptions(kindSkus.map((s) => s.mem_gb), (v) => `${v} GB`);

  const skus = kindSkus.filter((s) =>
    isCpu
      ? (!vcpu || s.vcpu === vcpu) && (!memGb || s.mem_gb === memGb)
      : (!gpuModel || s.gpu_model === gpuModel) &&
        (!tier || skuVariant(s.tier, s.pool_label) === tier) &&
        (!vram || s.vram_gb === vram) &&
        s.max_gpus_per_instance >= gpuCount,
  );

  const selected = skus.find((s) => s.id === selectedId);
  // CPU 实例一台占一份库存(不带卡),GPU 实例按卡数占
  const needed = isCpu ? 1 : gpuCount;
  const rentable = (s: SkuMarketOut) => (s.available_count ?? 0) >= needed;

  const columns = skuColumns({ fmt, t, availability: true, priceFontSize: 18, cpu: isCpu });

  return (
    // 不用 Space:其 ant-space-item 包装会让 sticky 结算条的包含块只剩自身高度
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
          <Segmented<Kind>
            value={kind}
            options={kindOptions}
            onChange={(v) => {
              setKind(v);
              setSelectedId(undefined); // 换栏必须清选中:上一栏的行不在本栏表里,结算条会挂着幽灵规格
            }}
          />
          {isCpu ? (
            <>
              <ChipRow label={t("market.chipVcpu")} value={vcpu} onChange={setVcpu} options={vcpuOptions} />
              <ChipRow label={t("market.chipMem")} value={memGb} onChange={setMemGb} options={memOptions} />
            </>
          ) : (
            <>
              <ChipRow label={t("market.chipGpuModel")} value={gpuModel} onChange={setGpuModel} options={modelOptions} />
              <ChipRow label={t("market.chipTier")} value={tier} onChange={setTier} options={tierOptions} />
              <ChipRow label={t("market.chipVram")} value={vram} onChange={setVram} options={vramOptions} />
              <ChipRow
                label={t("market.chipGpuCount")}
                value={gpuCount}
                onChange={setGpuCount}
                options={GPU_COUNT_STEPS.map((n) => ({ value: n, label: String(n) }))}
              />
            </>
          )}
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
            ? isCpu
              ? t("market.summaryCpu", {
                  vcpu: selected.vcpu,
                  mem: selected.mem_gb,
                  disk: selected.disk_gb,
                })
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
          {
            label: t("create.configCostLabel"),
            // CPU 规格的 price_hourly 已是整机时价(后端计费份数恒 1),不再乘卡数
            value: selected ? formatHourlyPrice(mulPrice(selected.price_hourly, needed)) : "--",
          },
        ]}
        detail={
          selected ? (
            <Space orientation="vertical" size={4}>
              <span>
                {isCpu
                  ? t("instances.pricePerInstance", { price: formatHourlyPrice(selected.price_hourly) })
                  : t("instances.pricePerCard", {
                      price: formatHourlyPrice(selected.price_hourly),
                      count: gpuCount,
                    })}
              </span>
              <Typography.Text type="secondary">
                {isCpu ? t("copy.billingBasisCpu") : t("copy.billingBasis")}
              </Typography.Text>
            </Space>
          ) : undefined
        }
        actions={
          loggedIn ? (
            <>
              {/* 服务形态与开发机走同一条创建流,只是带上 workload=service —— 选规格这一步没有区别,
                  分成两个入口页会让用户先挑形态再挑卡,而库存约束在卡这一侧 */}
              <Tooltip title={selected ? t("market.deployServiceHint") : t("market.selectFirst")}>
                <Button
                  size="large"
                  disabled={!selected}
                  onClick={() => {
                    if (!selected) return;
                    void navigate({
                      to: "/market/create/$skuId",
                      params: { skuId: String(selected.id) },
                      search: isCpu
                        ? { workload: "service" }
                        : { gpus: gpuCount, workload: "service" },
                    });
                  }}
                >
                  {t("market.deployService")}
                </Button>
              </Tooltip>
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
                      // CPU 规格不带卡数:创建页按 SKU 的 max_gpus_per_instance=0 提交 gpu_count: 0
                      search: isCpu ? {} : { gpus: gpuCount },
                    });
                  }}
                >
                  {t("market.next")}
                </Button>
              </Tooltip>
            </>
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
          {[
            t("copy.billingRules.r1"),
            t("copy.billingRules.r2"),
            t("copy.billingRules.r3"),
            policies
              ? t("copy.billingRules.r4", { hours: policies.freeze_grace_hours })
              : t("copy.billingRules.r4Fallback"),
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
