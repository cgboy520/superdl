/**
 * 创建实例:单栏卡片流(计费方式/已选规格/镜像/数据盘/SSH/名称)+ 底部结算条;经济档需知情同意。
 * 数据盘「新建」为行内直建:提交时先建盘再建实例;建盘成功而实例失败须提示盘已计费。
 */

import { isApiError, type DiskOut, type InstanceOut, type SkuMarketOut } from "@superdl/api-client";
import { compareAmounts, diskDailyEstimate, formatSizeGb, GPU_COUNT_STEPS, idemKeyOf, mulPrice } from "@superdl/ui";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import {
  Alert,
  App,
  Button,
  Card,
  Cascader,
  Checkbox,
  Form,
  Input,
  Modal,
  Radio,
  Select,
  Skeleton,
  Slider,
  Space,
  Table,
  Tabs,
  Tooltip,
  Typography,
} from "antd";
import { useMemo, useState } from "react";

import { useFormat } from "../lib/format";
import { useApiErrorText } from "../lib/apiError";
import { useAddSshKey, useCreateDisk, useCreateInstance } from "../api/mutations";
import { useDisks, useImages, usePolicies, useSkus, useSshKeys, useWallet } from "../api/queries";
import { ChipRow } from "../components/ChipRow";
import { CheckoutBar } from "../components/CheckoutBar";
import { DataErrorAlert } from "../components/QueryState";
import { BillingModeCard, skuColumns } from "../components/skuTable";
import { requireAuth } from "../lib/guard";

export const Route = createFileRoute("/_console/market_/create/$skuId")({
  validateSearch: (search: Record<string, unknown>): { gpus?: number } => {
    // 市场页带入的 GPU 数量(可改)
    const g = Number(search.gpus);
    return Number.isInteger(g) && g >= 1 && g <= 8 ? { gpus: g } : {};
  },
  beforeLoad: requireAuth,
  component: CreatePage,
});

function defaultDiskName(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `data-${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}`;
}

function CreatePage() {
  const { t } = useTranslation(["web", "shared"]);
  const fmt = useFormat();
  const { formatHourlyPrice } = fmt;
  const { skuId } = Route.useParams();
  const { gpus: gpusFromMarket } = Route.useSearch();
  const navigate = useNavigate();
  const { message } = App.useApp();

  const { data: skus, isLoading: skusLoading, isError: skusError, refetch: refetchSkus } = useSkus();
  const sku = (skus ?? []).find((s) => s.id === Number(skuId));

  const imagesQ = useImages();
  const keysQ = useSshKeys();
  const disksQ = useDisks();
  const { data: images } = imagesQ;
  const { data: keys } = keysQ;
  const { data: disks } = disksQ;
  const { data: wallet } = useWallet();
  const { data: policies } = usePolicies();

  const [gpuCount, setGpuCount] = useState(gpusFromMarket ?? 1);
  const [imageTab, setImageTab] = useState<"platform" | "custom">("platform");
  const [platformImage, setPlatformImage] = useState<string[]>();
  const [customImage, setCustomImage] = useState("");
  const [diskMode, setDiskMode] = useState<"none" | "new" | "existing">("none");
  const [newDiskName, setNewDiskName] = useState(defaultDiskName);
  const [newDiskGb, setNewDiskGb] = useState(100);
  const [existingDiskId, setExistingDiskId] = useState<number>();
  const [keyIds, setKeyIds] = useState<number[]>([]);
  const [name, setName] = useState("");
  const [ecoOpen, setEcoOpen] = useState(false);
  const [ecoChecked, setEcoChecked] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [keyForm] = Form.useForm<{ name: string; public_key: string }>();
  // 幂等键 = 本次挂载的 nonce + 参数快照:同参数重放同键;新进入本页才是新单
  const [formNonce] = useState(() => crypto.randomUUID());

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

  const errText = useApiErrorText();
  const create = useCreateInstance({
    // 错误统一在本页 doCreate 的 catch 里出(避免 NO_CAPACITY 引导与全局错误弹两条)
    silentError: true,
    onSuccess: (data) => {
      const inst = data as InstanceOut;
      message.success(t("create.creating", { name: inst.name }));
      void navigate({ to: "/instances" });
    },
  });
  const createDisk = useCreateDisk();
  const addKey = useAddSshKey({
    onSuccess: (key) => {
      message.success(t("create.keyAdded"));
      keyForm.resetFields();
      setKeyIds((ids) => (ids.includes(key.id) ? ids : [...ids, key.id]));
    },
  });

  // 规格三态:加载中骨架 / 加载失败可重试(绝不能渲染成「已下架」) / 真不存在才提示下架
  if (skusError && !skus) {
    return (
      <Space orientation="vertical" size={16} style={{ width: "100%" }}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          {t("create.title")}
        </Typography.Title>
        <DataErrorAlert onRetry={() => void refetchSkus()} />
      </Space>
    );
  }
  if (!skus) {
    return (
      <Space orientation="vertical" size={16} style={{ width: "100%" }}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          {t("create.title")}
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
          {t("create.title")}
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

  const diskPriceGbMonth = policies?.disk_price_gb_month;
  const diskGb =
    diskMode === "new"
      ? newDiskGb
      : diskMode === "existing"
        ? ((disks ?? []).find((d) => d.id === existingDiskId)?.size_gb ?? 0)
        : 0;
  // 「约 ¥X/日」为展示层估算(月价/30,BigInt 禁浮点);入账以后端日结为准
  const diskDaily = diskDailyEstimate(diskPriceGbMonth, diskGb);
  const hourlyTotal = mulPrice(sku.price_hourly, gpuCount);
  // BigInt 精确比较,禁浮点(与后端 require_balance_at_least 同口径:1 小时 GPU 费)。
  // 三态处理:未就绪 ≠ 余额为 0。
  const balanceReady = wallet != null;
  const enough = balanceReady && compareAmounts(wallet.balance, hourlyTotal) >= 0;

  const imageRef = imageTab === "platform" ? platformImage?.[3] : customImage.trim();
  const canSubmit = Boolean(imageRef) && keyIds.length > 0;

  const doCreate = async () => {
    setSubmitting(true);
    // 幂等键由本次提交的参数派生,失败时不轮换:响应丢失后重提不会开出第二台;
    // 参数变了键随之变,不会被上一次的结果遮住
    const idempotencyKey = idemKeyOf("inst", [
      formNonce,
      sku.id,
      gpuCount,
      imageRef ?? "",
      [...keyIds].sort((a, b) => a - b).join(","),
      name || null,
      diskMode,
      existingDiskId ?? null,
      diskMode === "new" ? newDiskName.trim() : null,
      diskMode === "new" ? newDiskGb : null,
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
        await create.mutateAsync({
          body: {
            sku_id: sku.id,
            gpu_count: gpuCount,
            image_ref: imageRef ?? "",
            ssh_key_ids: keyIds,
            name: name || null,
            data_disk_id: diskId,
          },
          idempotencyKey,
        });
      } catch (err) {
        // 刻意不换键:失败可能只是响应丢了而实例已经建好,换键会让重试开出第二台机器
        // silentError 模式下这里统一出提示:库存不足给换档引导,其余给错误原文
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

  const submit = () => {
    if (sku.tier === "shared_eco") {
      setEcoOpen(true);
      return;
    }
    void doCreate();
  };

  const gpuOptions = Array.from({ length: sku.max_gpus_per_instance }, (_, i) => i + 1).filter(
    (n) => GPU_COUNT_STEPS.includes(n) || n === sku.max_gpus_per_instance,
  );

  const columns = skuColumns({ fmt, t });

  return (
    // 不用 Space:其 ant-space-item 包装会让 sticky 结算条的包含块只剩自身高度
    <div style={{ display: "flex", flexDirection: "column", gap: 16, width: "100%" }}>
      <Typography.Title level={4} style={{ margin: 0 }}>
        {t("create.title")}
      </Typography.Title>

      <BillingModeCard />

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
          {/* 卡数选择受 available_count 约束:无库存档位禁用 + 提示 */}
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
                <Typography.Text type="warning" style={{ fontSize: 12 }}>
                  {t("copy.noStockForGpuCount")}
                </Typography.Text>
              ) : undefined
            }
          />
        </Space>
      </Card>

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
                      />
                      <Typography.Text type="secondary">{t("create.prewarmed")}</Typography.Text>
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
                    value={customImage}
                    onChange={(e) => setCustomImage(e.target.value)}
                  />
                  <Typography.Text type="secondary">
                    {t("create.customImageHint")}
                  </Typography.Text>
                </Space>
              ),
            },
            {
              key: "mine",
              label: <Tooltip title={t("copy.myImagesComingSoon")}>{t("create.tabMine")}</Tooltip>,
              disabled: true,
              children: null,
            },
          ]}
        />
      </Card>

      <Card title={t("create.diskCard")}>
        <Space orientation="vertical" size={12} style={{ width: "100%" }}>
          <Radio.Group
            value={diskMode}
            onChange={(e) => setDiskMode(e.target.value as typeof diskMode)}
            options={[
              { value: "none", label: t("create.diskNone") },
              { value: "new", label: t("create.diskNew") },
              { value: "existing", label: t("create.diskExisting") },
            ]}
          />
          {diskMode === "new" && (
            <>
              <Space size={12}>
                <Typography.Text type="secondary">{t("storage.nameLabel")}</Typography.Text>
                <Input
                  style={{ width: 260 }}
                  maxLength={64}
                  value={newDiskName}
                  onChange={(e) => setNewDiskName(e.target.value)}
                />
              </Space>
              <Slider
                min={policies?.disk_min_gb}
                max={policies?.disk_max_gb}
                step={10}
                value={newDiskGb}
                onChange={setNewDiskGb}
                disabled={!policies}
              />
              <Typography.Text type="secondary">
                {formatSizeGb(newDiskGb)}
                {diskPriceGbMonth
                  ? ` · ${t("common.gbMonthPrice", { price: diskPriceGbMonth })},${t("common.dailyApprox", { amount: diskDaily })}`
                  : ""}
                ;{t("create.diskAutoCreateNote")}
              </Typography.Text>
            </>
          )}
          {diskMode === "existing" &&
            (disksQ.isError ? (
              // 盘清单加载失败绝不伪装成「没有可挂载的盘」
              <DataErrorAlert onRetry={() => void disksQ.refetch()} />
            ) : (
              <Select
                style={{ width: 320 }}
                placeholder={t("create.selectDiskPlaceholder")}
                value={existingDiskId}
                onChange={setExistingDiskId}
                options={(disks ?? [])
                  .filter((d) => d.status === "active" && d.mounted_instance_id == null)
                  .map((d) => ({
                    value: d.id,
                    label: `${d.name}(${formatSizeGb(d.size_gb)})`,
                  }))}
                notFoundContent={t("create.noMountableDisks")}
              />
            ))}
          <Typography.Text type="secondary">
            {t("create.diskIndependentNote")}
          </Typography.Text>
        </Space>
      </Card>

      <Card title={t("create.sshCard")}>
        {keysQ.isError ? (
          // SSH key 查询失败绝不伪装成「你还没有密钥」(老客户会看到添加表单,
          // 提交又被 sshKeyDuplicate 拒绝——购买路径硬停)
          <DataErrorAlert onRetry={() => void keysQ.refetch()} />
        ) : (keys ?? []).length === 0 ? (
          <Space orientation="vertical" size={12} style={{ width: "100%" }}>
            <Alert type="warning" showIcon title={t("copy.sshKeyOnly")} />
            <Form
              form={keyForm}
              layout="inline"
              onFinish={(v) => addKey.mutate({ name: v.name, public_key: v.public_key })}
            >
              <Form.Item name="name" rules={[{ required: true, message: t("create.keyNameRequired") }]}>
                <Input placeholder={t("create.keyNamePlaceholder")} style={{ width: 160 }} />
              </Form.Item>
              <Form.Item
                name="public_key"
                rules={[{ required: true, message: t("create.keyContentRequired") }]}
                style={{ flex: 1 }}
              >
                <Input placeholder={t("create.keyPlaceholder")} />
              </Form.Item>
              <Form.Item>
                <Button type="primary" htmlType="submit" loading={addKey.isPending}>
                  {t("create.addKey")}
                </Button>
              </Form.Item>
            </Form>
          </Space>
        ) : (
          <Checkbox.Group
            value={keyIds}
            onChange={(v) => setKeyIds(v as number[])}
            options={(keys ?? []).map((k) => ({
              value: k.id,
              label: `${k.name}(${k.fingerprint.slice(0, 20)}…)`,
            }))}
          />
        )}
      </Card>

      <Card title={t("create.nameCard")}>
        <Input
          placeholder={t("create.namePlaceholder")}
          maxLength={64}
          value={name}
          onChange={(e) => setName(e.target.value)}
          style={{ width: 320 }}
        />
      </Card>

      <CheckoutBar
        summary={t("create.summary", { model: sku.gpu_model, count: gpuCount, vcpu: sku.vcpu * gpuCount, mem: sku.mem_gb * gpuCount })}
        items={[
          {
            label: t("create.dailyCostLabel"),
            hint: t("create.dailyCostHint"),
            value: t("common.dailyApprox", { amount: diskGb > 0 && diskPriceGbMonth ? diskDaily : "0.00" }),
          },
          { label: t("create.configCostLabel"), value: formatHourlyPrice(hourlyTotal) },
        ]}
        detail={
          <Space orientation="vertical" size={4} style={{ maxWidth: 360 }}>
            <span>
              {t("create.detailInstanceLine", {
                unit: formatHourlyPrice(sku.price_hourly),
                count: gpuCount,
                total: formatHourlyPrice(hourlyTotal),
              })}
            </span>
            <span>
              {diskGb > 0 && diskPriceGbMonth
                ? t("create.detailDiskLine", { size: diskGb, price: t("common.gbMonthPrice", { price: diskPriceGbMonth }) })
                : t("create.detailDiskNone")}
            </span>
            <Typography.Text type="secondary">
              {t("create.balanceNeedNote")}
            </Typography.Text>
          </Space>
        }
        balance={wallet?.balance ?? null}
        balanceReady={balanceReady}
        actions={
          <>
            <Button size="large" onClick={() => void navigate({ to: "/market" })}>
              {t("create.cancel")}
            </Button>
            {!balanceReady ? (
              // 余额未就绪:主 CTA 保持 primary + loading,不出现红色文案
              <Button type="primary" size="large" loading disabled>
                {t("create.createAndStart")}
              </Button>
            ) : enough ? (
              <Tooltip title={canSubmit ? undefined : t("create.selectImageAndKey")}>
                <Button
                  type="primary"
                  size="large"
                  disabled={!canSubmit}
                  loading={submitting || create.isPending}
                  onClick={submit}
                >
                  {t("create.createAndStart")}
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

      <Modal
        title={t("create.ecoModalTitle")}
        open={ecoOpen}
        onCancel={() => {
          setEcoOpen(false);
          setEcoChecked(false);
        }}
        footer={
          <Space>
            <Button
              onClick={() => {
                setEcoOpen(false);
                setEcoChecked(false);
              }}
            >
              {t("create.cancel")}
            </Button>
            <Button
              type="primary"
              disabled={!ecoChecked}
              loading={submitting || create.isPending}
              onClick={() => {
                setEcoOpen(false);
                void doCreate();
              }}
            >
              {t("create.ecoConfirm")}
            </Button>
          </Space>
        }
      >
        <ul style={{ paddingLeft: 20 }}>
          {[t("copy.ecoTierConsent.c1"), t("copy.ecoTierConsent.c2"), t("copy.ecoTierConsent.c3"), t("copy.ecoTierConsent.c4")].map((line) => (
            <li key={line} style={{ marginBottom: 8 }}>
              {line}
            </li>
          ))}
        </ul>
        <Checkbox checked={ecoChecked} onChange={(e) => setEcoChecked(e.target.checked)}>
          {t("create.ecoAgree")}
        </Checkbox>
      </Modal>
    </div>
  );
}
