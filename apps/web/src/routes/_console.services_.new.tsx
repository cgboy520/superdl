/** 部署服务:分段单页(① 基本信息 → ② 容器配置 → ③ 服务配置 → ④ 高级配置)+ 左侧步骤锚点 + 底部结算条。不用 antd Form,全部受控 state + 派生 issue。数据盘「新建」先建盘再部署,建盘成功而部署失败须提示盘已计费。 */

import { isApiError, type DiskOut, type SkuMarketOut } from "@superdl/api-client";
import {
  billingUnits,
  compareAmounts,
  diskDailyEstimate,
  formatDate,
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
import {
  Alert,
  App,
  Button,
  Card,
  Checkbox,
  Col,
  Descriptions,
  Grid,
  Input,
  Row,
  Space,
  Steps,
  Tooltip,
  Typography,
} from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useFormat } from "@superdl/ui";
import { useApiErrorText } from "@superdl/ui";
import { useCreateDisk, useCreateService } from "../api/mutations";
import { useDisks, usePolicies, useSkus, useWallet } from "../api/queries";
import { CheckoutBar } from "../components/CheckoutBar";
import { ConsentModal } from "../components/ConsentModal";
import { DataDiskCard, defaultDiskName, type DiskMode } from "../components/create/DataDiskCard";
import { SkuPicker } from "../components/create/SkuPicker";
import { SshKeyPicker } from "../components/create/SshKeyPicker";
import { PeriodQuoteRows, periodQuoteOf, usePeriodDiscounts } from "../components/periodBilling";
import { ContainerFields } from "../components/services/ContainerCard";
import { PublicAccessFields } from "../components/services/PublicAccessCard";
import { BillingModeCard, type BillingMode } from "../components/skuTable";
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

export interface DeploySearch {
  sku_id?: number;
  gpus?: number;
  period?: BillingPeriod;
  market?: "spot";
  count?: number;
}

/** 深链预填:规格 / 卡数 / 计费方式;竞价与包周期互斥,以 period 为准。 */
export function deployValidateSearch(search: Record<string, unknown>): DeploySearch {
  const out: DeploySearch = {};
  const sku = Number(search.sku_id);
  if (Number.isInteger(sku) && sku > 0) out.sku_id = sku;
  const g = Number(search.gpus);
  if (Number.isInteger(g) && g >= 1 && g <= 8) out.gpus = g;
  if (typeof search.period === "string" && isBillingPeriod(search.period)) {
    out.period = search.period;
    const c = Number(search.count);
    if (Number.isInteger(c) && c >= 1 && c <= MAX_PERIOD_COUNT) out.count = c;
  } else if (search.market === "spot") out.market = "spot";
  return out;
}

export const Route = createFileRoute("/_console/services_/new")({
  validateSearch: deployValidateSearch,
  beforeLoad: requireAuth,
  component: DeployPage,
});

const SECTION_IDS = ["deploy-basics", "deploy-container", "deploy-access", "deploy-advanced"] as const;

function DeployPage() {
  const { t } = useTranslation(["web", "shared"]);
  const { t: tErr } = useTranslation("errors");
  const fmt = useFormat();
  const { formatHourlyPrice } = fmt;
  const {
    sku_id: skuFromUrl,
    gpus: gpusFromUrl,
    period: periodFromUrl,
    market: marketFromUrl,
    count: countFromUrl,
  } = Route.useSearch();
  const navigate = useNavigate();
  const { message } = App.useApp();
  const confirm = useConfirm();
  const wide = Grid.useBreakpoint().md;

  const skusQ = useSkus({ refetchInterval: 30_000 });
  const { data: skus } = skusQ;
  const { data: disks } = useDisks();
  const walletQ = useWallet();
  const { data: wallet } = walletQ;
  const { data: policies } = usePolicies();
  const discounts = usePeriodDiscounts();
  const spotPolicy = useSpotPolicy();

  // ① 基本信息
  const [name, setName] = useState("");
  const [skuId, setSkuId] = useState<number | undefined>(skuFromUrl);
  const [gpuCount, setGpuCount] = useState(gpusFromUrl ?? 1);
  const [billingMode, setBillingMode] = useState<BillingMode>(
    periodFromUrl ?? (marketFromUrl === "spot" ? "spot" : "on_demand"),
  );
  const [periodCount, setPeriodCount] = useState(countFromUrl ?? 1);
  // ② 容器配置
  const [image, setImage] = useState("");
  const [command, setCommand] = useState("");
  const [argRows, setArgRows] = useState<ArgRow[]>([]);
  const [envRows, setEnvRows] = useState<EnvRow[]>([]);
  const [diskMode, setDiskMode] = useState<DiskMode>("none");
  const [newDiskName, setNewDiskName] = useState(defaultDiskName);
  const [newDiskGb, setNewDiskGb] = useState(100);
  const [existingDiskId, setExistingDiskId] = useState<number>();
  // ③ 服务配置
  const [servicePort, setServicePort] = useState<number | null>(null);
  const [healthPath, setHealthPath] = useState("");
  const [requireApiKey, setRequireApiKey] = useState(true);
  // ④ 高级配置
  const [withSsh, setWithSsh] = useState(false);
  const [keyIds, setKeyIds] = useState<number[]>([]);
  const [ecoOpen, setEcoOpen] = useState(false);
  const [spotOpen, setSpotOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  // 幂等键 = 本次挂载的 nonce + 参数快照
  const [formNonce] = useState(() => crypto.randomUUID());
  const [mountedAt] = useState(() => Date.now());
  const [mountSnapshot] = useState(() => ({ skuId, gpuCount, billingMode, newDiskName }));

  const formDirty =
    name.trim() !== "" ||
    skuId !== mountSnapshot.skuId ||
    gpuCount !== mountSnapshot.gpuCount ||
    billingMode !== mountSnapshot.billingMode ||
    periodCount !== (countFromUrl ?? 1) ||
    image.trim() !== "" ||
    command.trim() !== "" ||
    argRows.some((r) => r.value.trim() !== "") ||
    envRows.some((r) => r.name.trim() !== "" || r.value.trim() !== "") ||
    diskMode !== "none" ||
    newDiskName !== mountSnapshot.newDiskName ||
    newDiskGb !== 100 ||
    existingDiskId != null ||
    servicePort != null ||
    healthPath.trim() !== "" ||
    !requireApiKey ||
    withSsh ||
    keyIds.length > 0;
  const leave = useLeaveGuard(formDirty);

  const errText = useApiErrorText();
  const createService = useCreateService({
    // 错误统一在 doCreate 的 catch 里出
    silentError: true,
    onSuccess: (svc) => {
      message.success(t("services.deploying", { name: svc.name }));
      leave.bypass();
      void navigate({ to: "/services/$slug", params: { slug: svc.slug } });
    },
  });
  const createDisk = useCreateDisk();

  const sku: SkuMarketOut | undefined = (skus ?? []).find((s) => s.id === skuId);
  const isCpu = sku?.tier === "cpu";
  const gpus = isCpu ? 0 : gpuCount;
  const priceUnits = isCpu ? 1 : gpuCount;
  const periodBlocked = sku != null && !sku.period_enabled;
  const spotBlocked = sku == null || !sku.spot_enabled || spotPolicy == null;
  const mode: BillingMode =
    (periodBlocked && isBillingPeriod(billingMode)) || (spotBlocked && billingMode === "spot")
      ? "on_demand"
      : billingMode;
  const isSpot = mode === "spot";
  const period = isBillingPeriod(mode) ? mode : null;
  const unitHourly = sku
    ? ((isSpot ? spotPriceOf(sku.price_hourly, spotPolicy) : null) ?? sku.price_hourly)
    : null;
  const hourlyTotal = unitHourly ? mulPrice(unitHourly, priceUnits) : null;
  const quote =
    sku && period
      ? periodQuoteOf(sku.price_hourly, { units: billingUnits(gpus), period, periodCount }, discounts)
      : undefined;
  const expiresAt = period
    ? new Date(mountedAt + PERIOD_HOURS[period] * periodCount * 3_600_000).toISOString()
    : null;
  const diskPriceGbMonth = policies?.disk_price_gb_month;
  const diskGb =
    diskMode === "new"
      ? newDiskGb
      : diskMode === "existing"
        ? ((disks ?? []).find((d) => d.id === existingDiskId)?.size_gb ?? 0)
        : 0;
  const diskDaily = diskDailyEstimate(diskPriceGbMonth, diskGb);
  // BigInt 比较:按量门槛 = 1 小时费用,包周期 = 应付全额;报价未就绪不放行
  const needAmount = period ? quote?.amount : hourlyTotal;
  const balanceReady = wallet != null && (!period || quote != null);
  const enough =
    balanceReady && needAmount != null && compareAmounts(wallet.balance, needAmount) >= 0;

  const imageRef = image.trim();
  const envEntries = envEntriesOf(envRows);
  const envDict = Object.fromEntries(envEntries.map((r) => [r.name, r.value]));
  const envSecretKeys = envEntries.filter((r) => r.secret).map((r) => r.name);
  const commandList = commandToList(command);
  const argList = argRows.map((r) => r.value.trim()).filter((v) => v !== "");

  /** 每段的第一个问题(禁用主按钮的 tooltip 与左侧步骤条用);顺序即填写顺序。 */
  const sectionIssues: (string | null)[] = [
    sku ? null : t("services.form.specNeeded"),
    (() => {
      if (!imageRef) return t("services.form.needsImage");
      if (!isPinnedImageRef(imageRef)) return tErr("orchestrator.imageRefNotPinned");
      const bad = envRows.map((r) => envRowIssue(r, envRows)).find((e) => e != null);
      if (bad === "invalid") return t("services.form.envNameInvalid");
      if (bad === "reserved") return t("services.form.envNameReserved");
      if (bad === "duplicate") return t("services.form.envNameDuplicate");
      return null;
    })(),
    (() => {
      if (servicePort == null) return t("services.form.portRequired");
      if (RESERVED_SERVICE_PORTS.includes(servicePort)) return t("services.form.portReserved");
      if (healthPath.trim() !== "" && !healthPath.trim().startsWith("/")) {
        return t("services.form.healthPathSlash");
      }
      return null;
    })(),
    withSsh && keyIds.length === 0 ? t("services.form.needsKey") : null,
  ];
  const firstIssueIndex = sectionIssues.findIndex((i) => i != null);
  const firstIssue = firstIssueIndex >= 0 ? sectionIssues[firstIssueIndex] : null;
  const canSubmit = firstIssue == null;

  const scrollTo = (i: number) =>
    document
      .getElementById(SECTION_IDS[i] ?? "")
      ?.scrollIntoView({ behavior: "smooth", block: "start" });

  const onCancel = () => {
    if (!formDirty) {
      void navigate({ to: "/services" });
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
        void navigate({ to: "/services" });
      },
    });
  };

  const doCreate = async () => {
    if (!sku || !imageRef || servicePort == null) return;
    setSubmitting(true);
    // 幂等键由参数派生且失败不轮换
    const idempotencyKey = idemKeyOf("svc", [
      formNonce,
      sku.id,
      gpus,
      mode,
      period ? periodCount : null,
      imageRef,
      (withSsh ? [...keyIds].sort((a, b) => a - b) : []).join(","),
      name.trim() || null,
      diskMode,
      existingDiskId ?? null,
      diskMode === "new" ? newDiskName.trim() : null,
      diskMode === "new" ? newDiskGb : null,
      servicePort,
      commandList.join(" "),
      argList.join(" "),
      JSON.stringify(envDict),
      envSecretKeys.join(","),
      healthPath.trim(),
      String(requireApiKey),
      String(withSsh),
    ]);
    try {
      let diskId: number | null = diskMode === "existing" ? (existingDiskId ?? null) : null;
      if (diskMode === "new") {
        let disk: DiskOut;
        try {
          disk = (await createDisk.mutateAsync({
            body: { name: newDiskName.trim() || defaultDiskName(), size_gb: newDiskGb },
            // 与服务同一个参数快照派生
            idempotencyKey,
          })) as DiskOut;
        } catch {
          return; // 建盘失败,错误已由 useApiMutation 弹出
        }
        diskId = disk.id;
      }
      try {
        await createService.mutateAsync({
          body: {
            sku_id: sku.id,
            gpu_count: gpus,
            image_ref: imageRef,
            ssh_key_ids: withSsh ? keyIds : [],
            name: name.trim() || null,
            data_disk_id: diskId,
            ...(period
              ? { market: "subscription" as const, period, period_count: periodCount }
              : isSpot
                ? { market: "spot" as const }
                : {}),
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
      } catch (err) {
        if (isApiError(err) && err.code === "NO_CAPACITY") {
          message.warning(t("copy.noCapacityGuide"), 6);
        } else {
          message.error(errText(err));
        }
        if (diskMode === "new" && diskId != null) {
          message.warning(t("services.form.diskCreatedButDeployFailed"), 6);
        }
      }
    } finally {
      setSubmitting(false);
    }
  };

  /** 竞价同意之后的下一道闸:经济档 = hami 池共享(软切分超卖);mig 池不弹。 */
  const afterSpotConsent = () => {
    if (sku && skuVariant(sku.tier, sku.pool_label) === "shared_hami") {
      setEcoOpen(true);
      return;
    }
    void doCreate();
  };
  const submit = () => {
    if (isSpot) {
      setSpotOpen(true);
      return;
    }
    afterSpotConsent();
  };

  const submitLabel = period ? t("services.payAndDeploy") : t("services.deploy");
  const pending = submitting || createService.isPending;

  const steps = (
    <Steps
      orientation="vertical"
      size="small"
      current={firstIssueIndex >= 0 ? firstIssueIndex : SECTION_IDS.length - 1}
      onChange={scrollTo}
      items={[
        t("services.form.section1"),
        t("services.form.section2"),
        t("services.form.section3"),
        t("services.form.section4"),
      ].map((title, i) => ({
        title,
        status:
          firstIssueIndex < 0 || i < firstIssueIndex
            ? "finish"
            : i === firstIssueIndex
              ? "process"
              : "wait",
      }))}
    />
  );

  const sections = (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {/* ① 基本信息:名称 + 算力规格 + 计费方式 */}
      <Card id={SECTION_IDS[0]} title={t("services.form.section1")}>
        <Space orientation="vertical" size={16} style={{ width: "100%" }}>
          <Space orientation="vertical" size={4} style={{ width: "100%" }}>
            <Typography.Text type="secondary">{t("services.form.nameLabel")}</Typography.Text>
            <Input
              placeholder={t("services.form.namePlaceholder")}
              maxLength={64}
              aria-label={t("services.form.nameLabel")}
              value={name}
              onChange={(e) => setName(e.target.value)}
              style={{ width: "100%", maxWidth: 320 }}
            />
          </Space>
          <Space orientation="vertical" size={8} style={{ width: "100%" }}>
            <Typography.Text type="secondary">{t("services.form.specLabel")}</Typography.Text>
            <SkuPicker
              skus={skus}
              isLoading={skusQ.isLoading}
              isError={skusQ.isError}
              onRetry={() => void skusQ.refetch()}
              value={sku}
              onChange={(s) => setSkuId(s?.id)}
              gpuCount={gpuCount}
              onGpuCount={setGpuCount}
              {...(isSpot && spotPolicy ? { spot: spotPolicy } : {})}
            />
          </Space>
          <BillingModeCard
            value={mode}
            onChange={setBillingMode}
            periodEnabled={!periodBlocked}
            spotEnabled={sku?.spot_enabled ?? true}
            count={periodCount}
            onCountChange={setPeriodCount}
          />
          {period && <Alert type="info" showIcon title={t("copy.periodReserved")} />}
          {periodBlocked && isBillingPeriod(billingMode) && (
            <Alert type="info" showIcon title={t("period.fallbackToHourly")} />
          )}
          {sku && !sku.spot_enabled && billingMode === "spot" && (
            <Alert type="info" showIcon title={t("market.spotFallbackToHourly")} />
          )}
          {/* 竞价只警示不禁止;回收会断对外地址,下单前必须出现 */}
          {isSpot && <Alert type="warning" showIcon title={t("copy.spotNotForService")} />}
        </Space>
      </Card>

      {/* ② 容器配置 */}
      <Card id={SECTION_IDS[1]} title={t("services.form.section2")}>
        <ContainerFields
          image={image}
          onImage={setImage}
          command={command}
          onCommand={setCommand}
          argRows={argRows}
          onArgRows={setArgRows}
          envRows={envRows}
          onEnvRows={setEnvRows}
        />
      </Card>
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

      {/* ③ 服务配置 */}
      <Card id={SECTION_IDS[2]} title={t("services.form.section3")}>
        <PublicAccessFields
          port={servicePort}
          onPort={setServicePort}
          healthPath={healthPath}
          onHealthPath={setHealthPath}
          requireApiKey={requireApiKey}
          onRequireApiKey={setRequireApiKey}
        />
      </Card>

      {/* ④ 高级配置:调试 SSH + 更新策略说明 + 配置摘要 */}
      <Card id={SECTION_IDS[3]} title={t("services.form.section4")}>
        <Space orientation="vertical" size={16} style={{ width: "100%" }}>
          <Space orientation="vertical" size={8} style={{ width: "100%" }}>
            <Checkbox checked={withSsh} onChange={(e) => setWithSsh(e.target.checked)}>
              {t("services.form.withSsh")}
            </Checkbox>
            <Typography.Text type="secondary">{t("services.form.withSshHint")}</Typography.Text>
            {/* 勾了才要公钥:后端对 with_ssh 服务要求 ssh_key_ids 非空 */}
            {withSsh && <SshKeyPicker value={keyIds} onChange={setKeyIds} />}
          </Space>
          <Space orientation="vertical" size={4} style={{ width: "100%" }}>
            <Typography.Text type="secondary">{t("services.form.strategyLabel")}</Typography.Text>
            <Typography.Text>{t("services.form.strategyRecreate")}</Typography.Text>
          </Space>
          <Descriptions
            size="small"
            title={t("services.form.summaryLabel")}
            column={{ xs: 1, sm: 2 }}
            items={[
              {
                label: t("services.form.summarySpec"),
                children: sku
                  ? isCpu
                    ? t("create.summaryCpu", { vcpu: sku.vcpu, mem: sku.mem_gb })
                    : t("instances.specLine", { model: sku.gpu_model, count: gpuCount })
                  : t("services.form.summaryNone"),
              },
              {
                label: t("services.form.summaryBilling"),
                children: sku
                  ? period && quote
                    ? fmt.formatPeriodPrice(quote.amount, period, periodCount)
                    : hourlyTotal
                      ? formatHourlyPrice(hourlyTotal)
                      : "—"
                  : t("services.form.summaryNone"),
              },
              {
                label: t("services.form.summaryImage"),
                children: imageRef || t("services.form.summaryNone"),
              },
              {
                label: t("services.form.summaryPort"),
                children: servicePort ?? t("services.form.summaryNone"),
              },
              {
                label: t("services.form.summaryHealth"),
                children: healthPath.trim() || t("services.form.summaryNone"),
              },
              {
                label: t("services.form.summaryAuth"),
                children: requireApiKey
                  ? t("services.form.authRequire")
                  : t("services.form.authPublic"),
              },
              {
                label: t("services.form.summarySsh"),
                children: withSsh ? t("services.detail.sshOn") : t("services.detail.sshOff"),
              },
            ]}
          />
        </Space>
      </Card>
    </div>
  );

  return (
    // 不用 Space(ant-space-item 包装会破坏 sticky 结算条的包含块)
    <div style={{ display: "flex", flexDirection: "column", gap: 16, width: "100%" }}>
      <Typography.Title level={4} style={{ margin: 0 }}>
        {t("services.deploy")}
      </Typography.Title>
      {wide ? (
        <Row gutter={16} wrap={false}>
          <Col flex="160px">
            <div style={{ position: "sticky", top: 88 }}>{steps}</div>
          </Col>
          <Col flex="auto" style={{ minWidth: 0 }}>
            {sections}
          </Col>
        </Row>
      ) : (
        sections
      )}

      {walletQ.isError && <DataErrorAlert onRetry={() => void walletQ.refetch()} />}
      <CheckoutBar
        changeKey={sku ? `${sku.id}-${mode}-${periodCount}-${gpuCount}` : "none"}
        summary={
          sku
            ? isCpu
              ? t("create.summaryCpu", { vcpu: sku.vcpu, mem: sku.mem_gb })
              : t("create.summary", {
                  model: sku.gpu_model,
                  count: gpuCount,
                  vcpu: sku.vcpu * gpuCount,
                  mem: sku.mem_gb * gpuCount,
                })
            : t("services.form.specNeeded")
        }
        items={
          period && quote
            ? [
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
                  value: t("common.dailyApprox", {
                    amount: diskGb > 0 && diskPriceGbMonth ? diskDaily : "0.00",
                  }),
                },
                {
                  label: t("create.configCostLabel"),
                  value: !sku ? (
                    "--"
                  ) : isSpot ? (
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
          sku ? (
            <Space orientation="vertical" size={4} style={{ maxWidth: 360 }}>
              {period && quote ? (
                <PeriodQuoteRows quote={quote} gpuCount={gpuCount} cpu={isCpu} />
              ) : (
                <span>
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
          ) : undefined
        }
        balance={wallet?.balance ?? null}
        balanceReady={balanceReady}
        actions={
          <>
            <Button size="large" onClick={onCancel}>
              {t("create.cancel")}
            </Button>
            {walletQ.isError ? (
              <Tooltip title={t("services.form.walletQueryFailedRetry")}>
                <Button type="primary" size="large" disabled>
                  {submitLabel}
                </Button>
              </Tooltip>
            ) : !sku ? (
              <Tooltip title={t("services.form.specNeeded")}>
                <Button type="primary" size="large" disabled>
                  {submitLabel}
                </Button>
              </Tooltip>
            ) : !balanceReady ? (
              <Button type="primary" size="large" loading disabled>
                {submitLabel}
              </Button>
            ) : enough ? (
              <Tooltip title={canSubmit ? undefined : (firstIssue ?? undefined)}>
                <Button
                  type="primary"
                  size="large"
                  disabled={!canSubmit}
                  loading={pending}
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
        loading={pending}
        confirmLabel={t("services.form.ecoConfirm")}
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
        confirmLabel={t("services.form.ecoConfirm")}
        loading={pending}
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
