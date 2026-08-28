/**
 * 算力市场:GPU / CPU 分栏 + 筛选链 chips + 表格 radio 单选 + 底部结算条,数据行 = SKU。
 * CTA 即库存,售罄行灰置不隐藏。未登录可看,结算条 CTA 变「登录后租用」。
 * 两栏筛选维度不同:GPU 按「型号 / 档位 / 显存 / 卡数」,CPU 只按「vCPU / 内存」且价格是整机时价。
 */

import {
  billingUnits,
  GPU_COUNT_STEPS,
  isBillingPeriod,
  mulPrice,
  periodMap,
  skuTierMap,
  skuVariant,
} from "@superdl/ui";
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
import { PeriodQuoteRows, periodQuoteOf, usePeriodDiscounts } from "../components/periodBilling";
import { BillingModeCard, skuColumns, type BillingMode } from "../components/skuTable";
import { SpotPriceInline, spotPriceOf, useSpotPolicy } from "../components/spotBilling";
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
  const [billingMode, setBillingMode] = useState<BillingMode>("on_demand");
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
  const discounts = usePeriodDiscounts();
  const spotPolicy = useSpotPolicy();

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

  // 选中的规格不接受包周期 / 未上竞价时按量兜底:chips 已灰置,结算条也不能还挂着一个下不了的单
  const periodBlocked = selected != null && !selected.period_enabled;
  const spotUnavailable = selected != null && !selected.spot_enabled;
  const spotBlocked = spotUnavailable || spotPolicy == null;
  const mode: BillingMode =
    (periodBlocked && isBillingPeriod(billingMode)) || (spotBlocked && billingMode === "spot")
      ? "on_demand"
      : billingMode;
  const isSpot = mode === "spot";
  const period = isBillingPeriod(mode) ? mode : null;
  // 竞价档选中时,没上竞价的规格整行灰置而不是过滤掉(与「售罄行灰置不隐藏」同一条口径)
  const selectable = (s: SkuMarketOut) => rentable(s) && (!isSpot || s.spot_enabled);
  // 明细区摊开的单价:竞价档报折后价(与结算条大字同一个数),其余报 SKU 原价
  const unitPrice =
    selected && isSpot
      ? (spotPriceOf(selected.price_hourly, spotPolicy) ?? selected.price_hourly)
      : selected?.price_hourly;

  const columns = skuColumns({
    fmt,
    t,
    availability: true,
    priceFontSize: 18,
    cpu: isCpu,
    ...(isSpot && spotPolicy ? { spot: spotPolicy } : {}),
  });
  // 市场页没有报价端点,按 policies 折扣本地估算;数量恒 1(几个周期在创建页选)
  const quote =
    selected && period
      ? periodQuoteOf(
          selected.price_hourly,
          { units: billingUnits(isCpu ? 0 : gpuCount), period, periodCount: 1 },
          discounts,
        )
      : undefined;

  return (
    // 不用 Space:其 ant-space-item 包装会让 sticky 结算条的包含块只剩自身高度
    <div style={{ display: "flex", flexDirection: "column", gap: 16, width: "100%" }}>
      <Typography.Title level={4} style={{ margin: 0 }}>
        {t("market.title")}
      </Typography.Title>
      <Alert type="warning" showIcon title={t("copy.antiMiningNotice")} />

      <BillingModeCard
        value={mode}
        onChange={setBillingMode}
        periodEnabled={!periodBlocked}
        spotEnabled={!spotUnavailable}
        extra={<Typography.Link onClick={() => setRulesOpen(true)}>{t("market.billingRulesLink")}</Typography.Link>}
      />
      {periodBlocked && isBillingPeriod(billingMode) && (
        <Alert type="info" showIcon title={t("period.fallbackToHourly")} />
      )}
      {spotUnavailable && billingMode === "spot" && (
        <Alert type="info" showIcon title={t("market.spotFallbackToHourly")} />
      )}
      {/* 竞价档常驻提示:折扣是拿「可能被回收」换的,选中期间一直摆在页面上 */}
      {isSpot && <Alert type="warning" showIcon title={t("copy.spotReclaimNotice")} />}

      <Card title={t("market.selectSpec")} styles={{ body: { paddingBlock: 16 } }}>
        <Space orientation="vertical" size={12} style={{ width: "100%" }}>
          <Segmented<Kind>
            value={kind}
            options={kindOptions}
            onChange={(v) => {
              setKind(v);
              setSelectedId(undefined); // 换栏必须清选中:上一栏的行不在本栏表里
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
              getCheckboxProps: (s) => ({ disabled: !selectable(s) }),
            }}
            onRow={(s) => ({
              style: selectable(s) ? { cursor: "pointer" } : { opacity: 0.5 },
              onClick: () => {
                if (selectable(s)) setSelectedId(s.id);
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
          period && quote
            ? {
                label: t("period.costLabel", { period: t(periodMap[period].labelKey) }),
                value: (
                  <Space size={8} align="baseline">
                    <Typography.Text type="secondary" delete style={{ fontSize: 14 }}>
                      {fmt.formatMoney(quote.listAmount)}
                    </Typography.Text>
                    <span>{fmt.formatPeriodPrice(quote.amount, period, 1)}</span>
                  </Space>
                ),
              }
            : {
                label: t("create.configCostLabel"),
                // CPU 规格的 price_hourly 已是整机时价(后端计费份数恒 1),不再乘卡数
                value: !selected ? (
                  "--"
                ) : isSpot ? (
                  <SpotPriceInline baseHourly={selected.price_hourly} units={needed} policy={spotPolicy} />
                ) : (
                  formatHourlyPrice(mulPrice(selected.price_hourly, needed))
                ),
              },
        ]}
        detail={
          selected ? (
            period && quote ? (
              <PeriodQuoteRows
                quote={quote}
                gpuCount={gpuCount}
                cpu={isCpu}
                hint={t("period.hintFinalOnCreate")}
              />
            ) : (
              <Space orientation="vertical" size={4}>
                <span>
                  {/* 竞价档摊开的是折后时价:结算条大字与明细报两个不同的数,只会让人以为算错了 */}
                  {isCpu
                    ? t("instances.pricePerInstance", { price: formatHourlyPrice(unitPrice) })
                    : t("instances.pricePerCard", {
                        price: formatHourlyPrice(unitPrice),
                        count: gpuCount,
                      })}
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
          loggedIn ? (
            <>
              {/* 服务形态与开发机走同一条创建流,只是带上 workload=service */}
              <Tooltip title={selected ? t("market.deployServiceHint") : t("market.selectFirst")}>
                <Button
                  size="large"
                  disabled={!selected}
                  onClick={() => {
                    if (!selected) return;
                    void navigate({
                      to: "/market/create/$skuId",
                      params: { skuId: String(selected.id) },
                      search: {
                        ...(isCpu ? {} : { gpus: gpuCount }),
                        ...(period ? { period } : {}),
                        ...(isSpot ? { market: "spot" as const } : {}),
                        workload: "service" as const,
                      },
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
                      // CPU 规格不带卡数(创建页按 max_gpus_per_instance=0 提交 gpu_count: 0);计费方式随选择带过去
                      search: {
                        ...(isCpu ? {} : { gpus: gpuCount }),
                        ...(period ? { period } : {}),
                        ...(isSpot ? { market: "spot" as const } : {}),
                      },
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
