/**
 * 算力市场:GPU / CPU 分栏 + 筛选链 chips + 表格 radio 单选 + 底部结算条,数据行 = SKU。
 * CTA 即库存,售罄行灰置不隐藏。未登录可看,结算条 CTA 变「登录后租用」。
 * 两栏筛选维度不同:GPU 按「型号 / 档位 / 显存 / 卡数」,CPU 只按「vCPU / 内存」且价格是整机时价。
 */

import {
  billingUnits,
  fontSize,
  GPU_COUNT_STEPS,
  isBillingPeriod,
  MAX_PERIOD_COUNT,
  mulPrice,
  periodMap,
  skuTierMap,
  skuVariant,
} from "@superdl/ui";
import type { SkuMarketOut } from "@superdl/api-client";
import { TableErrorEmpty } from "@superdl/ui/components";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { Alert, Button, Card, Modal, Segmented, Space, Table, Tooltip, Typography } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useFormat } from "@superdl/ui";
import { dedupAvailableByModel } from "../lib/inventory";
import { usePolicies, useSkus } from "../api/queries";
import { ChipRow, type ChipOption } from "../components/ChipRow";
import { CheckoutBar } from "../components/CheckoutBar";
import { PeriodQuoteRows, periodQuoteOf, usePeriodDiscounts } from "../components/periodBilling";
import { BillingModeCard, skuColumns, type BillingMode } from "../components/skuTable";
import { SpotOffLabel, SpotPriceInline, spotPriceOf, useSpotPolicy } from "../components/spotBilling";
import { useIsLoggedIn } from "../stores/auth";

const ALL = "";

type Kind = "gpu" | "cpu";

/** 市场页 URL 状态:10 个筛选/选中参数,默认值一律剥离(kind=gpu / mode=on_demand /
 *  chips 空档 / gpus=1 / count=1 不进 URL);非法值丢弃回默认。 */
export interface MarketSearch {
  kind?: Kind;
  mode?: BillingMode;
  model?: string;
  tier?: string;
  vram?: number;
  gpus?: number;
  vcpu?: number;
  mem?: number;
  sku?: number;
  /** 购买时长(周期份数,1~36;仅包周期模式有意义) */
  count?: number;
}

function posInt(v: unknown): number | undefined {
  const n = Number(v);
  return Number.isInteger(n) && n > 0 ? n : undefined;
}

/** 独立导出供单测往返验证(与 Route.validateSearch 同一函数)。 */
export function marketValidateSearch(search: Record<string, unknown>): MarketSearch {
  const out: MarketSearch = {};
  if (search.kind === "cpu") out.kind = "cpu"; // gpu 为默认栏,剥离
  const mode = search.mode;
  if (mode === "spot") out.mode = "spot";
  else if (typeof mode === "string" && isBillingPeriod(mode)) out.mode = mode;
  // on_demand 为默认计费方式,剥离
  if (typeof search.model === "string" && search.model !== "") out.model = search.model;
  if (
    typeof search.tier === "string" &&
    search.tier !== "" &&
    search.tier !== "cpu" &&
    search.tier in skuTierMap
  ) {
    out.tier = search.tier;
  }
  const vram = posInt(search.vram);
  if (vram != null) out.vram = vram;
  const gpus = posInt(search.gpus);
  if (gpus != null && gpus > 1) out.gpus = gpus; // 1 卡为默认,剥离
  const vcpu = posInt(search.vcpu);
  if (vcpu != null) out.vcpu = vcpu;
  const mem = posInt(search.mem);
  if (mem != null) out.mem = mem;
  const sku = posInt(search.sku);
  if (sku != null) out.sku = sku;
  const count = posInt(search.count);
  // 1 份为默认时长,剥离;超上限非法值丢弃
  if (count != null && count > 1 && count <= MAX_PERIOD_COUNT) out.count = count;
  return out;
}

export const Route = createFileRoute("/_console/market")({
  validateSearch: marketValidateSearch,
  component: MarketPage,
});

function MarketPage() {
  const { t } = useTranslation(["web", "shared"]);
  const fmt = useFormat();
  const { formatHourlyPrice } = fmt;
  const navigate = useNavigate();
  const loggedIn = useIsLoggedIn();
  const [rulesOpen, setRulesOpen] = useState(false);
  // 筛选/选中全部入 URL(replace,不产生历史垃圾):可分享、刷新/返回不丢
  const search = Route.useSearch();
  const kind: Kind = search.kind ?? "gpu";
  const billingMode: BillingMode = search.mode ?? "on_demand";
  const gpuModel = search.model ?? ALL;
  const tier = search.tier ?? ALL;
  const vram = search.vram ?? 0;
  const gpuCount = search.gpus ?? 1;
  const vcpu = search.vcpu ?? 0;
  const memGb = search.mem ?? 0;
  const selectedId = search.sku;
  // 购买时长(1~36 个周期):入 URL 并透传创建页,与创建页数量选择器同源
  const periodCount = search.count ?? 1;
  const update = (next: MarketSearch) =>
    void navigate({ to: "/market", search: next, replace: true });

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
    priceFontSize: fontSize.pageTitle,
    cpu: isCpu,
    ...(isSpot && spotPolicy ? { spot: spotPolicy } : {}),
  });
  // 市场页没有报价端点,按 policies 折扣本地估算(展示值,创建页最终报价为准)
  const quote =
    selected && period
      ? periodQuoteOf(
          selected.price_hourly,
          { units: billingUnits(isCpu ? 0 : gpuCount), period, periodCount },
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
        onChange={(v) => update({ ...search, mode: v === "on_demand" ? undefined : v })}
        periodEnabled={!periodBlocked}
        spotEnabled={!spotUnavailable}
        count={periodCount}
        onCountChange={(n) => update({ ...search, count: n > 1 ? n : undefined })}
        extra={
          <Button type="link" size="small" onClick={() => setRulesOpen(true)}>
            {t("market.billingRulesLink")}
          </Button>
        }
      />
      {periodBlocked && isBillingPeriod(billingMode) && (
        <Alert type="info" showIcon title={t("period.fallbackToHourly")} />
      )}
      {spotUnavailable && billingMode === "spot" && (
        <Alert type="info" showIcon title={t("market.spotFallbackToHourly")} />
      )}
      {/* 竞价档常驻提示:折扣是拿「可能被回收」换的,选中期间一直摆在页面上;
          宽限秒数与通知渠道从 /policies 读(与知情同意 modal 同源),策略未就绪不出这句话 */}
      {isSpot && spotPolicy && (
        <Alert
          type="warning"
          showIcon
          title={t("copy.spotReclaimNotice", { seconds: spotPolicy.graceSeconds })}
        />
      )}

      <Card title={t("market.selectSpec")} styles={{ body: { paddingBlock: 16 } }}>
        <Space orientation="vertical" size={12} style={{ width: "100%" }}>
          <Segmented<Kind>
            value={kind}
            options={kindOptions}
            onChange={(v) => {
              // 换栏必须清选中:上一栏的行不在本栏表里(其余筛选保留,回切时仍在)
              update({ ...search, kind: v === "cpu" ? "cpu" : undefined, sku: undefined });
            }}
          />
          {isCpu ? (
            <>
              <ChipRow
                label={t("market.chipVcpu")}
                value={vcpu}
                onChange={(v) => update({ ...search, vcpu: v || undefined })}
                options={vcpuOptions}
              />
              <ChipRow
                label={t("market.chipMem")}
                value={memGb}
                onChange={(v) => update({ ...search, mem: v || undefined })}
                options={memOptions}
              />
            </>
          ) : (
            <>
              <ChipRow
                label={t("market.chipGpuModel")}
                value={gpuModel}
                onChange={(v) => update({ ...search, model: v || undefined })}
                options={modelOptions}
              />
              <ChipRow
                label={t("market.chipTier")}
                value={tier}
                onChange={(v) => update({ ...search, tier: v || undefined })}
                options={tierOptions}
              />
              <ChipRow
                label={t("market.chipVram")}
                value={vram}
                onChange={(v) => update({ ...search, vram: v || undefined })}
                options={vramOptions}
              />
              <ChipRow
                label={t("market.chipGpuCount")}
                value={gpuCount}
                onChange={(v) => update({ ...search, gpus: v > 1 ? v : undefined })}
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
                <TableErrorEmpty isError onRetry={() => void refetch()} />
              ) : (
                t("market.noMatch")
              ),
            }}
            rowSelection={{
              type: "radio",
              selectedRowKeys: selected ? [selected.id] : [],
              onChange: (keys) => update({ ...search, sku: keys[0] as number | undefined }),
              getCheckboxProps: (s) => ({ disabled: !selectable(s) }),
            }}
            onRow={(s) => ({
              style: selectable(s) ? { cursor: "pointer" } : { opacity: 0.5 },
              onClick: () => {
                if (selectable(s)) update({ ...search, sku: s.id });
              },
            })}
          />
        </Space>
      </Card>

      <CheckoutBar
        // 选中规格/计费方式/时长变化时,汇总/费用数字淡入(动效只在结算条数字区)
        changeKey={selected ? `${selected.id}-${mode}-${periodCount}` : "none"}
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
                    <Typography.Text type="secondary" delete style={{ fontSize: fontSize.body }}>
                      {fmt.formatMoney(quote.listAmount)}
                    </Typography.Text>
                    <span>{fmt.formatPeriodPrice(quote.amount, period, periodCount)}</span>
                  </Space>
                ),
              }
            : {
                label: t("create.configCostLabel"),
                // CPU 规格的 price_hourly 已是整机时价(后端计费份数恒 1),不再乘卡数
                value: !selected ? (
                  "--"
                ) : isSpot ? (
                  <Space size={8} align="baseline">
                    <SpotPriceInline baseHourly={selected.price_hourly} units={needed} policy={spotPolicy} />
                    <SpotOffLabel policy={spotPolicy} />
                  </Space>
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
              <Tooltip title={selected ? t("services.deployFromMarketHint") : t("market.selectFirst")}>
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
                        ...(period && periodCount > 1 ? { count: periodCount } : {}),
                        ...(isSpot ? { market: "spot" as const } : {}),
                        workload: "service" as const,
                      },
                    });
                  }}
                >
                  {t("services.deploy")}
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
                      search: {
                        ...(isCpu ? {} : { gpus: gpuCount }),
                        ...(period ? { period } : {}),
                        ...(period && periodCount > 1 ? { count: periodCount } : {}),
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
