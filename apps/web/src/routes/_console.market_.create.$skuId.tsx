/**
 * 创建实例:单栏卡片流 + 底部结算条;经济档需知情同意。
 * 数据盘「新建」为行内直建:提交时先建盘再建实例,建盘成功而实例失败必须提示盘已计费。
 * `?workload=service` 走服务形态:换掉镜像/SSH 两张卡,提交到 /services(部署在线服务)并跳服务详情;
 * 规格/数据盘/结算条/幂等键复用。
 */

import { isApiError, type DiskOut, type InstanceOut, type ServiceOut, type SkuMarketOut } from "@superdl/api-client";
import {
  billingUnits,
  compareAmounts,
  diskDailyEstimate,
  fontSize,
  formatDate,
  GPU_COUNT_STEPS,
  idemKeyOf,
  isBillingPeriod,
  MAX_PERIOD_COUNT,
  mulPrice,
  PERIOD_HOURS,
  periodMap,
  skuVariant,
  type BillingPeriod,
} from "@superdl/ui";
import { DataErrorAlert, useConfirm } from "@superdl/ui/components";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import {
  Alert,
  App,
  Button,
  Card,
  Cascader,
  Checkbox,
  Input,
  InputNumber,
  Radio,
  Skeleton,
  Space,
  Table,
  Tabs,
  Tooltip,
  Typography,
} from "antd";
import { useMemo, useState } from "react";

import { useFormat } from "@superdl/ui";
import { useApiErrorText } from "@superdl/ui";
import { useCreateDisk, useCreateInstance, useCreateService } from "../api/mutations";
import { useDisks, useImages, usePolicies, useSkus, useWallet } from "../api/queries";
import { ChipRow } from "../components/ChipRow";
import { CheckoutBar } from "../components/CheckoutBar";
import { ConsentModal } from "../components/ConsentModal";
import { ArgRowsEditor } from "../components/create/ArgRowsEditor";
import { DataDiskCard, defaultDiskName, type DiskMode } from "../components/create/DataDiskCard";
import { EnvRowsEditor } from "../components/create/EnvRowsEditor";
import { SshKeyPicker } from "../components/create/SshKeyPicker";
import { PeriodQuoteRows, periodQuoteOf, usePeriodDiscounts } from "../components/periodBilling";
import { BillingModeCard, skuColumns, type BillingMode } from "../components/skuTable";
import { SpotConsentModal, SpotPriceInline, spotPriceOf, useSpotPolicy } from "../components/spotBilling";
import { requireAuth } from "../lib/guard";
import {
  commandToList,
  envEntriesOf,
  envRowIssue,
  isPinnedImageRef,
  RESERVED_SERVICE_PORTS,
  type ArgRow,
  type EnvRow,
} from "../lib/serviceSpec";
import { useLeaveGuard } from "../lib/useLeaveGuard";

export const Route = createFileRoute("/_console/market_/create/$skuId")({
  validateSearch: (
    search: Record<string, unknown>,
  ): {
    gpus?: number;
    workload?: "service";
    period?: BillingPeriod;
    market?: "spot";
    count?: number;
  } => {
    // 竞价与包周期互斥,两个都带进来时必须以 period 为准
    const g = Number(search.gpus);
    const out: {
      gpus?: number;
      workload?: "service";
      period?: BillingPeriod;
      market?: "spot";
      count?: number;
    } = {};
    if (Number.isInteger(g) && g >= 1 && g <= 8) out.gpus = g;
    if (search.workload === "service") out.workload = "service";
    if (typeof search.period === "string" && isBillingPeriod(search.period)) {
      out.period = search.period;
      // 市场页购买时长选择器透传(1~36,与市场页 URL 同一口径)
      const c = Number(search.count);
      if (Number.isInteger(c) && c >= 1 && c <= MAX_PERIOD_COUNT) out.count = c;
    } else if (search.market === "spot") out.market = "spot";
    return out;
  },
  beforeLoad: requireAuth,
  component: CreatePage,
});

function CreatePage() {
  const { t } = useTranslation(["web", "shared"]);
  // 「镜像必须钉死版本」这句话的事实源在后端 messages.py,前端不另写一份
  const { t: tErr } = useTranslation("errors");
  const fmt = useFormat();
  const { formatHourlyPrice } = fmt;
  const { skuId } = Route.useParams();
  const { gpus: gpusFromMarket, workload, period: periodFromMarket, market: marketFromUrl, count: countFromMarket } =
    Route.useSearch();
  const isService = workload === "service";
  const navigate = useNavigate();
  const { message } = App.useApp();
  const confirm = useConfirm();

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
  const [platformImage, setPlatformImage] = useState<string[]>();
  const [customImage, setCustomImage] = useState("");
  const [diskMode, setDiskMode] = useState<DiskMode>("none");
  const [newDiskName, setNewDiskName] = useState(defaultDiskName);
  const [newDiskGb, setNewDiskGb] = useState(100);
  const [existingDiskId, setExistingDiskId] = useState<number>();
  const [keyIds, setKeyIds] = useState<number[]>([]);
  const [name, setName] = useState("");
  // 服务形态专属
  const [serviceImage, setServiceImage] = useState("");
  const [command, setCommand] = useState("");
  const [argRows, setArgRows] = useState<ArgRow[]>([]);
  const [envRows, setEnvRows] = useState<EnvRow[]>([]);
  const [servicePort, setServicePort] = useState<number | null>(null);
  const [healthPath, setHealthPath] = useState("");
  const [requireApiKey, setRequireApiKey] = useState(true);
  const [withSsh, setWithSsh] = useState(false);
  const [ecoOpen, setEcoOpen] = useState(false);
  const [spotOpen, setSpotOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  // 幂等键 = 本次挂载的 nonce + 参数快照:同参数重放同键;新进入本页才是新单
  const [formNonce] = useState(() => crypto.randomUUID());
  const [mountedAt] = useState(() => Date.now());
  // 「取消」脏判定的挂载快照:与初始值逐项比对,任一字段非默认即脏
  const [mountSnapshot] = useState(() => ({ gpuCount, billingMode, newDiskName }));

  // 表单脏 = 任一字段非挂载初值(服务形态下已填内容多,直接离开会整页丢)
  const formDirty =
    gpuCount !== mountSnapshot.gpuCount ||
    billingMode !== mountSnapshot.billingMode ||
    // 市场页可透传时长(countFromMarket):初值不是 1,不能一进来就判脏
    periodCount !== (countFromMarket ?? 1) ||
    imageTab !== "platform" ||
    platformImage != null ||
    customImage.trim() !== "" ||
    diskMode !== "none" ||
    newDiskName !== mountSnapshot.newDiskName ||
    newDiskGb !== 100 ||
    existingDiskId != null ||
    keyIds.length > 0 ||
    name.trim() !== "" ||
    serviceImage.trim() !== "" ||
    command.trim() !== "" ||
    argRows.some((r) => r.value.trim() !== "") ||
    envRows.some((r) => r.name.trim() !== "" || r.value.trim() !== "") ||
    servicePort != null ||
    healthPath.trim() !== "" ||
    !requireApiKey ||
    withSsh;

  const leave = useLeaveGuard(formDirty);

  const cascade = useMemo(() => {
    const tree: Record<string, Record<string, Record<string, Record<string, string>>>> = {};
    for (const img of images ?? []) {
      // CPU 向镜像(如 DataScience)的 cuda_version 不是版本号,直接原样显示,别拼成「CUDA CPU」
      const cudaLabel = /^\d/.test(img.cuda_version)
        ? `CUDA ${img.cuda_version}`
        : img.cuda_version;
      ((((tree[img.framework] ??= {})[img.framework_version] ??= {})[img.python_version] ??= {})[
        cudaLabel
      ] = img.image_ref);
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
  }, [images]);

  const pageTitle = isService ? t("services.deploy") : t("create.title");
  const errText = useApiErrorText();
  const create = useCreateInstance({
    // 错误统一在本页 doCreate 的 catch 里出(避免 NO_CAPACITY 引导与全局错误弹两条)
    silentError: true,
    onSuccess: (data) => {
      const inst = data as InstanceOut;
      message.success(t("create.creating", { name: inst.name }));
      leave.bypass();
      void navigate({ to: "/instances" });
    },
  });
  const createService = useCreateService({
    silentError: true,
    onSuccess: (svc: ServiceOut) => {
      message.success(t("services.deploying", { name: svc.name }));
      leave.bypass();
      void navigate({ to: "/services/$slug", params: { slug: svc.slug } });
    },
  });
  const createDisk = useCreateDisk();

  // 规格三态:加载中骨架 / 加载失败可重试(绝不能渲染成「已下架」) / 真不存在才提示下架
  if (skusError && !skus) {
    return (
      <Space orientation="vertical" size={16} style={{ width: "100%" }}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          {pageTitle}
        </Typography.Title>
        <DataErrorAlert onRetry={() => void refetchSkus()} />
      </Space>
    );
  }
  if (!skus) {
    return (
      <Space orientation="vertical" size={16} style={{ width: "100%" }}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          {pageTitle}
        </Typography.Title>
        <Card>
          <Skeleton active paragraph={{ rows: 6 }} loading={skusLoading} />
        </Card>
      </Space>
    );
  }
  if (!sku) {
    return (
      <Space orientation="vertical" size={16} style={{ width: "100%" }}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          {pageTitle}
        </Typography.Title>
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
      </Space>
    );
  }

  // CPU 规格:不带卡,提交 gpu_count: 0;价格是整机时价,不乘卡数
  const isCpu = sku.tier === "cpu";
  const gpus = isCpu ? 0 : gpuCount;
  const priceUnits = isCpu ? 1 : gpuCount;

  const diskPriceGbMonth = policies?.disk_price_gb_month;
  const diskGb =
    diskMode === "new"
      ? newDiskGb
      : diskMode === "existing"
        ? ((disks ?? []).find((d) => d.id === existingDiskId)?.size_gb ?? 0)
        : 0;
  // 「约 ¥X/日」为展示层估算(月价/30,BigInt 禁浮点);入账以后端日结为准
  const diskDaily = diskDailyEstimate(diskPriceGbMonth, diskGb);

  // 该规格未开包周期 / 未上竞价时按量兜底:提交体不能还带 period 或 market=spot,否则后端 400
  const periodBlocked = !sku.period_enabled;
  const spotBlocked = !sku.spot_enabled || spotPolicy == null;
  const mode: BillingMode =
    (periodBlocked && isBillingPeriod(billingMode)) || (spotBlocked && billingMode === "spot")
      ? "on_demand"
      : billingMode;
  const isSpot = mode === "spot";
  const period = isBillingPeriod(mode) ? mode : null;
  // 竞价单价 = SKU 现价 × spot_discount_pct / 100,与后端 pricing.effective_price_hourly 同算法
  const unitHourly = (isSpot ? spotPriceOf(sku.price_hourly, spotPolicy) : null) ?? sku.price_hourly;
  const hourlyTotal = mulPrice(unitHourly, priceUnits);
  // 创建页的 base 就是 SKU 现价,与后端下单用的是同一个数
  const quote = period
    ? periodQuoteOf(
        sku.price_hourly,
        { units: billingUnits(gpus), period, periodCount },
        discounts,
      )
    : undefined;
  // 「现在」在挂载时定一次(mountedAt):每次重渲染都取一遍属于渲染期副作用
  const expiresAt = period
    ? new Date(mountedAt + PERIOD_HOURS[period] * periodCount * 3_600_000).toISOString()
    : null;

  // BigInt 比较禁浮点:按量门槛 = 1 小时费用(同后端 require_balance_at_least),包周期 = 应付全额;
  // 报价未就绪必须不放行,否则会按 0 元判够。
  const needAmount = period ? quote?.amount : hourlyTotal;
  const balanceReady = wallet != null && (!period || quote != null);
  const enough =
    balanceReady && needAmount != null && compareAmounts(wallet.balance, needAmount) >= 0;

  const imageRef = isService
    ? serviceImage.trim()
    : imageTab === "platform"
      ? platformImage?.[3]
      : customImage.trim();

  const envEntries = envEntriesOf(envRows);
  const envDict = Object.fromEntries(envEntries.map((r) => [r.name, r.value]));
  const envSecretKeys = envEntries.filter((r) => r.secret).map((r) => r.name);
  const commandList = commandToList(command);
  const argList = argRows.map((r) => r.value.trim()).filter((v) => v !== "");

  /** 服务形态的提交前置条件,返回一句可读原因(挂在禁用按钮的 tooltip 上)。 */
  const serviceIssue = ((): string | null => {
    if (!isService) return null;
    if (!imageRef) return t("create.serviceNeedsImage");
    if (!isPinnedImageRef(imageRef)) return tErr("orchestrator.imageRefNotPinned");
    if (servicePort == null) return t("create.servicePortRequired");
    if (RESERVED_SERVICE_PORTS.includes(servicePort)) return t("create.servicePortReserved");
    if (healthPath.trim() !== "" && !healthPath.trim().startsWith("/")) {
      return t("create.healthPathSlash");
    }
    const bad = envRows.map((r) => envRowIssue(r, envRows)).find((e) => e != null);
    if (bad === "invalid") return t("create.envNameInvalid");
    if (bad === "reserved") return t("create.envNameReserved");
    if (bad === "duplicate") return t("create.envNameDuplicate");
    // 开了 SSH 必须至少选一把公钥(后端同款校验)
    if (withSsh && keyIds.length === 0) return t("create.serviceNeedsKey");
    return null;
  })();

  const canSubmit = isService
    ? serviceIssue == null
    : // dev 形态后端同判 pinned 硬闸(等 400 才知道太迟),前端同样拦截并即时红框
      imageRef != null && imageRef !== "" && isPinnedImageRef(imageRef) && keyIds.length > 0;

  const onCancel = () => {
    if (!formDirty) {
      void navigate({ to: "/market" });
      return;
    }
    confirm({
      title: t("create.discardConfirmTitle"),
      consequences: [t("create.discardConfirmBody")],
      okText: t("create.discardConfirmOk"),
      cancelText: t("create.discardConfirmCancel"),
      danger: true,
      onOk: () => {
        leave.bypass();
        void navigate({ to: "/market" });
      },
    });
  };

  const doCreate = async () => {
    // canSubmit 已保证有镜像(两种形态各有一条判据);这里再挡一次是把类型收窄到 string,
    // 下面拼幂等键与提交时不必再各写一次 ?? "" 兜底
    if (!imageRef) return;
    setSubmitting(true);
    // 幂等键由参数派生且失败不轮换:响应丢失后重提不会开出第二台,改了参数才是新单
    const idempotencyKey = idemKeyOf("inst", [
      formNonce,
      sku.id,
      gpus,
      // 计费方式进快照:同一台机器按量买和包月买是两张不同的单
      mode,
      period ? periodCount : null,
      imageRef,
      (isService && !withSsh ? [] : [...keyIds].sort((a, b) => a - b)).join(","),
      name || null,
      diskMode,
      existingDiskId ?? null,
      diskMode === "new" ? newDiskName.trim() : null,
      diskMode === "new" ? newDiskGb : null,
      // 服务参数也进快照:改了端口/环境变量再提交是一张新单
      isService ? "service" : "dev",
      isService ? servicePort : null,
      isService ? commandList.join(" ") : null,
      isService ? argList.join("\u0000") : null,
      isService ? JSON.stringify(envDict) : null,
      isService ? envSecretKeys.join(",") : null,
      isService ? healthPath.trim() : null,
      isService ? String(requireApiKey) : null,
      isService ? String(withSsh) : null,
    ]);
    try {
      let diskId: number | null = diskMode === "existing" ? (existingDiskId ?? null) : null;
      if (diskMode === "new") {
        let disk: DiskOut;
        try {
          disk = (await createDisk.mutateAsync({
            body: {
              name: newDiskName.trim() || defaultDiskName(),
              size_gb: newDiskGb,
            },
            // 与实例同一个参数快照派生:建盘成功但建实例失败时重提,不会再多一块盘
            idempotencyKey,
          })) as DiskOut;
        } catch {
          return; // 建盘失败:useApiMutation 已弹错误,直接终止
        }
        diskId = disk.id;
      }
      try {
        const marketBody = period
          ? { market: "subscription" as const, period, period_count: periodCount }
          : isSpot
            ? { market: "spot" as const }
            : {};
        if (isService) {
          // canSubmit 已保证端口非空(serviceIssue);这里只做类型收窄
          if (servicePort == null) return;
          await createService.mutateAsync({
            body: {
              sku_id: sku.id,
              gpu_count: gpus,
              image_ref: imageRef,
              // 不勾 SSH 就不带公钥(不开 sshd 的容器注入 authorized_keys 无意义)
              ssh_key_ids: withSsh ? keyIds : [],
              name: name || null,
              data_disk_id: diskId,
              ...marketBody,
              container_command: commandList.length > 0 ? commandList : null,
              container_args: argList.length > 0 ? argList : null,
              env: envEntries.length > 0 ? envDict : null,
              env_secret_keys: envSecretKeys.length > 0 ? envSecretKeys : null,
              service_port: servicePort,
              health_path: healthPath.trim() || null,
              require_api_key: requireApiKey,
              with_ssh: withSsh,
            },
            idempotencyKey,
          });
        } else {
          await create.mutateAsync({
            body: {
              sku_id: sku.id,
              gpu_count: gpus,
              image_ref: imageRef,
              ssh_key_ids: keyIds,
              name: name || null,
              data_disk_id: diskId,
              ...marketBody,
            },
            idempotencyKey,
          });
        }
      } catch (err) {
        // silentError 模式下提示统一在这里出:库存不足给换档引导,其余给错误原文
        if (isApiError(err) && err.code === "NO_CAPACITY") {
          message.warning(t("copy.noCapacityGuide"), 6);
        } else {
          message.error(errText(err));
        }
        if (diskMode === "new" && diskId != null) {
          message.warning(t("copy.diskCreatedButInstanceFailed"), 6);
        }
      }
    } finally {
      setSubmitting(false);
    }
  };

  /** 竞价同意之后的下一道闸:经济档 = 落 hami 池的共享(软切分超卖);mig 池是硬切分,不弹。 */
  const afterSpotConsent = () => {
    if (skuVariant(sku.tier, sku.pool_label) === "shared_hami") {
      setEcoOpen(true);
      return;
    }
    void doCreate();
  };

  // 两道知情同意串起来:竞价(可被回收)在前、经济档(性能可能波动)在后;同一台机器可能两条都占
  const submit = () => {
    if (isSpot) {
      setSpotOpen(true);
      return;
    }
    afterSpotConsent();
  };

  const gpuOptions = Array.from({ length: sku.max_gpus_per_instance }, (_, i) => i + 1).filter(
    (n) => GPU_COUNT_STEPS.includes(n) || n === sku.max_gpus_per_instance,
  );

  const columns = skuColumns({ fmt, t, cpu: isCpu });

  const submitLabel = period
    ? isService
      ? t("services.payAndDeploy")
      : t("create.payAndCreate")
    : isService
      ? t("services.deploy")
      : t("create.createAndStart");

  return (
    // 不用 Space:其 ant-space-item 包装会让 sticky 结算条的包含块只剩自身高度
    <div style={{ display: "flex", flexDirection: "column", gap: 16, width: "100%" }}>
      <Typography.Title level={4} style={{ margin: 0 }}>
        {pageTitle}
      </Typography.Title>

      <BillingModeCard
        value={mode}
        onChange={setBillingMode}
        periodEnabled={!periodBlocked}
        spotEnabled={sku.spot_enabled}
        count={periodCount}
        onCountChange={setPeriodCount}
      />
      {period && <Alert type="info" showIcon title={t("copy.periodReserved")} />}
      {periodBlocked && isBillingPeriod(billingMode) && (
        <Alert type="info" showIcon title={t("period.fallbackToHourly")} />
      )}
      {!sku.spot_enabled && billingMode === "spot" && (
        <Alert type="info" showIcon title={t("market.spotFallbackToHourly")} />
      )}
      {isSpot && spotPolicy && (
        <Alert
          type="warning"
          showIcon
          title={t("copy.spotReclaimNotice", { seconds: spotPolicy.graceSeconds })}
        />
      )}
      {/* 服务形态选竞价只警示不禁止,但被回收会断掉对外地址,这一句必须在下单前出现 */}
      {isSpot && isService && (
        <Alert type="warning" showIcon title={t("copy.spotNotForService")} />
      )}

      <Alert type="info" showIcon title={t("copy.instanceDiskLocalNotice")} />

      <Card title={t("create.selectedSpec")} extra={<Link to="/market">{t("create.changeSpec")}</Link>}>
        <Space orientation="vertical" size={12} style={{ width: "100%" }}>
          <Table<SkuMarketOut>
            size="small"
            rowKey="id"
            dataSource={[sku]}
            columns={columns}
            pagination={false}
          />
          {/* 卡数选择受 available_count 约束:无库存档位禁用 + 提示。CPU 规格不带卡,整行不出 */}
          {!isCpu && (
          <ChipRow
            label={t("market.chipGpuCount")}
            value={gpuCount}
            onChange={setGpuCount}
            options={gpuOptions.map((n) => ({
              value: n,
              label: t("market.cardsUnit", { count: n }),
              disabled: n > (sku.available_count ?? 0),
              disabledReason: t("copy.noStockForGpuCount"),
            }))}
            extra={
              gpuCount > (sku.available_count ?? 0) ? (
                <Typography.Text type="warning" style={{ fontSize: fontSize.caption }}>
                  {t("copy.noStockForGpuCount")}
                </Typography.Text>
              ) : undefined
            }
          />
          )}
        </Space>
      </Card>

      {isService ? (
        <>
          <Card title={t("create.containerCard")}>
            <Space orientation="vertical" size={12} style={{ width: "100%" }}>
              <Space orientation="vertical" size={4} style={{ width: "100%" }}>
                <Typography.Text type="secondary">{t("create.containerImageLabel")}</Typography.Text>
                <Input
                  placeholder="registry.example.com/your/image:v1.2.0"
                  aria-label={t("create.containerImageLabel")}
                  value={serviceImage}
                  onChange={(e) => setServiceImage(e.target.value)}
                  status={
                    serviceImage.trim() !== "" && !isPinnedImageRef(serviceImage.trim())
                      ? "error"
                      : undefined
                  }
                />
                <Typography.Text type="secondary">{t("copy.serviceImagePinned")}</Typography.Text>
              </Space>

              <Space orientation="vertical" size={4} style={{ width: "100%" }}>
                <Typography.Text type="secondary">{t("create.commandLabel")}</Typography.Text>
                <Input
                  placeholder={t("create.commandPlaceholder")}
                  aria-label={t("create.commandLabel")}
                  value={command}
                  onChange={(e) => setCommand(e.target.value)}
                />
                <Typography.Text type="secondary">{t("create.commandHint")}</Typography.Text>
              </Space>

              <ArgRowsEditor rows={argRows} onChange={setArgRows} />
              <EnvRowsEditor rows={envRows} onChange={setEnvRows} />
            </Space>
          </Card>

          <Card title={t("create.serviceCard")}>
            <Space orientation="vertical" size={12} style={{ width: "100%" }}>
              <Space size={24} wrap align="start">
                <Space orientation="vertical" size={4}>
                  <Typography.Text type="secondary">{t("create.servicePortLabel")}</Typography.Text>
                  <InputNumber
                    min={1}
                    max={65535}
                    style={{ width: "100%", maxWidth: 160 }}
                    placeholder="8000"
                    aria-label={t("create.servicePortLabel")}
                    status={
                      servicePort != null && RESERVED_SERVICE_PORTS.includes(servicePort)
                        ? "error"
                        : undefined
                    }
                    value={servicePort}
                    onChange={(v) => setServicePort(typeof v === "number" ? v : null)}
                  />
                </Space>
                <Space orientation="vertical" size={4}>
                  <Typography.Text type="secondary">{t("create.protocolLabel")}</Typography.Text>
                  {/* TCP / gRPC 未上线:灰置并写明,不隐藏 */}
                  <Radio.Group
                    value="http"
                    options={[
                      { value: "http", label: "HTTP" },
                      { value: "tcp", label: "TCP", disabled: true },
                      { value: "grpc", label: "gRPC", disabled: true },
                    ]}
                  />
                  <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
                    {t("create.protocolSoon")}
                  </Typography.Text>
                </Space>
              </Space>
              <Typography.Text type="secondary">{t("create.servicePortHint")}</Typography.Text>

              <Space orientation="vertical" size={4} style={{ width: "100%" }}>
                <Typography.Text type="secondary">{t("create.healthLabel")}</Typography.Text>
                <Input
                  style={{ width: "100%", maxWidth: 320 }}
                  placeholder="/healthz"
                  aria-label={t("create.healthLabel")}
                  status={
                    healthPath.trim() !== "" && !healthPath.trim().startsWith("/") ? "error" : undefined
                  }
                  value={healthPath}
                  onChange={(e) => setHealthPath(e.target.value)}
                />
                <Typography.Text type="secondary">{t("create.healthHint")}</Typography.Text>
              </Space>

              <Space orientation="vertical" size={4}>
                <Typography.Text type="secondary">{t("create.endpointLabel")}</Typography.Text>
                <Typography.Text type="secondary">{t("create.endpointPending")}</Typography.Text>
              </Space>

              <Space orientation="vertical" size={4} style={{ width: "100%" }}>
                <Typography.Text type="secondary">{t("create.authLabel")}</Typography.Text>
                <Radio.Group
                  value={requireApiKey ? "key" : "public"}
                  onChange={(e) => setRequireApiKey(e.target.value === "key")}
                  options={[
                    { value: "key", label: t("create.authRequire") },
                    { value: "public", label: t("create.authPublic") },
                  ]}
                />
                {!requireApiKey && (
                  <Typography.Text type="warning">{t("create.authPublicHint")}</Typography.Text>
                )}
                <Typography.Text type="secondary">{t("copy.serviceGatewayAuth")}</Typography.Text>
              </Space>
            </Space>
          </Card>
        </>
      ) : (
      <Card title={t("create.imageCard")}>
        <Tabs
          activeKey={imageTab}
          onChange={(k) => setImageTab(k as "platform" | "custom")}
          items={[
            {
              key: "platform",
              label: t("create.tabPlatform"),
              children: (
                <Space orientation="vertical" style={{ width: "100%" }}>
                  {imagesQ.isError ? (
                    // 镜像清单加载失败绝不伪装成「没有可用镜像」(购买路径硬停)
                    <DataErrorAlert onRetry={() => void imagesQ.refetch()} />
                  ) : (
                    <>
                      <Cascader
                        style={{ width: "100%" }}
                        options={cascade}
                        value={platformImage}
                        onChange={(v) => setPlatformImage(v as string[])}
                        placeholder={t("create.cascadePlaceholder")}
                        showSearch
                      />
                      <Typography.Text type="secondary">
                        {/* CPU 规格落无卡机,平台镜像不在那儿预热,不能对它承诺秒级启动 */}
                        {isCpu ? t("create.prewarmedNotForCpu") : t("create.prewarmed")}
                      </Typography.Text>
                    </>
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
                    onChange={(e) => setCustomImage(e.target.value)}
                    status={
                      customImage.trim() !== "" && !isPinnedImageRef(customImage.trim())
                        ? "error"
                        : undefined
                    }
                  />
                  {customImage.trim() !== "" && !isPinnedImageRef(customImage.trim()) ? (
                    <Typography.Text type="danger">
                      {tErr("orchestrator.imageRefNotPinned")}
                    </Typography.Text>
                  ) : null}
                  <Typography.Text type="secondary">
                    {t("create.customImageHint")}
                  </Typography.Text>
                </Space>
              ),
            },
          ]}
        />
      </Card>
      )}

      <DataDiskCard
        mode={diskMode}
        onModeChange={setDiskMode}
        newName={newDiskName}
        onNewNameChange={setNewDiskName}
        newGb={newDiskGb}
        onNewGbChange={setNewDiskGb}
        existingId={existingDiskId}
        onExistingIdChange={setExistingDiskId}
      />

      {!isService && (
        <Card title={t("create.sshCard")}>
          <SshKeyPicker value={keyIds} onChange={setKeyIds} />
        </Card>
      )}

      <Card title={t("create.nameCard")}>
        <Space orientation="vertical" size={12} style={{ width: "100%" }}>
          <Input
            placeholder={t("create.namePlaceholder")}
            maxLength={64}
            aria-label={t("create.nameCard")}
            value={name}
            onChange={(e) => setName(e.target.value)}
            style={{ width: "100%", maxWidth: 320 }}
          />
          {isService && (
            <>
              <Checkbox checked={withSsh} onChange={(e) => setWithSsh(e.target.checked)}>
                {t("create.withSsh")}
              </Checkbox>
              <Typography.Text type="secondary">{t("create.withSshHint")}</Typography.Text>
              {/* 勾了才要公钥:后端对 with_ssh 的实例同样要求 ssh_key_ids 非空 */}
              {withSsh && <SshKeyPicker value={keyIds} onChange={setKeyIds} />}
            </>
          )}
        </Space>
      </Card>

      {/* 余额查询失败绝不静默转圈:结算条上方给可重试错误条,CTA 改普通禁用态 */}
      {walletQ.isError && <DataErrorAlert onRetry={() => void walletQ.refetch()} />}
      <CheckoutBar
        summary={
          isCpu
            ? t("create.summaryCpu", { vcpu: sku.vcpu, mem: sku.mem_gb })
            : t("create.summary", {
                model: sku.gpu_model,
                count: gpuCount,
                vcpu: sku.vcpu * gpuCount,
                mem: sku.mem_gb * gpuCount,
              })
        }
        items={
          period && quote
            ? [
                // 包周期不出「日常费用(按量口径)」;数据盘仍按日计费,只在真挂了盘时才提这一栏
                ...(diskGb > 0 && diskPriceGbMonth
                  ? [
                      {
                        label: t("create.dailyCostLabel"),
                        hint: t("create.dailyCostHint"),
                        value: t("common.dailyApprox", { amount: diskDaily }),
                      },
                    ]
                  : []),
                {
                  label: t("create.expiresAtLabel"),
                  value: t("create.expiresAtApprox", { date: formatDate(expiresAt) }),
                },
                {
                  label: t("period.costLabel", { period: t(periodMap[period].labelKey) }),
                  value: fmt.formatPeriodPrice(quote.amount, period, periodCount),
                },
              ]
            : [
                {
                  label: t("create.dailyCostLabel"),
                  hint: t("create.dailyCostHint"),
                  value: t("common.dailyApprox", { amount: diskGb > 0 && diskPriceGbMonth ? diskDaily : "0.00" }),
                },
                {
                  label: t("create.configCostLabel"),
                  value: isSpot ? (
                    <SpotPriceInline
                      baseHourly={sku.price_hourly}
                      units={priceUnits}
                      policy={spotPolicy}
                    />
                  ) : (
                    formatHourlyPrice(hourlyTotal)
                  ),
                },
              ]
        }
        detail={
          <Space orientation="vertical" size={4} style={{ maxWidth: 360 }}>
            {period && quote ? (
              <PeriodQuoteRows quote={quote} gpuCount={gpuCount} cpu={isCpu} />
            ) : (
              <span>
                {/* 竞价档摊开的是折后单价:结算条大字与明细报两个不同的数,只会让人以为算错了 */}
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
                ? t("create.detailDiskLine", { size: diskGb, price: t("common.gbMonthPrice", { price: diskPriceGbMonth }) })
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
          <>
            <Button size="large" onClick={onCancel}>
              {t("create.cancel")}
            </Button>
            {walletQ.isError ? (
              // 余额查询失败:CTA 普通禁用态 + 原因提示(重试入口在上方错误条)
              <Tooltip title={t("create.walletQueryFailedRetry")}>
                <Button type="primary" size="large" disabled>
                  {submitLabel}
                </Button>
              </Tooltip>
            ) : !balanceReady ? (
              // 余额未就绪:主 CTA 保持 primary + loading,不出现红色文案
              <Button type="primary" size="large" loading disabled>
                {submitLabel}
              </Button>
            ) : enough ? (
              <Tooltip
                title={
                  canSubmit ? undefined : (serviceIssue ?? t("create.selectImageAndKey"))
                }
              >
                <Button
                  type="primary"
                  size="large"
                  disabled={!canSubmit}
                  loading={submitting || create.isPending || createService.isPending}
                  onClick={submit}
                >
                  {submitLabel}
                </Button>
              </Tooltip>
            ) : (
              <Link to="/billing">
                <Button type="primary" danger size="large">
                  {t("create.notEnoughGoRecharge")}
                </Button>
              </Link>
            )}
          </>
        }
      />

      <SpotConsentModal
        open={spotOpen}
        policy={spotPolicy}
        loading={submitting || create.isPending || createService.isPending}
        onCancel={() => setSpotOpen(false)}
        onConfirm={() => {
          setSpotOpen(false);
          afterSpotConsent();
        }}
      />
      <ConsentModal
        open={ecoOpen}
        title={t("create.ecoModalTitle")}
        lines={[
          t("copy.ecoTierConsent.c1"),
          t("copy.ecoTierConsent.c2"),
          t("copy.ecoTierConsent.c3"),
          t("copy.ecoTierConsent.c4"),
        ]}
        agreeLabel={t("create.ecoAgree")}
        confirmLabel={t("create.ecoConfirm")}
        loading={submitting || create.isPending || createService.isPending}
        onCancel={() => setEcoOpen(false)}
        onConfirm={() => {
          setEcoOpen(false);
          void doCreate();
        }}
      />
      {leave.modal}
    </div>
  );
}
