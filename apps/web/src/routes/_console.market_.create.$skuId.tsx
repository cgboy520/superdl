/** 创建实例(开发机):SectionRail 分段长表单(基本信息 → 计费方式 → 镜像 → 数据盘 → SSH 密钥)+ 底部结算条。
 *  一键创建:镜像默认推荐项、单把公钥自动选中、必填卡标红星、未完成项在结算条上方给可点击清单(不靠禁用按钮的 tooltip)。
 *  段状态由 deriveSectionStatus 派生(首屏不出红叉:未触碰且没点过提交的问题段只标 wait)。
 *  知情同意合并为一个分节 modal(ConsentGate);数据盘「新建」为行内直建:先建盘再建实例,建盘成功而实例失败用不自动消失的 Alert 告知并给存储页入口。
 *  返回市场的两个出口(页头 back / 「更换规格」)都带回 listSearchStore 记下的市场筛选态。部署在线服务走 /services/new。 */

import { isApiError, type DiskOut, type InstanceOut, type SkuMarketOut } from "@superdl/api-client";
import {
  billingUnits,
  compareAmounts,
  controlWidth,
  diskDailyEstimate,
  fontSize,
  formatDate,
  GPU_COUNT_STEPS,
  idemKeyOf,
  isBillingPeriod,
  mulPrice,
  PERIOD_HOURS,
  periodMap,
  skuVariant,
  space,
} from "@superdl/ui";
import {
  ChipRow,
  CopyField,
  DataErrorAlert,
  GatedButton,
  OptionTileGroup,
  PageContainer,
  scrollToSection,
  SectionAnchor,
  SectionRail,
  type SectionDef,
} from "@superdl/ui/components";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { Alert, App, Button, Card, Cascader, Input, Skeleton, Space, Tabs, Typography } from "antd";
import { useMemo, useState, type ReactNode } from "react";

import { useFormat } from "@superdl/ui";
import { useApiErrorText } from "@superdl/ui";
import { useCreateDisk, useCreateInstance } from "../api/mutations";
import { useDisks, useImages, usePolicies, useSkus, useWallet } from "../api/queries";
import { CheckoutBar } from "../components/CheckoutBar";
import { useConsentGate } from "../components/ConsentGate";
import { DataDiskCard, defaultDiskName, type DiskMode } from "../components/create/DataDiskCard";
import { SshKeyPicker } from "../components/create/SshKeyPicker";
import { PeriodQuoteRows, periodQuoteOf, usePeriodDiscounts } from "../components/periodBilling";
import { BillingModeCard, type BillingMode } from "../components/skuTable";
import { SpotPriceInline, spotPriceOf, useSpotPolicy } from "../components/spotBilling";
import { parseDeployDeepLink, type DeploySearch } from "../lib/deployLink";
import { requireAuth } from "../lib/guard";
import { isPinnedImageRef } from "../lib/serviceSpec";
import { useLeaveGuard } from "../lib/useLeaveGuard";
import { useRememberedListSearch } from "../stores/listSearch";
import { TierTag } from "../components/common";
import type { MarketSearch } from "./_console.market";

export const Route = createFileRoute("/_console/market_/create/$skuId")({
  // 深链解析与 /services/new 共用;sku 在路径参数里,查询串的 sku_id 无意义
  validateSearch: (search: Record<string, unknown>): Omit<DeploySearch, "sku_id"> => {
    const parsed = parseDeployDeepLink(search);
    delete parsed.sku_id;
    return parsed;
  },
  beforeLoad: requireAuth,
  component: CreatePage,
});

/** 卡片锚点 id(未完成项清单跳转) */
const ANCHOR = {
  basic: "card-basic",
  billing: "card-billing",
  image: "card-image",
  disk: "card-disk",
  ssh: "card-ssh",
} as const;

/** 必填卡标题:红星 + 标题 */
function RequiredTitle({ children }: { children: ReactNode }) {
  return (
    <span>
      <span aria-hidden style={{ color: "var(--sdl-color-required)", marginInlineEnd: 4 }}>
        *
      </span>
      {children}
    </span>
  );
}

const RECOMMENDED_FRAMEWORK_ORDER = ["PyTorch", "TensorFlow", "Paddle", "Miniconda", "DataScience"];

function CreatePage() {
  const { t } = useTranslation(["web", "shared"]);
  // 「镜像必须钉死版本」文案事实源在后端 messages.py
  const { t: tErr } = useTranslation("errors");
  const fmt = useFormat();
  const { formatHourlyPrice } = fmt;
  const { skuId } = Route.useParams();
  const {
    gpus: gpusFromMarket,
    period: periodFromMarket,
    market: marketFromUrl,
    count: countFromMarket,
  } = Route.useSearch();
  const navigate = useNavigate();
  const { message } = App.useApp();

  const { data: skus, isLoading: skusLoading, isError: skusError, refetch: refetchSkus } = useSkus();
  const sku = (skus ?? []).find((s) => s.id === Number(skuId));

  const imagesQ = useImages();
  const { data: images } = imagesQ;
  const { data: disks } = useDisks();
  const walletQ = useWallet();
  const { data: wallet } = walletQ;
  const { data: policies } = usePolicies();
  const discounts = usePeriodDiscounts();
  const spotPolicy = useSpotPolicy();

  const [gpuCount, setGpuCount] = useState(gpusFromMarket ?? 1);
  const [billingMode, setBillingMode] = useState<BillingMode>(
    periodFromMarket ?? (marketFromUrl === "spot" ? "spot" : "on_demand"),
  );
  const [periodCount, setPeriodCount] = useState(countFromMarket ?? 1);
  const [imageTab, setImageTab] = useState<"platform" | "custom">("platform");
  // 平台镜像:常用框架卡片 + 「更多」级联;两者同写 platformImage(image_ref 数组路径)
  const [platformImage, setPlatformImage] = useState<string[]>();
  const [imageTouched, setImageTouched] = useState(false);
  const [moreImages, setMoreImages] = useState(false);
  const [customImage, setCustomImage] = useState("");
  const [diskMode, setDiskMode] = useState<DiskMode>("none");
  const [newDiskName, setNewDiskName] = useState(defaultDiskName);
  const [newDiskGb, setNewDiskGb] = useState<number>();
  const [existingDiskId, setExistingDiskId] = useState<number>();
  const [keyIds, setKeyIds] = useState<number[]>([]);
  const [name, setName] = useState("");
  const [phase, setPhase] = useState<"disk" | "instance" | null>(null);
  // 建盘成功而建实例失败:页内常驻告知,直到用户处理
  const [diskCreatedButFailed, setDiskCreatedButFailed] = useState<DiskOut | null>(null);
  // 空闲 GPU 不足:结算条上方常驻 Alert + 「换个规格」,不用 toast(ui-ux-spec §3.5)
  const [noCapacity, setNoCapacity] = useState(false);
  // 段状态:碰过的段(改字段 / 点进段内)+ 点过主 CTA;首屏两者皆空,问题段只标 wait 不出红叉
  const [touchedIds, setTouchedIds] = useState<ReadonlySet<string>>(() => new Set());
  const [submitted, setSubmitted] = useState(false);
  const touch = (id: string) => setTouchedIds((prev) => (prev.has(id) ? prev : new Set(prev).add(id)));
  // 幂等键 = 本次挂载的 nonce + 参数快照
  const [formNonce] = useState(() => crypto.randomUUID());
  const [mountedAt] = useState(() => Date.now());
  // 「取消」脏判定的挂载快照
  const [mountSnapshot] = useState(() => ({ gpuCount, billingMode, newDiskName }));

  const isCpu = sku?.tier === "cpu";
  // 盘容量初值取策略下限(不写死 100)
  const diskGbValue = newDiskGb ?? policies?.disk_min_gb ?? 100;

  // 镜像清单:CPU 规格只给不带 CUDA 的镜像,GPU 规格只给带 CUDA 的
  const usableImages = useMemo(
    () => (images ?? []).filter((img) => (/^\d/.test(img.cuda_version) ? !isCpu : isCpu)),
    [images, isCpu],
  );
  const cascade = useMemo(() => {
    const tree: Record<string, Record<string, Record<string, Record<string, string>>>> = {};
    for (const img of usableImages) {
      // CPU 向镜像的 cuda_version 不是版本号,原样显示
      const cudaLabel = /^\d/.test(img.cuda_version) ? `CUDA ${img.cuda_version}` : img.cuda_version;
      (((tree[img.framework] ??= {})[img.framework_version] ??= {})[img.python_version] ??= {})[cudaLabel] =
        img.image_ref;
    }
    return Object.entries(tree).map(([fw, versions]) => ({
      value: fw,
      label: fw,
      children: Object.entries(versions).map(([ver, pys]) => ({
        value: ver,
        label: ver,
        children: Object.entries(pys).map(([py, cudas]) => ({
          value: py,
          label: `Python ${py}`,
          children: Object.entries(cudas).map(([cuda, ref]) => ({ value: ref, label: cuda })),
        })),
      })),
    }));
  }, [usableImages]);
  /** 常用镜像卡片:每个框架取清单里的第一条(后端按框架 / 版本排好序),最多 4 个 */
  const quickImages = useMemo(() => {
    const byFramework = new Map<string, (typeof usableImages)[number]>();
    for (const img of usableImages) if (!byFramework.has(img.framework)) byFramework.set(img.framework, img);
    return Array.from(byFramework.values())
      .sort((a, b) => {
        const ia = RECOMMENDED_FRAMEWORK_ORDER.indexOf(a.framework);
        const ib = RECOMMENDED_FRAMEWORK_ORDER.indexOf(b.framework);
        return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
      })
      .slice(0, 4);
  }, [usableImages]);
  const pathOf = (img: (typeof usableImages)[number]): string[] => [
    img.framework,
    img.framework_version,
    img.python_version,
    img.image_ref,
  ];
  // 默认镜像 = 推荐框架的第一条(用户未动过镜像时);渲染期派生,不用 effect 回写 state
  const defaultImage = quickImages[0];
  const effectivePlatformImage = platformImage ?? (!imageTouched && defaultImage ? pathOf(defaultImage) : undefined);

  // 表单脏 = 任一字段非挂载初值
  const formDirty =
    gpuCount !== mountSnapshot.gpuCount ||
    billingMode !== mountSnapshot.billingMode ||
    // 市场页透传时长时初值不是 1
    periodCount !== (countFromMarket ?? 1) ||
    imageTab !== "platform" ||
    imageTouched ||
    customImage.trim() !== "" ||
    diskMode !== "none" ||
    newDiskName !== mountSnapshot.newDiskName ||
    newDiskGb != null ||
    existingDiskId != null ||
    name.trim() !== "";

  const leave = useLeaveGuard(formDirty);

  const pageTitle = t("create.title");
  const errText = useApiErrorText();
  const create = useCreateInstance({
    // 错误统一在 doCreate 的 catch 里出
    silentError: true,
    onSuccess: (data) => {
      const inst = data as InstanceOut;
      message.success(t("create.creating", { name: inst.name }));
      leave.bypass();
      // 成功直接落到这台实例的「连接」Tab,不丢回列表自己找
      void navigate({ to: "/instances/$uuid", params: { uuid: inst.uuid }, search: { tab: "access" } });
    },
  });
  const createDisk = useCreateDisk();

  const gpus = isCpu ? 0 : gpuCount;
  const priceUnits = isCpu ? 1 : gpuCount;

  const diskPriceGbMonth = policies?.disk_price_gb_month;
  const diskGb =
    diskMode === "new"
      ? diskGbValue
      : diskMode === "existing"
        ? ((disks ?? []).find((d) => d.id === existingDiskId)?.size_gb ?? 0)
        : 0;
  // 「约 ¥X/日」为展示层估算(月价/30,BigInt);入账以后端日结为准;单价未就绪不估算(不显假 0.00)
  const diskDaily = diskPriceGbMonth === undefined ? undefined : diskDailyEstimate(diskPriceGbMonth, diskGb);

  // 未开包周期 / 未上竞价时按量兜底,提交体不带 period 或 market=spot
  const periodBlocked = sku != null && !sku.period_enabled;
  const spotBlocked = (sku != null && !sku.spot_enabled) || spotPolicy == null;
  const mode: BillingMode =
    (periodBlocked && isBillingPeriod(billingMode)) || (spotBlocked && billingMode === "spot")
      ? "on_demand"
      : billingMode;
  const isSpot = mode === "spot";
  const period = isBillingPeriod(mode) ? mode : null;
  // 竞价单价 = SKU 现价 × spot_discount_pct / 100,与后端 pricing.effective_price_hourly 同算法
  const basePrice = sku?.price_hourly ?? "0";
  const unitHourly = (isSpot ? spotPriceOf(basePrice, spotPolicy) : null) ?? basePrice;
  const hourlyTotal = mulPrice(unitHourly, priceUnits);
  // base = SKU 现价,与后端下单同一个数
  const quote =
    period && sku
      ? periodQuoteOf(sku.price_hourly, { units: billingUnits(gpus), period, periodCount }, discounts)
      : undefined;
  // 「现在」在挂载时定一次(mountedAt)
  const expiresAt = period ? new Date(mountedAt + PERIOD_HOURS[period] * periodCount * 3_600_000).toISOString() : null;

  // BigInt 比较:按量门槛 = 1 小时费用(同后端 require_balance_at_least),包周期 = 应付全额;报价未就绪不放行
  const needAmount = period ? quote?.amount : hourlyTotal;
  const balanceReady = wallet != null && (!period || quote != null);
  const enough = balanceReady && needAmount != null && compareAmounts(wallet.balance, needAmount) >= 0;

  const imageRef = imageTab === "platform" ? effectivePlatformImage?.[3] : customImage.trim();
  const selectedImage = usableImages.find((i) => i.image_ref === imageRef);
  const customInvalid = imageTab === "custom" && customImage.trim() !== "" && !isPinnedImageRef(customImage.trim());

  // 每段的第一个问题(rail 段状态与未完成项清单同一事实源)
  const imageIssue = !imageRef
    ? t("create.issueImage")
    : !isPinnedImageRef(imageRef)
      ? tErr("orchestrator.imageRefNotPinned")
      : null;
  const sshIssue = keyIds.length === 0 ? t("create.issueSsh") : null;
  const diskIssue = diskMode === "existing" && existingDiskId == null ? t("create.issueDisk") : null;

  // 未完成项清单(结算条上方,可点击跳到对应段)
  const issues: { key: string; label: string; anchor: string }[] = [];
  if (imageIssue) issues.push({ key: "image", label: imageIssue, anchor: ANCHOR.image });
  if (sshIssue) issues.push({ key: "ssh", label: sshIssue, anchor: ANCHOR.ssh });
  if (diskIssue) issues.push({ key: "disk", label: diskIssue, anchor: ANCHOR.disk });
  const canSubmit = issues.length === 0;

  const sections: SectionDef[] = [
    { id: ANCHOR.basic, title: t("create.basicCard"), issue: null, touched: touchedIds.has(ANCHOR.basic) },
    { id: ANCHOR.billing, title: t("sku.billingModeTitle"), issue: null, touched: touchedIds.has(ANCHOR.billing) },
    { id: ANCHOR.image, title: t("create.imageCard"), issue: imageIssue, touched: touchedIds.has(ANCHOR.image) },
    { id: ANCHOR.disk, title: t("create.diskCard"), issue: diskIssue, touched: touchedIds.has(ANCHOR.disk) },
    { id: ANCHOR.ssh, title: t("create.sshCard"), issue: sshIssue, touched: touchedIds.has(ANCHOR.ssh) },
  ];

  // 返回市场的唯一出口(页头 back 与「更换规格」同一个):带回市场页记下的筛选态,脏表单先确认
  const marketSearch = useRememberedListSearch("/market") as MarketSearch;
  const goMarket = () =>
    leave.confirmLeave(() => {
      leave.bypass();
      void navigate({ to: "/market", search: marketSearch });
    });
  const back = { label: t("create.backToMarket"), onClick: goMarket };

  const doCreate = async () => {
    if (!imageRef || !sku) return;
    // 幂等键由参数派生且失败不轮换
    const idempotencyKey = idemKeyOf("inst", [
      formNonce,
      sku.id,
      gpus,
      // 计费方式进快照
      mode,
      period ? periodCount : null,
      imageRef,
      [...keyIds].sort((a, b) => a - b).join(","),
      name || null,
      diskMode,
      existingDiskId ?? null,
      diskMode === "new" ? newDiskName.trim() : null,
      diskMode === "new" ? diskGbValue : null,
    ]);
    try {
      let diskId: number | null = diskMode === "existing" ? (existingDiskId ?? null) : null;
      let createdDisk: DiskOut | null = diskCreatedButFailed;
      if (diskMode === "new" && !createdDisk) {
        setPhase("disk");
        try {
          createdDisk = await createDisk.mutateAsync({
            body: { name: newDiskName.trim() || defaultDiskName(), size_gb: diskGbValue },
            // 与实例同一个参数快照派生
            idempotencyKey,
          });
        } catch {
          return; // 建盘失败,错误已由 useApiMutation 弹出
        }
      }
      if (createdDisk) diskId = createdDisk.id;
      setPhase("instance");
      try {
        await create.mutateAsync({
          body: {
            sku_id: sku.id,
            gpu_count: gpus,
            image_ref: imageRef,
            ssh_key_ids: keyIds,
            name: name || null,
            data_disk_id: diskId,
            ...(period
              ? { market: "subscription" as const, period, period_count: periodCount }
              : isSpot
                ? { market: "spot" as const }
                : {}),
          },
          idempotencyKey,
        });
        setDiskCreatedButFailed(null);
        setNoCapacity(false);
      } catch (err) {
        // silentError 模式下提示统一在这里出:库存不足给常驻 Alert + 换规格入口(toast 会自己消失,用户回头就找不到原因了)
        if (isApiError(err) && err.code === "NO_CAPACITY") {
          setNoCapacity(true);
        } else {
          message.error(errText(err));
        }
        // 建盘成功而实例失败:常驻告知(不用 toast),再点提交会复用这块盘不再重建
        if (diskMode === "new" && createdDisk) setDiskCreatedButFailed(createdDisk);
      }
    } finally {
      setPhase(null);
    }
  };

  const submitLabel = period ? t("create.payAndCreate") : t("create.createAndStart");
  const pending = phase !== null || create.isPending;
  const gate = useConsentGate({
    spot: isSpot,
    eco: sku != null && skuVariant(sku.tier, sku.pool_label) === "shared_hami",
    spotPolicy,
    confirmLabel: t("create.consentConfirm"),
    loading: pending,
    onProceed: () => doCreate(),
  });

  // 规格三态:加载中骨架 / 加载失败可重试 / 真不存在才提示下架
  if (skusError && !skus) {
    return (
      <PageContainer title={pageTitle} back={back}>
        <DataErrorAlert onRetry={() => void refetchSkus()} />
      </PageContainer>
    );
  }
  if (!skus) {
    return (
      <PageContainer title={pageTitle} back={back}>
        <Card>
          <Skeleton active paragraph={{ rows: 6 }} loading={skusLoading} />
        </Card>
      </PageContainer>
    );
  }
  if (!sku) {
    return (
      <PageContainer title={pageTitle} back={back}>
        <Alert
          type="warning"
          showIcon
          title={t("create.skuMissing")}
          action={
            <Link to="/market">
              <Button size="small">{t("instances.goMarket")}</Button>
            </Link>
          }
        />
      </PageContainer>
    );
  }

  // 三态守卫已早返回,此处 sku 必非空
  const skuNN: SkuMarketOut = sku;
  const maxGpus = skuNN.max_gpus_per_instance;
  const gpuOptions = Array.from({ length: maxGpus }, (_, i) => i + 1).filter(
    (n) => GPU_COUNT_STEPS.includes(n) || n === maxGpus,
  );

  const pickQuick = (img: (typeof usableImages)[number]) => {
    setImageTouched(true);
    touch(ANCHOR.image);
    setImageTab("platform");
    setPlatformImage(pathOf(img));
  };

  return (
    <PageContainer title={pageTitle} back={back}>
      {/* 不用 Space(ant-space-item 包装会破坏 sticky 结算条的包含块) */}
      <div style={{ display: "flex", flexDirection: "column", gap: space.lg, width: "100%" }}>
        <SectionRail sections={sections} submitted={submitted} ariaLabel={t("create.railAria")}>
          {/* ① 基本信息:已选规格一行摘要 + 名称 + GPU 数量 */}
          <div onFocusCapture={() => touch(ANCHOR.basic)} onClickCapture={() => touch(ANCHOR.basic)}>
            <SectionAnchor
              id={ANCHOR.basic}
              title={t("create.basicCard")}
              extra={
                <Button type="link" style={{ paddingInline: 0 }} onClick={goMarket}>
                  {t("create.changeSpec")}
                </Button>
              }
            >
              <Space orientation="vertical" size={space.md} style={{ width: "100%" }}>
                {/* 规格回显是一行摘要,不是表格:本页不再做规格比价 */}
                <Space size={space.sm} align="center" wrap>
                  <Typography.Text>
                    {isCpu
                      ? t("market.summaryCpu", { vcpu: skuNN.vcpu, mem: skuNN.mem_gb, disk: skuNN.disk_gb })
                      : t("market.summary", {
                          model: skuNN.gpu_model,
                          count: gpuCount,
                          vcpu: skuNN.vcpu * gpuCount,
                          mem: skuNN.mem_gb * gpuCount,
                          disk: skuNN.disk_gb,
                        })}
                  </Typography.Text>
                  <TierTag tier={skuNN.tier} pool={skuNN.pool_label} />
                  <Typography.Text strong>{formatHourlyPrice(skuNN.price_hourly)}</Typography.Text>
                </Space>
                <Space size={space.md} align="center" wrap>
                  <Typography.Text type="secondary">{t("create.nameLabel")}</Typography.Text>
                  <Input
                    placeholder={t("create.namePlaceholder")}
                    maxLength={64}
                    aria-label={t("create.nameLabel")}
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    style={{ width: controlWidth.lg }}
                  />
                </Space>
                {/* 卡数选择受 available_count 约束:无库存档位禁用 + 提示。CPU 规格不带卡,整行不出 */}
                {!isCpu && (
                  <ChipRow
                    label={t("market.chipGpuCount")}
                    value={gpuCount}
                    onChange={(n) => {
                      setGpuCount(n);
                      // 换了卡数 = 换了库存诉求,上一次的「空闲不足」结论作废
                      setNoCapacity(false);
                    }}
                    options={gpuOptions.map((n) => ({
                      value: n,
                      label: t("market.cardsUnit", { count: n }),
                      disabled: n > (skuNN.available_count ?? 0),
                      disabledReason: t("copy.noStockForGpuCount"),
                    }))}
                  />
                )}
              </Space>
            </SectionAnchor>
          </div>

          {/* ② 计费方式(风险摘要贴在 chip 下方,由 BillingModeCard 出) */}
          <div onFocusCapture={() => touch(ANCHOR.billing)} onClickCapture={() => touch(ANCHOR.billing)}>
            <SectionAnchor id={ANCHOR.billing} card={false}>
              <BillingModeCard
                value={mode}
                onChange={setBillingMode}
                periodEnabled={!periodBlocked}
                spotEnabled={skuNN.spot_enabled}
                count={periodCount}
                onCountChange={setPeriodCount}
              />
            </SectionAnchor>
          </div>

          {/* ③ 镜像(必填):常用卡片 + 更多级联 / 自定义 */}
          <div onFocusCapture={() => touch(ANCHOR.image)} onClickCapture={() => touch(ANCHOR.image)}>
            <SectionAnchor id={ANCHOR.image} title={<RequiredTitle>{t("create.imageCard")}</RequiredTitle>}>
              <Tabs
                activeKey={imageTab}
                onChange={(k) => {
                  // 切 Tab 清另一侧的选择,当前生效的镜像只有一个来源
                  setImageTab(k as "platform" | "custom");
                  setImageTouched(true);
                  touch(ANCHOR.image);
                  if (k === "custom") setPlatformImage(undefined);
                  else setCustomImage("");
                }}
                items={[
                  {
                    key: "platform",
                    label: t("create.tabPlatform"),
                    children: imagesQ.isError ? (
                      // 镜像清单加载失败不伪装成「没有可用镜像」
                      <DataErrorAlert onRetry={() => void imagesQ.refetch()} />
                    ) : (
                      <Space orientation="vertical" size={space.md} style={{ width: "100%" }}>
                        {/* 常用镜像 tile:每框架首条,副行 CUDA · Py · 预热状态;CPU 规格不承诺预热 */}
                        <OptionTileGroup
                          label={t("create.tabPlatform")}
                          hideLabel
                          columns={4}
                          size="sm"
                          value={effectivePlatformImage?.[3]}
                          onChange={(ref) => {
                            const img = quickImages.find((i) => i.image_ref === ref);
                            if (img) pickQuick(img);
                          }}
                          options={quickImages.map((img) => ({
                            value: img.image_ref,
                            title: `${img.framework} ${img.framework_version}`,
                            description: [
                              /^\d/.test(img.cuda_version) ? `CUDA ${img.cuda_version}` : img.cuda_version,
                              `Py ${img.python_version}`,
                              ...(isCpu
                                ? []
                                : [img.is_prewarmed ? t("create.tilePrewarmed") : t("create.tileNotPrewarmed")]),
                            ].join(" · "),
                          }))}
                        />
                        <Button type="link" style={{ paddingInline: 0 }} onClick={() => setMoreImages((v) => !v)}>
                          {moreImages ? t("create.lessImages") : t("create.moreImages")}
                        </Button>
                        {moreImages && (
                          <Cascader
                            style={{ width: "100%", maxWidth: 640 }}
                            options={cascade}
                            value={effectivePlatformImage}
                            onChange={(v) => {
                              setImageTouched(true);
                              touch(ANCHOR.image);
                              setPlatformImage(v);
                            }}
                            placeholder={t("create.cascadePlaceholder")}
                            showSearch
                          />
                        )}
                        {/* 选定后回显完整镜像地址与预热状态,便于核对 */}
                        {selectedImage && (
                          <Space size={space.sm} wrap>
                            <CopyField value={selectedImage.image_ref} code />
                            <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
                              {isCpu
                                ? t("create.prewarmedNotForCpu")
                                : selectedImage.is_prewarmed
                                  ? t("create.prewarmed")
                                  : t("create.notPrewarmed")}
                            </Typography.Text>
                          </Space>
                        )}
                      </Space>
                    ),
                  },
                  {
                    key: "custom",
                    label: t("create.tabCustom"),
                    children: (
                      <Space orientation="vertical" style={{ width: "100%" }}>
                        <Input
                          placeholder="registry.example.com/your/image:tag"
                          aria-label={t("create.tabCustom")}
                          value={customImage}
                          onChange={(e) => {
                            touch(ANCHOR.image);
                            setCustomImage(e.target.value);
                          }}
                          status={customInvalid ? "error" : undefined}
                          className="mono"
                        />
                        {customInvalid && (
                          <Typography.Text type="danger">{tErr("orchestrator.imageRefNotPinned")}</Typography.Text>
                        )}
                        <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
                          {t("create.customImageHint")}
                        </Typography.Text>
                      </Space>
                    ),
                  },
                ]}
              />
            </SectionAnchor>
          </div>

          {/* ④ 数据盘(可选) */}
          <div onFocusCapture={() => touch(ANCHOR.disk)} onClickCapture={() => touch(ANCHOR.disk)}>
            <SectionAnchor id={ANCHOR.disk} card={false}>
              <DataDiskCard
                mode={diskMode}
                onModeChange={setDiskMode}
                newName={newDiskName}
                onNewNameChange={setNewDiskName}
                newGb={diskGbValue}
                onNewGbChange={setNewDiskGb}
                existingId={existingDiskId}
                onExistingIdChange={setExistingDiskId}
              />
            </SectionAnchor>
          </div>

          {/* ⑤ SSH 密钥(必填) */}
          <div onFocusCapture={() => touch(ANCHOR.ssh)} onClickCapture={() => touch(ANCHOR.ssh)}>
            <SectionAnchor id={ANCHOR.ssh} title={<RequiredTitle>{t("create.sshCard")}</RequiredTitle>}>
              <SshKeyPicker value={keyIds} onChange={setKeyIds} />
            </SectionAnchor>
          </div>
        </SectionRail>

        {/* 余额查询失败绝不静默转圈:结算条上方给可重试错误条,CTA 改普通禁用态 */}
        {walletQ.isError && <DataErrorAlert onRetry={() => void walletQ.refetch()} />}
        <CheckoutBar
          notice={
            <>
              {diskCreatedButFailed && (
                <Alert
                  type="warning"
                  showIcon
                  title={t("copy.diskCreatedButInstanceFailed")}
                  description={t("create.diskCreatedReuse", { name: diskCreatedButFailed.name })}
                  action={
                    <Link to="/storage">
                      <Button size="small">{t("create.goStorage")}</Button>
                    </Link>
                  }
                />
              )}
              {/* 空闲 GPU 不足:常驻在条上方并给换规格出口,不用会自己消失的 toast */}
              {noCapacity && (
                <Alert
                  type="warning"
                  showIcon
                  title={t("copy.noCapacityGuide")}
                  action={
                    <Button size="small" onClick={goMarket}>
                      {t("create.changeSpecCta")}
                    </Button>
                  }
                />
              )}
              {issues.length > 0 && (
                <Space size={space.sm} wrap style={{ fontSize: fontSize.caption }}>
                  <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
                    {t("create.issuesTitle", { count: issues.length })}
                  </Typography.Text>
                  {issues.map((i) => (
                    <Button
                      key={i.key}
                      type="link"
                      size="small"
                      style={{ paddingInline: 0, fontSize: fontSize.caption }}
                      onClick={() => scrollToSection(i.anchor)}
                    >
                      {i.label}
                    </Button>
                  ))}
                </Space>
              )}
            </>
          }
          {...(issues.length > 0 ? { noticeSummary: t("create.issuesShort", { count: issues.length }) } : {})}
          summary={
            isCpu
              ? t("create.summaryCpu", { vcpu: skuNN.vcpu, mem: skuNN.mem_gb })
              : t("create.summary", {
                  model: skuNN.gpu_model,
                  count: gpuCount,
                  vcpu: skuNN.vcpu * gpuCount,
                  mem: skuNN.mem_gb * gpuCount,
                })
          }
          items={
            period && quote
              ? [
                  // 包周期:主数字 = 周期费用;数据盘一栏只在真挂了盘时出;到期时间降级为正文
                  {
                    label: t("period.costLabel", { period: t(periodMap[period].labelKey) }),
                    value: fmt.formatPeriodPrice(quote.amount, period, periodCount),
                  },
                  ...(diskGb > 0 && diskDaily !== undefined
                    ? [
                        {
                          label: t("create.diskCostLabel"),
                          hint: t("create.dailyCostHint"),
                          value: t("common.dailyApprox", { amount: diskDaily }),
                        },
                      ]
                    : []),
                  {
                    label: t("create.expiresAtLabel"),
                    value: t("create.expiresAtApprox", { date: formatDate(expiresAt) }),
                    muted: true,
                  },
                ]
              : [
                  {
                    label: t("create.configCostLabel"),
                    suffix: isCpu ? t("sku.wholeMachine") : t("sku.timesCards", { count: gpuCount }),
                    value: isSpot ? (
                      <SpotPriceInline baseHourly={skuNN.price_hourly} units={priceUnits} policy={spotPolicy} />
                    ) : (
                      formatHourlyPrice(hourlyTotal)
                    ),
                  },
                  ...(diskGb > 0 && diskDaily !== undefined
                    ? [
                        {
                          label: t("create.diskCostLabel"),
                          hint: t("create.dailyCostHint"),
                          value: t("common.dailyApprox", { amount: diskDaily }),
                        },
                      ]
                    : []),
                ]
          }
          breakdown={period && quote ? <PeriodQuoteRows quote={quote} gpuCount={gpuCount} cpu={isCpu} /> : undefined}
          detail={
            <Space orientation="vertical" size={4} style={{ maxWidth: 360 }}>
              {period && quote ? (
                <PeriodQuoteRows quote={quote} gpuCount={gpuCount} cpu={isCpu} />
              ) : (
                <span>
                  {/* 竞价档摊开折后单价,与结算条大字同数 */}
                  {isCpu
                    ? t("create.detailInstanceLineCpu", { total: formatHourlyPrice(hourlyTotal) })
                    : t("create.detailInstanceLine", {
                        unit: formatHourlyPrice(unitHourly),
                        count: gpuCount,
                        total: formatHourlyPrice(hourlyTotal),
                      })}
                </span>
              )}
              <span>
                {diskGb > 0 && diskPriceGbMonth
                  ? t("create.detailDiskLine", {
                      size: diskGb,
                      price: t("common.gbMonthPrice", { price: diskPriceGbMonth }),
                    })
                  : t("create.detailDiskNone")}
              </span>
              <Typography.Text type="secondary">
                {period ? t("create.balanceNeedNotePeriod") : t("create.balanceNeedNote")}
              </Typography.Text>
            </Space>
          }
          balance={wallet?.balance ?? null}
          balanceReady={balanceReady}
          actions={
            // 点过主 CTA 后所有问题段都标红(首屏不标);「取消」不放结算条,页头返回是唯一出口
            <span onClickCapture={() => setSubmitted(true)}>
              {walletQ.isError ? (
                // 余额查询失败:CTA 门控并提示原因
                <GatedButton type="primary" size="large" reason={t("create.walletQueryFailedRetry")}>
                  {submitLabel}
                </GatedButton>
              ) : !balanceReady ? (
                // 余额未就绪:主 CTA 保持 primary + loading
                <Button type="primary" size="large" loading disabled>
                  {submitLabel}
                </Button>
              ) : enough ? (
                <Button type="primary" size="large" disabled={!canSubmit} loading={pending} onClick={gate.submit}>
                  {phase === "disk"
                    ? t("create.phaseDisk")
                    : phase === "instance"
                      ? t("create.phaseInstance")
                      : submitLabel}
                </Button>
              ) : (
                <Link to="/billing">
                  <Button type="primary" danger size="large">
                    {t("create.notEnoughGoRecharge")}
                  </Button>
                </Link>
              )}
            </span>
          }
        />
        {gate.modal}
        {leave.modal}
      </div>
    </PageContainer>
  );
}
